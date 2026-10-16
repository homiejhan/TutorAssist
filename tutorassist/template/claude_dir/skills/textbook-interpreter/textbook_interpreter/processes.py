"""Step-by-step process detection.

Detects three shapes common in STEM textbooks:

1. numbered_steps — a run of lines "1. ...", "2) ...", sequential from 1.
   Guarded against exercise lists ("1.13 The charge entering ...") by
   rejecting chapter.problem numbering and requiring sequential integers.

2. procedure — the same, but introduced by a cue line containing
   "Steps to", "Procedure", "Algorithm", "Method", "How to", etc.
   The cue line becomes the title and boosts acceptance.

3. worked_example — "Example N.M <title>" followed (within a few pages)
   by a "Solution" marker. Steps inside the solution are captured when
   numbered; otherwise the example is still recorded with its page span
   so it can be located.
"""

from __future__ import annotations

import re
from typing import Optional

from .config import ProcessConfig
from .loader import TextbookPdf
from .models import Process, ProcessStep
from .structure import SectionIndex

_STEP_RE = re.compile(r"^\s*(?:Step\s+)?(\d{1,2})\s*[.):]\s+(\S.*)$", re.I)
_PROBLEM_NUM_RE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\b")   # "1.13 ..." exercises
_CUE_RE = re.compile(
    r"\b(steps? (?:to|in|for)|procedure|algorithm|methodology|"
    r"method (?:of|for)|how to|the following steps|design process|"
    r"apply the following|we follow these steps)\b", re.I)
_EXAMPLE_RE = re.compile(r"^\s*(?:EXAMPLE|Example)\s+(\d{1,2}\.\d{1,2})\s*[:.]?\s*(.{0,100})$")
_SOLUTION_RE = re.compile(r"^\s*(?:Solution|SOLUTION|Answer:)\b")
_WS = re.compile(r"\s+")
_EXERCISE_CTX = re.compile(r"\b(exercises?|problems?|review questions?|homework)\b", re.I)


def _mostly_math(text: str) -> bool:
    toks = text.split()
    if not toks:
        return True
    shorties = sum(len(t) <= 2 or not any(c.isalpha() for c in t) for t in toks)
    return shorties / len(toks) > 0.5


def _clean(s: str) -> str:
    return _WS.sub(" ", s).strip()


# --------------------------------------------------------------------------- #
def _scan_numbered_runs(
    lines: list[tuple[int, str]], cfg: ProcessConfig
) -> list[dict]:
    """Find sequential numbered-step runs in (page, line) pairs."""
    runs: list[dict] = []
    i = 0
    n = len(lines)
    while i < n:
        page, line = lines[i]
        m = _STEP_RE.match(line)
        if not m or int(m.group(1)) != 1 or _PROBLEM_NUM_RE.match(line):
            i += 1
            continue

        steps: list[tuple[int, int, str]] = [(1, page, m.group(2))]
        expected = 2
        j = i + 1
        gap = 0
        last_j = i
        while j < n and expected <= cfg.max_steps:
            pg, ln = lines[j]
            m2 = _STEP_RE.match(ln)
            if m2 and not _PROBLEM_NUM_RE.match(ln):
                num = int(m2.group(1))
                if num == expected:
                    steps.append((num, pg, m2.group(2)))
                    expected += 1
                    gap = 0
                    last_j = j
                    j += 1
                    continue
                if num == 1:               # a new list starts
                    break
            # continuation line of previous step (wrapped text)
            if ln.strip() and not m2 and gap <= cfg.max_gap_lines:
                num, spg, stext = steps[-1]
                if len(stext) < cfg.max_step_chars and not _looks_like_heading(ln):
                    steps[-1] = (num, spg, stext + " " + ln.strip())
                    last_j = j
                gap += 1
                j += 1
                continue
            gap += 1
            if gap > cfg.max_gap_lines:
                break
            j += 1

        if len(steps) >= cfg.min_steps:
            # title: up to two preceding non-step lines (captures the full
            # lead-in sentence when it wraps), newest last
            title_parts: list[str] = []
            cue = False
            for k in range(i - 1, max(-1, i - 8), -1):
                prev = lines[k][1].strip()
                if not prev or _STEP_RE.match(prev):
                    if title_parts:
                        break
                    continue
                title_parts.insert(0, _clean(prev))
                cue = cue or bool(_CUE_RE.search(prev))
                if len(title_parts) == 2:
                    break
            title = " ".join(title_parts)[-160:].strip()
            runs.append({
                "steps": steps,
                "title": title,
                "cue": cue,
                "page_start": steps[0][1],
                "page_end": steps[-1][1],
            })
            i = last_j + 1
        else:
            i += 1
    return runs


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    return bool(re.match(r"^\d{1,2}\.\d{1,2}\s+[A-Z]", s)) or (
        s.isupper() and len(s.split()) <= 8
    )


# --------------------------------------------------------------------------- #
def _scan_examples(
    lines: list[tuple[int, str]], cfg: ProcessConfig
) -> list[dict]:
    out: list[dict] = []
    for i, (page, line) in enumerate(lines):
        m = _EXAMPLE_RE.match(line)
        if not m:
            continue
        # require a Solution marker within the next ~120 lines / 4 pages
        has_solution = False
        end_page = page
        for j in range(i + 1, min(i + 120, len(lines))):
            pg, ln = lines[j]
            if pg > page + 4:
                break
            if _SOLUTION_RE.match(ln):
                has_solution = True
                end_page = pg
                break
        if not has_solution:
            continue
        title = _clean(m.group(2)) or f"Example {m.group(1)}"
        out.append({
            "number": m.group(1),
            "title": title[:120],
            "page_start": page,
            "page_end": end_page,
        })
    return out


# --------------------------------------------------------------------------- #
def extract_processes(
    pdf: TextbookPdf,
    section_index: SectionIndex,
    cfg: ProcessConfig,
    page_start: int = 1,
    page_end: Optional[int] = None,
) -> list[Process]:
    lines: list[tuple[int, str]] = []
    for page in pdf.pages(page_start, page_end, with_spans=False):
        for ln in page.text.split("\n"):
            lines.append((page.number, ln))

    processes: list[Process] = []
    pid = 0

    for run in _scan_numbered_runs(lines, cfg):
        section = section_index.label_for_page(run["page_start"])
        # Exercise/problem enumerations are numbered lists but not processes.
        ctx = f"{run['title']} {section or ''}"
        if _EXERCISE_CTX.search(ctx):
            continue
        if _mostly_math(run["title"]):
            run["title"] = ""
        pid += 1
        if run["cue"]:
            ptype = "procedure"
        else:
            # equation / item dominated lists are not action sequences —
            # label honestly so consumers can filter
            texts = [t for _n, _p, t in run["steps"]]
            mathish = sum(("=" in t and len(t.split()) < 12) or len(t.split()) < 4
                          for t in texts)
            ptype = "enumerated_list" if mathish / len(texts) > 0.6 else "numbered_steps"
        title = run["title"] or "Numbered steps"
        processes.append(Process(
            id=f"proc-{pid:04d}",
            type=ptype,
            title=title,
            page_start=run["page_start"],
            page_end=run["page_end"],
            section=section,
            steps=[ProcessStep(n=num, text=_clean(t)[: cfg.max_step_chars])
                   for num, _pg, t in run["steps"]],
        ))

    for ex in _scan_examples(lines, cfg):
        pid += 1
        ex_title = ex["title"]
        if not ex_title.lower().startswith("example"):
            ex_title = f"Example {ex['number']}: {ex_title}"
        processes.append(Process(
            id=f"proc-{pid:04d}",
            type="worked_example",
            title=ex_title.rstrip(": "),
            page_start=ex["page_start"],
            page_end=ex["page_end"],
            section=section_index.label_for_page(ex["page_start"]),
            steps=[],
        ))

    processes.sort(key=lambda p: (p.page_start, p.id))
    return processes
