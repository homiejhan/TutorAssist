"""Run bookkeeping: queueing, live event fan-out, persistence, post-run tidy-up.

A *conversation* is a thread the tutor sees in the chat panel; each message the
tutor sends (or each Prep / Feedback button press) is a *run* inside it. Runs
in the same conversation resume the same Claude Code session, so follow-ups
("make problem 3 easier") keep their context. A new Prep always starts a new
conversation — the spec's no-chat-history rule means prep must work from
roster.json alone.

Only one run executes at a time per workspace (they all write roster.json);
later ones wait in a queue.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import calendar_ics, documents, roster, studio
from .config import WorkspaceConfig, write_json_atomic
from .engine import AgentProcess, RunSpec

LATEX_JUNK = {".aux", ".log", ".out", ".tex", ".fls", ".fdb_latexmk"}


@dataclass
class Run:
    id: str
    conversationId: str
    kind: str  # prep | feedback | chat
    prompt: str  # what the tutor typed / the button sent (shown in the chat)
    title: str
    status: str = "queued"  # queued | running | done | error | stopped
    createdAt: str = ""
    startedAt: str = ""
    endedAt: str = ""
    sessionId: str = ""
    costUsd: float | None = None
    summary: str = ""
    files: list[str] = field(default_factory=list)
    extraFiles: list[str] = field(default_factory=list)
    rosterChanges: list[dict] = field(default_factory=list)
    source: str = "app"  # app | schedule | cli
    attachments: list[str] = field(default_factory=list)

    def public(self) -> dict:
        return asdict(self)


class RunManager:
    def __init__(self, ws: Path):
        self.ws = ws
        self.dir = ws / ".tutor" / "runs"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()
        self.runs: dict[str, Run] = {}
        self.events: dict[str, list[dict]] = {}
        self.subscribers: dict[str, list[asyncio.Queue]] = {}
        self.procs: dict[str, AgentProcess] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.listeners: list[asyncio.Queue] = []  # app-wide notifications (run status changes)
        self._load()

    # ------------------------------------------------------------------ persistence
    @property
    def _index_path(self) -> Path:
        return self.dir / "index.json"

    @property
    def _conv_path(self) -> Path:
        return self.ws / ".tutor" / "conversations.json"

    def _load(self) -> None:
        try:
            for raw in json.loads(self._index_path.read_text()):
                r = Run(**{k: v for k, v in raw.items() if k in Run.__dataclass_fields__})
                if r.status in ("queued", "running"):
                    r.status = "error"
                    r.summary = r.summary or "The app stopped before this run finished."
                self.runs[r.id] = r
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        try:
            self.conversations: dict[str, dict] = {c["id"]: c for c in json.loads(self._conv_path.read_text())}
        except (FileNotFoundError, json.JSONDecodeError):
            self.conversations = {}

    def _save(self) -> None:
        runs = sorted(self.runs.values(), key=lambda r: r.createdAt)[-500:]
        write_json_atomic(self._index_path, [r.public() for r in runs])
        convs = sorted(self.conversations.values(), key=lambda c: c["updatedAt"])[-200:]
        write_json_atomic(self._conv_path, convs)

    def event_log(self, run_id: str) -> list[dict]:
        if run_id in self.events:
            return self.events[run_id]
        p = self.dir / f"{run_id}.jsonl"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

    # ------------------------------------------------------------------ pub/sub
    def subscribe(self, run_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(run_id, []).append(q)
        return q

    def unsubscribe(self, run_id: str, q: asyncio.Queue) -> None:
        if q in self.subscribers.get(run_id, []):
            self.subscribers[run_id].remove(q)

    def listen(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.listeners.append(q)
        return q

    def unlisten(self, q: asyncio.Queue) -> None:
        if q in self.listeners:
            self.listeners.remove(q)

    def _notify(self, run: Run) -> None:
        for q in list(self.listeners):
            q.put_nowait({"t": "run", "run": run.public()})

    def _emit(self, run: Run, ev: dict) -> None:
        log = self.events.setdefault(run.id, [])
        ev = {**ev, "ts": time.time(), "seq": len(log)}
        log.append(ev)
        with open(self.dir / f"{run.id}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        for q in list(self.subscribers.get(run.id, [])):
            q.put_nowait(ev)

    # ------------------------------------------------------------------ public API
    def busy(self) -> bool:
        return any(r.status in ("queued", "running") for r in self.runs.values())

    def start(self, *, kind: str, prompt: str, agent_prompt: str | None = None, title: str = "",
              conversation_id: str | None = None, source: str = "app",
              attachments: list[str] | None = None, student: str | None = None) -> Run:
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if conversation_id and conversation_id in self.conversations:
            conv = self.conversations[conversation_id]
        else:
            conv = {"id": uuid.uuid4().hex[:12], "title": title or _title_from(prompt), "kind": kind,
                    "student": student, "sessionId": "", "createdAt": now, "updatedAt": now, "runIds": []}
            self.conversations[conv["id"]] = conv
        run = Run(id=uuid.uuid4().hex[:12], conversationId=conv["id"], kind=kind, prompt=prompt,
                  title=title or _title_from(prompt), createdAt=now, source=source,
                  attachments=attachments or [])
        conv["runIds"].append(run.id)
        conv["updatedAt"] = now
        self.runs[run.id] = run
        self.events[run.id] = []
        self._save()
        self._notify(run)
        self.tasks[run.id] = asyncio.create_task(self._execute(run, agent_prompt or prompt))
        return run

    async def cancel(self, run_id: str) -> None:
        run = self.runs.get(run_id)
        if not run:
            return
        if run.status == "queued":
            run.status = "stopped"
            task = self.tasks.get(run_id)
            if task:
                task.cancel()
            self._emit(run, {"t": "done", "status": "stopped"})
            self._save()
            self._notify(run)
        elif run_id in self.procs:
            await self.procs[run_id].cancel()

    def _recap(self, conv: dict, current_run_id: str, limit: int = 8) -> str:
        prior = [self.runs[r] for r in conv.get("runIds", []) if r != current_run_id and r in self.runs]
        lines = []
        for r in prior[-limit:]:
            lines.append(f"Tutor: {_clip(r.prompt, 1500)}")
            if r.summary:
                lines.append(f"You: {_clip(r.summary, 2500)}")
        if not lines:
            return ""
        return ("[Earlier in this conversation. The previous Claude session is no longer available, "
                "so here is what was said; any files it mentions are still in the workspace.]\n\n"
                + "\n\n".join(lines) + "\n\n[The tutor's new message follows.]\n\n")

    # ------------------------------------------------------------------ execution
    async def _execute(self, run: Run, agent_prompt: str) -> None:
        try:
            async with self.lock:
                await self._execute_locked(run, agent_prompt)
        except asyncio.CancelledError:
            return

    async def _execute_locked(self, run: Run, agent_prompt: str) -> None:
        cfg = WorkspaceConfig.load(self.ws)
        tz = ZoneInfo(cfg.timezone or "UTC")
        conv = self.conversations[run.conversationId]
        run.status = "running"
        run.startedAt = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self._save()
        self._notify(run)
        self._emit(run, {"t": "status", "text": "Refreshing the calendar…"})

        # 1. Fresh schedule.json for the agent.
        try:
            names = [s.get("name", "") for s in roster.load(self.ws).get("students", [])]
        except roster.RosterError:
            names = []
        sched = await asyncio.to_thread(calendar_ics.refresh, self.ws, cfg, names)
        if sched.get("error"):
            self._emit(run, {"t": "status", "text": f"Calendar problem: {sched['error']}"})

        # 2. Snapshots for the post-run report.
        try:
            roster_before = roster.load(self.ws)
        except roster.RosterError:
            roster_before = {"students": []}
        material_before = _snapshot(self.ws / "material")
        work_before = _snapshot(self.ws / ".work" / "other")
        sources_before = _snapshot(self.ws / ".work" / "sources")
        for d in ("sources", "scratch", "inbox", "other"):
            (self.ws / ".work" / d).mkdir(parents=True, exist_ok=True)

        now = dt.datetime.now(tz)
        stamped = f"[{now:%A %Y-%m-%d, %H:%M} {cfg.timezone} — sent from TutorAssist]\n\n{agent_prompt}"
        resume = conv.get("sessionId") or None
        prompt_text = stamped
        self._emit(run, {"t": "status", "text": "Starting Claude…"})
        final: dict[str, Any] | None = None
        error_text = ""
        for _attempt in range(2):
            spec = RunSpec(
                workspace=self.ws,
                prompt=prompt_text,
                system_append=run_context(cfg, run.kind),
                resume_session=resume,
                model=cfg.model,
                full_shell=cfg.shellAccess == "full",
            )
            proc = AgentProcess(spec)
            self.procs[run.id] = proc
            final, error_text, lost = None, "", False
            started = False
            source_writes: dict[str, tuple[str, float]] = {}  # tool id → (source, start)
            try:
                async for ev in proc.events():
                    if resume and not started and ev["t"] in ("result", "error") and _session_missing(ev):
                        lost = True  # Claude Code no longer has this chat (e.g. the folder moved)
                        continue
                    if ev["t"] == "init" and ev.get("sessionId"):
                        started = True
                        run.sessionId = ev["sessionId"]
                        conv["sessionId"] = ev["sessionId"]
                        self._save()
                    elif ev["t"] == "result":
                        final = ev
                        if ev.get("sessionId"):
                            run.sessionId = conv["sessionId"] = ev["sessionId"]
                    elif ev["t"] == "error":
                        error_text = ev["text"]
                    self._emit(run, ev)
                    if ev["t"] == "tool" and str(ev.get("path", "")).endswith(".txt"):
                        rel = _ws_rel(self.ws, ev["path"])
                        if rel and rel.startswith(".work/sources/"):
                            source_writes[ev.get("id", "")] = (rel, time.time() - 1)
                    elif ev["t"] == "tool_result" and ev.get("id") in source_writes:
                        rel, since = source_writes.pop(ev["id"])
                        report = _hook_report(self.ws, rel, since)
                        if report:
                            self._emit(run, {"t": "build", **report})
            except Exception as e:  # noqa: BLE001 — surface anything unexpected in the chat
                error_text = f"The app hit an error while running Claude: {e}"
                self._emit(run, {"t": "error", "text": error_text})
            finally:
                self.procs.pop(run.id, None)
            if not (lost and not started and not proc.cancelled):
                break
            # Carry the conversation into a fresh Claude session, with a recap from the
            # app's own record of it.
            conv["sessionId"] = ""
            resume = None
            prompt_text = self._recap(conv, run.id) + stamped
            self._emit(run, {"t": "status", "text": "Picking this conversation back up in a new Claude session…"})

        # 3. Post-run: build any source the agent saved whose PDFs aren't current (the hook
        #    normally built them already). material/ keeps only the five session PDFs per
        #    student: LaTeX by-products this run left are deleted, anything else is moved to
        #    .work/ (still shown in the app). Then report the files and roster changes.
        for rel, m in _snapshot(self.ws / ".work" / "sources").items():
            src = self.ws / ".work" / rel
            if sources_before.get(rel) == m or not studio.needs_build(self.ws, src):
                continue
            res = await asyncio.to_thread(studio.build_source, self.ws, src)
            self._emit(run, {"t": "build", "ok": res.ok, "text": res.message(), "final": True})
        changed = [p for p, m in _snapshot(self.ws / "material").items() if material_before.get(p) != m]
        for rel in changed:  # incl. odd names like X.pdf.tex from a bad -o
            if Path(rel).suffix in LATEX_JUNK:
                (self.ws / rel).unlink(missing_ok=True)
        await asyncio.to_thread(documents.tidy, self.ws)
        after = _snapshot(self.ws / "material")
        run.files = sorted(p for p, m in after.items() if material_before.get(p) != m)
        run.extraFiles = sorted(
            ".work/other/" + p.split("/", 1)[1] for p, m in _snapshot(self.ws / ".work" / "other").items()
            if work_before.get(p) != m)
        try:
            run.rosterChanges = roster.diff_students(roster_before, roster.load(self.ws))
        except roster.RosterError as e:
            run.rosterChanges = []
            self._emit(run, {"t": "error", "text": f"roster.json is no longer valid JSON after this run: {e}"})
        if run.files or run.extraFiles:
            self._emit(run, {"t": "files", "files": run.files, "extra": run.extraFiles})
        if run.rosterChanges:
            self._emit(run, {"t": "roster", "changes": run.rosterChanges})

        if proc.cancelled:
            run.status = "stopped"
        elif final and final.get("ok"):
            run.status = "done"
        else:
            run.status = "error"
        run.summary = (final or {}).get("text") or error_text
        run.costUsd = (final or {}).get("costUsd")
        run.endedAt = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        conv["updatedAt"] = run.endedAt
        self._emit(run, {"t": "done", "status": run.status})
        self._save()
        self._notify(run)
        self.events.pop(run.id, None)


def run_context(cfg: WorkspaceConfig, kind: str) -> str:
    return (
        "# Run context (from TutorAssist)\n"
        f"- Tutor: {cfg.tutorName or 'the tutor'}\n"
        f"- Tutor's timezone: {cfg.timezone}\n"
        "- Today's date and the current time are stamped at the top of each message — use the "
        "latest stamp as \"today\".\n"
        f"- This conversation started as a **{kind}** run. Follow CLAUDE.md "
        "(\"Running inside TutorAssist\" covers headless runs and the final report).\n"
    )


def _ws_rel(ws: Path, path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(ws.resolve()))
    except (ValueError, OSError):
        return ""


def _hook_report(ws: Path, source_rel: str, since: float) -> dict | None:
    from .studio.hook import report_path

    try:
        data = json.loads(report_path(ws, source_rel).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("at", 0) < since:
        return None
    return {"ok": bool(data.get("ok")), "text": str(data.get("text", ""))}


def _session_missing(ev: dict) -> bool:
    """Claude Code's answer to --resume with a session it doesn't have (for this folder)."""
    text = " ".join([ev.get("text") or "", *(ev.get("errors") or [])])
    return "No conversation found" in text


def _clip(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _title_from(prompt: str) -> str:
    line = " ".join(prompt.strip().split())
    return line[:60] + ("…" if len(line) > 60 else "")


def _snapshot(root: Path) -> dict[str, float]:
    if not root.exists():
        return {}
    base = root.parent
    return {str(p.relative_to(base)): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


def clear_scratch(ws: Path) -> None:
    s = ws / ".work" / "scratch"
    if s.exists():
        shutil.rmtree(s, ignore_errors=True)
    s.mkdir(parents=True, exist_ok=True)
