"""PDF loading layer.

Single pass over the document producing `PageData` objects that every
downstream analyzer consumes. Uses PyMuPDF for speed on 500–1200 page books.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, Optional

import pymupdf

from .models import PageData, Span

log = logging.getLogger(__name__)

_BOLD_FLAG = 1 << 4    # PyMuPDF span flag bit: bold
_ITALIC_FLAG = 1 << 1  # italic


class PdfLoadError(RuntimeError):
    pass


class TextbookPdf:
    """Wraps an open PDF and lazily materializes per-page data."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise PdfLoadError(f"File not found: {self.path}")
        try:
            self.doc = pymupdf.open(str(self.path))
        except Exception as exc:  # pragma: no cover
            raise PdfLoadError(f"Cannot open PDF: {exc}") from exc
        if self.doc.is_encrypted and not self.doc.authenticate(""):
            raise PdfLoadError("PDF is password-protected.")
        self._pages: dict[int, PageData] = {}
        self._repair_shifts: list[int] = []

    # ------------------------------------------------------------------ #
    def enable_repair(self, candidate_shifts: list[int]) -> None:
        """Activate per-span broken-cmap repair; invalidates cached pages."""
        self._repair_shifts = sorted(set(candidate_shifts))
        self._pages.clear()

    # ------------------------------------------------------------------ #
    @property
    def page_count(self) -> int:
        return len(self.doc)

    @property
    def metadata(self) -> dict:
        md = self.doc.metadata or {}
        return {
            "title": (md.get("title") or "").strip() or None,
            "author": (md.get("author") or "").strip() or None,
        }

    def get_toc(self) -> list[tuple[int, str, int]]:
        """Embedded outline as (level, title, page_1based)."""
        try:
            return [(lvl, t.strip(), pg) for lvl, t, pg in self.doc.get_toc(simple=True) if pg > 0]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    def page(self, number: int, with_spans: bool = True) -> PageData:
        """Return PageData for a 1-based page number (cached)."""
        cached = self._pages.get(number)
        if cached is not None and (cached.spans or not with_spans):
            return cached

        p = self.doc[number - 1]
        if self._repair_shifts:
            spans = self._extract_spans(p, self._repair_shifts)
            text = self._text_from_spans(spans)
        else:
            text = p.get_text("text")
            spans = self._extract_spans(p) if with_spans else []
        try:
            image_count = len(p.get_images(full=True))
        except Exception:
            image_count = 0

        data = PageData(
            number=number,
            text=text,
            spans=spans,
            image_count=image_count,
            char_count=len(text.strip()),
        )
        self._pages[number] = data
        return data

    def pages(
        self,
        start: int = 1,
        end: Optional[int] = None,
        with_spans: bool = True,
    ) -> Iterator[PageData]:
        end = end or self.page_count
        end = min(end, self.page_count)
        for n in range(max(1, start), end + 1):
            yield self.page(n, with_spans=with_spans)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_spans(
        p: "pymupdf.Page", repair_shifts: Optional[list[int]] = None
    ) -> list[Span]:
        from .repair import repair_text

        spans: list[Span] = []
        try:
            d = p.get_text("dict")
        except Exception:
            return spans
        line_id = 0
        for block in d.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                for s in line.get("spans", []):
                    txt = s.get("text", "")
                    font = s.get("font", "")
                    if repair_shifts:
                        txt = repair_text(txt, repair_shifts)
                    if not txt.strip():
                        continue
                    flags = s.get("flags", 0)
                    fl = font.lower()
                    spans.append(
                        Span(
                            text=txt,
                            size=round(float(s.get("size", 0.0)), 2),
                            bold=bool(flags & _BOLD_FLAG) or "bold" in fl or "demi" in fl,
                            italic=bool(flags & _ITALIC_FLAG) or "italic" in fl or "oblique" in fl,
                            font=font,
                            bbox=tuple(round(v, 1) for v in s.get("bbox", (0, 0, 0, 0))),
                            line_id=line_id,
                        )
                    )
                line_id += 1
        return spans

    @staticmethod
    def _text_from_spans(spans: list[Span]) -> str:
        """Reconstruct plain page text (one physical line per output line)."""
        lines: dict[int, list[Span]] = {}
        for sp in spans:
            lines.setdefault(sp.line_id, []).append(sp)
        out = []
        for lid in sorted(lines):
            parts = sorted(lines[lid], key=lambda s: s.bbox[0])
            out.append(" ".join(p.text.strip() for p in parts if p.text.strip()))
        return "\n".join(out)

    # ------------------------------------------------------------------ #
    def export_page_images(self, number: int, out_dir: Path) -> list[str]:
        """Export embedded raster images of one page; returns file paths."""
        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        p = self.doc[number - 1]
        for i, img in enumerate(p.get_images(full=True)):
            xref = img[0]
            try:
                pix = pymupdf.Pixmap(self.doc, xref)
                if pix.n - pix.alpha > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                if pix.width < 32 or pix.height < 32:   # skip masks/decoration
                    continue
                fp = out_dir / f"p{number:04d}_img{i}.png"
                pix.save(str(fp))
                paths.append(str(fp))
            except Exception as exc:
                log.debug("image export failed p%d #%d: %s", number, i, exc)
        return paths

    def close(self) -> None:
        self.doc.close()

    def __enter__(self) -> "TextbookPdf":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
