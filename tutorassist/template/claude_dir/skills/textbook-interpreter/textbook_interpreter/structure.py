"""Chapter / sub-chapter detection.

Strategy 1 (preferred): the PDF's embedded outline (bookmarks/TOC).
Strategy 2 (fallback): heuristic scan for heading-like lines —
"Chapter N", "N Title", "N.M Title" — using font-size spans where
available and regex on plain text otherwise.

Also provides `SectionIndex`, a page → deepest-section resolver used by
every other analyzer to attribute results to a section.
"""

from __future__ import annotations

import logging
import re
from bisect import bisect_right
from collections import Counter
from typing import Optional

from .config import StructureConfig
from .loader import TextbookPdf
from .models import SectionNode

log = logging.getLogger(__name__)

_CHAPTER_RE = re.compile(r"^\s*(?:chapter|CHAPTER)\s+(\d{1,2})\b[.:\s]*(.{0,80})$")
_NUMBERED_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,2})\s+([A-Z][^\n]{2,80})$")
_TOC_NUM_RE = re.compile(r"^\s*(?:chapter\s+)?(\d{1,2}(?:\.\d{1,2}){0,3})\s*[.:]?\s+(.*)$", re.I)

# Outline entries that are front/back matter rather than content structure.
_NONCONTENT = re.compile(
    r"^((the|a|an)\s+)?(cover|half.?title|title|copyright|dedication|preface|foreword|"
    r"contents|table of contents|list of |index|bibliograph|references$|"
    r"about the|author biograph|acknowledg|front matter|back matter|"
    r"answers to|photo credits|notation|companion website|website|"
    r"errata|glossary$|es-\d|fm$|tribute|to the (student|instructor)|technology in|how to use)", re.I,
)


def _split_number_title(raw_title: str) -> tuple[Optional[str], str]:
    m = _TOC_NUM_RE.match(raw_title)
    if m:
        num, rest = m.group(1), m.group(2).strip()
        if rest:
            return num, rest
    return None, raw_title.strip()


# --------------------------------------------------------------------------- #
def _from_embedded_toc(
    toc: list[tuple[int, str, int]], page_count: int, cfg: StructureConfig
) -> list[SectionNode]:
    """Build a chapter tree from the PDF outline."""
    entries = [(lvl, t, pg) for lvl, t, pg in toc if lvl <= cfg.max_toc_depth and t.strip()]
    if not entries:
        return []

    # Normalize levels: many books nest everything under a single root.
    min_lvl = min(lvl for lvl, _, _ in entries)
    entries = [(lvl - min_lvl + 1, t, pg) for lvl, t, pg in entries]

    # If titles carry section numbers ("1.2 Switches"), the number depth is a
    # more reliable nesting signal than the outline level (many books export
    # flat outlines). Use it whenever present.
    if sum(1 for _l, t, _p in entries if _split_number_title(t)[0]) >= len(entries) * 0.5:
        entries = [
            (min((_split_number_title(t)[0] or "").count(".") + 1, cfg.max_toc_depth)
             if _split_number_title(t)[0] else lvl, t, pg)
            for lvl, t, pg in entries
        ]

    roots: list[SectionNode] = []
    stack: list[SectionNode] = []
    seen_ch_nums: set[str] = set()
    for lvl, title, pg in entries:
        num, clean = _split_number_title(title)
        # "1 review" / "1 Test" after chapter 1 exists: end-of-chapter
        # material, not a new chapter — demote under the real chapter
        if lvl == 1 and num and num in seen_ch_nums:
            lvl = 2
        elif lvl == 1 and num:
            seen_ch_nums.add(num)
        node = SectionNode(number=num, title=clean, level=lvl,
                           page_start=pg, page_end=page_count)
        while stack and stack[-1].level >= lvl:
            stack.pop()
        if stack:
            stack[-1].sections.append(node)
        else:
            roots.append(node)
        stack.append(node)

    _fix_page_ends(roots, page_count)
    return roots


def _fix_page_ends(nodes: list[SectionNode], parent_end: int) -> None:
    for i, node in enumerate(nodes):
        node.page_end = (nodes[i + 1].page_start - 1) if i + 1 < len(nodes) else parent_end
        node.page_end = max(node.page_end, node.page_start)
        _fix_page_ends(node.sections, node.page_end)


# --------------------------------------------------------------------------- #
# Strategy 2: parse the *printed* table-of-contents pages, then calibrate a
# printed-page → PDF-page offset by locating chapter titles in the body.
# --------------------------------------------------------------------------- #

_PTOC_CHAPTER = re.compile(r"^\s*CHAPTER\s+(\d{1,2})\b\s*(.*)$", re.I)
_PTOC_NUMCH = re.compile(r"^\s*(\d{1,2})\s+([A-Z][A-Z0-9 ,&'\-]{5,70}?)\s+(\d{1,4})\s*$")
_PTOC_CAPSCH = re.compile(r"^\s*([A-Z][A-Z0-9 ,&'\-]{5,70}?)\s+(\d{1,4})\s*$")
_PTOC_K12SEC = re.compile(r"^\s*(\d{1,2})\.\s+(\S.*?)\s+(\d{1,4})\s*$")


def _caps_title_ok(t: str) -> bool:
    letters = [c for c in t if c.isalpha()]
    return (len(letters) >= 5 and sum(c.isupper() for c in letters) / len(letters) > 0.8
            and bool(re.search(r"[AEIOU]", t)))


def _parse_k12_toc(lines: list[str], page_only) -> list[tuple[int, "Optional[str]", str, int]]:
    """K-12 dialect: ALL-CAPS chapter rows (or bare-number chapter rows),
    sections numbered 1. 2. 3. restarting per chapter. Handles inline rows
    ("3.  Perpendiculars   9") and multi-line triplets ("3" / "Perpendiculars
    and slants" / "9"), plus caps chapter rows with the page on the next line."""
    entries: list[tuple[int, Optional[str], str, int]] = []
    ch_count = 0
    last_sec = 0
    numch_noPage = re.compile(r"^\s*(\d{1,2})\s+([A-Z][A-Z0-9 ,&'\-]{5,70})\s*$")
    num_only = re.compile(r"^\s*(\d{1,2})\s*$")
    skip = 0
    for i, line in enumerate(lines):
        if skip:
            skip -= 1
            continue
        if not line:
            continue

        # chapter row without a page: "1 LINES AND PLANES" / next line "1"
        m = numch_noPage.match(line)
        if m and _caps_title_ok(m.group(2)) and i + 1 < len(lines) \
                and page_only.match(lines[i + 1] or ""):
            ch_count = int(m.group(1))
            last_sec = 0
            entries.append((1, str(ch_count), m.group(2).title().strip(" .-"),
                            int(lines[i + 1])))
            skip = 1
            continue

        # multi-line section triplet: "3" / "Title ..." / "9"
        m = num_only.match(line)
        if m and ch_count and i + 2 < len(lines):
            n = int(m.group(1))
            title = (lines[i + 1] or "").strip(" .-")
            if ((last_sec < n <= last_sec + 3 or n == 1) and title and not num_only.match(title)
                    and any(c.isalpha() for c in title) and len(title) >= 4
                    and page_only.match(lines[i + 2] or "")):
                entries.append((2, f"{ch_count}.{n}", title.rstrip(" ."),
                                int(lines[i + 2])))
                last_sec = n
                skip = 2
                continue
        m = _PTOC_NUMCH.match(line)
        if m and _caps_title_ok(m.group(2)):
            ch_count = int(m.group(1))
            last_sec = 0
            entries.append((1, str(ch_count), m.group(2).title().strip(" .-"),
                            int(m.group(3))))
            continue
        m = _PTOC_K12SEC.match(line)
        if m and ch_count:
            n = int(m.group(1))
            if (last_sec < n <= last_sec + 3 or n == 1) and len(m.group(2).split()) >= 1:
                title = m.group(2).strip(" .-")
                if (len(title) >= 3 and any(c.isalpha() for c in title)
                        and not re.match(r"^\d+[\s.\-]", title)):
                    entries.append((2, f"{ch_count}.{n}", title, int(m.group(3))))
                    last_sec = n
            continue
        m = _PTOC_CAPSCH.match(line)
        if m and _caps_title_ok(m.group(1)):
            ch_count += 1
            last_sec = 0
            entries.append((1, str(ch_count), m.group(1).title().strip(" .-"),
                            int(m.group(2))))
    return entries
_PTOC_TITLEPG = re.compile(r"^\s*(.{3,80}?)[\s.:]*?\.{0,40}\s*:?\s*(\d{1,4})\s*$")
_PTOC_SECTION = re.compile(r"^\s*(\d{1,2}[.\-]\d{1,2})\.?\s*(.*)$")


def _leader_toc_page(text: str) -> bool:
    """TOC pages whose 'Contents' header is a graphic: detect dotted leaders."""
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    leaders = sum(bool(re.search(r"\.{4,}\s*:?\s*\d{1,4}\s*$", ln)) for ln in lines)
    return leaders >= 6


def _is_toc_page(text: str) -> bool:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) < 10:
        return False
    ends_num = sum(bool(re.search(r"\d{1,4}\s*$", ln)) for ln in lines)
    return ends_num / len(lines) > 0.25


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


_BAD_TITLE_START = re.compile(
    r"^(and|or|with|for|in|of|to|the|introductory example|example)\b", re.I)


def _plausible_title(line: str) -> bool:
    if not (4 <= len(line) <= 90):
        return False
    words = re.findall(r"[A-Za-z]{2,}", line)
    if not words or (len(words) == 1 and len(words[0]) < 6):
        return False
    if _BAD_TITLE_START.match(line.strip()):
        return False
    letters = sum(c.isalpha() for c in line)
    return letters / max(len(line), 1) > 0.6 and bool(re.search(r"[aeiouAEIOU]", line))


def _try_inline(level, number, rest):
    """Parse 'Title  page' inline form; None if it does not match."""
    m = _PTOC_TITLEPG.match(rest)
    if m and m.group(1).strip() and _plausible_title(m.group(1)):
        return (level, number, m.group(1).strip(" .-"), int(m.group(2)))
    return None


def _parse_printed_toc(pdf: TextbookPdf) -> list[tuple[int, Optional[str], str, int]]:
    """Returns flat entries: (level, number, title, printed_page).

    Handles two common layouts:
      inline:      "1.1  Systems of Linear Equations  2"
      multi-line:  "1.1" / "Systems of Linear Equations" / "2"
    Chapter rows may be garbled (broken font subsets), so chapters can also
    be synthesized from section numbering: the readable title line preceding
    the first "N.1" entry becomes chapter N's title.
    """
    limit = max(4, pdf.page_count // 8)
    toc_pages: list[int] = []
    for pno in range(1, min(limit, pdf.page_count) + 1):
        text = pdf.page(pno, with_spans=False).text
        head = text.strip()[:200].lower()
        if ("contents" in head and _is_toc_page(text)) or _leader_toc_page(text) or (
            toc_pages and pno == toc_pages[-1] + 1 and _is_toc_page(text)
        ):
            toc_pages.append(pno)
    if not toc_pages:
        return []

    lines: list[str] = []
    for pno in toc_pages:
        lines.extend(ln.strip() for ln in pdf.page(pno, with_spans=False).text.split("\n"))

    entries: list[tuple[int, Optional[str], str, int]] = []
    page_only = re.compile(r"^(\d{1,4})$")
    n = len(lines)
    i = 0
    prev_title: tuple[str, Optional[int]] = ("", None)  # (title, its page if seen)
    pending_chapter: Optional[str] = None

    def have_chapter(num: str) -> bool:
        return any(lv == 1 and nm == num for lv, nm, _t, _p in entries)

    while i < n:
        line = lines[i]
        if not line:
            i += 1
            continue

        mch = _PTOC_CHAPTER.match(line)
        if mch:
            rest = mch.group(2).strip()
            entry = _try_inline(1, mch.group(1), rest) if rest else None
            if entry:
                entries.append(entry)
            else:
                pending_chapter = mch.group(1)
            i += 1
            continue

        mnum = _PTOC_NUMCH.match(line)
        if mnum and _caps_title_ok(mnum.group(2)):
            # "1  PRELIMINARY INFORMATION  1" — number, caps title, page
            entries.append((1, mnum.group(1),
                            mnum.group(2).title().strip(" .-"),
                            int(mnum.group(3))))
            prev_title = ("", None)
            i += 1
            continue

        msec = _PTOC_SECTION.match(line)
        if msec:
            num, rest = msec.group(1).replace("-", "."), msec.group(2).strip()
            entry = None
            consumed = 1
            if rest:
                entry = _try_inline(2, num, rest)
                if entry is None and i + 1 < n and page_only.match(lines[i + 1] or ""):
                    if _plausible_title(rest):        # "1.1 Title" / "2"
                        entry = (2, num, rest.rstrip(" .-"), int(lines[i + 1]))
                        consumed = 2
            elif i + 1 < n and lines[i + 1]:
                if i + 2 < n and page_only.match(lines[i + 2] or ""):
                    if _plausible_title(lines[i + 1]):  # "1.1" / "Title" / "2"
                        entry = (2, num, lines[i + 1].rstrip(" .-"),
                                 int(lines[i + 2]))
                        consumed = 3
                else:
                    entry = _try_inline(2, num, lines[i + 1])
                    consumed = 2 if entry else 1
            if entry:
                ch_num = num.split(".")[0]
                if not have_chapter(ch_num):
                    title, tpage = prev_title
                    if not _plausible_title(title):
                        title = f"Chapter {ch_num}"
                    entries.append((1, ch_num, title, tpage or entry[3]))
                    pending_chapter = None
                entries.append(entry)
                prev_title = ("", None)
                i += consumed
                continue
            i += 1
            continue

        if _plausible_title(line):
            if pending_chapter is not None:
                inline = _try_inline(1, pending_chapter, line)
                if inline is not None:          # "Datapath Components 167"
                    if not have_chapter(pending_chapter):
                        entries.append(inline)
                    pending_chapter = None
                    prev_title = ("", None)
                    i += 1
                    continue
            pg = None
            if i + 1 < n and page_only.match(lines[i + 1] or ""):
                pg = int(lines[i + 1])
            if pending_chapter is not None and pg is not None:
                if not have_chapter(pending_chapter):
                    entries.append((1, pending_chapter, line.rstrip(" .-"), pg))
                pending_chapter = None
                prev_title = ("", None)
                i += 2
                continue
            prev_title = (line, pg)
            i += 2 if pg is not None else 1
            continue

        i += 1

    return entries


def _parse_k12_toc_from_pdf(pdf: TextbookPdf):
    limit = max(4, pdf.page_count // 8)
    toc_pages: list[int] = []
    for pno in range(1, min(limit, pdf.page_count) + 1):
        text = pdf.page(pno, with_spans=False).text
        head = text.strip()[:200].lower()
        if ("contents" in head and _is_toc_page(text)) or _leader_toc_page(text) or (
            toc_pages and pno == toc_pages[-1] + 1 and _is_toc_page(text)
        ):
            toc_pages.append(pno)
    if not toc_pages:
        return []
    lines: list[str] = []
    for pno in toc_pages:
        lines.extend(ln.strip() for ln in pdf.page(pno, with_spans=False).text.split("\n"))
    page_only = re.compile(r"^(\d{1,4})$")
    return _parse_k12_toc(lines, page_only)


def _calibrate_offset(
    pdf: TextbookPdf, entries: list[tuple[int, Optional[str], str, int]]
) -> Optional[int]:
    """Find the printed→PDF page offset by locating anchor titles."""
    anchors = [e for e in entries if e[0] == 1][:8] or entries[:8]
    offsets: Counter = Counter()
    for _lvl, _num, title, printed in anchors:
        # whitespace-insensitive key: OCR/span rebuilds inject spaces mid-word
        key = re.sub(r"\s+", "", _norm(title))[:24]
        if len(key) < 6:
            continue
        lo = max(1, printed)
        hi = min(pdf.page_count, printed + 100)
        for pno in range(lo, hi + 1):
            # heading match only: title must appear near the top of the page,
            # otherwise prose mentions scatter the votes
            head = re.sub(r"\s+", "", _norm(pdf.page(pno, with_spans=False).text[:600]))
            if key in head:
                offsets[pno - printed] += 1   # count every plausible page
    if not offsets:
        return None
    off, votes = offsets.most_common(1)[0]
    return off if votes >= 2 else None


def _from_printed_toc(pdf: TextbookPdf, cfg: StructureConfig) -> list[SectionNode]:
    candidates = []
    std = _parse_printed_toc(pdf)
    if len(std) >= 4 and sum(1 for lvl, *_ in std if lvl == 1) >= 2:
        candidates.append(("standard", std))
    k12 = _parse_k12_toc_from_pdf(pdf)
    if len(k12) >= 4 and sum(1 for lvl, *_ in k12 if lvl == 1) >= 2:
        candidates.append(("k12", k12))
    entries = offset = None
    for name, cand in candidates:
        off = _calibrate_offset(pdf, cand)
        if off is not None:
            entries, offset = cand, off
            log.info("Printed TOC parsed via %s dialect.", name)
            break
    if entries is None:
        log.info("Printed TOC found but page offset could not be calibrated.")
        return []

    def _local_offset(title: str, printed: int, base: int,
                      back: int = 2, ahead: int = 14) -> Optional[int]:
        """Re-anchor an entry near its expected page to absorb drift from
        un-numbered insert pages (labs, chapter openers). None if not found."""
        key = re.sub(r"\s+", "", _norm(title))[:24]
        if len(key) < 8:
            return None
        for pno in range(max(1, printed + base - back),
                         min(pdf.page_count, printed + base + ahead) + 1):
            head = re.sub(r"\s+", "", _norm(pdf.page(pno, with_spans=False).text[:600]))
            if key in head:
                return pno - printed
        return None
    log.info("Printed TOC parsed (%d entries, page offset %+d).", len(entries), offset)

    roots: list[SectionNode] = []
    stack: list[SectionNode] = []
    last_page = 0
    # Cumulative drift correction: printed numbering skips insert pages, so
    # the printed→PDF offset grows through the book. Re-anchor on every entry
    # whose title is found as a page heading; carry the offset forward.
    cur_offset = offset
    for lvl, num, title, printed in entries:
        title = re.sub(r"\s+\d{1,4}$", "", title).strip()
        loc = _local_offset(title, printed, cur_offset,
                            back=6, ahead=14 if lvl == 1 else 9)
        if loc is not None and abs(loc - cur_offset) <= 12:
            cur_offset = loc
        pg = printed + cur_offset
        if pg < 1 or pg > pdf.page_count or pg < last_page - 2:
            continue
        last_page = max(last_page, pg)
        node = SectionNode(number=num, title=title, level=lvl,
                           page_start=pg, page_end=pdf.page_count)
        while stack and stack[-1].level >= lvl:
            stack.pop()
        if stack:
            stack[-1].sections.append(node)
        elif lvl == 1:
            roots.append(node)
        else:
            continue
        stack.append(node)
    _fix_page_ends(roots, pdf.page_count)
    return roots


# --------------------------------------------------------------------------- #
def _body_font_size(pdf: TextbookPdf, sample: list[int]) -> float:
    sizes: Counter = Counter()
    for pno in sample:
        for s in pdf.page(pno).spans:
            sizes[s.size] += len(s.text)
    return sizes.most_common(1)[0][0] if sizes else 10.0


def _heuristic_scan(pdf: TextbookPdf, cfg: StructureConfig) -> list[SectionNode]:
    """Fallback structure detection when no embedded outline exists."""
    n = pdf.page_count
    sample = [max(1, round(i * n / 25)) for i in range(1, 26)]
    body_size = _body_font_size(pdf, sample)
    min_heading = body_size * cfg.heading_min_font_ratio

    flat: list[SectionNode] = []
    for page in pdf.pages():
        # group spans into physical lines, big-font first
        lines: dict[int, list] = {}
        for s in page.spans:
            lines.setdefault(s.line_id, []).append(s)
        for spans in lines.values():
            text = "".join(sp.text for sp in spans).strip()
            if not text or len(text.split()) > cfg.max_heading_words:
                continue
            max_size = max(sp.size for sp in spans)
            is_big = max_size >= min_heading
            is_bold = any(sp.bold for sp in spans)

            m = _CHAPTER_RE.match(text)
            if m and (is_big or is_bold):
                flat.append(SectionNode(m.group(1), m.group(2).strip() or text,
                                        1, page.number, n))
                continue
            m = _NUMBERED_RE.match(text)
            if m and (is_big or is_bold):
                num = m.group(1)
                level = min(num.count(".") + 1, 3)
                if level == 1 and not is_big:
                    continue  # bare "5 Something" needs big font to count
                flat.append(SectionNode(num, m.group(2).strip(), level, page.number, n))

    return _nest_flat(flat, n, cfg)


def _nest_flat(flat: list[SectionNode], page_count: int,
               cfg: StructureConfig) -> list[SectionNode]:
    if not flat:
        return []
    # de-duplicate repeats (running headers) — keep first page occurrence
    seen: dict[tuple, SectionNode] = {}
    for node in flat:
        # running headers repeat a title on every page (often with the page
        # number OCR-glued on as a bogus chapter number) — title-only key
        key = (node.level, re.sub(r"\s+", " ", node.title.lower()))
        if key not in seen:
            seen[key] = node
    ordered = sorted(seen.values(), key=lambda x: (x.page_start, x.level))

    roots: list[SectionNode] = []
    stack: list[SectionNode] = []
    last_ch_page = -10
    for node in ordered:
        if node.level == 1:
            if node.page_start - last_ch_page < cfg.min_chapter_gap_pages:
                continue
            last_ch_page = node.page_start
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].sections.append(node)
        elif node.level == 1:
            roots.append(node)
            stack.append(node)
            continue
        else:
            # orphan sub-section before any chapter — skip
            continue
        stack.append(node)

    _fix_page_ends(roots, page_count)
    return roots


# --------------------------------------------------------------------------- #
def detect_structure(
    pdf: TextbookPdf, cfg: StructureConfig, allow_text_scan: bool = True
) -> tuple[list[SectionNode], str]:
    """Returns (chapter_tree, source), source ∈ {embedded_toc, printed_toc, heuristic, none}.

    `allow_text_scan=False` (scanned PDFs) restricts detection to the
    embedded outline, avoiding pointless full-book text scans.
    """
    toc = pdf.get_toc()
    if toc:
        # Broken outlines resolve every destination to page 1 — unusable.
        same_page = sum(pg == toc[0][2] for _l, _t, pg in toc)
        if len(toc) >= 5 and same_page / len(toc) > 0.7:
            log.info("Embedded TOC has degenerate destinations — ignoring it.")
            toc = []
        elif len(toc) >= 0.8 * pdf.page_count and len({pg for _l, _t, pg in toc}) >= 0.8 * len(toc):
            log.info("Embedded TOC is per-page bookmarks — ignoring it.")
            toc = []
    if toc:
        tree = _from_embedded_toc(toc, pdf.page_count, cfg)
        # keep front/back matter out of the chapter list but don't lose it silently
        content = [c for c in tree if not _NONCONTENT.match(c.title)]
        if content:
            return content, "embedded_toc"
    if not allow_text_scan:
        return [], "none"
    log.info("No usable embedded TOC — trying printed-TOC parse.")
    tree = _from_printed_toc(pdf, cfg)
    if tree:
        return tree, "printed_toc"
    log.info("No printed TOC — running heuristic structure scan.")
    tree = _heuristic_scan(pdf, cfg)
    return (tree, "heuristic") if tree else ([], "none")


# --------------------------------------------------------------------------- #
class SectionIndex:
    """Resolves a page number to its deepest enclosing section label."""

    def __init__(self, roots: list[SectionNode]):
        self._starts: list[int] = []
        self._labels: list[str] = []
        flat: list[tuple[int, int, str]] = []  # (page_start, level, label)

        def walk(node: SectionNode, path: list[str]) -> None:
            label = f"{node.number} {node.title}".strip() if node.number else node.title
            flat.append((node.page_start, node.level, label))
            for child in node.sections:
                walk(child, path + [label])

        for r in roots:
            walk(r, [])
        # deepest section starting at/before the page wins; sort by page then level
        for pg, _lvl, label in sorted(flat, key=lambda x: (x[0], x[1])):
            self._starts.append(pg)
            self._labels.append(label)

    def label_for_page(self, page: int) -> Optional[str]:
        i = bisect_right(self._starts, page) - 1
        return self._labels[i] if i >= 0 else None
