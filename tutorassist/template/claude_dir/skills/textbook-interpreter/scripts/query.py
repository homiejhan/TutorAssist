#!/usr/bin/env python3
"""Targeted, low-token queries against a textbook index (and its PDF).

Every subcommand prints a small, bounded result — designed so the answer to
a question costs tens of lines, not a full JSON dump or PDF read.

    outline   INDEX [--depth N] [--chapter N]      chapter/section tree
    term      INDEX QUERY [-n N]                   term/definition lookup (fuzzy)
    section   INDEX NUM_OR_TITLE                   one section: pages + its terms/figs/procs
    figures   INDEX [--contains S] [--chapter N] [-n N]
    processes INDEX [--type T] [--contains S] [-n N]
    locate    INDEX PDF PHRASE [-n N]              where is this phrase discussed
    pages     INDEX PDF A [B]                      raw text of pages A..B (max 4)

INDEX is the path printed by build_index.py.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys

try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT))


def load(index: Path) -> dict:
    return json.loads(Path(index).read_text())


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


# ---------------------------------------------------------------- outline --
def cmd_outline(args) -> int:
    d = load(args.index)
    chapters = d["structure"]["chapters"]
    if args.chapter:
        chapters = [c for c in chapters if c["number"] == str(args.chapter)]

    def walk(node, depth):
        if depth > args.depth:
            return
        num = f"{node['number']} " if node["number"] else ""
        print("  " * (depth - 1) + f"{num}{node['title']}  [p{node['page_start']}-{node['page_end']}]")
        for c in node["sections"]:
            walk(c, depth + 1)

    print(f"# {d['source']['file']} — {d['source']['pages']} pages, "
          f"structure via {d['structure']['source']}, "
          f"text quality: {d['extraction_quality']['text_layer']}")
    for c in chapters:
        walk(c, 1)
    return 0


# ------------------------------------------------------------------- term --
def _sing(text: str) -> str:
    """Light singularization so 'inverse function' matches 'Inverse Functions'."""
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss")
                    else w for w in text.split())


def _score_term(q: str, term: str) -> float:
    tq, tt = _sing(norm(q)), _sing(norm(term))
    if tq == tt:
        return 3.0
    # containment only at word boundaries — "position" must NOT match
    # "eigendecomposition"
    if re.search(r"\b" + re.escape(tq) + r"\b", tt):
        return 2.0
    if re.search(r"\b" + re.escape(tt) + r"\b", tq):
        return 1.6
    qw, tw = set(tq.split()), set(tt.split())
    hits = 0.0
    for a in qw:
        for b in tw:
            if a == b or (min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))):
                hits += 1.0
                break
    return hits / max(len(qw), 1)


def cmd_term(args) -> int:
    d = load(args.index)
    scored = sorted(
        ((_score_term(args.query, t["term"]), t) for t in d["terms"]),
        key=lambda x: (-x[0], x[1]["page"]),
    )
    hits = [t for s, t in scored if s >= 1.0][: args.n]
    if not hits:
        print(f"no indexed term matches {args.query!r} — try `locate` for a "
              f"full-text search, or `term` with a shorter query.")
        return 1
    for t in hits:
        sec = f" | {t['section']}" if t["section"] else ""
        print(f"• {t['term']}  [p{t['page']}{sec}] ({t['method']}, conf {t['confidence']})")
        print(f"  {t['description']}")
    return 0


# ---------------------------------------------------------------- section --
def _find_section(chapters, want):
    wantn = norm(str(want))
    best = None

    def walk(node):
        nonlocal best
        if node["number"] == str(want):
            best = node
            return
        if wantn and wantn in norm(node["title"]) and best is None:
            best = node
        for c in node["sections"]:
            walk(c)

    for c in chapters:
        walk(c)
        if best and best["number"] == str(want):
            break
    return best


def cmd_section(args) -> int:
    d = load(args.index)
    node = _find_section(d["structure"]["chapters"], args.which)
    if not node:
        print(f"section {args.which!r} not found — run `outline` to see the tree.")
        return 1
    num = f"{node['number']} " if node["number"] else ""
    a, b = node["page_start"], node["page_end"]
    print(f"{num}{node['title']}  [p{a}-{b}]")
    for c in node["sections"]:
        cn = f"{c['number']} " if c["number"] else ""
        print(f"  ↳ {cn}{c['title']}  [p{c['page_start']}-{c['page_end']}]")
    label = f"{node['number']} {node['title']}".strip() if node["number"] else node["title"]
    terms = [t for t in d["terms"] if a <= t["page"] <= b]
    figs = [f for f in d["figures"] if a <= f["page"] <= b]
    procs = [p for p in d["processes"] if a <= p["page_start"] <= b]
    print(f"contains: {len(terms)} terms, {len(figs)} figures/tables, {len(procs)} processes")
    for t in terms[:10]:
        print(f"  term: {t['term']}  [p{t['page']}]")
    for p in procs[:6]:
        print(f"  proc: [{p['type']}] {p['title'][:70]}  [p{p['page_start']}]")
    for f in figs[:8]:
        print(f"  fig:  {f['label']}  [p{f['page']}] {f['caption'][:60]}")
    if len(terms) > 10 or len(figs) > 8:
        print("  (truncated — use `term`/`figures` subcommands to drill in)")
    _ = label
    return 0


# ---------------------------------------------------------------- figures --
def cmd_figures(args) -> int:
    d = load(args.index)
    figs = d["figures"]
    if args.chapter:
        figs = [f for f in figs if f["number"].split(".")[0] == str(args.chapter)
                or (f["section"] or "").startswith(str(args.chapter))]
    if args.contains:
        q = norm(args.contains)
        figs = [f for f in figs if q in norm(f["caption"]) or q in norm(f["label"])]
    if not figs:
        print("no matching figures/tables in the index.")
        return 1
    for f in figs[: args.n]:
        sec = f" | {f['section']}" if f["section"] else ""
        print(f"• {f['label']}  [p{f['page']}{sec}] {f['caption']}")
    if len(figs) > args.n:
        print(f"({len(figs) - args.n} more — narrow with --contains/--chapter)")
    return 0


# -------------------------------------------------------------- processes --
def cmd_processes(args) -> int:
    d = load(args.index)
    procs = d["processes"]
    if args.type:
        procs = [p for p in procs if p["type"] == args.type]
    if args.contains:
        q = norm(args.contains)
        procs = [p for p in procs
                 if q in norm(p["title"]) or q in norm(p.get("section") or "")
                 or any(q in norm(s["text"]) for s in p["steps"])]
    if not procs:
        print("no matching processes in the index.")
        return 1
    for p in procs[: args.n]:
        sec = f" | {p['section']}" if p["section"] else ""
        print(f"• [{p['type']}] {p['title']}  [p{p['page_start']}-{p['page_end']}{sec}]")
        for s in p["steps"]:
            print(f"    {s['n']}. {s['text']}")
    if len(procs) > args.n:
        print(f"({len(procs) - args.n} more — narrow with --contains/--type)")
    return 0


# ----------------------------------------------------------------- locate --
def cmd_locate(args) -> int:
    from textbook_interpreter.loader import TextbookPdf
    from textbook_interpreter.locator import locate
    from textbook_interpreter.structure import SectionIndex
    from textbook_interpreter.models import SectionNode

    d = load(args.index)

    def to_node(c) -> SectionNode:
        return SectionNode(
            number=c["number"], title=c["title"], level=c["level"],
            page_start=c["page_start"], page_end=c["page_end"],
            sections=[to_node(x) for x in c["sections"]],
        )

    chapters = d["structure"]["chapters"]
    si = SectionIndex([to_node(c) for c in chapters])
    # skip front matter: TOC/preface mentions shadow the real coverage
    content_start = 1
    numbered = [c["page_start"] for c in chapters if c.get("number")]
    if numbered:
        content_start = min(numbered)
    with TextbookPdf(args.pdf) as pdf:
        shifts = d["extraction_quality"].get("repair_shifts") or []
        if shifts:
            pdf.enable_repair(shifts)
        hits = locate(pdf, args.phrase, si, max_matches=args.n,
                      page_start=content_start)
        if not hits and content_start > 1:   # fall back to full book
            hits = locate(pdf, args.phrase, si, max_matches=args.n)
    if not hits:
        print(f"{args.phrase!r} not found in the book text.")
        return 1
    for m in hits:
        sec = f" | {m.section}" if m.section else ""
        print(f"• p{m.page}{sec}")
        print(f"  {m.snippet}")
    return 0


# ------------------------------------------------------------------ pages --
def cmd_pages(args) -> int:
    from textbook_interpreter.loader import TextbookPdf

    d = load(args.index)
    a = args.start
    b = min(args.end or a, a + 3)          # hard cap: 4 pages per call
    with TextbookPdf(args.pdf) as pdf:
        shifts = d["extraction_quality"].get("repair_shifts") or []
        if shifts:
            pdf.enable_repair(shifts)
        for pno in range(a, b + 1):
            if pno < 1 or pno > pdf.page_count:
                continue
            print(f"===== page {pno} =====")
            print(pdf.page(pno, with_spans=False).text)
    return 0


# ------------------------------------------------------------------- main --
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("outline"); p.add_argument("index")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--chapter", default=None)
    p.set_defaults(fn=cmd_outline)

    p = sub.add_parser("term"); p.add_argument("index"); p.add_argument("query")
    p.add_argument("-n", type=int, default=5)
    p.set_defaults(fn=cmd_term)

    p = sub.add_parser("section"); p.add_argument("index"); p.add_argument("which")
    p.set_defaults(fn=cmd_section)

    p = sub.add_parser("figures"); p.add_argument("index")
    p.add_argument("--contains", default=None); p.add_argument("--chapter", default=None)
    p.add_argument("-n", type=int, default=12)
    p.set_defaults(fn=cmd_figures)

    p = sub.add_parser("processes"); p.add_argument("index")
    p.add_argument("--type", default=None); p.add_argument("--contains", default=None)
    p.add_argument("-n", type=int, default=5)
    p.set_defaults(fn=cmd_processes)

    p = sub.add_parser("locate"); p.add_argument("index"); p.add_argument("pdf")
    p.add_argument("phrase"); p.add_argument("-n", type=int, default=6)
    p.set_defaults(fn=cmd_locate)

    p = sub.add_parser("pages"); p.add_argument("index"); p.add_argument("pdf")
    p.add_argument("start", type=int); p.add_argument("end", type=int, nargs="?")
    p.set_defaults(fn=cmd_pages)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
