"""Figure / table / diagram location.

Captions are the reliable anchor: "Figure 3.12 ...", "Fig. 2.4 ...",
"Table 5.1 ...". Each caption yields a Figure record with its page and
enclosing section. Embedded raster images can optionally be exported for
pages that contain detected figures.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from .config import FiguresConfig
from .loader import TextbookPdf
from .models import Figure
from .structure import SectionIndex

_CAPTION_RE = re.compile(
    r"^\s*(Figure|FIGURE|Fig\.|FIG\.|Table|TABLE)\s+"
    r"(\d{1,2}[.\u2013-]\d{1,3}[a-z]?)\s*[:.\u2014-]?\s*(\S.*)?$"
)
# Single-number captions ("FIGURE 3") are accepted only in ALL-CAPS form —
# lowercase "Figure 3 shows…" is a prose reference, not a caption.
_CAPTION_SINGLE_RE = re.compile(
    r"^\s*(FIGURE|TABLE)\s+(\d{1,3})\s*[:.\u2014-]?\s*(\S.*)?$"
)
_WS = re.compile(r"\s+")


def _clean(s: str) -> str:
    return _WS.sub(" ", s).strip()


def extract_figures(
    pdf: TextbookPdf,
    section_index: SectionIndex,
    cfg: FiguresConfig,
    page_start: int = 1,
    page_end: Optional[int] = None,
    export_dir: Optional[Path] = None,
) -> list[Figure]:
    seen: set[tuple] = set()
    figures: list[Figure] = []
    exported_pages: dict[int, list[str]] = {}

    # Book-adaptive caption style: when a book overwhelmingly captions with
    # the full word ("Figure 3.1"), line-start "Fig. 3.1" occurrences are
    # prose references that would shadow the real captions — skip them.
    full_ct = abbrev_ct = 0
    for page in pdf.pages(page_start, page_end, with_spans=False):
        for line in page.text.split("\n"):
            m = _CAPTION_RE.match(line)
            if m:
                if m.group(1).lower().startswith("fig."):
                    abbrev_ct += 1
                else:
                    full_ct += 1
    skip_abbrev = full_ct >= 10 and full_ct >= 3 * max(abbrev_ct, 1)

    for page in pdf.pages(page_start, page_end, with_spans=False):
        lines = page.text.split("\n")
        for i, line in enumerate(lines):
            m = _CAPTION_RE.match(line) or _CAPTION_SINGLE_RE.match(line)
            if not m:
                continue
            if skip_abbrev and m.group(1).lower().startswith("fig."):
                continue
            kind_raw, number, rest = m.group(1), m.group(2), m.group(3) or ""
            # Wrapped prose references ("…in Fig. 3.14.) We analyze…") start a
            # line but are not captions: captions never open with a lowercase
            # word or closing punctuation.
            if rest and (rest[0] in ")],;" or (rest[0].islower() and not
                         rest.startswith(("a)", "b)", "c)")))):
                continue
            kind = "table" if kind_raw.lower().startswith("t") else "figure"

            # Captions frequently wrap onto following lines. Absorb
            # continuations only while the caption sentence is unfinished;
            # after a sentence end, continue only into sub-part text "(a) …".
            caption = _clean(rest)
            j = i + 1
            while (
                j < len(lines)
                and len(caption) < cfg.max_caption_chars
                and lines[j].strip()
                and not _CAPTION_RE.match(lines[j])
                and not re.match(r"^\s*\d+\.\d+\s+[A-Z]", lines[j])
            ):
                nxt = _clean(lines[j])
                finished = caption.rstrip().endswith((".", "?", "!"))
                if finished and not nxt.startswith("("):
                    break
                caption = (caption + " " + nxt).strip()
                j += 1
            if kind == "table":
                # table "captions" are titles; absorbed lines are usually the
                # table body itself — keep only the title-length prefix
                caption = caption[:140].strip()
            caption = caption[: cfg.max_caption_chars].strip()

            section = section_index.label_for_page(page.number)
            # single-number schemes restart per section — dedupe within scope
            key = (kind, number, section if "." not in number else None)
            if key in seen:
                continue
            seen.add(key)

            label = f"{'Table' if kind == 'table' else 'Figure'} {number}"
            exported = None
            if export_dir is not None and kind == "figure":
                if page.number not in exported_pages:
                    exported_pages[page.number] = pdf.export_page_images(
                        page.number, export_dir
                    )
                paths = exported_pages[page.number]
                exported = paths[0] if paths else None

            figures.append(Figure(
                label=label,
                kind=kind,
                number=number,
                caption=caption,
                page=page.number,
                section=section,
                exported_path=exported,
            ))

    figures.sort(key=lambda f: (f.page, f.label))
    return figures
