"""Workspaces: create one from the template, keep its app-managed files current,
and import an existing Cowork / Claude Code tutoring folder.

Layout of a workspace (the agent's working directory)::

    CLAUDE.md            the agent's operating spec (tutor may edit)
    .claude/             settings.json (permissions) + skills/ (app-managed)
    roster.json          students — the only source of teaching context
    books.json           textbook keys, notes, default book per subject
    open-questions.md    proposals awaiting the tutor's sign-off
    schedule.json        written by the app from the calendar before each run
    uploads/textbooks/   textbook PDFs           uploads/  other reference PDFs
    material/YYYY-MM-DD/ ONLY the five PDFs per student per session (see documents.py)
    .work/sources/       Worksheet Studio sources of those PDFs (shown in the app)
    .work/inbox/         files the tutor attaches to requests
    .work/scratch/       the agent's temporary files (cleared daily)
    .work/other/         anything else that ended up in material/ (shown in the app)
    .textbook_index/     cached textbook indexes
    .tutor/              app state (config, runs, conversations) — agent never reads it
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from importlib import resources
from pathlib import Path

from . import TEMPLATE_VERSION, books, documents
from .config import WorkspaceConfig, write_json_atomic

DIRS = ["uploads/textbooks", "material", ".work/sources", ".work/inbox", ".work/scratch", ".work/other",
        ".textbook_index", ".tutor/runs"]
SEED_FILES = ["roster.json", "books.json", "open-questions.md"]  # copied once, then tutor-owned


def template_root() -> Path:
    # The template's .claude/ folder ships as claude_dir/ (packaging tools skip dot-dirs).
    return Path(str(resources.files("tutorassist") / "template"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _meta_path(ws: Path) -> Path:
    return ws / ".tutor" / "template.json"


def is_workspace(ws: Path) -> bool:
    return (ws / ".tutor" / "config.json").exists()


SAMPLE_STUDENTS = ("Test Student 1", "Test Student 2")


def create(ws: Path, sample: bool = False) -> list[str]:
    """Create (or complete) a workspace. Never overwrites tutor-owned files.

    sample=True (a brand-new install) adds the two example students from
    template/sample.json, and the two textbook keys their topics cite, to an empty roster.
    """
    ws = ws.expanduser().resolve()
    notes = []
    ws.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (ws / d).mkdir(parents=True, exist_ok=True)
    tpl = template_root()
    for name in SEED_FILES:
        if not (ws / name).exists():
            shutil.copy2(tpl / name, ws / name)
            notes.append(f"created {name}")
    if not WorkspaceConfig.path(ws).exists():
        WorkspaceConfig().save(ws)
    if sample:
        notes += _add_sample(ws)
    notes += sync_managed(ws)
    gi = ws / ".gitignore"
    if not gi.exists():
        gi.write_text(".tutor/\n.work/\n.textbook_index/\n")
    return notes


def _add_sample(ws: Path) -> list[str]:
    data = json.loads((template_root() / "sample.json").read_text())
    r = json.loads((ws / "roster.json").read_text())
    if r.get("students"):
        return []
    r["students"] = data["students"]
    write_json_atomic(ws / "roster.json", r)
    b = json.loads((ws / "books.json").read_text())
    have = {x.get("key") for x in b.get("books", [])}
    b["books"] = b.get("books", []) + [x for x in data["books"] if x["key"] not in have]
    if not b.get("subjectDefaults"):
        b["subjectDefaults"] = data["subjectDefaults"]
    write_json_atomic(ws / "books.json", b)
    return ["added two example students (Test Student 1 and 2) — edit or delete them on the Students page"]


def only_sample_students(ws: Path) -> bool:
    """True when the roster is empty or holds nothing but the example students."""
    try:
        names = [s.get("name") for s in json.loads((ws / "roster.json").read_text()).get("students", [])]
    except (OSError, json.JSONDecodeError):
        return True
    return all(n in SAMPLE_STUDENTS for n in names)


def sync_managed(ws: Path, force_spec: bool = False) -> list[str]:
    """Install/refresh the app-managed files: skills, settings.json, CLAUDE.md.

    CLAUDE.md is the tutor's to customise, so it is only replaced when it still
    matches the copy the app last installed (or doesn't exist). Otherwise the new
    version is written beside it as CLAUDE.md.new for the tutor to merge.
    """
    tpl = template_root()
    notes = []
    meta = {}
    try:
        meta = json.loads(_meta_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    skills_dst = ws / ".claude" / "skills"
    skills_dst.mkdir(parents=True, exist_ok=True)
    for skill in (tpl / "claude_dir" / "skills").iterdir():
        dst = skills_dst / skill.name
        if dst.exists():
            shutil.rmtree(dst)
        # worksheet_gen.py / render_png.py: older copies of the app shipped them in the skill;
        # the generator now lives in the app (tutorassist/studio), so never hand them to the agent
        shutil.copytree(skill, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "worksheet_gen.py",
                                                                  "render_png.py"))
    shutil.copy2(tpl / "claude_dir" / "settings.json", ws / ".claude" / "settings.json")

    # Shipped as agent-spec.md so Claude Code doesn't load it while you develop the app.
    spec, new_spec = ws / "CLAUDE.md", tpl / "agent-spec.md"
    installed_sha = meta.get("specSha", "")
    if not spec.exists() or force_spec or _sha(spec) == installed_sha:
        if _sha(spec) != _sha(new_spec):
            shutil.copy2(new_spec, spec)
            notes.append("installed the current CLAUDE.md")
        (ws / "CLAUDE.md.new").unlink(missing_ok=True)
        installed_sha = _sha(new_spec)
    elif _sha(new_spec) != meta.get("templateSpecSha"):
        shutil.copy2(new_spec, ws / "CLAUDE.md.new")
        notes.append("CLAUDE.md has your edits, so the updated spec was saved as CLAUDE.md.new — merge what you want")

    moved = documents.tidy(ws)
    if moved:
        notes.append(f"moved {moved} file(s) that weren't one of the five session PDFs out of material/ "
                     "(they're still shown in the app)")
    write_json_atomic(_meta_path(ws), {
        "templateVersion": TEMPLATE_VERSION,
        "templateHash": template_hash(),
        "specSha": installed_sha,
        "templateSpecSha": _sha(new_spec),
    })
    return notes


def template_hash() -> str:
    """Fingerprint of everything sync_managed installs, so any app update that
    changes a skill, the permissions, or the spec gets synced on next start."""
    h = hashlib.sha256()
    tpl = template_root()
    for p in sorted([tpl / "agent-spec.md", *(tpl / "claude_dir").rglob("*")]):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(str(p.relative_to(tpl)).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def needs_sync(ws: Path) -> bool:
    try:
        meta = json.loads(_meta_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return True
    return meta.get("templateHash") != template_hash()


# --------------------------------------------------------------------------- import
def import_legacy(src: Path, ws: Path, with_material: bool = True) -> list[str]:
    """Copy a Cowork-era tutoring folder (CLAUDE.md + roster.json + uploads/...) into a workspace.

    The source folder is only read, never modified, so the old setup keeps working.
    """
    src = src.expanduser().resolve()
    ws = ws.expanduser().resolve()
    if not (src / "roster.json").exists():
        raise FileNotFoundError(f"{src} has no roster.json — is that the right folder?")
    if src == ws:
        raise ValueError("Pick a new folder for the workspace; the old one is left untouched.")
    create(ws)
    notes: list[str] = []

    data = json.loads((src / "roster.json").read_text())
    data.pop("calendarId", None)  # the calendar now comes from the app's ICS link
    if "_comment" in data:
        tpl_comment = json.loads((template_root() / "roster.json").read_text())["_comment"]
        data["_comment"] = tpl_comment
    write_json_atomic(ws / "roster.json", data)
    notes.append(f"imported roster.json ({len(data.get('students', []))} students)")

    if (src / "open-questions.md").exists():
        _copy(src / "open-questions.md", ws / "open-questions.md")
        notes.append("imported open-questions.md")

    if (src / "CLAUDE.md").exists():
        parsed = books.parse_legacy_spec((src / "CLAUDE.md").read_text())
        if parsed["books"]:
            current = books.load(ws)
            current.update({k: v for k, v in parsed.items() if v})
            books.save(ws, current)
            notes.append(f"built books.json from the old CLAUDE.md ({len(parsed['books'])} book keys)")
        cal = re.search(r"calendar ID `([^`]+@group\.calendar\.google\.com)`", (src / "CLAUDE.md").read_text())
        if cal:
            notes.append(f"your old spec used Google calendar {cal.group(1)[:12]}… — paste that calendar's "
                         "secret iCal address in Settings → Calendar")

    def copy_tree(rel: str) -> None:
        s, d = src / rel, ws / rel
        if s.exists():
            shutil.copytree(s, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".DS_Store"),
                            copy_function=_copy)
            notes.append(f"copied {rel}/")

    copy_tree("uploads")
    copy_tree(".textbook_index")
    copy_tree("reference")
    if with_material:
        copy_tree("material")
        moved = documents.tidy(ws)
        if moved:
            notes.append(f"kept only the session PDFs in material/; {moved} other file(s) (worksheet sources, "
                         "extra versions) are still viewable in the app")
    return notes


def _copy(src, dst) -> str:
    """Copy contents + timestamps but make the copy writable (sources may be read-only)."""
    shutil.copyfile(src, dst)
    os.chmod(dst, 0o644)
    st = os.stat(src)
    os.utime(dst, (st.st_atime, st.st_mtime))
    return str(dst)
