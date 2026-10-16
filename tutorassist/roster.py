"""roster.json (schemaVersion 4) — load, validate, edit.

The agent reads and writes this file during runs; the web UI edits it between
runs. Validation mirrors the schema in CLAUDE.md: exactly eight per-student
fields, three of which (coverNextSession / strengths / weaknesses) are the
student's entire teaching context.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .config import write_json_atomic

STUDENT_FIELDS = ["name", "subject", "hw", "status", "coverNextSession", "strengths", "weaknesses", "textbook"]
LIST_FIELDS = ("coverNextSession", "strengths", "weaknesses")
STATUSES = ("active", "paused")


class RosterError(ValueError):
    pass


def roster_path(ws: Path) -> Path:
    return ws / "roster.json"


def load(ws: Path) -> dict:
    p = roster_path(ws)
    if not p.exists():
        return {"schemaVersion": 4, "lastUpdated": "", "students": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RosterError(f"roster.json is not valid JSON (line {e.lineno}): {e.msg}") from e
    data.setdefault("students", [])
    return data


def save(ws: Path, data: dict, today: dt.date | None = None) -> None:
    errors = validate(data)
    if errors:
        raise RosterError("; ".join(errors))
    data["lastUpdated"] = (today or dt.date.today()).isoformat()
    write_json_atomic(roster_path(ws), data)


def normalize_student(raw: dict[str, Any]) -> dict:
    """Coerce a student dict from the UI into schema shape (drops unknown keys)."""
    s: dict[str, Any] = {
        "name": str(raw.get("name", "")).strip(),
        "subject": str(raw.get("subject", "")).strip(),
        "hw": bool(raw.get("hw", False)),
        "status": raw.get("status") if raw.get("status") in STATUSES else "active",
    }
    for f in LIST_FIELDS:
        items = raw.get(f) or []
        if isinstance(items, str):
            items = items.splitlines()
        s[f] = [str(x).strip() for x in items if str(x).strip()]
    tb = str(raw.get("textbook") or "").strip()
    if tb:
        s["textbook"] = tb
    return s


def validate(data: dict) -> list[str]:
    errs: list[str] = []
    students = data.get("students")
    if not isinstance(students, list):
        return ["`students` must be a list"]
    seen: set[str] = set()
    for i, s in enumerate(students):
        label = s.get("name") or f"student #{i + 1}"
        if not str(s.get("name", "")).strip():
            errs.append(f"{label}: name is required")
        key = str(s.get("name", "")).strip().lower()
        if key in seen:
            errs.append(f"{label}: duplicate name (names must be unique — they're matched against calendar titles)")
        seen.add(key)
        extra = set(s) - set(STUDENT_FIELDS)
        if extra:
            errs.append(f"{label}: unknown field(s) {sorted(extra)} — the schema allows only {STUDENT_FIELDS}")
        for f in LIST_FIELDS:
            if f in s and not (isinstance(s[f], list) and all(isinstance(x, str) for x in s[f])):
                errs.append(f"{label}: {f} must be a list of strings")
        if s.get("status") not in STATUSES:
            errs.append(f"{label}: status must be 'active' or 'paused'")
    return errs


def upsert_student(ws: Path, student: dict, original_name: str | None = None) -> dict:
    data = load(ws)
    s = normalize_student(student)
    if not s["name"]:
        raise RosterError("name is required")
    target = (original_name or s["name"]).strip().lower()
    for i, existing in enumerate(data["students"]):
        if existing.get("name", "").strip().lower() == target:
            data["students"][i] = s
            break
    else:
        data["students"].append(s)
    save(ws, data)
    return s


def delete_student(ws: Path, name: str) -> None:
    data = load(ws)
    before = len(data["students"])
    data["students"] = [s for s in data["students"] if s.get("name", "").strip().lower() != name.strip().lower()]
    if len(data["students"]) == before:
        raise RosterError(f"no student named {name!r}")
    save(ws, data)


def diff_students(before: dict, after: dict) -> list[dict]:
    """Per-student summary of what a run changed, for the run report."""
    b = {s.get("name", ""): s for s in before.get("students", [])}
    a = {s.get("name", ""): s for s in after.get("students", [])}
    out = []
    for name in sorted(set(a) | set(b)):
        if name not in b:
            out.append({"name": name, "change": "added", "fields": []})
        elif name not in a:
            out.append({"name": name, "change": "removed", "fields": []})
        else:
            fields = [f for f in STUDENT_FIELDS if b[name].get(f) != a[name].get(f)]
            if fields:
                out.append({"name": name, "change": "updated", "fields": fields})
    return out
