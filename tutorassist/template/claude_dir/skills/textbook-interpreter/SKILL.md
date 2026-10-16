---
name: textbook-interpreter
description: Index and query textbook PDFs cheaply instead of reading them raw. Use this skill WHENEVER a textbook, course book, or reference-book PDF is involved and the task is to find, extract, cite, or summarize anything from it — definitions, concept explanations, chapter/section locations, worked examples, step-by-step procedures, figure/table captions, or "where does the book cover X". Also use it before generating tutoring content, worksheets, or study material sourced from a textbook, and whenever tempted to run pdftotext/pdftoppm/pypdf on a large PDF — this skill's index-then-query workflow answers most questions in a few dozen output lines instead of whole-book reads. Do not use for PDF creation/editing (use the pdf skill) or for short non-book PDFs like papers, forms, or slides.
---

> **Running inside TutorAssist.** This copy of the skill lives in
> `.claude/skills/textbook-interpreter/` inside the tutor's workspace, and the workspace is
> your working directory. Wherever the instructions below mention `/home/claude`,
> `/mnt/user-data/...`, `/mnt/skills/...` or a `present_files` step, use the
> workspace instead: write `.txt` sources to `.work/sources/YYYY-MM-DD/` exactly as
> `CLAUDE.md` says (the app turns them into the session PDFs in `material/`), put scratch
> work in `.work/scratch/`, run scripts as `python3 .claude/skills/<skill>/<script>`, and
> skip any copy-to-outputs or present step — the app links every file listed in your
> final report.
> PyMuPDF is preinstalled, and `build_index.py` caches indexes in the workspace's `.textbook_index/` (or `$TEXTBOOK_INDEX_DIR`).

# Textbook Interpreter

Turns a textbook PDF into a queryable JSON index (structure, terms/definitions,
step-by-step processes, figure/table captions), then answers questions with
small targeted queries. Ground-truth verified at 640/640 randomized probes
across 9 college textbooks (354–1137 pages) plus 5 scanned K-12 math texts
(Jacobs, Savvas/Foerster, Holt McDougal, Kiselev — 100% on randomized
section/term/process probes).

v2 structure detection handles four printed-TOC dialects: college "N.M"
numbering, hyphenated "N-N" (Savvas/Foerster), K-12 ALL-CAPS chapters with
per-chapter "1. 2. 3." sections (Jacobs), and multi-line number/title/page
triplets (Kiselev). TOC pages are found even when the "Contents" header is a
graphic (dotted-leader detection, Holt-style), per-page-bookmark outlines are
rejected, and printed→PDF page offsets are calibrated automatically with
cumulative drift correction for books whose un-numbered insert pages shift
the mapping mid-book.

**The core rule: index once, query many, read pages only as a last resort.**
Never `cat` the index JSON, never `pdftotext` a whole book, never rasterize a
page unless a specific figure must be *seen*.

## Setup (once per session)

```bash
python3 -c "import pymupdf" 2>/dev/null || pip install --break-system-packages -q PyMuPDF
```

## Workflow

### Step 1 — Build/reuse the index (fast, cached)

```bash
python3 .claude/skills/textbook-interpreter/scripts/build_index.py "uploads/textbooks/book.pdf"
```

Prints the index path + a one-line stats summary. Cached by content hash, so
re-running is free. Typical cost: 4–35 s first time, instant after.
Capture the printed index path — every query needs it.

If it reports `quality=none`, the book is scanned with no text layer: the
index will be empty and only visual page reading works (rasterize single
pages with `pdftoppm -jpeg -r 150 -f N -l N`, then Read the image). If the
user will query such a book repeatedly, suggest OCR once via
`ocrmypdf --skip-text in.pdf out.pdf` and index the result instead.

### Step 2 — Answer questions with targeted queries

All subcommands print bounded output (tens of lines max).

```bash
Q=python3\ <skill>/scripts/query.py     # (illustrative; spell it out in real calls)

# What does the book cover? / find the right chapter
python3 <skill>/scripts/query.py outline INDEX --depth 2
python3 <skill>/scripts/query.py outline INDEX --chapter 4 --depth 3

# Definition / explanation of a concept  (fuzzy-matches indexed terms)
python3 <skill>/scripts/query.py term INDEX "superposition"

# Everything in one section: page range, sub-sections, its terms/figures/processes
python3 <skill>/scripts/query.py section INDEX 4.3
python3 <skill>/scripts/query.py section INDEX "node analysis"

# Figures / tables / diagrams by caption
python3 <skill>/scripts/query.py figures INDEX --contains "op amp" 
python3 <skill>/scripts/query.py figures INDEX --chapter 5 -n 20

# Step-by-step procedures and worked examples
python3 <skill>/scripts/query.py processes INDEX --contains "mesh analysis"
python3 <skill>/scripts/query.py processes INDEX --type procedure

# Full-text "where is X discussed" (when `term` misses — searches every page)
python3 <skill>/scripts/query.py locate INDEX "/path/book.pdf" "maximum power transfer"
```

### Step 3 — Read actual page text ONLY when the index answer is insufficient

```bash
# Hard-capped at 4 pages per call; applies broken-font repair automatically
python3 <skill>/scripts/query.py pages INDEX "/path/book.pdf" 155 156
```

Use `locate`/`term`/`section` first to learn *which* page, then pull just
that page. Rasterize (`pdftoppm`) only when a diagram/figure must be seen
visually — one page at a time, never in bulk.

## Choosing the right query

| Question shape | Query |
|---|---|
| "What's in this book?" / "which chapter covers X" | `outline`, then `section` |
| "Define X" / "what is X" | `term` → fallback `locate` |
| "How do I do X" / "steps for X" | `processes --contains` |
| "Show me the figure about X" / "what does Fig N.M show" | `figures` → `pages` or rasterize that one page |
| "Where does the book discuss X" | `locate` (gives page + section + snippet) |
| "Summarize section N.M" | `section` for scope → `pages` on its range (chunks of ≤4) |
| Quote or verbatim passage needed | `locate` for the page → `pages` |

## Interpreting index fields (when reading query output)

- All page numbers are **1-based PDF pages** (viewer numbering), not printed
  book pages.
- Term `confidence`: ≥0.8 = explicit definition sentence; 0.45–0.75 =
  emphasized vocabulary with its introducing sentence. Prefer high-confidence
  hits when citing definitions.
- Process `type`: `procedure` (cue-introduced steps) and `numbered_steps` are
  real how-to sequences; `worked_example` marks Example N.M + Solution spans
  (use `pages` on its range for the full solution); `enumerated_list` is an
  equation/item list, usually not instructional.
- `quality=partial/garbled` in build output: treat extracted text as
  possibly lossy; `U+FFFD` characters mark unrecoverable glyphs (usually
  ligatures/math symbols).

## Hard don'ts (these defeat the skill's purpose)

- Don't read the index JSON directly (`cat`, `view`, `json.load`+print) —
  it can be megabytes. The query subcommands exist for that.
- Don't extract or read more than ~8 pages of raw text for a single
  question; if that seems necessary, the question is really several
  questions — go back to `outline`/`section` to scope it.
- Don't rebuild the index with `--force` unless the PDF changed.
- Don't fall back to pypdf/pdfplumber/pdftotext for content questions —
  `locate` + `pages` already handle it, including broken-font repair that
  raw tools lack.

## Bundled implementation

`textbook_interpreter/` is the full extraction package (loader, quality
scoring, broken-font cmap repair, 3-strategy structure detection including
printed-TOC parsing with page-offset calibration, term/process/figure
analyzers, locator). `scripts/build_index.py` and `scripts/query.py` are the
only entry points needed; the package CLI
(`python3 -m textbook_interpreter --help`, run from the skill root) offers
`interpret`/`locate`/`info` directly, plus `--export-images DIR` on
`interpret` to pull embedded raster figures if the user asks for image files.

## Output format (index JSON, schema 1.0)

`source` (file, pages, title), `extraction_quality` (text_layer:
good|partial|garbled|none, notes, repair_shifts), `structure.chapters[]`
(number, title, level 1–3, page_start/end, nested sections), `terms[]`
(term, description, page, section, method, confidence), `processes[]`
(id, type, title, page span, section, steps[]), `figures[]` (label, kind
figure|table, number, caption, page, section), `stats`.
