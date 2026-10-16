"""The local web app: a FastAPI server that serves the UI and drives runs."""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, books, calendar_ics, doctor, documents, roster, scheduler, studio, workspace
from .config import AppState, WorkspaceConfig
from .runs import RunManager

log = logging.getLogger("tutorassist.server")
WEB = Path(__file__).parent / "web"
SERVABLE_ROOTS = ("material", "uploads", "reference", ".work")
LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def create_app(ws: Path) -> FastAPI:
    ws = ws.expanduser().resolve()
    app_state = AppState.load()
    access_token = os.environ.get("TUTOR_ACCESS_TOKEN", "")  # set when hosting beyond localhost
    extra_hosts = {h.strip().lower() for h in os.environ.get("TUTOR_ALLOWED_HOSTS", "").split(",") if h.strip()}
    runs = RunManager(ws)
    stop = asyncio.Event()
    index_jobs: dict[str, dict] = {}

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(scheduler.loop(ws, runs, stop))
        yield
        stop.set()
        for rid in list(runs.procs):
            await runs.cancel(rid)
        task.cancel()

    app = FastAPI(title="TutorAssist", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    # ------------------------------------------------------------------ security
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if host not in LOOPBACK and host not in extra_hosts:
            return JSONResponse({"detail": "Host not allowed. Set TUTOR_ALLOWED_HOSTS to serve on this name."},
                                status_code=403)
        if access_token:
            key = request.query_params.get("key")
            if key == access_token:
                resp = RedirectResponse(str(request.url.remove_query_params("key")))
                resp.set_cookie("tutor_auth", access_token, httponly=True, samesite="strict", max_age=60 * 60 * 24 * 90)
                return resp
            if request.cookies.get("tutor_auth") != access_token:
                return HTMLResponse("<p>Open this app with the link that includes <code>?key=…</code>.</p>",
                                    status_code=401)
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("x-tutor-token") != app_state.token:
                return JSONResponse({"detail": "Missing or wrong app token."}, status_code=403)
        return await call_next(request)

    # ------------------------------------------------------------------ helpers
    def cfg() -> WorkspaceConfig:
        return WorkspaceConfig.load(ws)

    def tz() -> ZoneInfo:
        try:
            return ZoneInfo(cfg().timezone or "UTC")
        except Exception:  # noqa: BLE001
            return ZoneInfo("UTC")

    def today() -> dt.date:
        return dt.datetime.now(tz()).date()

    def safe_path(rel: str) -> Path:
        p = (ws / rel).resolve()
        if not str(p).startswith(str(ws) + os.sep) or p.relative_to(ws).parts[0] not in SERVABLE_ROOTS:
            raise HTTPException(404)
        return p

    def schedule_view() -> dict:
        sched = calendar_ics.load_schedule(ws)
        z = tz()
        t = today()
        names = {s.get("name", "").lower() for s in _roster_or_empty().get("students", [])}
        days: dict[str, list] = {}
        for ev in sched.get("events", []):
            start = calendar_ics.event_start(ev, z)
            item = {**ev, "date": start.date().isoformat(), "time": start.strftime("%H:%M") if "dateTime" in ev["start"] else "",
                    "known": (ev.get("student") or "").lower() in names}
            days.setdefault(start.date().isoformat(), []).append(item)
        return {
            "today": t.isoformat(),
            "refreshedAt": sched.get("refreshedAt"),
            "error": sched.get("error"),
            "warning": sched.get("warning"),
            "days": [{"date": d, "events": evs} for d, evs in sorted(days.items())],
            "manual": calendar_ics.load_manual(ws),
        }

    def _roster_or_empty() -> dict:
        try:
            return roster.load(ws)
        except roster.RosterError as e:
            return {"students": [], "error": str(e)}

    def materials() -> list[dict]:
        root = ws / "material"
        out = []
        if not root.exists():
            return out
        other_root = ws / ".work" / "other"
        dates = {p.name for p in root.iterdir() if p.is_dir()}
        if other_root.exists():
            dates |= {p.name for p in other_root.iterdir() if p.is_dir()}
        for date in sorted(dates, reverse=True):
            files = []
            d = root / date
            for f in sorted(d.iterdir()) if d.is_dir() else []:
                m = documents.DOC_RE.match(f.name)
                if not f.is_file() or not m:
                    continue
                src = ws / ".work" / "sources" / date / f"{m.group('student')}_{m.group('type')}.txt"
                if not src.exists() and m.group("type").endswith("_Answers"):
                    # one Eval / Homework source makes both the student copy and the answers
                    src = src.with_name(src.name.replace("_Answers.txt", ".txt"))
                files.append({
                    "name": f.name, "path": f"material/{date}/{f.name}", "ext": ".pdf",
                    "student": m.group("student"), "type": m.group("type").replace("_", " "),
                    "source": str(src.relative_to(ws)) if src.exists() else None,
                })
            od = other_root / date
            other = [{"name": f.name, "path": str(f.relative_to(ws)), "student": f.name.split("_")[0]}
                     for f in sorted(od.iterdir()) if f.is_file()] if od.is_dir() else []
            out.append({"date": date, "files": files, "other": other})
        return out

    # ------------------------------------------------------------------ pages
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        html = (WEB / "index.html").read_text(encoding="utf-8")
        html = html.replace("{{TOKEN}}", app_state.token).replace("{{VERSION}}", __version__)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.api_route("/files/{rel:path}", methods=["GET", "HEAD"])
    async def files(rel: str, download: bool = False):
        p = safe_path(rel)
        if not p.is_file():
            raise HTTPException(404)
        media = "text/plain; charset=utf-8" if p.suffix.lower() in (".txt", ".md", ".tex", ".log") else None
        return FileResponse(p, media_type=media, headers={"Cache-Control": "no-cache"}, filename=p.name,
                            content_disposition_type="attachment" if download else "inline")

    # ------------------------------------------------------------------ state
    @app.get("/api/state")
    async def state():
        c = cfg()
        convs = sorted(runs.conversations.values(), key=lambda x: x["updatedAt"], reverse=True)[:60]
        return {
            "version": __version__,
            "workspace": str(ws),
            "config": c.to_dict(),
            "setupNeeded": not c.setupComplete,
            "roster": _roster_or_empty(),
            "schedule": schedule_view(),
            "conversations": convs,
            "runs": {r.id: r.public() for r in runs.runs.values()
                     if any(r.id in cv["runIds"] for cv in convs)},
            "busy": runs.busy(),
            "specUpdate": (ws / "CLAUDE.md.new").exists(),
            "suggestedName": _os_full_name(),
        }

    @app.get("/api/events")
    async def app_events(request: Request):
        q = runs.listen()
        watched = {name: ws / name for name in ("roster.json", "schedule.json", "open-questions.md", "books.json")}

        def mtimes():
            return {k: (p.stat().st_mtime if p.exists() else 0) for k, p in watched.items()}

        async def gen():
            last = mtimes()
            try:
                yield "retry: 3000\n\n"
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=5)
                        yield f"data: {json.dumps(ev)}\n\n"
                    except asyncio.TimeoutError:
                        now = mtimes()
                        changed = [k for k in now if now[k] != last[k]]
                        last = now
                        for k in changed:
                            yield f"data: {json.dumps({'t': 'changed', 'what': k})}\n\n"
                        if not changed:
                            yield ": ping\n\n"
            finally:
                runs.unlisten(q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ roster
    @app.get("/api/roster")
    async def get_roster():
        return _roster_or_empty()

    @app.put("/api/roster/student")
    async def put_student(body: dict):
        if runs.busy():
            raise HTTPException(409, "The agent is working right now — save again when the run finishes.")
        try:
            s = roster.upsert_student(ws, body.get("student", {}), body.get("originalName"))
        except roster.RosterError as e:
            raise HTTPException(400, str(e)) from e
        return {"student": s}

    @app.delete("/api/roster/student/{name}")
    async def del_student(name: str):
        if runs.busy():
            raise HTTPException(409, "The agent is working right now — try again when the run finishes.")
        try:
            roster.delete_student(ws, name)
        except roster.RosterError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    # ------------------------------------------------------------------ schedule
    @app.get("/api/schedule")
    async def get_schedule():
        return schedule_view()

    @app.post("/api/schedule/refresh")
    async def refresh_schedule():
        names = [s.get("name", "") for s in _roster_or_empty().get("students", [])]
        await asyncio.to_thread(calendar_ics.refresh, ws, cfg(), names)
        return schedule_view()

    @app.post("/api/schedule/manual")
    async def add_manual(body: dict):
        try:
            calendar_ics.add_manual(ws, body["student"], body["date"], body["start"], body["end"])
        except (KeyError, ValueError) as e:
            raise HTTPException(400, f"Need student, date (YYYY-MM-DD), start and end (HH:MM): {e}") from e
        return await refresh_schedule()

    @app.delete("/api/schedule/manual/{item_id}")
    async def del_manual(item_id: str):
        calendar_ics.remove_manual(ws, item_id)
        return await refresh_schedule()

    @app.post("/api/calendar/test")
    async def test_calendar(body: dict):
        url = body.get("icsUrl", "")
        z = tz()
        start = dt.datetime.combine(today(), dt.time(0, 0), z)
        try:
            data = await asyncio.to_thread(calendar_ics.fetch_ics, url)
            evs = calendar_ics.parse_events(data, z, start, start + dt.timedelta(days=8))
        except calendar_ics.CalendarError as e:
            return {"ok": False, "error": str(e)}
        flt = (body.get("filter") or "").strip().lower()
        kept = [e for e in evs if not flt or flt in e["summary"].lower()]
        names = [s.get("name", "") for s in _roster_or_empty().get("students", [])]
        return {"ok": True, "total": len(evs), "kept": len(kept),
                "sample": [{"summary": e["summary"], "student": calendar_ics.match_student(e["summary"], names),
                            "start": e["start"].get("dateTime") or e["start"].get("date")} for e in kept[:12]]}

    # ------------------------------------------------------------------ config
    @app.get("/api/config")
    async def get_config():
        return cfg().to_dict()

    @app.put("/api/config")
    async def put_config(body: dict):
        merged = {**cfg().to_dict(), **body}
        new = WorkspaceConfig.from_dict(merged)
        try:
            ZoneInfo(new.timezone)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Unknown timezone {new.timezone!r}") from e
        new.save(ws)
        if "calendar" in body:
            names = [s.get("name", "") for s in _roster_or_empty().get("students", [])]
            await asyncio.to_thread(calendar_ics.refresh, ws, new, names)
        return new.to_dict()

    # ------------------------------------------------------------------ runs
    @app.post("/api/runs")
    async def start_run(body: dict):
        kind = body.get("kind", "chat")
        conv = body.get("conversationId") or None
        attachments = [a for a in body.get("attachments", []) if isinstance(a, str) and a.startswith(".work/inbox/")]
        student = (body.get("student") or "").strip() or None
        agent_prompt = None
        att_text = ""
        if attachments:
            att_text = "\n\nAttached files (read them):\n" + "\n".join(f"- {a}" for a in attachments)
        if kind == "prep":
            day = dt.date.fromisoformat(body.get("date") or today().isoformat())
            only = [s for s in body.get("students", []) if s]
            prompt = scheduler.prep_prompt(day, today(), only)
            if body.get("text"):
                prompt += f"\n\nNote from the tutor: {body['text'].strip()}"
            title = "Prep " + ("today" if day == today() else day.strftime("%a %b %-d")) + (
                f": {', '.join(only)}" if only else "")
            conv = None  # prep always starts clean (no-chat-history rule)
            student = only[0] if len(only) == 1 else None
        elif kind == "feedback":
            if not student:
                raise HTTPException(400, "Pick a student.")
            date = body.get("date") or today().isoformat()
            parts = [f"Post-session feedback for {student} (session on {date})."]
            if (body.get("answers") or "").strip():
                parts.append("Eval answers, in question order:\n" + body["answers"].strip())
            else:
                parts.append("No eval answers this time — notes only.")
            if (body.get("notes") or "").strip():
                parts.append("Notes:\n" + body["notes"].strip())
            prompt = "\n\n".join(parts)
            title = f"Feedback: {student}"
        else:
            prompt = (body.get("text") or "").strip()
            if not prompt and not attachments:
                raise HTTPException(400, "Type a message.")
            title = f"{student}: {prompt[:50]}" if student else ""
            if student:
                # The tutor picked this student in the app; say so, so "make the eval shorter" is unambiguous.
                date = body.get("date") or today().isoformat()
                when = "today's session" if date == today().isoformat() else f"the {date} session"
                agent_prompt = (f"[The tutor has {student} selected in the app; this message is about "
                                f"{student} and {when} unless it says otherwise.]\n\n{prompt}{att_text}")
        run = runs.start(kind=kind, prompt=prompt + att_text, agent_prompt=agent_prompt, title=title,
                         conversation_id=conv, attachments=attachments, student=student)
        return {"run": run.public(), "conversation": runs.conversations[run.conversationId]}

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        r = runs.runs.get(run_id)
        if not r:
            raise HTTPException(404)
        return {"run": r.public(), "events": runs.event_log(run_id)}

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        await runs.cancel(run_id)
        return {"ok": True}

    @app.get("/api/runs/{run_id}/stream")
    async def stream_run(run_id: str, request: Request, after: int = -1):
        r = runs.runs.get(run_id)
        if not r:
            raise HTTPException(404)
        q = runs.subscribe(run_id)

        async def gen():
            last = after
            try:
                for ev in runs.event_log(run_id):
                    if ev.get("seq", -1) > last:
                        last = ev["seq"]
                        yield f"data: {json.dumps(ev)}\n\n"
                        if ev["t"] == "done":
                            return
                if runs.runs[run_id].status not in ("queued", "running"):
                    yield f"data: {json.dumps({'t': 'done', 'status': runs.runs[run_id].status, 'seq': last + 1})}\n\n"
                    return
                while not await request.is_disconnected():
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if ev.get("seq", -1) <= last:
                        continue
                    last = ev["seq"]
                    yield f"data: {json.dumps(ev)}\n\n"
                    if ev["t"] == "done":
                        return
            finally:
                runs.unsubscribe(run_id, q)

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/conversations/{conv_id}")
    async def get_conversation(conv_id: str):
        c = runs.conversations.get(conv_id)
        if not c:
            raise HTTPException(404)
        return {"conversation": c, "runs": [{"run": runs.runs[i].public(), "events": runs.event_log(i)}
                                            for i in c["runIds"] if i in runs.runs]}

    @app.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str):
        c = runs.conversations.get(conv_id)
        if not c:
            raise HTTPException(404)
        if any(runs.runs[i].status in ("queued", "running") for i in c["runIds"] if i in runs.runs):
            raise HTTPException(409, "Stop the run first.")
        for i in c["runIds"]:
            runs.runs.pop(i, None)
            (runs.dir / f"{i}.jsonl").unlink(missing_ok=True)
        runs.conversations.pop(conv_id, None)
        runs._save()  # noqa: SLF001
        return {"ok": True}

    # ------------------------------------------------------------------ files in / out
    @app.post("/api/inbox")
    async def upload_inbox(files: list[UploadFile] = File(...)):
        day = today().isoformat()
        dest = ws / ".work" / "inbox" / day
        dest.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = _clean_name(f.filename or "file")
            target = _unique(dest / name)
            with open(target, "wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append(str(target.relative_to(ws)))
        return {"paths": saved}

    @app.get("/api/students/{name}/documents")
    async def student_documents(name: str):
        z = tz()
        sched = calendar_ics.load_schedule(ws)
        scheduled = []
        for ev in sched.get("events", []):
            if (ev.get("student") or "").lower() == name.strip().lower():
                st = calendar_ics.event_start(ev, z)
                scheduled.append({"date": st.date().isoformat(),
                                  "time": st.strftime("%H:%M") if "dateTime" in ev["start"] else "",
                                  "minutes": ev.get("minutes")})
        return {"student": name, "today": today().isoformat(),
                "sessions": documents.student_sessions(ws, name, scheduled)}

    @app.get("/api/materials")
    async def get_materials():
        return {"days": materials()}

    # ------------------------------------------------------------------ worksheet sources
    def source_path(rel: str) -> Path:
        p = (ws / (rel or "")).resolve()
        if not studio.source_info(ws, p):
            raise HTTPException(400, "Only a session source (.work/sources/DATE/Name_Type.txt) can be built.")
        return p

    @app.put("/api/sources")
    async def save_source(body: dict):
        """The app's editor: save a source's text and rebuild its PDFs (no agent, no tokens)."""
        if runs.busy():
            raise HTTPException(409, "The agent is working right now — save again when the run finishes.")
        p = source_path(body.get("path", ""))
        text = body.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "Send the text to save.")
        res = await asyncio.to_thread(studio.build_text, ws, str(p.relative_to(ws)), text)
        return {**res.to_dict(), "message": res.message()}

    @app.post("/api/sources/build")
    async def build_source(body: dict):
        p = source_path(body.get("path", ""))
        if not p.exists():
            raise HTTPException(404, "That source doesn't exist.")
        res = await asyncio.to_thread(studio.build_source, ws, p)
        return {**res.to_dict(), "message": res.message()}

    # ------------------------------------------------------------------ library
    @app.get("/api/library")
    async def get_library():
        return {"files": books.library(ws), "books": books.load(ws), "jobs": index_jobs}

    @app.put("/api/books")
    async def put_books(body: dict):
        try:
            books.save(ws, {**books.load(ws), **body})
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return books.load(ws)

    @app.post("/api/library/upload")
    async def upload_textbooks(files: list[UploadFile] = File(...)):
        dest = ws / "uploads" / "textbooks"
        dest.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = _clean_name(f.filename or "book.pdf")
            if not name.lower().endswith(".pdf"):
                raise HTTPException(400, f"{name}: textbooks must be PDFs")
            target = dest / name
            with open(target, "wb") as out:
                shutil.copyfileobj(f.file, out)
            saved.append(name)
        for name in saved:
            _start_index(name)
        return {"saved": saved}

    def _start_index(name: str) -> None:
        pdf = ws / "uploads" / "textbooks" / name
        if not pdf.exists() or index_jobs.get(name, {}).get("status") == "running":
            return
        script = ws / ".claude" / "skills" / "textbook-interpreter" / "scripts" / "build_index.py"
        index_jobs[name] = {"status": "running", "detail": ""}

        async def job():
            env = {**os.environ, "TEXTBOOK_INDEX_DIR": str(ws / ".textbook_index")}
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(script), str(pdf), cwd=str(ws), env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await proc.communicate()
            index_jobs[name] = {"status": "done" if proc.returncode == 0 else "error",
                                "detail": (out.decode()[-300:] if proc.returncode == 0 else err.decode()[-500:]).strip()}

        asyncio.create_task(job())

    @app.post("/api/library/index")
    async def index_book(body: dict):
        _start_index(body.get("file", ""))
        return {"jobs": index_jobs}

    # ------------------------------------------------------------------ open questions, doctor, setup
    @app.get("/api/open-questions")
    async def get_oq():
        p = ws / "open-questions.md"
        return {"markdown": p.read_text(encoding="utf-8") if p.exists() else ""}

    @app.put("/api/open-questions")
    async def put_oq(body: dict):
        (ws / "open-questions.md").write_text(body.get("markdown", ""), encoding="utf-8")
        return {"ok": True}

    doctor_cache: dict = {}

    @app.get("/api/doctor")
    async def get_doctor(full: bool = False, fresh: bool = False):
        # Each check starts `claude` subprocesses; cache the quick check for a few minutes.
        now = asyncio.get_running_loop().time()
        if not full and not fresh and doctor_cache.get("at", -1e9) > now - 300:
            return {"checks": doctor_cache["checks"]}
        checks = await asyncio.to_thread(doctor.run_all, ws, full)
        if not full:
            doctor_cache.update(at=now, checks=checks)
        return {"checks": checks}

    @app.post("/api/setup/import")
    async def setup_import(body: dict):
        src = Path(body.get("path", "")).expanduser()
        if runs.busy():
            raise HTTPException(409, "Wait for the current run to finish.")
        try:
            notes = await asyncio.to_thread(workspace.import_legacy, src, ws, bool(body.get("withMaterial", True)))
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            raise HTTPException(400, str(e)) from e
        return {"notes": notes}

    @app.post("/api/workspace/sync")
    async def sync_spec(body: dict):
        notes = await asyncio.to_thread(workspace.sync_managed, ws, bool(body.get("forceSpec")))
        return {"notes": notes}

    @app.post("/api/reveal")
    async def reveal(body: dict):
        """Open a file or the workspace in Finder / the file manager (local use only)."""
        rel = body.get("path") or ""
        p = safe_path(rel) if rel else ws
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        if shutil.which(opener):
            args = [opener, "-R", str(p)] if sys.platform == "darwin" and p.is_file() else [opener, str(p)]
            subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # noqa: S603
        return {"ok": True}

    return app


def _os_full_name() -> str:
    """The computer account's full name, to pre-fill setup."""
    try:
        if sys.platform == "darwin":
            return subprocess.run(["id", "-F"], capture_output=True, text=True, timeout=3).stdout.strip()
        import pwd

        return pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    except Exception:  # noqa: BLE001
        return ""


def _clean_name(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^\w.\- ()&,'+]", "_", name).strip() or "file"
    return name[:180]


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        cand = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not cand.exists():
            return cand
    return p.with_name(f"{p.stem}-{uuid.uuid4().hex[:6]}{p.suffix}")
