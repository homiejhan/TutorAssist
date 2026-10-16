"""Term / concept extraction.

Two complementary strategies:

1. definition_pattern — sentences shaped like definitions:
       "<Term> is defined as ..." / "<Term> is called ..." /
       "... is called the <term>" / "... known as <term>" /
       "The <term>, denoted ..., is ..."
   High precision; the sentence itself becomes the description.

2. emphasis — textbooks introduce new vocabulary in bold/italic inside a
   body paragraph. The emphasized phrase is the term; the sentence that
   contains it is the description. Filtered aggressively (length, casing,
   stopwords, headings) to keep precision reasonable.

Deduplication is by normalized term; the highest-confidence, earliest
occurrence wins.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from .config import TermsConfig
from .loader import TextbookPdf
from .models import Term
from .structure import SectionIndex

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")
_WS = re.compile(r"\s+")

# --- definition sentence patterns (term-first) ------------------------------
_PAT_TERM_FIRST = [
    (re.compile(
        r"^(?:A|An|The)?\s*([A-Z][A-Za-z0-9\- ()]{2,60}?)\s+"
        r"(?:is|are)\s+defined\s+(?:as|to be)\b", ), 0.95),
    (re.compile(
        r"^(?:A|An|The)?\s*([A-Z][A-Za-z0-9\- ()]{2,60}?)\s+"
        r"(?:is|are)\s+(?:a|an|the)\s+\w+", ), 0.55),
]
# --- definition patterns (term-last) -----------------------------------------
_PAT_TERM_LAST = [
    (re.compile(r"\bis\s+(?:formally\s+)?defined\s+as\s+(?:a|an|the)?\s*"
                r"([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)[.,;]"), 0.9),
    (re.compile(r"\b(?:is|are)\s+called\s+(?:a|an|the)?\s*"
                r"\*?([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)\*?[.,;]"), 0.85),
    (re.compile(r"\b(?:is|are)\s+(?:also\s+)?known\s+as\s+(?:a|an|the)?\s*"
                r"([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)[.,;]"), 0.85),
    (re.compile(r"\b(?:is|are)\s+referred\s+to\s+as\s+(?:a|an|the)?\s*"
                r"([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)[.,;]"), 0.8),
    (re.compile(r"\b(?:is|are)\s+said\s+to\s+be\s+(?:a|an|the)?\s*"
                r"([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)\s+(?:if|when|whenever|provided)\b"), 0.85),
    (re.compile(r"\b(?:is|are)\s+termed\s+(?:a|an|the)?\s*"
                r"([a-zA-Z][a-zA-Z0-9\- ]{2,50}?)[.,;]"), 0.8),
]

_STOP_TERMS = frozenset(
    """figure fig table example exercise problem solution chapter section
    equation eq note answer step case part this that these those it we you
    the a an is are was were and or not if then thus hence therefore
    summary review questions homework page result value there here where
    first second third one two three important key however since because
    could would should shall might must can may will start stop end
    using given following called nan very much many some most any all""".split()
)

_BAD_TERM_CHARS = re.compile(r"[=<>{}\[\]|\\^_@#$%~`]")
_LABEL_TERM = re.compile(
    r"^(figure|fig\.?|table|example|exercise|problem|chapter|section|"
    r"eq\.?|equation|appendix|lab|part|step)\b", re.I)
_LEADIN_TERM = re.compile(
    r"^(recall|note|notice|observe|remember|especially|clearly|obviously|"
    r"thus|hence|therefore|however|moreover|furthermore|similarly|likewise|"
    r"suppose|consider|assume|let|indeed|finally|first|second|third|next|"
    r"here|now|then|also|since|although|while|when|where|because|"
    r"in general|for instance|for example|it follows)\b", re.I)


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _valid_term(term: str, cfg: TermsConfig) -> bool:
    term = term.strip(" .,:;()*")
    if not (cfg.min_term_chars <= len(term) <= 60):
        return False
    words = term.split()
    if not (1 <= len(words) <= cfg.max_term_words):
        return False
    if _BAD_TERM_CHARS.search(term):
        return False
    if not re.search(r"[a-zA-Z]{3}", term):
        return False
    lw = term.lower()
    if lw in _STOP_TERMS or words[0].lower() in {"the", "a", "an", "this", "that"}:
        return False
    if _LABEL_TERM.match(term) or _LEADIN_TERM.match(term):
        return False
    if "," in term:
        return False
    if sum(w.lower() in _STOP_TERMS for w in words) > len(words) // 2:
        return False
    if sum(c.isdigit() for c in term) > len(term) * 0.3:
        return False
    return True


def _sentences(text: str) -> list[str]:
    # join hyphenated line breaks, flatten newlines, split into sentences
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = text.replace("\n", " ")
    return [_clean(s) for s in _SENT_SPLIT.split(text) if len(s.strip()) > 15]


def _junk_sentence(sent: str, strict: bool = True) -> bool:
    """Running headers, axis-tick runs, page furniture, inline math soup.

    strict=True additionally rejects math-token-dominated sentences — right
    for choosing emphasis descriptions, too aggressive for definition-pattern
    sentences, whose formal statements are legitimately notation-heavy.
    """
    if re.search(r"\bChapter \d+:", sent):
        return True
    nonspace = [c for c in sent if not c.isspace()]
    if nonspace and sum(c.isdigit() for c in nonspace) / len(nonspace) > 0.22:
        return True
    if "\u200b" in sent or sent.count("_") >= 3:
        return True
    if strict:
        toks = sent.split()
        if toks:
            shorties = sum(len(t) <= 2 or not any(c.isalpha() for c in t) for t in toks)
            if shorties / len(toks) > 0.45:
                return True
    return False


def _is_toc_like_page(text: str) -> bool:
    """Table-of-contents / index pages: most lines end with a page number."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 8:
        return False
    ends_num = sum(bool(re.search(r"(\.{2,}\s*)?\d{1,4}$", ln)) for ln in lines)
    return ends_num / len(lines) > 0.55


def _norm_key(term: str) -> str:
    t = _WS.sub(" ", term.lower().strip(" .,:;()*"))
    return t[:-1] if t.endswith("s") and len(t) > 4 else t


# --------------------------------------------------------------------------- #
def _pattern_terms(sent: str, cfg: TermsConfig) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    for pat, conf in _PAT_TERM_FIRST:
        m = pat.match(sent)
        if m and _valid_term(m.group(1), cfg):
            found.append((_clean(m.group(1)), conf))
            break
    for pat, conf in _PAT_TERM_LAST:
        for m in pat.finditer(sent):
            if _valid_term(m.group(1), cfg):
                found.append((_clean(m.group(1)), conf))
    return found


def _emphasis_terms(page, body_size: float, cfg: TermsConfig) -> list[str]:
    """Bold/italic phrases inside body-sized text lines."""
    out: list[str] = []
    lines: dict[int, list] = {}
    for s in page.spans:
        lines.setdefault(s.line_id, []).append(s)
    for spans in lines.values():
        line_text = "".join(sp.text for sp in spans)
        if len(line_text.split()) < 6:          # headings / labels
            continue
        for sp in spans:
            if not (sp.bold or sp.italic):
                continue
            if sp.size > body_size * 1.15:      # heading-sized emphasis
                continue
            cand = _clean(sp.text)
            if _valid_term(cand, cfg) and not cand[0].isdigit():
                out.append(cand.strip(" .,:;()*"))
    return out


# --------------------------------------------------------------------------- #
def extract_terms(
    pdf: TextbookPdf,
    section_index: SectionIndex,
    cfg: TermsConfig,
    page_start: int = 1,
    page_end: Optional[int] = None,
) -> list[Term]:
    # body font estimate
    n = pdf.page_count
    sample = [max(1, round(i * n / 15)) for i in range(1, 16)]
    sizes: Counter = Counter()
    for pno in sample:
        for s in pdf.page(pno).spans:
            sizes[s.size] += len(s.text)
    body_size = sizes.most_common(1)[0][0] if sizes else 10.0

    best: dict[str, Term] = {}

    def offer(term: str, desc: str, page: int, method: str, conf: float) -> None:
        desc = _clean(desc)
        combined = term + " " + desc
        if any(ord(c) < 0x20 for c in combined):
            return
        if combined.count("\ufffd") > len(combined) * 0.03:
            return
        letters = [c for c in desc if not c.isspace()]
        if letters and sum(0x20 <= ord(c) <= 0x7E for c in letters) / len(letters) < 0.85:
            return
        if not (cfg.min_description_chars <= len(desc)):
            return
        if len(desc) > cfg.max_description_chars:
            # keep the term visible: window around its first occurrence,
            # then cut at a word boundary
            pos = desc.lower().find(term.lower())
            if pos > cfg.max_description_chars - len(term) - 20:
                start = max(0, pos - cfg.max_description_chars // 2)
                desc = ("…" if start else "") + desc[start:]
            desc = desc[: cfg.max_description_chars]
            cut = desc.rfind(" ")
            if cut > cfg.max_description_chars * 0.6:
                desc = desc[:cut]
            desc = desc.rstrip(" ,;:-") + "…"
        if not re.search(r"\b" + re.escape(term.lower()) + r"\b", desc.lower()):
            return
        key = _norm_key(term)
        cur = best.get(key)
        if cur is None or conf > cur.confidence + 1e-9:
            best[key] = Term(
                term=term, description=desc, page=page,
                section=section_index.label_for_page(page),
                method=method, confidence=round(conf, 2),
            )

    for page in pdf.pages(page_start, page_end):
        if _is_toc_like_page(page.text):
            continue
        page_terms = 0
        sents = _sentences(page.text)

        # strategy 1: definition patterns
        for sent in sents:
            if _junk_sentence(sent, strict=False):
                continue
            for term, conf in _pattern_terms(sent, cfg):
                if page_terms >= cfg.max_terms_per_page:
                    break
                offer(term, sent, page.number, "definition_pattern", conf)
                page_terms += 1

        # strategy 2: emphasized vocabulary
        if page_terms < cfg.max_terms_per_page:
            for cand in _emphasis_terms(page, body_size, cfg):
                if page_terms >= cfg.max_terms_per_page:
                    break
                cand_re = re.compile(r"\b" + re.escape(cand) + r"\b")
                cand_sents = [s for s in sents
                              if cand_re.search(s) and not _junk_sentence(s)]
                if not cand_sents:
                    continue
                # prefer a defining sentence over mere first occurrence
                sent = next(
                    (s for s in cand_sents
                     if re.search(r"\b(is|are|means|refers?|denotes?|represents?|called)\b", s)),
                    cand_sents[0],
                )
                # emphasized term inside a defining-ish sentence scores higher
                conf = cfg.emphasis_min_confidence
                if re.search(r"\b(is|are|means|refers?|denotes?|represents?)\b",
                             sent):
                    conf += 0.2
                offer(cand, sent, page.number, "emphasis", min(conf, 0.75))
                page_terms += 1

    return sorted(best.values(), key=lambda t: (t.page, t.term.lower()))
