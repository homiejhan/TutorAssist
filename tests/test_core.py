import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tutorassist import books, calendar_ics, roster, workspace
from tutorassist.config import WorkspaceConfig
from tutorassist.engine import RunSpec, build_command, translate
from tutorassist.scheduler import due_auto_prep, prep_prompt

TZ = ZoneInfo("America/Chicago")

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:weekly-sam
DTSTART;TZID=America/Chicago:20261009T200000
DTEND;TZID=America/Chicago:20261009T213000
RRULE:FREQ=WEEKLY;BYDAY=FR
SUMMARY:Test Student 2
END:VEVENT
BEGIN:VEVENT
UID:one-off
DTSTART:20261016T190000Z
DTEND:20261016T200000Z
SUMMARY:Tutoring - Ana Lee
END:VEVENT
BEGIN:VEVENT
UID:dentist
DTSTART;TZID=America/Chicago:20261016T090000
DTEND;TZID=America/Chicago:20261016T100000
SUMMARY:Dentist
END:VEVENT
BEGIN:VEVENT
UID:cancelled
DTSTART;TZID=America/Chicago:20261017T090000
DTEND;TZID=America/Chicago:20261017T100000
SUMMARY:Test Student 2
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    workspace.create(w)
    return w


def test_workspace_layout(ws):
    for rel in ("CLAUDE.md", "roster.json", "books.json", "open-questions.md", ".claude/settings.json",
                ".claude/skills/worksheet-studio-pdf/SKILL.md",
                ".claude/skills/worksheet-studio-pdf/guided_template.txt",
                ".claude/skills/textbook-interpreter/scripts/build_index.py",
                ".claude/skills/sat-math-module/SKILL.md", ".tutor/config.json", "uploads/textbooks"):
        assert (ws / rel).exists(), rel
    # the generator is part of the app; the agent only gets the format reference
    assert not (ws / ".claude/skills/worksheet-studio-pdf/worksheet_gen.py").exists()
    spec = (ws / "CLAUDE.md").read_text()
    assert "Running inside TutorAssist" in spec


def test_spec_edits_are_preserved(ws):
    spec = ws / "CLAUDE.md"
    spec.write_text(spec.read_text() + "\n\n## My own rule\n")
    meta = ws / ".tutor" / "template.json"
    m = json.loads(meta.read_text())
    m["templateSpecSha"] = "older"
    meta.write_text(json.dumps(m))
    notes = workspace.sync_managed(ws)
    assert "My own rule" in spec.read_text()
    assert (ws / "CLAUDE.md.new").exists()
    assert any("CLAUDE.md.new" in n for n in notes)


def test_calendar_refresh(ws):
    cfg = WorkspaceConfig.from_dict({"timezone": "America/Chicago", "calendar": {"icsUrl": "https://x/y.ics"}})
    now = dt.datetime(2026, 10, 16, 8, 0, tzinfo=TZ)
    doc = calendar_ics.refresh(ws, cfg, ["Test Student 2", "Ana Lee", "Ana"], now=now, fetcher=lambda url: ICS)
    titles = [(e["summary"], e.get("student"), e["start"]["dateTime"][:16]) for e in doc["events"]]
    assert ("Dentist", None, "2026-10-16T09:00") in titles
    assert ("Tutoring - Ana Lee", "Ana Lee", "2026-10-16T14:00") in titles  # UTC converted, longest name wins
    assert ("Test Student 2", "Test Student 2", "2026-10-16T20:00") in titles  # weekly recurrence expanded
    assert ("Test Student 2", "Test Student 2", "2026-10-23T20:00") in titles
    assert not any(t[2].startswith("2026-10-17T09") for t in titles)  # cancelled dropped
    sam = next(e for e in doc["events"] if e["summary"] == "Test Student 2")
    assert sam["minutes"] == 90
    assert json.loads((ws / "schedule.json").read_text())["fetchedAt"] == "2026-10-16"


def test_calendar_filter_and_failure(ws):
    cfg = WorkspaceConfig.from_dict({"timezone": "America/Chicago",
                                     "calendar": {"icsUrl": "https://x/y.ics", "filter": "tutoring"}})
    now = dt.datetime(2026, 10, 16, 8, 0, tzinfo=TZ)
    doc = calendar_ics.refresh(ws, cfg, ["Ana"], now=now, fetcher=lambda url: ICS)
    assert [e["summary"] for e in doc["events"]] == ["Tutoring - Ana Lee"]

    def boom(url):
        raise calendar_ics.CalendarError("offline")
    doc = calendar_ics.refresh(ws, cfg, ["Ana"], now=now, fetcher=boom)
    assert "warning" in doc and doc["events"]  # same-day copy kept
    doc = calendar_ics.refresh(ws, cfg, ["Ana"], now=now + dt.timedelta(days=1), fetcher=boom)
    assert doc["error"] == "offline"


def test_manual_sessions(ws):
    cfg = WorkspaceConfig.from_dict({"timezone": "America/Chicago"})
    calendar_ics.add_manual(ws, "Test Student 1", "2026-10-16", "17:00", "18:15")
    doc = calendar_ics.refresh(ws, cfg, ["Test Student 1"], now=dt.datetime(2026, 10, 16, 8, 0, tzinfo=TZ))
    assert doc["events"][0]["student"] == "Test Student 1" and doc["events"][0]["minutes"] == 75


def test_roster_validation(ws):
    s = roster.upsert_student(ws, {"name": "Ana", "subject": "Algebra I", "hw": True,
                                    "coverNextSession": "Holt-A1 §5-2: Solving Systems by Substitution\n\n"})
    assert s["coverNextSession"] == ["Holt-A1 §5-2: Solving Systems by Substitution"]
    with pytest.raises(roster.RosterError):
        roster.upsert_student(ws, {"name": "  "})
    data = roster.load(ws)
    data["students"].append({"name": "ana", "status": "active"})
    assert any("duplicate" in e for e in roster.validate(data))
    data["students"][-1] = {"name": "Bo", "status": "active", "sessions": []}
    assert any("unknown field" in e for e in roster.validate(data))
    roster.upsert_student(ws, {"name": "Ana B", "subject": "Geometry"}, original_name="Ana")
    assert [x["name"] for x in roster.load(ws)["students"]] == ["Ana B"]


LEGACY_SPEC = """# Tutoring agent

Calendar: use calendar ID `abc123example@group.calendar.google.com` for sessions.

| Key | Book | File prefix |
|---|---|---|
| `Holt-A1` | Holt McDougal *Algebra 1* (2012) | `Holt McDougal Algebra 1` |
| `Foerster-AT` | Foerster, *Algebra and Trigonometry: Functions and Applications* | `Foerster Algebra and Trigonometry` |

| Subject | Book order |
|---|---|
| SAT Math | `Holt-A1` → `Foerster-AT` |
"""


def test_legacy_import(tmp_path):
    """A Cowork-era folder: an old CLAUDE.md with book tables, roster.json, textbooks."""
    src = tmp_path / "old"
    (src / "uploads" / "textbooks").mkdir(parents=True)
    (src / "CLAUDE.md").write_text(LEGACY_SPEC)
    sample = json.loads((workspace.template_root() / "sample.json").read_text())
    (src / "roster.json").write_text(json.dumps({"schemaVersion": 4, "calendarId": "x", "students": sample["students"]}))
    (src / "uploads" / "textbooks" / "Holt McDougal Algebra 1.pdf").write_bytes(b"%PDF-1.4")
    ws = tmp_path / "imported"
    notes = workspace.import_legacy(src, ws)
    data = roster.load(ws)
    assert [s["name"] for s in data["students"]] == ["Test Student 1", "Test Student 2"] and "calendarId" not in data
    b = books.load(ws)
    assert {x["key"] for x in b["books"]} == {"Holt-A1", "Foerster-AT"}
    assert b["subjectDefaults"][0]["books"] == ["Holt-A1", "Foerster-AT"]
    assert any("secret iCal" in n for n in notes)
    lib = books.library(ws)
    assert next(x for x in lib if x["file"].startswith("Holt"))["key"] == "Holt-A1"


def test_new_installs_get_the_example_students(tmp_path):
    ws = tmp_path / "fresh"
    workspace.create(ws, sample=True)
    assert [s["name"] for s in roster.load(ws)["students"]] == ["Test Student 1", "Test Student 2"]
    assert {x["key"] for x in books.load(ws)["books"]} == {"Holt-A1", "Foerster-AT"}
    assert workspace.only_sample_students(ws)  # so `tutor setup --import` may still replace them
    roster.upsert_student(ws, {"name": "Real Kid", "subject": "Algebra I"})
    assert not workspace.only_sample_students(ws)
    plain = tmp_path / "plain"
    workspace.create(plain)
    assert roster.load(plain)["students"] == []


def test_translate_and_command(tmp_path):
    evs = translate({"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "python3 x.py", "description": "Build PDF"}},
        {"type": "tool_use", "id": "b", "name": "TodoWrite", "input": {"todos": [{"content": "Prep Ana", "status": "in_progress"}]}},
    ]}})
    assert evs[0]["summary"] == "Build PDF" and evs[-1]["t"] == "todos"
    spec = RunSpec(workspace=tmp_path, prompt="p", system_append="ctx", resume_session="abc", model="sonnet")
    cmd = build_command("/bin/claude", spec)
    assert cmd[cmd.index("--resume") + 1] == "abc" and cmd[cmd.index("--model") + 1] == "sonnet"
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    # the app builds the PDFs; the agent has no business running LaTeX or page renderers
    assert "Bash" not in allowed and "Bash(python3 *)" in allowed and "Bash(pdflatex *)" not in allowed
    assert "Bash(curl *)" in cmd[cmd.index("--disallowedTools") + 1].split(",")
    spec.full_shell = True
    assert "Bash" in build_command("/bin/claude", spec)[cmd.index("--allowedTools") + 1].split(",")


def test_auto_prep_due():
    cfg = WorkspaceConfig.from_dict({"autoPrep": {"enabled": True, "time": "06:30", "days": [4]}})
    fri = dt.datetime(2026, 10, 16, 6, 45, tzinfo=TZ)  # a Friday
    assert due_auto_prep(cfg, {}, fri)
    assert not due_auto_prep(cfg, {"lastAutoPrep": "2026-10-16"}, fri)
    assert not due_auto_prep(cfg, {}, fri.replace(hour=6, minute=0))
    assert not due_auto_prep(cfg, {}, fri + dt.timedelta(days=1))
    assert prep_prompt(dt.date(2026, 10, 17), dt.date(2026, 10, 16), ["Sam"]) == "prep 2026-10-17 — only for: Sam"


def test_setup_never_reimports_over_live_roster(tmp_path, monkeypatch):
    from types import SimpleNamespace  # noqa: F401
    from tutorassist import setup
    monkeypatch.setenv("TUTORASSIST_HOME", str(tmp_path / "home"))
    legacy = tmp_path / "old"
    legacy.mkdir()
    (legacy / "CLAUDE.md").write_text("# old spec\n")
    (legacy / "roster.json").write_text('{"schemaVersion": 4, "students": [{"name": "Old", "status": "active"}]}')
    ws = tmp_path / "ws"
    setup.ensure_workspace(ws, str(legacy), None)
    assert [s["name"] for s in roster.load(ws)["students"]] == ["Old"]
    roster.upsert_student(ws, {"name": "New", "subject": "Geometry"})
    setup.ensure_workspace(ws, str(legacy), None)  # re-run with --import
    assert [s["name"] for s in roster.load(ws)["students"]] == ["Old", "New"]
    assert (ws / "CLAUDE.md").read_text().startswith("# TutorAssist — Operating Spec")


def test_tidy_keeps_only_session_pdfs(ws):
    from tutorassist import documents
    day = ws / "material" / "2026-10-03"
    day.mkdir(parents=True)
    keep = ["Test Student 2_2026-10-03_Guided.pdf", "Test Student 2_2026-10-03_Eval.pdf", "Test Student 2_2026-10-03_Eval_Answers.pdf",
            "Test Student 2_2026-10-03_Homework.pdf", "Test Student 2_2026-10-03_Homework_Answers.pdf", "Test Student 1_2026-10-03_Eval.pdf"]
    move = ["Test Student 2_2026-10-03_Eval_v2.pdf", "Test Student 2_2026-10-03_Extra_Practice.pdf", "notes.md"]
    for n in keep + move:
        (day / n).write_bytes(b"x")
    (day / "Test Student 2_Eval.txt").write_text("src")
    (day / "Test Student 2_2026-10-03_Eval.aux").write_text("junk")
    (ws / "material" / "2026-08-08").mkdir()
    (ws / "material" / "2026-08-08" / "Old_2026-08-08_Guided.tex").write_text("tex without a pdf")
    (ws / "inbox").mkdir(exist_ok=True)
    (ws / "inbox" / "photo.jpg").write_bytes(b"jpg")
    moved = documents.tidy(ws)
    assert sorted(p.name for p in day.iterdir()) == sorted(keep)
    assert (ws / ".work/sources/2026-10-03/Test Student 2_Eval.txt").exists()
    assert {p.name for p in (ws / ".work/other/2026-10-03").iterdir()} == set(move)
    assert (ws / ".work/other/2026-08-08/Old_2026-08-08_Guided.tex").exists()
    assert not (ws / "material" / "2026-08-08").exists()  # emptied folders are removed
    assert (ws / ".work/inbox/photo.jpg").exists() and not (ws / "inbox").exists()
    assert moved == 5
    assert documents.tidy(ws) == 0  # idempotent
    sessions = documents.student_sessions(ws, "test student 2", [{"date": "2026-10-20", "time": "19:00", "minutes": 90}])
    assert [s["date"] for s in sessions] == ["2026-10-20", "2026-10-03"]
    oct3 = sessions[1]
    assert all(d["pdf"] for d in oct3["docs"])
    assert oct3["docs"][1]["source"] == ".work/sources/2026-10-03/Test Student 2_Eval.txt"
    assert {o["name"] for o in oct3["other"]} == {"Test Student 2_2026-10-03_Eval_v2.pdf", "Test Student 2_2026-10-03_Extra_Practice.pdf"}
    assert sessions[0]["session"] == {"time": "19:00", "minutes": 90} and not any(d["pdf"] for d in sessions[0]["docs"])


def _old_install(home: Path) -> Path:
    """What a Mac install from when the app was called "Tutoring Agent" leaves behind."""
    old_ws = home / "TutoringAgent"
    workspace.create(old_ws)
    roster.upsert_student(old_ws, {"name": "Ana", "subject": "Algebra I", "hw": True})
    (home / ".tutor-agent").mkdir()
    (home / ".tutor-agent" / "app.json").write_text(json.dumps(
        {"workspace": str(old_ws), "token": "tok", "source": "/src/tutoring-agent-app"}))
    (home / ".tutor-agent" / "server.log").write_text("log")
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    (home / "Library" / "LaunchAgents" / "com.tutoring-agent.server.plist").write_text("<plist/>")
    (home / "Applications" / "Tutoring Agent.app" / "Contents").mkdir(parents=True)
    return old_ws


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    import importlib

    from tutorassist import migrate
    home = tmp_path / "h"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("TUTORASSIST_HOME", raising=False)
    monkeypatch.delenv("TUTOR_AGENT_HOME", raising=False)
    m = importlib.reload(migrate)  # its paths are computed from HOME at import
    yield home, m
    monkeypatch.undo()
    importlib.reload(migrate)


def test_migrates_an_install_from_the_old_name(fake_home):
    from tutorassist.config import AppState
    home, migrate = fake_home
    _old_install(home)
    assert migrate.pending()
    notes = migrate.run(stop_service=True)
    assert len(notes) == 4, notes
    assert not (home / ".tutor-agent").exists() and (home / ".tutorassist" / "server.log").exists()
    assert not (home / "TutoringAgent").exists()
    assert roster.load(home / "TutorAssist")["students"][0]["name"] == "Ana"
    st = AppState.load()
    assert st.workspace == str(home / "TutorAssist") and st.token == "tok" and st.source == "/src/tutoring-agent-app"
    assert not (home / "Library" / "LaunchAgents" / "com.tutoring-agent.server.plist").exists()
    assert not (home / "Applications" / "Tutoring Agent.app").exists()
    assert not migrate.pending() and migrate.run(stop_service=True) == []  # idempotent


def test_migration_waits_while_the_old_app_is_running(fake_home, monkeypatch):
    home, migrate = fake_home
    _old_install(home)
    monkeypatch.setattr(migrate, "server_running", lambda port=8787: True)
    assert migrate.run(stop_service=False) == []
    assert (home / "TutoringAgent").exists() and (home / ".tutor-agent").exists()
    monkeypatch.setattr(migrate, "server_running", lambda port=8787: False)
    migrate.run(stop_service=False)  # e.g. the old login item started the new code
    assert (home / "TutorAssist").exists() and (home / ".tutorassist").exists()
    # the old login item is only removed by setup / update
    assert (home / "Library" / "LaunchAgents" / "com.tutoring-agent.server.plist").exists()


def test_migration_leaves_a_custom_workspace_alone(fake_home):
    from tutorassist.config import AppState
    home, migrate = fake_home
    custom = home / "Documents" / "Tutoring"
    workspace.create(custom)
    (home / ".tutor-agent").mkdir()
    (home / ".tutor-agent" / "app.json").write_text(json.dumps({"workspace": str(custom), "token": "t"}))
    (home / "TutoringAgent").mkdir()  # an unrelated folder with the old name
    migrate.run(stop_service=True)
    assert AppState.load().workspace == str(custom)
    assert (home / "TutoringAgent").exists() and not (home / "TutorAssist").exists()
