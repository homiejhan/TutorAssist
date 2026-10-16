import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tutorassist import roster, workspace

FAKE = str(Path(__file__).parent / "fake_claude.py")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTORASSIST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TUTOR_CLAUDE_BIN", FAKE)
    ws = tmp_path / "ws"
    workspace.create(ws)
    roster.upsert_student(ws, {"name": "Ana", "subject": "Algebra I", "hw": True})
    from tutorassist.server import create_app
    app = create_app(ws)
    with TestClient(app, base_url="http://127.0.0.1:8787") as c:
        token = c.get("/").text.split('name="tutor-token" content="')[1].split('"')[0]
        c.headers["x-tutor-token"] = token
        c.ws = ws
        yield c


def wait_done(c, run_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        r = c.get(f"/api/runs/{run_id}").json()
        if r["run"]["status"] not in ("queued", "running"):
            return r
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_guards(client):
    assert client.post("/api/runs", json={"kind": "chat", "text": "hi"}, headers={"x-tutor-token": "nope"}).status_code == 403
    assert client.get("/api/state", headers={"host": "evil.example"}).status_code == 403


def test_state_and_roster_edit(client):
    st = client.get("/api/state").json()
    assert st["setupNeeded"] and st["roster"]["students"][0]["name"] == "Ana"
    r = client.put("/api/roster/student", json={"student": {"name": "Ben", "subject": "Geometry",
                                                             "strengths": ["a", "b"]}})
    assert r.status_code == 200
    assert [s["name"] for s in client.get("/api/roster").json()["students"]] == ["Ana", "Ben"]
    assert client.put("/api/roster/student", json={"student": {"name": ""}}).status_code == 400


def test_prep_run_end_to_end(client):
    r = client.post("/api/runs", json={"kind": "prep", "students": ["Ana"]}).json()
    done = wait_done(client, r["run"]["id"])
    run, events = done["run"], done["events"]
    assert run["status"] == "done"
    # material/ ends up with only the session PDF; the rest is moved into .work/ and reported
    assert run["files"] == ["material/2026-10-16/Ana_2026-10-16_Guided.pdf"]
    assert run["extraFiles"] == [".work/other/2026-10-16/Ana_2026-10-16_Extra_Practice.pdf"]
    assert sorted(p.name for p in (client.ws / "material/2026-10-16").iterdir()) == ["Ana_2026-10-16_Guided.pdf"]
    assert (client.ws / ".work/sources/2026-10-16/Ana_Guided.txt").exists()
    docs = client.get("/api/students/Ana/documents").json()["sessions"]
    day = next(s for s in docs if s["date"] == "2026-10-16")
    guided = day["docs"][0]
    assert guided["type"] == "Guided" and guided["pdf"].endswith("Ana_2026-10-16_Guided.pdf")
    assert guided["source"] == ".work/sources/2026-10-16/Ana_Guided.txt"
    assert [d["pdf"] for d in day["docs"][1:]] == [None] * 4
    assert day["other"][0]["name"] == "Ana_2026-10-16_Extra_Practice.pdf"
    r = client.get("/files/" + guided["source"] + "?download=1")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    kinds = [e["t"] for e in events]
    assert kinds[-1] == "done" and "init" in kinds and "files" in kinds
    sent = json.loads((client.ws / ".fake_last_args.json").read_text())
    assert "prep today — only for: Ana" in sent["prompt"] and "Run context" in " ".join(sent["args"])
    # SSE replay of a finished run ends with done
    with client.stream("GET", f"/api/runs/{run['id']}/stream") as s:
        lines = [l for l in s.iter_lines() if l.startswith("data:")]
    assert json.loads(lines[-1][5:])["t"] == "done"


def test_feedback_resumes_and_reports_roster(client):
    first = client.post("/api/runs", json={"kind": "chat", "text": "hello"}).json()
    wait_done(client, first["run"]["id"])
    conv = first["conversation"]["id"]
    second = client.post("/api/runs", json={"kind": "chat", "text": "feedback: Ana did well", "conversationId": conv}).json()
    done = wait_done(client, second["run"]["id"])
    sent = json.loads((client.ws / ".fake_last_args.json").read_text())
    assert "--resume" in sent["args"]
    assert done["run"]["rosterChanges"] == [{"name": "Ana", "change": "updated", "fields": ["strengths"]}]
    c = client.get(f"/api/conversations/{conv}").json()
    assert len(c["runs"]) == 2


def test_files_and_inbox(client):
    up = client.post("/api/inbox", files=[("files", ("work page.jpg", b"\xff\xd8fake", "image/jpeg"))]).json()
    assert up["paths"][0].startswith(".work/inbox/") and up["paths"][0].endswith("work page.jpg")
    assert client.get("/files/" + up["paths"][0]).status_code == 200
    assert client.get("/files/.tutor/config.json").status_code == 404
    assert client.get("/files/../../etc/passwd").status_code == 404


def test_config_and_manual_sessions(client):
    r = client.put("/api/config", json={"tutorName": "Alex", "timezone": "America/Chicago", "setupComplete": True})
    assert r.json()["tutorName"] == "Alex"
    assert client.put("/api/config", json={"timezone": "Mars/Olympus"}).status_code == 400
    import datetime as dt
    from zoneinfo import ZoneInfo
    today = dt.datetime.now(ZoneInfo("America/Chicago")).date().isoformat()
    s = client.post("/api/schedule/manual", json={"student": "Ana", "date": today, "start": "23:00", "end": "23:45"}).json()
    ev = s["days"][0]["events"][0]
    assert ev["student"] == "Ana" and ev["known"] and ev["minutes"] == 45


def test_chat_about_selected_student(client):
    r = client.post("/api/runs", json={"kind": "chat", "text": "make the eval shorter", "student": "Ana",
                                       "date": "2026-10-16"}).json()
    wait_done(client, r["run"]["id"])
    assert r["conversation"]["student"] == "Ana"
    assert r["run"]["prompt"] == "make the eval shorter"  # what the tutor sees
    sent = json.loads((client.ws / ".fake_last_args.json").read_text())["prompt"]
    assert "The tutor has Ana selected in the app" in sent and "make the eval shorter" in sent
    st = client.get("/api/state").json()
    assert any(c["student"] == "Ana" for c in st["conversations"])
    # a prep for one student is tagged to that student too
    p = client.post("/api/runs", json={"kind": "prep", "students": ["Ana"]}).json()
    wait_done(client, p["run"]["id"])
    assert p["conversation"]["student"] == "Ana"


def test_lost_claude_session_is_carried_over(client, monkeypatch):
    """After the workspace folder moves, Claude Code can't --resume older chats; the app
    starts a new session with a recap instead of failing."""
    first = client.post("/api/runs", json={"kind": "chat", "text": "make Ana's eval shorter"}).json()
    wait_done(client, first["run"]["id"])
    conv = first["conversation"]["id"]
    old_session = client.get(f"/api/conversations/{conv}").json()["conversation"]["sessionId"]
    monkeypatch.setenv("FAKE_FORGET_SESSIONS", "1")
    second = client.post("/api/runs", json={"kind": "chat", "text": "now add a graph", "conversationId": conv}).json()
    done = wait_done(client, second["run"]["id"])
    assert done["run"]["status"] == "done"
    sent = json.loads((client.ws / ".fake_last_args.json").read_text())
    assert "--resume" not in sent["args"]
    assert "Earlier in this conversation" in sent["prompt"] and "make Ana's eval shorter" in sent["prompt"]
    assert sent["prompt"].rstrip().endswith("now add a graph")
    events = client.get(f"/api/runs/{second['run']['id']}").json()["events"]
    assert not [e for e in events if e["t"] == "error"]
    new_session = client.get(f"/api/conversations/{conv}").json()["conversation"]["sessionId"]
    assert new_session and new_session != old_session


def test_source_editor_rebuilds_without_the_agent(client):
    import shutil as _sh
    if not _sh.which("pdflatex"):
        return
    src = ".work/sources/2026-10-16/Ana_Eval.txt"
    body = "Ana — Eval\n\nProblem 1. Factor $x^2-4$.\n\nAnswer Key\n1. $(x-2)(x+2)$\n"
    r = client.put("/api/sources", json={"path": src, "text": body}).json()
    assert r["ok"] and len(r["outputs"]) == 2 and "Built" in r["message"]
    assert (client.ws / src).read_text() == body
    docs = client.get("/api/students/Ana/documents").json()["sessions"][0]["docs"]
    by_type = {d["type"]: d for d in docs}
    assert by_type["Eval_Answers"]["pdf"] and by_type["Eval_Answers"]["source"] == src
    bad = client.put("/api/sources", json={"path": "roster.json", "text": "{}"})
    assert bad.status_code == 400
    assert client.put("/api/sources", json={"path": "../x/2026-10-16/Ana_Eval.txt", "text": "x"}).status_code == 400


def test_sources_the_agent_saves_are_built_after_the_run(client):
    import shutil as _sh
    if not _sh.which("pdflatex"):
        return
    r = client.post("/api/runs", json={"kind": "chat", "text": "write the eval for Ana"}).json()
    done = wait_done(client, r["run"]["id"], timeout=60)
    assert "material/2026-10-16/Ana_2026-10-16_Eval.pdf" in done["run"]["files"]
    assert "material/2026-10-16/Ana_2026-10-16_Eval_Answers.pdf" in done["run"]["files"]
    assert any(e["t"] == "build" and e["ok"] for e in done["events"])
