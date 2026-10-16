"""Central tunables for the extraction pipeline.

Every heuristic threshold lives here so behavior can be adjusted in one
place (or overridden programmatically) without touching analyzer code.
"""

from dataclasses import dataclass, field


@dataclass
class QualityConfig:
    """Text-layer quality classification thresholds."""
    min_chars_per_page: int = 120        # below this a page counts as "empty"
    sample_pages: int = 40               # pages sampled for quality scoring
    min_alpha_ratio: float = 0.55        # alphabetic chars / total non-space
    min_ascii_ratio: float = 0.80        # printable-ascii ratio (garble detector)
    min_dict_word_ratio: float = 0.50    # recognizable English word ratio
    good_page_ratio: float = 0.70        # fraction of sampled pages that must pass


@dataclass
class StructureConfig:
    """Chapter/section detection."""
    max_toc_depth: int = 3               # deepest outline level kept
    heading_min_font_ratio: float = 1.25 # heading font vs body font (heuristic mode)
    max_heading_words: int = 14
    min_chapter_gap_pages: int = 3       # heuristic chapters must be pages apart


@dataclass
class TermsConfig:
    """Term / definition extraction."""
    max_term_words: int = 6
    min_term_chars: int = 3
    max_description_chars: int = 420
    min_description_chars: int = 25
    max_terms_per_page: int = 8          # guards against style-heavy pages
    emphasis_min_confidence: float = 0.45


@dataclass
class ProcessConfig:
    """Step-by-step process detection."""
    min_steps: int = 3
    max_steps: int = 40
    max_step_chars: int = 600
    max_gap_lines: int = 6               # blank/wrapped lines allowed between steps


@dataclass
class FiguresConfig:
    max_caption_chars: int = 320


@dataclass
class PipelineConfig:
    quality: QualityConfig = field(default_factory=QualityConfig)
    structure: StructureConfig = field(default_factory=StructureConfig)
    terms: TermsConfig = field(default_factory=TermsConfig)
    processes: ProcessConfig = field(default_factory=ProcessConfig)
    figures: FiguresConfig = field(default_factory=FiguresConfig)


DEFAULT_CONFIG = PipelineConfig()
