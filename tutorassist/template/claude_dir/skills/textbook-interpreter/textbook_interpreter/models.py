"""Typed data models. Every model serializes to plain dicts for JSON output.

All page numbers in this schema are 1-based (human/PDF-viewer convention).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# --------------------------------------------------------------------------- #
# Raw page data (internal, produced by loader — not serialized directly)
# --------------------------------------------------------------------------- #

@dataclass
class Span:
    """A run of text with uniform styling."""
    text: str
    size: float
    bold: bool
    italic: bool
    font: str
    bbox: tuple[float, float, float, float]
    line_id: int          # index of the physical line this span belongs to


@dataclass
class PageData:
    number: int                       # 1-based
    text: str                         # plain text ("text" mode)
    spans: list[Span]
    image_count: int
    char_count: int


# --------------------------------------------------------------------------- #
# Serialized result models
# --------------------------------------------------------------------------- #

@dataclass
class SectionNode:
    number: Optional[str]             # e.g. "3.2" (None if unnumbered)
    title: str
    level: int                        # 1 = chapter, 2 = section, 3 = subsection
    page_start: int
    page_end: int
    sections: list["SectionNode"] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "level": self.level,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "sections": [s.to_dict() for s in self.sections],
        }


@dataclass
class Term:
    term: str
    description: str
    page: int
    section: Optional[str]            # "3.2 Node Analysis" style path label
    method: str                       # "definition_pattern" | "emphasis" | "glossary"
    confidence: float                 # 0..1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProcessStep:
    n: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Process:
    id: str                           # e.g. "proc-0007"
    type: str                         # "numbered_steps" | "worked_example" | "procedure"
    title: str
    page_start: int
    page_end: int
    section: Optional[str]
    steps: list[ProcessStep]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section": self.section,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class Figure:
    label: str                        # "Figure 3.12" / "Table 2.1"
    kind: str                         # "figure" | "table"
    number: str                       # "3.12"
    caption: str
    page: int
    section: Optional[str]
    exported_path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocateMatch:
    query: str
    page: int
    section: Optional[str]
    snippet: str
    match_kind: str                   # "exact" | "case_insensitive"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class QualityReport:
    text_layer: str                   # "good" | "partial" | "garbled" | "none"
    pages_sampled: int
    pages_with_text: int
    avg_chars_per_page: float
    notes: list[str] = field(default_factory=list)
    repair_shifts: list[int] = field(default_factory=list)  # broken-cmap shifts applied

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
