"""Broken-cmap repair.

Some PDFs embed subset fonts with Identity-H encoding and no ToUnicode
map. Text extraction then yields glyph indices instead of characters —
frequently a *constant offset* from ASCII (e.g. "FRHIÀFLHQWV" + 0x1D →
"coefficients", "$)\"15&3" + 0x1F → "CHAPTER").

This module samples spans per font, scores candidate shifts against a
small English dictionary, and returns a font → shift map. The loader
applies the map when rebuilding page text, turning a "garbled" book into
a readable one. Glyphs that don't land in printable ASCII (ligatures,
math symbols) become U+FFFD so the loss is visible, not silent.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict

log = logging.getLogger(__name__)

_WORDS = frozenset(
    """the of and to in a is that for it as with on be are this by an or
    from at which not can we have has one all their more when will if
    each other than these its was also into time used use where number
    value example chapter section equation system solution matrix linear
    vector function set given form space row column theorem definition
    follows shows since then let both must may such called two first""".split()
)

_SHIFT_RANGE = [s for s in range(-4, 49) if s != 0]
_MIN_SAMPLE_CHARS = 200
_MIN_RAW_SCORE_OK = 0.10     # raw text at/above this ratio is already readable
_MIN_SHIFT_SCORE = 0.18      # decoded text must reach this to accept a shift
_MIN_IMPROVEMENT = 3.0       # decoded score must beat raw by this factor


def decode_with_shift(text: str, shift: int) -> str:
    out = []
    for ch in text:
        if ch.isspace():
            out.append(ch)
            continue
        o = ord(ch) + shift
        out.append(chr(o) if 0x20 <= o <= 0x7E else "\ufffd")
    return "".join(out)


def _dict_score(text: str) -> float:
    words = re.findall(r"[A-Za-z]{2,}", text.lower())
    if len(words) < 8:
        return 0.0
    return sum(w in _WORDS for w in words[:600]) / min(len(words), 600)


def build_font_repair_map(pdf, max_sample_pages: int = 30) -> dict[str, int]:
    """Sample spans across the book; return {font_name: shift} for broken fonts."""
    n = pdf.page_count
    step = max(1, n // max_sample_pages)
    samples: dict[str, list[str]] = defaultdict(list)
    sizes: Counter = Counter()

    for pno in range(1, n + 1, step):
        for sp in pdf.page(pno, with_spans=True).spans:
            if sum(len(t) for t in samples[sp.font]) < 4000:
                samples[sp.font].append(sp.text)
            sizes[sp.font] += len(sp.text)

    repair: dict[str, int] = {}
    for font, chunks in samples.items():
        blob = " ".join(chunks)
        if len(blob) < _MIN_SAMPLE_CHARS:
            continue
        raw = _dict_score(blob)
        if raw >= _MIN_RAW_SCORE_OK:
            continue  # font already extracts readable text
        best_shift, best_score = 0, raw
        for s in _SHIFT_RANGE:
            score = _dict_score(decode_with_shift(blob, s))
            if score > best_score:
                best_shift, best_score = s, score
        if (
            best_shift != 0
            and best_score >= _MIN_SHIFT_SCORE
            and best_score >= max(raw, 0.02) * _MIN_IMPROVEMENT
        ):
            repair[font] = best_shift
            log.info(
                "Font %r: repairing with shift %+d (score %.2f → %.2f, %d chars sampled)",
                font, best_shift, raw, best_score, sizes[font],
            )
    return repair


# --------------------------------------------------------------------------- #
# Per-span repair. Font names are shared between readable and broken subset
# fonts in the wild, so shifts must be gated span-by-span: decode only when
# the decoded text is clearly more plausible English than the raw text.
# --------------------------------------------------------------------------- #

def plausibility(text: str) -> float:
    """Heuristic 'looks like real text' score for short strings."""
    if not text.strip():
        return 0.0
    score = 0.0
    if any(ord(c) < 0x20 and not c.isspace() for c in text):
        score -= 2.0
    visible = [c for c in text if not c.isspace()]
    if visible:
        score += sum(0x20 <= ord(c) <= 0x7E for c in visible) / len(visible)
    letters = [c for c in text if c.isalpha()]
    if letters:
        score += sum(c.islower() for c in letters) / len(letters)
        vr = sum(c.lower() in "aeiou" for c in letters) / len(letters)
        if len(letters) >= 4 and 0.18 <= vr <= 0.62:
            score += 0.5
    # common-word bonus: require length ≥3 so decoded junk can't win via
    # accidental "it"/"in"/"as" substrings
    words = re.findall(r"[A-Za-z]{3,}", text.lower())
    if any(w in _WORDS for w in words):
        score += 1.0
    return score


def repair_text(text: str, candidate_shifts: list[int], margin: float = 0.35) -> str:  # noqa: C901
    """Return the most plausible reading of `text` given candidate shifts."""
    if not text.strip() or not candidate_shifts:
        return text
    has_ctrl = any(ord(c) < 0x20 and not c.isspace() for c in text)
    # Absolute guard: text that already reads as clean prose is NEVER decoded.
    # Broken-cmap output has ~zero lowercase (real lowercase shifts into
    # caps/symbols) or control characters — clean mixed-case ASCII means the
    # span's font was fine, whatever the book-level candidate shifts are.
    if not has_ctrl:
        letters = [c for c in text if c.isalpha()]
        visible = [c for c in text if not c.isspace()]
        if (len(letters) >= 4
                and sum(c.islower() for c in letters) / len(letters) >= 0.5
                and sum(0x20 <= ord(c) <= 0x7E for c in visible) / len(visible) >= 0.95):
            return text
    # Short spans (numeric labels "1.2", math symbols, "ARG", variable names)
    # carry too little signal for the lowercase-ratio heuristic — demand
    # decisive evidence: control characters, or a decoded common English word.
    n_letters = sum(c.isalpha() for c in text)
    if n_letters < 5 and not has_ctrl:
        margin = 1.1
    raw_score = plausibility(text)
    best_text, best_score = text, raw_score
    for shift in candidate_shifts:
        cand = decode_with_shift(text, shift)
        cs = plausibility(cand)
        if cs > best_score:
            best_text, best_score = cand, cs
    if best_text is not text and best_score >= raw_score + margin:
        return best_text
    return text
