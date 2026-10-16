"""Text-layer quality assessment.

Classifies the extractable text layer as good / partial / garbled / none.
Scanned books (no fonts) and Identity-H fonts missing ToUnicode maps are
the two common failure modes; both are detected here so downstream results
carry an honest quality label instead of silently emitting junk.
"""

from __future__ import annotations

import re
import string

from .config import QualityConfig
from .loader import TextbookPdf
from .models import QualityReport

# Small high-frequency English vocabulary — enough to separate real prose
# from mis-mapped glyph soup without shipping a dictionary.
_COMMON_WORDS = frozenset(
    """the of and to in a is that for it as with on be are this by an or
    from at which not can we have has one all their more when will if
    each other than these its two may was also into time used use where
    number function value figure example chapter section equation signal
    system circuit voltage current data first between shown given only
    such then following called defined result form case set let solution
    problem using see input output state process next new both same
    called since must through would there they you your what how does""".split()
)

_PRINTABLE = set(string.printable)


def _page_score(text: str, cfg: QualityConfig) -> tuple[bool, bool]:
    """Return (has_text, looks_clean) for one page."""
    stripped = text.strip()
    if len(stripped) < cfg.min_chars_per_page:
        return False, False

    nonspace = [c for c in stripped if not c.isspace()]
    if not nonspace:
        return False, False

    alpha_ratio = sum(c.isalpha() for c in nonspace) / len(nonspace)
    ascii_ratio = sum(c in _PRINTABLE for c in stripped) / len(stripped)

    words = re.findall(r"[A-Za-z]{2,}", stripped.lower())
    if words:
        dict_ratio = sum(w in _COMMON_WORDS for w in words[:400]) / min(len(words), 400)
        # prose typically contains >8% high-frequency function words;
        # glyph soup contains almost none.
        dict_ok = dict_ratio >= 0.08
    else:
        dict_ok = False

    clean = (
        alpha_ratio >= cfg.min_alpha_ratio * 0.8  # math-heavy pages allowed
        and ascii_ratio >= cfg.min_ascii_ratio
        and dict_ok
    )
    return True, clean


def assess_quality(pdf: TextbookPdf, cfg: QualityConfig) -> QualityReport:
    n = pdf.page_count
    sample_count = min(cfg.sample_pages, n)
    # even spread, skipping front matter bias
    idxs = sorted({max(1, round(i * n / sample_count)) for i in range(1, sample_count + 1)})

    with_text = 0
    clean = 0
    total_chars = 0
    for pno in idxs:
        page = pdf.page(pno, with_spans=False)
        total_chars += page.char_count
        has_text, looks_clean = _page_score(page.text, cfg)
        with_text += has_text
        clean += looks_clean

    sampled = len(idxs)
    avg_chars = total_chars / sampled if sampled else 0.0
    notes: list[str] = []

    if with_text == 0:
        layer = "none"
        notes.append(
            "No extractable text layer found (likely a scanned/raster PDF). "
            "Text-based extraction is skipped; OCR would be required."
        )
    elif clean / sampled >= cfg.good_page_ratio:
        layer = "good"
    elif clean / sampled >= 0.35:
        layer = "partial"
        notes.append(
            "Some sampled pages contain non-prose or mis-encoded text; "
            "results may be incomplete on those pages."
        )
    else:
        layer = "garbled"
        notes.append(
            "Text layer appears mis-encoded (e.g. Identity-H fonts without "
            "ToUnicode maps). Extraction results will be unreliable."
        )

    return QualityReport(
        text_layer=layer,
        pages_sampled=sampled,
        pages_with_text=with_text,
        avg_chars_per_page=round(avg_chars, 1),
        notes=notes,
    )
