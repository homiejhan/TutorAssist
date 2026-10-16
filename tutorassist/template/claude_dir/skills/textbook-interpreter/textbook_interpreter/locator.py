"""Locate a term / concept / phrase anywhere in the book.

Returns page-level matches with the enclosing section and a context
snippet, so "where does this book cover superposition?" is answerable
without re-reading the PDF manually.
"""

from __future__ import annotations

import re
from typing import Optional

from .loader import TextbookPdf
from .models import LocateMatch
from .structure import SectionIndex

_WS = re.compile(r"\s+")


def _snippet(text: str, start: int, end: int, radius: int = 140) -> str:
    lo = max(0, start - radius)
    hi = min(len(text), end + radius)
    snip = _WS.sub(" ", text[lo:hi]).strip()
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return f"{prefix}{snip}{suffix}"


def locate(
    pdf: TextbookPdf,
    query: str,
    section_index: SectionIndex,
    max_matches: int = 50,
    page_start: int = 1,
    page_end: Optional[int] = None,
) -> list[LocateMatch]:
    query = query.strip()
    if not query:
        return []
    # whole-word-ish, whitespace-tolerant pattern
    pat = re.compile(
        r"\b" + r"\s+".join(re.escape(w) for w in query.split()) + r"\b",
        re.IGNORECASE,
    )
    matches: list[LocateMatch] = []
    for page in pdf.pages(page_start, page_end, with_spans=False):
        flat = page.text.replace("\n", " ")
        for m in pat.finditer(flat):
            exact = flat[m.start(): m.end()] == query
            matches.append(LocateMatch(
                query=query,
                page=page.number,
                section=section_index.label_for_page(page.number),
                snippet=_snippet(flat, m.start(), m.end()),
                match_kind="exact" if exact else "case_insensitive",
            ))
            break  # one match record per page keeps output readable
        if len(matches) >= max_matches:
            break
    return matches
