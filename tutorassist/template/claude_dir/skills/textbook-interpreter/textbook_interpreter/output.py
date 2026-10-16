"""Versioned JSON serialization for pipeline results."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import SCHEMA_VERSION
from .models import Figure, LocateMatch, Process, QualityReport, SectionNode, Term


def build_document(
    *,
    source_path: Path,
    page_count: int,
    metadata: dict,
    quality: QualityReport,
    structure: list[SectionNode],
    structure_source: str,
    terms: list[Term],
    processes: list[Process],
    figures: list[Figure],
    pages_analyzed: tuple[int, int],
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "file": source_path.name,
            "pages": page_count,
            "pages_analyzed": {"start": pages_analyzed[0], "end": pages_analyzed[1]},
            "title": metadata.get("title"),
            "author": metadata.get("author"),
        },
        "extraction_quality": quality.to_dict(),
        "structure": {
            "source": structure_source,
            "chapters": [c.to_dict() for c in structure],
        },
        "terms": [t.to_dict() for t in terms],
        "processes": [p.to_dict() for p in processes],
        "figures": [f.to_dict() for f in figures],
        "stats": {
            "chapters": len(structure),
            "sections_total": _count_nodes(structure),
            "terms": len(terms),
            "processes": len(processes),
            "figures": sum(f.kind == "figure" for f in figures),
            "tables": sum(f.kind == "table" for f in figures),
        },
    }


def build_locate_document(
    *, source_path: Path, query: str, matches: list[LocateMatch]
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {"file": source_path.name},
        "query": query,
        "match_count": len(matches),
        "matches": [m.to_dict() for m in matches],
    }


def _count_nodes(nodes: list[SectionNode]) -> int:
    return sum(1 + _count_nodes(n.sections) for n in nodes)


def write_json(doc: dict[str, Any], out_path: Optional[Path]) -> Optional[Path]:
    payload = json.dumps(doc, indent=2, ensure_ascii=False)
    if out_path is None:
        print(payload)
        return None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    return out_path
