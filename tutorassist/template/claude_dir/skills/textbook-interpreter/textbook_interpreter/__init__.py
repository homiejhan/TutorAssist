"""textbook_interpreter — extract structure, terms, processes, and figures from textbook PDFs.

Layers:
    loader      -> raw page data (text, spans, images) + text-layer quality
    structure   -> chapters / sub-chapters (embedded TOC or heuristics)
    terms       -> term / concept-name / description extraction
    processes   -> step-by-step procedures and worked examples
    figures     -> figure / table / diagram captions and embedded image metadata
    locator     -> query-time search ("where is X discussed?")
    output      -> versioned JSON serialization
    interpreter -> pipeline orchestrator
"""

__version__ = "1.0.0"
SCHEMA_VERSION = "1.0"

from .interpreter import TextbookInterpreter

__all__ = ["TextbookInterpreter", "SCHEMA_VERSION", "__version__"]
