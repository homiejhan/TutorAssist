#!/usr/bin/env python3
"""Bring the agent's skills, and Worksheet Studio's PDF generator, into the app.

The skills are authored for claude.ai (paths like /home/claude and
/mnt/user-data/outputs, a compile step, a `present_files` step). Inside TutorAssist:

* Worksheet Studio's generator (`worksheet_gen.py`) becomes part of the app, as
  `tutorassist/studio/worksheet_gen.py`. The app builds every PDF itself the moment the
  agent saves a `.txt` source, so the agent only ever writes plain text.
* The workspace copy of `worksheet-studio-pdf` keeps just the format reference
  (SKILL.md, rewritten for "write the txt, the app builds it") and the example files.
* `textbook-interpreter` and `sat-math-module` are copied with their paths adapted.

Re-run it whenever you improve a skill elsewhere:

    python3 scripts/vendor_skills.py /path/to/skills-dir

`/path/to/skills-dir` must contain worksheet-studio-pdf/, textbook-interpreter/ and
sat-math-module/. Every patch is asserted, so a skill whose text drifted fails loudly
instead of shipping half-adapted. Files in scripts/skill_extras/<skill>/ are added to
that skill's copy (the guided-worksheet template lives there).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ["worksheet-studio-pdf", "textbook-interpreter", "sat-math-module"]
DEST = ROOT / "tutorassist" / "template" / "claude_dir" / "skills"
GENERATOR_DEST = ROOT / "tutorassist" / "studio" / "worksheet_gen.py"
EXTRAS = ROOT / "scripts" / "skill_extras"
# Only these files of worksheet-studio-pdf go to the workspace: the agent never runs the
# generator, so it doesn't get a copy of it.
WORKSHEET_DOC_FILES = ("SKILL.md", "example_*.txt")

APP_NOTE = """
> **Running inside TutorAssist.** This copy of the skill lives in
> `.claude/skills/{name}/` inside the tutor's workspace, and the workspace is
> your working directory. Wherever the instructions below mention `/home/claude`,
> `/mnt/user-data/...`, `/mnt/skills/...` or a `present_files` step, use the
> workspace instead: write `.txt` sources to `.work/sources/YYYY-MM-DD/` exactly as
> `CLAUDE.md` says (the app turns them into the session PDFs in `material/`), put scratch
> work in `.work/scratch/`, run scripts as `python3 .claude/skills/<skill>/<script>`, and
> skip any copy-to-outputs or present step — the app links every file listed in your
> final report.
> {extra}
"""

EXTRA = {
    "worksheet-studio-pdf": (
        "**The PDF generator is built into the app.** You only write the `.txt`; saving it "
        "builds the PDFs and gets you a build report (see *Workflow* below). There is no "
        "script to run here."
    ),
    "textbook-interpreter": (
        "PyMuPDF is preinstalled, and `build_index.py` caches indexes in the "
        "workspace's `.textbook_index/` (or `$TEXTBOOK_INDEX_DIR`)."
    ),
    "sat-math-module": (
        "sympy is preinstalled for the verification step. The app builds the PDF from the "
        "`.txt` as soon as you save it (no compiler to run)."
    ),
}

APP_WORKFLOW = """## Workflow (inside TutorAssist)

TutorAssist has this skill's PDF generator built in, so the only thing you produce is
plain text:

1. Write the worksheet in the format below, with the Write tool, to
   `.work/sources/YYYY-MM-DD/{Student}_{Type}.txt`, where Type is `Guided`, `Eval` or
   `Homework` (CLAUDE.md says which documents a session gets).
2. The moment the file is saved, the app builds it into `material/YYYY-MM-DD/` and
   answers with a build report: the PDFs it made, the page count, which problem sits on
   which page, and anything wrong (a format error names the bad line; a LaTeX error
   quotes the text it choked on). Fix the `.txt` with Edit and it rebuilds. Never run a
   compiler, never render pages to images and never open the PDFs: the build report is
   your view of the pages.
3. An Eval or Homework source holds the questions *and* their `Answer Key` section. The
   app makes two PDFs from it: the student copy with no key, and `{Type}_Answers` with
   only the key. A Guided worksheet keeps its key at the end of the same PDF.
4. Always verify every answer computationally before writing the Answer Key.
   For graphs, also check that what's drawn matches what's asked: compute
   intercepts/intersections/vertices, and pick an `x:`/`y:` window that
   actually shows them.
   For figures, check that every label you *write* matches the drawn
   geometry (a side labeled 5 should be 5 long in the construction) — build
   shapes from the same measurements the problem states, or use `measure`
   to have the figure print the computed value.
5. Only when a source has `[figure]` blocks, the build report also lists preview images
   of the pages. Read those to check that labels are legible and nothing collides.

"""

# Problems holding a graph/figure/table were an unbreakable minipage. When one grew taller
# than a page (a long guided-worksheet problem with a graph), LaTeX silently cut off the
# rest: the practice part vanished while the Answer Key still had its answers. The patch
# measures the minipage first: one that fits is placed exactly as before; a taller one is
# typeset like every other problem instead (samepage, breaking between paragraphs).
OLD_PROBLEM_BLOCK = r"""        inner = "\\setlength{\\parskip}{\\wsparskip}\n" + "\n\n".join(parts)
        return ("\\noindent\\begin{minipage}{\\linewidth}\n" + inner
                + "\n\\end{minipage}" + workspace + tail)
"""
NEW_PROBLEM_BLOCK = r"""        inner = "\\setlength{\\parskip}{\\wsparskip}\n" + "\n\n".join(parts)
        # TutorAssist: measure the minipage first. A box taller than a page would be cut
        # off at the page edge, so such a problem flows like any other problem instead.
        need = "\\needspace{6\\baselineskip}\n" if title else ""
        return ("\\setbox\\wsprobbox=\\hbox{\\begin{minipage}{\\linewidth}\n" + inner
                + "\n\\end{minipage}}%\n"
                "\\ifdim\\dimexpr\\ht\\wsprobbox+\\dp\\wsprobbox\\relax>"
                "\\dimexpr\\textheight-2\\baselineskip\\relax\n"
                + need + "\\begin{samepage}\n" + "\n\n".join(parts) + "\n\\end{samepage}\n"
                "\\else\n\\noindent\\usebox\\wsprobbox\n\\fi" + workspace + tail)
"""

GENERATOR_PATCHES: list[tuple[str, str]] = [
    (OLD_PROBLEM_BLOCK, NEW_PROBLEM_BLOCK),
    ("\\usepackage{needspace}\n", "\\usepackage{needspace}\n\\newbox\\wsprobbox\n"),
]

PATCHES: dict[str, list[tuple[str, str, str]]] = {
    # (relative file, old, new)
    "textbook-interpreter": [
        ("scripts/build_index.py",
         'CACHE_DIR = Path("/home/claude/.txtbook_cache")',
         'CACHE_DIR = Path(os.environ.get("TEXTBOOK_INDEX_DIR", ".textbook_index")).resolve()'),
        ("scripts/build_index.py",
         "import argparse\n",
         "import argparse\nimport os\n"),
        ("scripts/build_index.py",
         "Idempotent: the index is cached under /home/claude/.txtbook_cache keyed",
         "Idempotent: the index is cached under $TEXTBOOK_INDEX_DIR (default ./.textbook_index) keyed"),
        ("SKILL.md",
         'python3 <skill>/scripts/build_index.py "/mnt/user-data/uploads/book.pdf"',
         'python3 .claude/skills/textbook-interpreter/scripts/build_index.py "uploads/textbooks/book.pdf"'),
    ],
    "worksheet-studio-pdf": [
        ("SKILL.md",
         "- Render and look at the pages; if a section spills a few lines onto a new\n"
         "  page, try `density: compact` or `spacing: none` before cutting content.",
         "- Read the build report; if a section spills a few lines onto a new page,\n"
         "  try `density: compact` or `spacing: none` before cutting content.\n"
         "- In TutorAssist the house format for guided worksheets is\n"
         "  `guided_template.txt` in this folder: follow its structure."),
        ("SKILL.md",
         "- Each problem is wrapped in a `samepage` block so it doesn't split across a\n  page break;",
         "- Each problem is kept together on one page when it fits (a problem taller\n"
         "  than a page flows onto the next one instead of being cut off);"),
        ("SKILL.md",
         "  a starred problem, currency, and an answer key) — run it once to sanity\n"
         "  check the toolchain: `python3 worksheet_gen.py example_input.txt -o example`.",
         "  a starred problem, currency, and an answer key)."),
    ],
    "sat-math-module": [
        ("SKILL.md",
         "3. **Draft the `.txt`** in `/home/claude/<slug>.txt` following the format",
         "3. **Draft the `.txt`** as the session's source, `.work/sources/YYYY-MM-DD/<Name>_Homework.txt`\n"
         "   (or `_Eval.txt`), following the format"),
    ],
}

# Whole sections replaced by regex: (skill, file, pattern, replacement).
SECTION_PATCHES = [
    ("worksheet-studio-pdf", "SKILL.md", r"## Workflow\n.*?(?=## Format spec)", APP_WORKFLOW),
    ("worksheet-studio-pdf", "SKILL.md",
     r"- `worksheet_gen\.py` — the parser.*?(?=- `example_input\.txt`)",
     "- The generator is built into TutorAssist; there is no script in this folder.\n"
     "- `guided_template.txt` — TutorAssist's house format for a guided worksheet:\n"
     "  per topic a titled problem with a key-idea box (and a gold rule box when a rule\n"
     "  trips students up), worked examples as bold-labeled bullets with a check, a\n"
     "  **Now you try.** block of (a)/(b)/(c) parts, and a worked Answer Key per part.\n"
     "  Read it before writing any guided worksheet.\n"),
    ("sat-math-module", "SKILL.md",
     r"6\. \*\*Compile\*\* with the worksheet-studio-pdf skill:.*?(?=\nIf the user asks for the text only)",
     "6. **Build:** the app builds the PDFs every time you save the `.txt` and replies\n"
     "   with a build report: pages, which questions sit on which page, and any error\n"
     "   with the line to fix. Put a `[settings spacing: compact]` line under the title\n"
     "   to keep a 27-item module near 8–10 pages. Fix the `.txt`, never anything else.\n"
     "7. **Check the build report** for split option lists or a graph/table that broke\n"
     "   across pages, and adjust the spacing settings if needed. Don't render pages.\n"
     "8. **Deliver:** list the PDFs in your final report (the app shows the `.txt`\n"
     "   source next to them).\n"),
]


def insert_note(skill_md: Path, name: str) -> None:
    text = skill_md.read_text()
    assert text.startswith("---"), f"{skill_md}: no frontmatter"
    end = text.index("\n---", 3) + len("\n---\n")
    note = APP_NOTE.format(name=name, extra=EXTRA[name]).rstrip() + "\n"
    if "Running inside TutorAssist" in text or "Running inside the Tutoring Agent app" in text:
        return
    skill_md.write_text(text[:end] + note + text[end:])


def patch(path: Path, old: str, new: str) -> None:
    t = path.read_text()
    if t.count(old) != 1:
        raise SystemExit(f"patch failed for {path}: {old[:70]!r} found {t.count(old)}x")
    path.write_text(t.replace(old, new))


def patch_section(path: Path, pattern: str, new: str) -> None:
    t = path.read_text()
    found = re.findall(pattern, t, flags=re.S)
    if len(found) != 1:
        raise SystemExit(f"section patch failed for {path}: {pattern[:60]!r} found {len(found)}x")
    path.write_text(re.sub(pattern, lambda _m: new, t, flags=re.S))


def vendor_generator(src: Path) -> None:
    text = src.read_text()
    for old, new in GENERATOR_PATCHES:
        if text.count(old) != 1:
            raise SystemExit(f"generator patch failed: {old[:70]!r} found {text.count(old)}x")
        text = text.replace(old, new)
    header = ("# Vendored into TutorAssist from the worksheet-studio-pdf skill by\n"
              "# scripts/vendor_skills.py (with its patches). Edit the skill, then re-vendor.\n")
    GENERATOR_DEST.parent.mkdir(parents=True, exist_ok=True)
    GENERATOR_DEST.write_text(text.replace("\nimport argparse\n", "\n" + header + "import argparse\n", 1))


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src_root = Path(sys.argv[1]).expanduser().resolve()
    DEST.mkdir(parents=True, exist_ok=True)
    for name in SKILLS:
        src = src_root / name
        if not (src / "SKILL.md").exists():
            print(f"missing {src}/SKILL.md", file=sys.stderr)
            return 1
        dst = DEST / name
        if dst.exists():
            shutil.rmtree(dst)
        if name == "worksheet-studio-pdf":
            vendor_generator(src / "worksheet_gen.py")
            dst.mkdir(parents=True)
            for pattern in WORKSHEET_DOC_FILES:
                for f in sorted(src.glob(pattern)):
                    shutil.copy2(f, dst / f.name)
        else:
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
        for rel, old, new in PATCHES.get(name, []):
            patch(dst / rel, old, new)
        for skill, rel, pattern, new in SECTION_PATCHES:
            if skill == name:
                patch_section(dst / rel, pattern, new)
        if (EXTRAS / name).is_dir():
            for f in sorted((EXTRAS / name).iterdir()):
                shutil.copy2(f, dst / f.name)
        insert_note(dst / "SKILL.md", name)
        print(f"vendored {name}")
    print(f"vendored the generator into {GENERATOR_DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
