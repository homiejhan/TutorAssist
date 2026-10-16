"""CLI entry point.

Usage:
    python -m textbook_interpreter interpret book.pdf -o out.json
    python -m textbook_interpreter interpret book.pdf --pages 1-120 --export-images imgs/
    python -m textbook_interpreter locate book.pdf "Kirchhoff's current law" -o hits.json
    python -m textbook_interpreter info book.pdf
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .interpreter import TextbookInterpreter
from .loader import PdfLoadError, TextbookPdf
from .output import write_json
from .quality import assess_quality
from .config import DEFAULT_CONFIG


def _parse_pages(spec: str | None, page_count: int) -> tuple[int, int]:
    if not spec:
        return 1, page_count
    try:
        if "-" in spec:
            a, b = spec.split("-", 1)
            return max(1, int(a)), min(int(b), page_count)
        p = int(spec)
        return p, p
    except ValueError:
        raise SystemExit(f"Invalid --pages value: {spec!r} (expected N or A-B)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="textbook_interpreter",
        description="Interpret a textbook PDF: structure, terms, processes, figures.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_int = sub.add_parser("interpret", help="Full extraction → JSON")
    p_int.add_argument("pdf", type=Path)
    p_int.add_argument("-o", "--output", type=Path, default=None,
                       help="Output JSON path (default: stdout)")
    p_int.add_argument("--pages", default=None, help="Page range, e.g. 1-120")
    p_int.add_argument("--export-images", type=Path, default=None,
                       metavar="DIR", help="Export embedded figure images here")
    p_int.add_argument("--no-terms", action="store_true")
    p_int.add_argument("--no-processes", action="store_true")
    p_int.add_argument("--no-figures", action="store_true")

    p_loc = sub.add_parser("locate", help="Find where a term/concept appears")
    p_loc.add_argument("pdf", type=Path)
    p_loc.add_argument("query")
    p_loc.add_argument("-o", "--output", type=Path, default=None)
    p_loc.add_argument("--max-matches", type=int, default=50)

    p_info = sub.add_parser("info", help="Quick diagnostics (quality, TOC, size)")
    p_info.add_argument("pdf", type=Path)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        if args.command == "interpret":
            with TextbookInterpreter(args.pdf) as ti:
                start, end = _parse_pages(args.pages, ti.pdf.page_count)
                doc = ti.interpret(
                    page_start=start,
                    page_end=end,
                    include_terms=not args.no_terms,
                    include_processes=not args.no_processes,
                    include_figures=not args.no_figures,
                    export_images_dir=args.export_images,
                )
            path = write_json(doc, args.output)
            if path:
                s = doc["stats"]
                print(
                    f"Wrote {path}  "
                    f"[quality={doc['extraction_quality']['text_layer']}, "
                    f"chapters={s['chapters']}, terms={s['terms']}, "
                    f"processes={s['processes']}, figures={s['figures']}, "
                    f"tables={s['tables']}]",
                    file=sys.stderr,
                )

        elif args.command == "locate":
            with TextbookInterpreter(args.pdf) as ti:
                doc = ti.locate(args.query, max_matches=args.max_matches)
            path = write_json(doc, args.output)
            if path:
                print(f"Wrote {path}  [{doc['match_count']} matches]", file=sys.stderr)

        elif args.command == "info":
            with TextbookPdf(args.pdf) as pdf:
                q = assess_quality(pdf, DEFAULT_CONFIG.quality)
                info = {
                    "file": pdf.path.name,
                    "pages": pdf.page_count,
                    "metadata": pdf.metadata,
                    "embedded_toc_entries": len(pdf.get_toc()),
                    "quality": q.to_dict(),
                }
            print(json.dumps(info, indent=2, ensure_ascii=False))

        return 0

    except PdfLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
