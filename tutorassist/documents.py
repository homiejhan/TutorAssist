"""The five session documents, and keeping material/ down to just those.

Per student, per session date, the agent produces at most five PDFs:

    material/YYYY-MM-DD/{Name}_{YYYY-MM-DD}_Guided.pdf
                        {Name}_{YYYY-MM-DD}_Eval.pdf
                        {Name}_{YYYY-MM-DD}_Eval_Answers.pdf
                        {Name}_{YYYY-MM-DD}_Homework.pdf
                        {Name}_{YYYY-MM-DD}_Homework_Answers.pdf

Everything else lives out of sight under .work/ and is shown in the app instead:
Worksheet Studio sources in .work/sources/DATE/{Name}_{Type}.txt, and anything else
that lands in material/ (variants, notes, legacy extras) in .work/other/DATE/.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

DOC_TYPES = [
    ("Guided", "Guided worksheet"),
    ("Eval", "Eval"),
    ("Eval_Answers", "Eval answers"),
    ("Homework", "Homework"),
    ("Homework_Answers", "Homework answers"),
]
TYPES = [t for t, _ in DOC_TYPES]
DOC_RE = re.compile(r"^(?P<student>.+)_(?P<date>\d{4}-\d{2}-\d{2})_(?P<type>Guided|Eval|Eval_Answers|Homework|"
                    r"Homework_Answers)\.pdf$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LATEX_JUNK = {".aux", ".log", ".out", ".tex", ".fls", ".fdb_latexmk", ".synctex"}


def is_session_doc(path: Path) -> bool:
    m = DOC_RE.match(path.name)
    return bool(m) and path.parent.name == m.group("date")


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(2, 1000):
        c = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not c.exists():
            return c
    return p


def tidy(ws: Path) -> int:
    """Move everything in material/ that isn't a session PDF into .work/. Idempotent.

    Also folds the older top-level inbox/ and scratch/ folders into .work/.
    Returns how many files moved. Only LaTeX by-products next to their PDF are deleted.
    """
    moved = 0
    material = ws / "material"
    if material.exists():
        for p in sorted(material.rglob("*")):
            if not p.is_file() or is_session_doc(p):
                continue
            if p.name == ".DS_Store":
                p.unlink(missing_ok=True)
                continue
            rel_parent = p.parent.relative_to(material)
            day = rel_parent.parts[0] if rel_parent.parts and DATE_RE.match(rel_parent.parts[0]) else "undated"
            if p.suffix in LATEX_JUNK and (p.with_suffix(".pdf").exists() or
                                           p.with_name(p.name.split(".")[0] + ".pdf").exists()):
                p.unlink(missing_ok=True)
                continue
            dest_root = ws / ".work" / ("sources" if p.suffix == ".txt" else "other") / day
            dest_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(_unique(dest_root / p.name)))
            moved += 1
        for d in sorted((d for d in material.rglob("*") if d.is_dir()), key=lambda d: len(d.parts), reverse=True):
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                pass
    for old, new in (("inbox", ".work/inbox"), ("scratch", ".work/scratch")):
        src = ws / old
        if src.is_dir():
            dst = ws / new
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                if item.name != ".DS_Store":
                    shutil.move(str(item), str(_unique(dst / item.name)))
            shutil.rmtree(src, ignore_errors=True)
    return moved


def _rel(ws: Path, p: Path) -> str:
    return str(p.relative_to(ws))


def student_sessions(ws: Path, name: str, scheduled: list[dict] | None = None) -> list[dict]:
    """Every session date that has (or will have) documents for this student, newest first.

    `scheduled` is a list of {"date", "time", "minutes"} for the student's calendar sessions.
    """
    key = name.strip().lower()
    days: dict[str, dict] = {}

    def day(d: str) -> dict:
        if d not in days:
            days[d] = {"date": d, "docs": {t: {"type": t, "label": lbl, "pdf": None, "source": None}
                                           for t, lbl in DOC_TYPES},
                       "other": [], "session": None}
        return days[d]

    material = ws / "material"
    if material.exists():
        for p in material.glob("*/*.pdf"):
            m = DOC_RE.match(p.name)
            if m and m.group("student").strip().lower() == key and p.parent.name == m.group("date"):
                day(m.group("date"))["docs"][m.group("type")]["pdf"] = _rel(ws, p)
    src_root = ws / ".work" / "sources"
    if src_root.exists():
        for p in src_root.glob("*/*.txt"):
            if not DATE_RE.match(p.parent.name):
                continue
            stem = p.stem
            prefix = f"{name.strip()}_".lower()
            if not stem.lower().startswith(prefix):
                continue
            rest = stem[len(prefix):]
            rest = re.sub(r"^\d{4}-\d{2}-\d{2}_", "", rest)
            if rest in TYPES:
                day(p.parent.name)["docs"][rest]["source"] = _rel(ws, p)
            else:
                day(p.parent.name)["other"].append({"name": p.name, "path": _rel(ws, p)})
    other_root = ws / ".work" / "other"
    if other_root.exists():
        for p in other_root.glob("*/*"):
            if p.is_file() and p.name.lower().startswith(f"{name.strip()}_".lower()):
                day(p.parent.name)["other"].append({"name": p.name, "path": _rel(ws, p)})
    for s in scheduled or []:
        day(s["date"])["session"] = {"time": s.get("time", ""), "minutes": s.get("minutes")}
    # One Eval / Homework source makes both PDFs: the student copy and the answers.
    for d in days.values():
        for base in ("Eval", "Homework"):
            ans = d["docs"][f"{base}_Answers"]
            if not ans["source"] and d["docs"][base]["source"]:
                ans["source"] = d["docs"][base]["source"]

    out = []
    for d in sorted(days.values(), key=lambda x: x["date"], reverse=True):
        d["docs"] = [d["docs"][t] for t in TYPES]
        d["other"].sort(key=lambda o: o["name"])
        out.append(d)
    return out
