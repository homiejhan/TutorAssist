"""Pipeline orchestrator: loader → quality → structure → analyzers → JSON."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_CONFIG, PipelineConfig
from .figures import extract_figures
from .loader import TextbookPdf
from .locator import locate as _locate
from .output import build_document, build_locate_document
from .processes import extract_processes
from .quality import assess_quality
from .repair import build_font_repair_map
from .structure import SectionIndex, detect_structure
from .terms import extract_terms

log = logging.getLogger(__name__)


class TextbookInterpreter:
    """High-level API.

    >>> ti = TextbookInterpreter("book.pdf")
    >>> doc = ti.interpret()           # full JSON-ready dict
    >>> hits = ti.locate("Kirchhoff")  # query-time search
    """

    def __init__(self, pdf_path: str | Path, config: Optional[PipelineConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self.pdf = TextbookPdf(pdf_path)
        self._structure = None
        self._structure_source = None
        self._section_index: Optional[SectionIndex] = None

    # ------------------------------------------------------------------ #
    def _ensure_structure(self, allow_text_scan: bool = True) -> SectionIndex:
        if self._section_index is None:
            self._structure, self._structure_source = detect_structure(
                self.pdf, self.config.structure, allow_text_scan=allow_text_scan
            )
            self._section_index = SectionIndex(self._structure)
        return self._section_index

    # ------------------------------------------------------------------ #
    def interpret(
        self,
        page_start: int = 1,
        page_end: Optional[int] = None,
        include_terms: bool = True,
        include_processes: bool = True,
        include_figures: bool = True,
        export_images_dir: Optional[Path] = None,
    ) -> dict[str, Any]:
        page_end = min(page_end or self.pdf.page_count, self.pdf.page_count)

        log.info("Assessing text-layer quality …")
        quality = assess_quality(self.pdf, self.config.quality)

        # Broken-cmap repair: garbled/partial books are often shifted-font
        # extractions that a constant per-font offset fully recovers.
        if quality.text_layer != "none":
            log.info("Checking for broken font cmaps …")
            repair_map = build_font_repair_map(self.pdf)
            if repair_map:
                shifts = sorted(set(repair_map.values()))
                self.pdf.enable_repair(shifts)
                quality = assess_quality(self.pdf, self.config.quality)
                quality.repair_shifts = shifts
                quality.notes.insert(
                    0,
                    f"Detected {len(repair_map)} broken font cmap(s); applied "
                    f"per-span constant-shift repair (shifts {shifts}) and "
                    f"re-assessed quality.",
                )

        log.info("Detecting structure …")
        section_index = self._ensure_structure(
            allow_text_scan=(quality.text_layer != "none")
        )

        terms = processes = figures = []
        if quality.text_layer == "none":
            log.warning("No text layer — skipping text-based analyzers.")
        elif quality.text_layer == "garbled":
            log.warning("Garbled text layer — analyzers run, results flagged unreliable.")

        # Terms/processes belong to content chapters, not front matter:
        # start at the first numbered chapter when the caller didn't narrow.
        content_start = page_start
        if page_start == 1 and self._structure:
            numbered = [c.page_start for c in self._structure if c.number]
            if numbered:
                content_start = max(page_start, min(numbered))

        if quality.text_layer != "none":
            if include_terms:
                log.info("Extracting terms …")
                terms = extract_terms(self.pdf, section_index,
                                      self.config.terms, content_start, page_end)
            if include_processes:
                log.info("Extracting processes …")
                processes = extract_processes(self.pdf, section_index,
                                              self.config.processes,
                                              content_start, page_end)
            if include_figures:
                log.info("Extracting figures …")
                figures = extract_figures(self.pdf, section_index,
                                          self.config.figures,
                                          page_start, page_end,
                                          export_dir=export_images_dir)

        return build_document(
            source_path=self.pdf.path,
            page_count=self.pdf.page_count,
            metadata=self.pdf.metadata,
            quality=quality,
            structure=self._structure or [],
            structure_source=self._structure_source or "none",
            terms=terms,
            processes=processes,
            figures=figures,
            pages_analyzed=(page_start, page_end),
        )

    # ------------------------------------------------------------------ #
    def locate(self, query: str, max_matches: int = 50) -> dict[str, Any]:
        section_index = self._ensure_structure()
        matches = _locate(self.pdf, query, section_index, max_matches=max_matches)
        return build_locate_document(
            source_path=self.pdf.path, query=query, matches=matches
        )

    def close(self) -> None:
        self.pdf.close()

    def __enter__(self) -> "TextbookInterpreter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
