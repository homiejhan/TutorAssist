"""books.json (textbook key registry) and the textbook library on disk."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .config import write_json_atomic


def books_path(ws: Path) -> Path:
    return ws / "books.json"


def load(ws: Path) -> dict:
    try:
        data = json.loads(books_path(ws).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("generalNotes", "")
    data.setdefault("books", [])
    data.setdefault("subjectDefaults", [])
    return data


def save(ws: Path, data: dict) -> None:
    keys = [b.get("key", "").strip() for b in data.get("books", [])]
    if any(not k for k in keys):
        raise ValueError("every book needs a key")
    if len(set(k.lower() for k in keys)) != len(keys):
        raise ValueError("book keys must be unique")
    write_json_atomic(books_path(ws), data)


def content_key(pdf: Path) -> str:
    """Same cache key the textbook-interpreter skill uses (build_index.py)."""
    h = hashlib.sha1()
    st = pdf.stat()
    h.update(str(st.st_size).encode())
    with open(pdf, "rb") as fh:
        h.update(fh.read(1 << 16))
        fh.seek(max(0, st.st_size - (1 << 16)))
        h.update(fh.read(1 << 16))
    return h.hexdigest()[:12]


def index_file_for(ws: Path, pdf: Path) -> Path:
    return ws / ".textbook_index" / f"{pdf.stem[:40]}_{content_key(pdf)}.json"


def library(ws: Path) -> list[dict]:
    """Every PDF in uploads/textbooks with its book key (if any) and index status."""
    data = load(ws)
    out = []
    tb_dir = ws / "uploads" / "textbooks"
    if not tb_dir.is_dir():
        return out
    for pdf in sorted(tb_dir.glob("*.pdf"), key=lambda p: p.name.lower()):
        key = None
        for b in data["books"]:
            prefix = (b.get("filePrefix") or "").strip()
            if prefix and pdf.name.startswith(prefix):
                key = b.get("key")
                break
        idx = index_file_for(ws, pdf)
        quality = None
        if idx.exists():
            try:
                quality = json.loads(idx.read_text())["extraction_quality"]["text_layer"]
            except Exception:  # noqa: BLE001 — a half-written index just reads as unknown
                quality = "unknown"
        out.append({
            "file": pdf.name,
            "path": f"uploads/textbooks/{pdf.name}",
            "sizeMB": round(pdf.stat().st_size / 1e6, 1),
            "key": key,
            "indexed": idx.exists(),
            "quality": quality,
        })
    return out


# --------------------------------------------------------------------------- import helper
_KEY_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*(.+?)\s*\|\s*`([^`]+)`\s*\|\s*$")


def _strip_md(s: str) -> str:
    return re.sub(r"[*_`]", "", s).strip()


def parse_legacy_spec(text: str) -> dict:
    """Pull the book-key and subject-default tables out of a Cowork-era CLAUDE.md."""
    books, defaults = [], []
    notes = ""
    lines = text.splitlines()
    in_subject_table = False
    for line in lines:
        m = _KEY_ROW.match(line)
        if m:
            key, book, prefix = m.groups()
            title, author = _strip_md(book), ""
            am = re.match(r"^(.*?),\s*\*(.+?)\*(.*)$", book)  # "Jacobs, *Elementary Algebra*"
            if am:
                author, title = _strip_md(am.group(1)), _strip_md(am.group(2) + am.group(3))
            books.append({"key": key, "title": title, "author": author, "filePrefix": prefix, "notes": ""})
            continue
        if line.strip().startswith("| Subject") and "Book order" in line:
            in_subject_table = True
            continue
        if in_subject_table:
            if not line.strip().startswith("|"):
                in_subject_table = False
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2 or set(cells[0]) <= set("-: "):
                continue
            keys = re.findall(r"`([^`]+)`", cells[1])
            leftover = re.sub(r"`[^`]+`|→|->|[\s;,]", "", cells[1])
            note = _strip_md(cells[1]) if leftover else ""
            defaults.append({"subject": cells[0], "books": keys, "note": note})
    m = re.search(r"\*\*Practical notes on querying these particular books\.\*\*(.+?)(?:\n\n\*\*|\n#)", text, re.S)
    if m:
        notes = re.sub(r"\s+", " ", m.group(1)).strip()
        # A sentence that names one book's title becomes that book's note too.
        for sentence in re.split(r"(?<=[.;])\s+(?=[A-Z])", notes):
            for b in books:
                core = re.sub(r"\s*\(.*?\)", "", b["title"]).strip()
                if len(core) > 15 and core.lower() in _strip_md(sentence).lower():
                    b["notes"] = (b["notes"] + " " + _strip_md(sentence)).strip()
    return {"generalNotes": notes, "books": books, "subjectDefaults": defaults}
