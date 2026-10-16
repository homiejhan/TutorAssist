#!/usr/bin/env python3
"""Build (or reuse) a textbook index JSON for a PDF.

Idempotent: the index is cached under $TEXTBOOK_INDEX_DIR (default ./.textbook_index) keyed by a
content hash, so repeated calls in one session cost nothing. Prints ONLY the
index path and a one-line stats summary — never the JSON itself.

Usage:
    python3 build_index.py /path/to/book.pdf
    python3 build_index.py /path/to/book.pdf --force
"""

from __future__ import annotations

import argparse
import os
import hashlib
import json
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))

CACHE_DIR = Path(os.environ.get("TEXTBOOK_INDEX_DIR", ".textbook_index")).resolve()


def content_key(pdf: Path) -> str:
    h = hashlib.sha1()
    st = pdf.stat()
    h.update(str(st.st_size).encode())
    with open(pdf, "rb") as fh:
        h.update(fh.read(1 << 16))          # first 64 KB
        fh.seek(max(0, st.st_size - (1 << 16)))
        h.update(fh.read(1 << 16))          # last 64 KB
    return h.hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--force", action="store_true", help="rebuild even if cached")
    ap.add_argument("--pages", default=None, help="page range A-B (rarely needed)")
    args = ap.parse_args()

    pdf = args.pdf.resolve()
    if not pdf.exists():
        print(f"error: {pdf} not found", file=sys.stderr)
        return 2

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = content_key(pdf)
    out = CACHE_DIR / f"{pdf.stem[:40]}_{key}.json"

    if out.exists() and not args.force:
        d = json.loads(out.read_text())
        s, q = d["stats"], d["extraction_quality"]["text_layer"]
    else:
        from textbook_interpreter import TextbookInterpreter
        from textbook_interpreter.output import write_json

        start, end = 1, None
        if args.pages and "-" in args.pages:
            a, b = args.pages.split("-", 1)
            start, end = int(a), int(b)
        with TextbookInterpreter(pdf) as ti:
            doc = ti.interpret(page_start=start, page_end=end)
        write_json(doc, out)
        s, q = doc["stats"], doc["extraction_quality"]["text_layer"]

    print(out)
    print(
        f"quality={q} chapters={s['chapters']} sections={s['sections_total']} "
        f"terms={s['terms']} processes={s['processes']} "
        f"figures={s['figures']} tables={s['tables']}"
    )
    if q == "none":
        print("note: scanned PDF with no text layer — only visual page reading "
              "is possible for this book.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
