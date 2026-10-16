#!/usr/bin/env python3
"""
worksheet_gen.py — Worksheet Studio text format -> typeset PDF via LaTeX.

This is a line-for-line port of the Model parser in
https://github.com/homiejhan/pdfgenerator (js/app.js), re-targeted at a real
LaTeX toolchain (pdflatex) instead of the browser/KaTeX/html2canvas/jsPDF
pipeline the website uses. Anything that parses correctly on the website
parses identically here, and since KaTeX's supported math subset is itself a
subset of real LaTeX, $...$ / $$...$$ math spans pass through to pdflatex
almost verbatim.

Usage:
    python3 worksheet_gen.py input.txt -o my_worksheet --spacing standard
    python3 worksheet_gen.py input.txt --density relaxed --part-spacing standard

Output:
    <slug>.pdf   (and intermediate <slug>.tex / .aux / .log next to it)
"""

# Vendored into TutorAssist from the worksheet-studio-pdf skill by
# scripts/vendor_skills.py (with its patches). Edit the skill, then re-vendor.
import argparse
import math
import re
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

# =====================================================================
# MODEL — parser (port of Model.parse in js/app.js)
# =====================================================================

PROBLEM_RE = re.compile(
    r"^\s*([*\u2605\u22C6]\s*)?(?:Problem|Question|Q|#)?\s*(\d+)\s*[.):]\s*(.*)$",
    re.IGNORECASE,
)
ANSWER_HEADER_RE = re.compile(
    r"^\s*(answer\s*key|answers|solutions?)\s*:?\s*$", re.IGNORECASE
)


# [grid 4] / [columns 3] ... [/grid] / [/columns] — compact layouts for short
# problems (LaTeX-only extension). Options after the number: boxed | plain
# (grid), rule | norule (columns), space: none|compact|standard|large.
LAYOUT_OPEN_RE = re.compile(
    r"^\s*\[\s*(grid|columns|column|cols)\s*[:=]?\s*(\d+)\s*[,;]?\s*([^\]]*)\]\s*$", re.IGNORECASE
)
LAYOUT_CLOSE_RE = re.compile(r"^\s*\[/\s*(grid|columns|column|cols)\s*\]\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------
# Formatting + spacing extensions (LaTeX-only; the website shows these
# lines as plain text)
# ---------------------------------------------------------------------

# [box] / [box: Key idea] / [box gold: Watch the signs] ... [/box]
BOX_OPEN_RE = re.compile(r"^\s*\[\s*box\b\s*([^\]]*)\]\s*$", re.IGNORECASE)
BOX_CLOSE_RE = re.compile(r"^\s*\[/\s*box\s*\]\s*$", re.IGNORECASE)
# "> text" lines -> a quick untitled callout box
QUOTE_RE = re.compile(r"^\s*>(?:\s+(.*)|\s*)$")
# (1) text  -> numbered step with a navy badge;  Step 1. text -> "Step 1." label
STEP_RE = re.compile(r"^\s*\((\d{1,2})\)\s+(.*)$")
STEPWORD_RE = re.compile(r"^\s*(step\s+\d{1,2})\s*[.):]\s+(.*)$", re.IGNORECASE)
# - text  /  • text  -> bullet
BULLET_RE = re.compile(r"^\s*[-•]\s+(.*)$")
# ## Heading  -> navy sub-heading inside a problem (### for a smaller one)
HEADING_RE = re.compile(r"^\s*(#{2,3})\s+(.+?)\s*$")
# an indented line (2+ spaces or a tab) right under a list item continues it on a new line
CONT_RE = re.compile(r"^(?: {2,}|\t)\s*\S")
# a paragraph that opens with a bold run-in label (**Example A.** ...) gets
# extra space above it and is kept with what follows
LEAD_RE = re.compile(r"^\s*\*\*[^*]+?\*\*")
# a problem whose first line is only bold text uses it as a title on the header line
TITLE_LINE_RE = re.compile(r"^\s*\*\*([^*]+)\*\*\s*$")

# spacing directives (each on its own line)
SPACE_RE = re.compile(r"^\s*\[\s*(?:space|vspace|gap)\b\s*[:=]?\s*([^\]]*)\]\s*$", re.IGNORECASE)
WORKSPACE_RE = re.compile(r"^\s*\[\s*workspace\b\s*[:=]?\s*([^\]]*)\]\s*$", re.IGNORECASE)
LINES_RE = re.compile(r"^\s*\[\s*(?:lines|writing\s*lines)\s*[:=]?\s*(\d{1,2})\s*\]\s*$", re.IGNORECASE)
NEWPAGE_RE = re.compile(r"^\s*\[\s*(?:new\s*page|page\s*break)\s*\]\s*$", re.IGNORECASE)
RULE_RE = re.compile(r"^\s*(?:\[\s*(?:rule|hr|divider)\s*\]|-{3,})\s*$", re.IGNORECASE)
SETTINGS_RE = re.compile(r"^\s*\[\s*(?:settings|style)\b\s*[:=]?\s*([^\]]*)\]\s*$", re.IGNORECASE)

SPACING_MAP = {
    "none": "0in",
    "compact": "0.4in",
    "standard": "0.9in",
    "large": "1.6in",
    "xlarge": "2.4in",
}
_SPACE_ALIASES = {"0": "none", "off": "none", "small": "compact", "medium": "standard",
                  "default": "standard", "big": "large", "huge": "xlarge", "x-large": "xlarge"}

# --density presets: line spacing, paragraph gap, gap above labeled
# paragraphs/headings, list-item gap, gap around boxes, gap between problems.
DENSITY_PRESETS = {
    "compact":  dict(spread="1.0",  parskip="3pt",  lead="2pt",  item="1pt", box="4pt",
                     problem="6pt plus 2pt minus 2pt"),
    "normal":   dict(spread="1.0",  parskip="6pt",  lead="6pt",  item="3pt", box="8pt",
                     problem="12pt plus 4pt minus 4pt"),
    "relaxed":  dict(spread="1.1",  parskip="8pt",  lead="10pt", item="4pt", box="11pt",
                     problem="20pt plus 4pt minus 4pt"),
    "spacious": dict(spread="1.2",  parskip="11pt", lead="14pt", item="6pt", box="14pt",
                     problem="28pt plus 6pt minus 4pt"),
}
_DENSITY_ALIASES = {"tight": "compact", "default": "normal", "standard": "normal",
                    "airy": "relaxed", "loose": "relaxed", "roomy": "spacious"}

_LEN_RE = re.compile(r"^(-?\d*\.?\d+)\s*(in|cm|mm|pt|em|ex)?$", re.IGNORECASE)
_PER_IN = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27,
           "em": 11 / 72.27, "ex": 5 / 72.27}


def _parse_space(val: str, what: str, default: str = "standard") -> str:
    """'large' / 'small' / '1.2in' / '2cm' / '-6pt' / '' -> a LaTeX length."""
    v = (val or "").strip().lower()
    if not v:
        v = default
    v = _SPACE_ALIASES.get(v, v)
    if v in SPACING_MAP:
        return SPACING_MAP[v]
    m = _LEN_RE.match(v)
    if m:
        return f"{m.group(1)}{(m.group(2) or 'in').lower()}"
    raise WorksheetError(
        f"{what}: can't read '{val.strip()}' — use none, compact, standard, large, "
        f"xlarge, or a length like 0.5in / 2cm / 12pt")


def _len_in(length: str) -> float:
    m = _LEN_RE.match(length.strip())
    return float(m.group(1)) * _PER_IN[(m.group(2) or "in").lower()] if m else 0.0


def _norm_density(val: str, what: str = "density") -> str:
    v = _DENSITY_ALIASES.get(val.strip().lower(), val.strip().lower())
    if v not in DENSITY_PRESETS:
        raise WorksheetError(f"{what}: '{val}' is not one of compact, normal, relaxed, spacious")
    return v


def _parse_settings(s: str) -> dict:
    """'[settings density: relaxed, spacing: compact, part-spacing: standard]'."""
    out = {}
    for part in re.split(r"[,;]", s):
        if not part.strip():
            continue
        m = re.match(r"\s*([A-Za-z][A-Za-z \-_]*?)\s*[:=]\s*(\S.*?)\s*$", part)
        if not m:
            raise WorksheetError(
                f"[settings]: can't read '{part.strip()}' (write key: value, e.g. density: relaxed)")
        key = re.sub(r"[\s_\-]+", "", m.group(1).lower())
        val = m.group(2).strip()
        if key in ("density", "leading"):
            out["density"] = _norm_density(val, "[settings] density")
        elif key in ("spacing", "workspace"):
            out["spacing"] = _parse_space(val, "[settings] spacing")
        elif key in ("partspacing", "partspace", "parts", "subpartspacing", "partworkspace"):
            out["part_spacing"] = _parse_space(val, "[settings] part-spacing")
        else:
            raise WorksheetError(
                f"[settings]: unknown key '{m.group(1).strip()}' (density, spacing, part-spacing)")
    return out


def _is_directive(line: str) -> bool:
    return bool(SPACE_RE.match(line) or WORKSPACE_RE.match(line) or LINES_RE.match(line)
                or NEWPAGE_RE.match(line) or SETTINGS_RE.match(line))


def _parse_layout(m, ident: int):
    kind = "grid" if m.group(1).lower() == "grid" else "columns"
    n = int(m.group(2))
    if not 1 <= n <= 4:
        raise WorksheetError(f"[{m.group(1)} {n}]: use 1 to 4 columns (4 max)")
    lay = {"kind": kind, "n": n, "boxed": True, "rule": True, "space": None, "id": ident}
    opts = m.group(3).strip()
    sm = re.search(r"\b(?:space|workspace)\s*[:=]?\s*(none|compact|standard|large)\b", opts, re.I)
    if sm:
        lay["space"] = sm.group(1).lower()
        opts = opts[: sm.start()] + opts[sm.end():]
    for w in re.split(r"[\s,;]+", opts.lower()):
        if not w:
            continue
        if w in ("plain", "noborder", "noborders", "unboxed"):
            lay["boxed"] = False
        elif w in ("boxed", "border", "borders", "lines"):
            lay["boxed"] = True
        elif w in ("norule", "nolines"):
            lay["rule"] = False
        elif w == "rule":
            lay["rule"] = True
        else:
            raise WorksheetError(
                f"[{m.group(1)} {n}]: unknown option '{w}' (boxed, plain, rule, norule, "
                f"space: none|compact|standard|large)")
    if kind == "columns" and n == 1:
        return None  # [columns 1] = back to the normal one-column layout
    return lay


def parse_worksheet(text: str) -> dict:
    """Parse Worksheet Studio source text into
    {title, subtitle, problems: [...], answers: [...]}.

    Each problem/answer dict is {num, star, lines: [raw str, ...]}.
    This mirrors Model.parse exactly, including its quirks (e.g. a line
    that "looks like" a problem marker before a title has been set is
    consumed as the title itself, and stray head-section lines once both
    title and subtitle are set are silently dropped) — fidelity to the
    website's parser is the whole point, so nothing here is "improved".
    """
    lines = text.splitlines()
    title = None
    subtitle = None
    mode = "head"  # head -> problems -> (answers once header seen)
    current = None
    problems = []
    answers = []

    def close():
        nonlocal current
        if current is not None:
            (answers if current["isAnswer"] else problems).append(current)
        current = None

    in_block = None  # the close-tag regex of the [graph]/[figure] block we're inside
    box_depth = 0    # >0 while inside [box] ... [/box] (boxes may nest)
    layout = None    # current [grid N] / [columns N] layout (None = normal)
    layout_count = 0
    settings = {}
    for raw in lines:
        # Lines inside a [graph]/[figure] block belong to the current problem
        # verbatim, even if one happens to look like "2." or "Answers".
        if in_block is not None:
            if current is not None:
                current["lines"].append(raw)
            if in_block.match(raw):
                in_block = None
            continue
        # Same for [box] ... [/box]: a "1." or "Answers" line inside a box is box text.
        if box_depth:
            if current is not None:
                current["lines"].append(raw)
            if BOX_OPEN_RE.match(raw):
                box_depth += 1
            elif BOX_CLOSE_RE.match(raw):
                box_depth -= 1
            continue

        # [settings ...] before the first problem sets worksheet-wide spacing;
        # other spacing directives there are ignored (never become the subtitle).
        if mode == "head":
            sm = SETTINGS_RE.match(raw)
            if sm:
                settings.update(_parse_settings(sm.group(1)))
                continue
            if title and _is_directive(raw):
                continue

        lo, lc = LAYOUT_OPEN_RE.match(raw), LAYOUT_CLOSE_RE.match(raw)
        if lo or lc:
            close()
            if mode == "head" and title:
                mode = "problems"
            layout = None
            if lo:
                layout_count += 1
                layout = _parse_layout(lo, layout_count)
            continue

        if ANSWER_HEADER_RE.match(raw):
            close()
            mode = "answers"
            layout = None
            continue

        m = PROBLEM_RE.match(raw)

        if mode == "head":
            if m and title:
                mode = "problems"
            elif raw.strip():
                if title is None:
                    title = raw.strip()
                else:
                    if subtitle is None:
                        subtitle = raw.strip()
                continue
            else:
                continue

        if m:
            close()
            body = m.group(3).strip()
            current = {
                "num": int(m.group(2)),
                "star": bool(m.group(1)),
                "lines": [m.group(3)] if body else [],
                "isAnswer": mode == "answers",
                "layout": layout,
            }
        elif current is not None:
            current["lines"].append(raw)
            if GRAPH_OPEN_RE.match(raw):
                in_block = GRAPH_CLOSE_RE
            elif FIGURE_OPEN_RE.match(raw):
                in_block = FIGURE_CLOSE_RE
            elif BOX_OPEN_RE.match(raw):
                box_depth = 1

    close()
    return {
        "title": title or "Worksheet",
        "subtitle": subtitle,
        "problems": problems,
        "answers": answers,
        "settings": settings,
    }


def slugify(title: str) -> str:
    """Filename-safe slug of a title, matching Model.slug()."""
    no_math = re.sub(r"\$[^$]*\$", "", title)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", no_math).strip("_")
    return slug or "worksheet"


# =====================================================================
# VIEW — LaTeX rendering (port of View.inlineFormat / renderBody)
# =====================================================================

# Placeholder used while splitting text into math/non-math spans so an
# escaped literal dollar sign ("\$", e.g. currency) is never mistaken for
# a math delimiter. LaTeX already treats "\$" as a literal dollar sign
# natively, so text spans just restore this token straight to "\$".
DOLLAR_PLACEHOLDER = "\x00DOLLAR\x00"

# Placeholder that stands in for a fully-rendered math span while markdown
# (**bold** / *italic*) is applied to the line as a whole. Rendered math
# never contains a NUL byte, so these tokens can't collide with math output.
MATH_TOKEN_RE = re.compile(r"\x00M(\d+)\x00")

MATH_SPLIT_RE = re.compile(r"(\${1,2}[^$]*\${1,2})")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(^|[\s(])\*(?!\s)([^*]+?)(?<!\s)\*(?!\*)")
PART_RE = re.compile(r"^\s*\(?([a-h])[).]\s+(.*)$")
SMART_QUOTE_RE = re.compile(r'(^|[\s(\[{])"(\S[^"]*?)"(?=$|[\s.,;:!?)\]}\x00])')

_LATEX_SPECIALS = [
    ("\\", r"\textbackslash{}"),
    ("&", r"\&"),
    ("%", r"\%"),
    ("#", r"\#"),
    ("_", r"\_"),
    ("{", r"\{"),
    ("}", r"\}"),
    ("~", r"\textasciitilde{}"),
    ("^", r"\textasciicircum{}"),
]


def escape_latex(s: str) -> str:
    """Escape LaTeX special characters in plain (non-math) text."""
    for char, repl in _LATEX_SPECIALS:
        s = s.replace(char, repl)
    return s


def apply_markdown(s: str) -> str:
    """**bold** -> \\textbf{}, single *italic* -> \\textit{} (don't nest)."""
    s = BOLD_RE.sub(r"\\textbf{\1}", s)
    s = ITALIC_RE.sub(r"\1\\textit{\2}", s)
    return s


def inline_format(line: str) -> str:
    """Format one raw line: escape/markdown outside $...$, pass math through.

    Math spans are first swapped out for NUL-delimited placeholder tokens so
    that markdown (**bold** / *italic*) is applied to the line *as a whole*
    rather than to each text fragment in isolation. Without this, a bold or
    italic span that encloses inline math — e.g. ``**Step 1: solve $x$.**`` —
    gets sliced across the math boundary by MATH_SPLIT_RE, the ``**`` markers
    land in different fragments, and the pair never matches, so the literal
    asterisks leak into the PDF. Tokenizing keeps the markers contiguous.
    """
    protected = line.replace("\\$", DOLLAR_PLACEHOLDER)
    segments = MATH_SPLIT_RE.split(protected)

    math_store = []
    pieces = []
    for seg in segments:
        if not seg:
            continue
        if seg.startswith("$"):
            display = seg.startswith("$$")
            body = seg[2:-2] if display else seg[1:-1]
            body = body.replace(DOLLAR_PLACEHOLDER, r"\$").replace("\u00b0", "^{\\circ}")
            rendered = rf"\(\displaystyle {body}\)" if display else f"${body}$"
            pieces.append(f"\x00M{len(math_store)}\x00")
            math_store.append(rendered)
        else:
            text = escape_latex(seg)
            text = text.replace(DOLLAR_PLACEHOLDER, r"\$")
            pieces.append(text)

    combined = apply_markdown("".join(pieces))
    # "quoted text" -> proper curly quotes (an inch mark like 5" is left alone)
    combined = SMART_QUOTE_RE.sub(r"\1``\2''", combined)
    # Restore rendered math via a function replacement so backslashes in the
    # math body are inserted literally (never treated as regex escapes).
    return MATH_TOKEN_RE.sub(lambda m: math_store[int(m.group(1))], combined)


def is_bare_display_math(line: str) -> str | None:
    """If line is *only* a $$...$$ span, return its inner LaTeX body, else None."""
    stripped = line.strip()
    protected = stripped.replace("\\$", DOLLAR_PLACEHOLDER)
    if protected.startswith("$$") and protected.endswith("$$") and len(protected) > 3:
        body = protected[2:-2]
        if "$" not in body:  # no unescaped, unmatched dollars inside
            return body.replace(DOLLAR_PLACEHOLDER, r"\$")
    return None


# =====================================================================
# BLOCKS — [graph]...[/graph] and | pipe | tables
#
# LaTeX-only extension of the text format (the website does not know
# these blocks). Entry points:
#     render_graph(lines)  -> TikZ/pgfplots code for one [graph] block
#     render_table(rows)   -> tabular code for one run of "| a | b |" lines
# render_body() below detects the blocks and calls these.
# =====================================================================


class WorksheetError(ValueError):
    """Bad [graph] / table source. The message names the offending line."""


GRAPH_OPEN_RE = re.compile(r"^\s*\[graph\]\s*$", re.IGNORECASE)
GRAPH_CLOSE_RE = re.compile(r"^\s*\[/graph\]\s*$", re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"^\s*\|")

# ---------------------------------------------------------------------
# Expression compiler: "2x^2 - 3x + 1", "|x - 2|", "\frac{1}{2}x", "sin x"
# ---------------------------------------------------------------------

_EXPR_FUNCS = {
    "sqrt": math.sqrt,
    "cbrt": lambda v: math.copysign(abs(v) ** (1.0 / 3.0), v),
    "abs": abs,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sec": lambda v: 1.0 / math.cos(v),
    "csc": lambda v: 1.0 / math.sin(v),
    "cot": lambda v: 1.0 / math.tan(v),
    "arcsin": math.asin,
    "arccos": math.acos,
    "arctan": math.atan,
    "ln": math.log,
    "log": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}
_EXPR_CONSTS = {"pi": math.pi, "e": math.e}
_EXPR_NAMES = sorted(
    list(_EXPR_FUNCS) + list(_EXPR_CONSTS) + ["x"], key=len, reverse=True
)
_EXPR_NS = {"__builtins__": {}, **_EXPR_FUNCS, **_EXPR_CONSTS}


def _brace_group(s: str, i: int):
    """s[i] == '{' -> (inner text, index just past the matching '}')."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
    raise WorksheetError(f"unbalanced braces in '{s}'")


def _expand_latex(s: str) -> str:
    r"""Rewrite \frac{a}{b} and \sqrt[n]{a} into plain parenthesised math."""
    out = []
    i = 0
    while i < len(s):
        m = re.match(r"\\[dt]?frac\s*(?=\{)", s[i:])
        if m:
            a, j = _brace_group(s, i + m.end())
            while j < len(s) and s[j] == " ":
                j += 1
            if j >= len(s) or s[j] != "{":
                raise WorksheetError(r"\frac needs two {...} groups")
            b, j = _brace_group(s, j)
            out.append(f"(({_expand_latex(a)})/({_expand_latex(b)}))")
            i = j
            continue
        m = re.match(r"\\sqrt\s*(?:\[([^\]]*)\])?\s*(?=\{)", s[i:])
        if m:
            a, j = _brace_group(s, i + m.end())
            a = _expand_latex(a)
            n = (m.group(1) or "").strip()
            if not n or n == "2":
                out.append(f"sqrt({a})")
            elif n == "3":
                out.append(f"cbrt({a})")
            else:
                out.append(f"(({a})^(1/({n})))")
            i = j
            continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _delatex(src: str) -> str:
    s = src.replace("$", "")
    for a, b in (("\u2212", "-"), ("\u00b7", "*"), ("\u00d7", "*"), ("\u00f7", "/"),
                 ("\u03c0", "pi"), ("\u00b2", "^2"), ("\u00b3", "^3")):
        s = s.replace(a, b)
    s = re.sub(r"\\(?:left|right|big|Big)\b", "", s)
    s = re.sub(r"\\[,;:! ]|\\quad\b", " ", s)
    s = re.sub(r"\\(?:cdot|times)\b", "*", s)
    s = re.sub(r"\\div\b", "/", s)
    s = _expand_latex(s)
    s = re.sub(r"\\([A-Za-z]+)", r" \1 ", s)  # \pi -> pi, \sin -> sin
    return s.replace("{", "(").replace("}", ")")


def compile_expr(src: str, allow_x: bool = True):
    """Compile a math expression in x into a float -> float|None function.

    Accepts implicit multiplication (2x, 3(x+1), (x+1)(x-2)), ^ powers,
    |x| absolute value bars, sqrt/cbrt/abs/sin/cos/tan/ln/log/exp/floor/ceil,
    pi and e, and common LaTeX (\\frac, \\sqrt, \\cdot, \\pi). Returns None
    wherever the expression is undefined (so the plot leaves a gap).
    Only whitelisted tokens reach eval(), so this is not an injection path.
    """
    s = _delatex(src)
    toks = []  # (kind, text)
    i = 0
    while i < len(s):
        c = s[i]
        if c.isspace():
            i += 1
            continue
        m = re.match(r"\d+(?:\.\d*)?|\.\d+", s[i:])
        if m:
            toks.append(("num", m.group()))
            i += m.end()
            continue
        if c.isalpha():
            word = re.match(r"[A-Za-z]+", s[i:]).group()
            j = 0
            while j < len(word):
                for name in _EXPR_NAMES:
                    if word.startswith(name, j):
                        if name == "x":
                            kind = "x"
                        elif name in _EXPR_CONSTS:
                            kind = "const"
                        else:
                            kind = "func"
                        toks.append((kind, name))
                        j += len(name)
                        break
                else:
                    raise WorksheetError(
                        f"unknown name '{word}' in '{src}' (the variable must be x)"
                    )
            i += len(word)
            continue
        if s.startswith("**", i):
            toks.append(("op", "^"))
            i += 2
            continue
        if c in "+-*/^":
            toks.append(("op", c))
        elif c == "(":
            toks.append(("lp", c))
        elif c == ")":
            toks.append(("rp", c))
        elif c == "|":
            toks.append(("bar", c))
        else:
            raise WorksheetError(f"unexpected character '{c}' in '{src}'")
        i += 1

    if not allow_x and any(k == "x" for k, _ in toks):
        raise WorksheetError(f"'{src}' must be a number, not an expression in x")

    # "sin x" / "sqrt 2" -> sin(x) / sqrt(2)
    fixed = []
    k = 0
    while k < len(toks):
        fixed.append(toks[k])
        if toks[k][0] == "func":
            nxt = toks[k + 1] if k + 1 < len(toks) else None
            if nxt and nxt[0] in ("num", "x", "const"):
                fixed += [("lp", "("), nxt, ("rp", ")")]
                k += 2
                continue
            if not nxt or nxt[0] != "lp":
                raise WorksheetError(
                    f"'{toks[k][1]}' needs its argument in parentheses, e.g. {toks[k][1]}(x)"
                )
        k += 1

    value_end = {"num", "x", "const", "rp", "barclose"}
    value_start = {"num", "x", "const", "func", "lp", "baropen"}
    out = []
    prev = None
    open_bars = 0
    for kind, text in fixed:
        if kind == "bar":
            # a bar closes only if one is open and it follows a complete value
            if open_bars and prev in value_end:
                kind, open_bars = "barclose", open_bars - 1
            else:
                kind, open_bars = "baropen", open_bars + 1
        if prev in value_end and kind in value_start:
            out.append("*")  # implicit multiplication
        if kind == "op":
            out.append("**" if text == "^" else text)
        elif kind == "baropen":
            out.append("abs(")
        elif kind == "barclose":
            out.append(")")
        else:
            out.append(text)
        prev = kind
    py = "".join(out)
    try:
        code = compile(py, "<expr>", "eval")
    except SyntaxError:
        raise WorksheetError(f"can't read the expression '{src}'") from None
    if not set(code.co_names) <= set(_EXPR_NS) | {"x"}:
        raise WorksheetError(f"can't read the expression '{src}'")

    def f(xv: float):
        try:
            v = eval(code, _EXPR_NS, {"x": xv})  # noqa: S307 (whitelisted tokens)
        except (ArithmeticError, ValueError, TypeError):
            return None
        if isinstance(v, complex):
            return None
        v = float(v)
        return v if math.isfinite(v) else None

    return f


def const_value(src: str) -> float:
    v = compile_expr(src, allow_x=False)(0.0)
    if v is None:
        raise WorksheetError(f"'{src}' is not a real number")
    return v


# ---------------------------------------------------------------------
# Small text helpers
# ---------------------------------------------------------------------

def _split_top(s: str, sep: str = ",") -> list:
    """Split on sep, ignoring separators inside (), {}, [], $...$ or quotes."""
    parts, buf, depth, in_math, quote = [], [], 0, False, None
    for c in s:
        if quote:
            quote = None if c == quote else quote
        elif c in "\"'" and not in_math and depth == 0 and not buf:
            quote = c
        elif c == "$":
            in_math = not in_math
        elif c in "([{" and not in_math:
            depth += 1
        elif c in ")]}" and not in_math:
            depth -= 1
        elif c == sep and depth == 0 and not in_math:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    parts.append("".join(buf))
    return parts


def _num(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _path(pts) -> str:
    chunks = [f"({_num(x)},{_num(y)})" for x, y in pts]
    return " --\n    ".join(" -- ".join(chunks[i : i + 6]) for i in range(0, len(chunks), 6))


# ---------------------------------------------------------------------
# Curve sampling: gaps at undefined points, breaks at jumps/asymptotes
# ---------------------------------------------------------------------

def _refine_edge(f, good_x: float, bad_x: float):
    """Bisect toward the edge of f's domain; return (x, f(x)) on the good side."""
    gy = f(good_x)
    for _ in range(45):
        m = (good_x + bad_x) / 2
        fm = f(m)
        if fm is None:
            bad_x = m
        else:
            good_x, gy = m, fm
    return (good_x, gy)


def _jumps(f, x0, y0, x1, y1, ylo, yhi) -> bool:
    """True if f is discontinuous between x0 and x1 (asymptote or step)."""
    yr = yhi - ylo
    if (y0 > yhi and y1 > yhi) or (y0 < ylo and y1 < ylo):
        return False  # entirely off-screen: clipping hides it anyway
    if abs(y1 - y0) < 0.02 * yr:
        return False
    a, fa, b, fb = x0, y0, x1, y1
    for _ in range(45):
        m = (a + b) / 2
        fm = f(m)
        if fm is None:
            return True
        if abs(fm - fa) >= abs(fb - fm):
            b, fb = m, fm
        else:
            a, fa = m, fm
        if abs(fb - fa) < 1e-3 * yr:
            return False  # jump shrinks with the interval: continuous, just steep
    return True


def _sample_runs(f, lo: float, hi: float, ylo: float, yhi: float, n: int = 600):
    """Sample f on [lo, hi] into continuous runs of (x, y)."""
    runs, cur, prev_bad = [], [], None
    for i in range(n + 1):
        x = lo + (hi - lo) * i / n
        y = f(x)
        if y is None:
            if cur:
                cur.append(_refine_edge(f, cur[-1][0], x))
                runs.append(cur)
                cur = []
            prev_bad = x
            continue
        if not cur and prev_bad is not None:
            cur.append(_refine_edge(f, x, prev_bad))
        if cur and _jumps(f, cur[-1][0], cur[-1][1], x, y, ylo, yhi):
            runs.append(cur)
            cur = []
        cur.append((x, y))
        prev_bad = None
    if cur:
        runs.append(cur)
    return [r for r in runs if len(r) >= 2]


def _clip_y(run, lo: float, hi: float):
    """Clip a polyline to lo <= y <= hi; returns the visible pieces."""
    pieces, cur = [], []
    for (x0, y0), (x1, y1) in zip(run, run[1:]):
        dy = y1 - y0
        if dy == 0:
            if not lo <= y0 <= hi:
                t0, t1 = 1.0, 0.0
            else:
                t0, t1 = 0.0, 1.0
        else:
            ta, tb = (lo - y0) / dy, (hi - y0) / dy
            t0, t1 = max(0.0, min(ta, tb)), min(1.0, max(ta, tb))
        if t0 > t1:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = []
            continue
        a = (x0 + t0 * (x1 - x0), y0 + t0 * dy)
        b = (x0 + t1 * (x1 - x0), y0 + t1 * dy)
        if cur and abs(cur[-1][0] - a[0]) < 1e-12 and abs(cur[-1][1] - a[1]) < 1e-12:
            cur.append(b)
        else:
            if len(cur) >= 2:
                pieces.append(cur)
            cur = [a, b]
    if len(cur) >= 2:
        pieces.append(cur)
    return pieces


def _simplify(pts, xr: float, yr: float, eps: float = 6e-4):
    """Ramer-Douglas-Peucker in window-normalised units (keeps .tex small)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        ax, ay = pts[i][0] / xr, pts[i][1] / yr
        bx, by = pts[j][0] / xr, pts[j][1] / yr
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1e-12
        best, idx = -1.0, None
        for k in range(i + 1, j):
            px, py = pts[k][0] / xr, pts[k][1] / yr
            d = abs(dy * (px - ax) - dx * (py - ay)) / norm
            if d > best:
                best, idx = d, k
        if idx is not None and best > eps:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


# ---------------------------------------------------------------------
# Graph spec parsing
# ---------------------------------------------------------------------

_GRAPH_COLORS = {
    "navy": "wsnavy", "blue": "wsblue", "red": "wsred", "green": "wsgreen",
    "orange": "wsorange", "purple": "wspurple", "gold": "wsgold",
    "gray": "wsgray", "grey": "wsgray", "black": "black",
}
_COLOR_CYCLE = ["wsnavy", "wsred", "wsgreen", "wsorange", "wspurple"]
_SIZE_IN = {"small": 1.7, "medium": 3.0, "large": 4.2}
_REL = r"(<=|>=|=<|=>|\u2264|\u2265|\\leq?|\\geq?|<|>|=)"
_SETTING_RE = re.compile(
    r"^(x|y|xstep|ystep|step|grid|numbers|xlabel|ylabel|size|title)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_CURVE_RE = re.compile(r"^(y|[A-Za-z]\s*\(\s*x\s*\))\s*" + _REL + r"\s*(.+)$")
_VLINE_RE = re.compile(r"^x\s*" + _REL + r"\s*(.+)$")
_SHAPE_RE = re.compile(
    r"^(open\s+point|points?|segment|vector|line|polygon|circle|text)\b\s*(.*)$", re.IGNORECASE
)
_FILLER = {"to", "and", "from", "through", "center", "centre", "at", "with", "vertices"}


def _norm_rel(op: str) -> str:
    return {"=<": "<=", "=>": ">=", "\u2264": "<=", "\u2265": ">=", "\\le": "<=",
            "\\leq": "<=", "\\ge": ">=", "\\geq": ">="}.get(op, op)


def _parse_range(val: str, what: str):
    val = val.strip().strip("[]")
    parts = [p for p in re.split(r"\s*(?:\bto\b|\.\.|,)\s*", val) if p.strip()]
    if len(parts) != 2:
        raise WorksheetError(f"{what} range should look like '-5 to 5', got '{val}'")
    lo, hi = const_value(parts[0]), const_value(parts[1])
    if not lo < hi:
        raise WorksheetError(f"{what} range '{val}': the first number must be smaller")
    return lo, hi


def _on_off(val: str, key: str) -> bool:
    v = val.strip().lower()
    if v in ("on", "yes", "true", "show"):
        return True
    if v in ("off", "no", "false", "none", "hide"):
        return False
    raise WorksheetError(f"{key}: expected on/off, got '{val}'")


_FIG_FLAGS = {
    "flip": "flip", "mirror": "flip", "fill": "shade", "shade": "shade", "shaded": "shade",
    "filled": "shade", "hidden": "hidden", "hide": "hidden", "nolabel": "nolabel",
    "nolabels": "nolabel", "unlabeled": "nolabel", "lengths": "lengths", "angles": "angles",
    "reflex": "reflex", "right": "right", "nodots": "nodots", "nocenter": "nocenter",
    "noarc": "noarc",
}


def _split_opts(line: str, figure: bool = False):
    """'y = x^2 [red, dashed, label: $f$]' -> ('y = x^2', {...}).

    figure=True also accepts the [figure] annotation options: ticks: n,
    arcs: n, parallel: n, and flags like flip, fill, hidden, lengths."""
    m = re.match(r"^(.*?)\s*\[([^\[\]]*)\]\s*$", line)
    if not m:
        return line, {}
    body, opts = m.group(1), {}
    for piece in _split_top(m.group(2), ","):
        piece = piece.strip()
        if not piece:
            continue
        km = re.match(r"^(label|colou?r)\s*[:=]\s*(.*)$", piece, re.IGNORECASE)
        low = piece.lower()
        if figure:
            cm = re.match(r"^(ticks?|arcs?|parallel|arrows?)\s*(?:[:=]\s*(\d+))?$", low)
            if cm:
                key = {"tick": "ticks", "arc": "arcs", "arrow": "parallel",
                       "arrows": "parallel"}.get(cm.group(1), cm.group(1))
                n = int(cm.group(2)) if cm.group(2) else 1
                if not 0 <= n <= 4:
                    raise WorksheetError(f"{key}: use 0 to 4")
                opts[key] = n
                continue
            if low.replace(" ", "") in _FIG_FLAGS:
                flag = _FIG_FLAGS[low.replace(" ", "")]
                if flag == "noarc":
                    opts["arcs"] = 0
                else:
                    opts[flag] = True
                continue
        if km:
            key, val = km.group(1).lower(), km.group(2).strip()
            if key == "label":
                opts["label"] = val.strip("\"'")
            elif val.lower() in _GRAPH_COLORS:
                opts["color"] = _GRAPH_COLORS[val.lower()]
            else:
                raise WorksheetError(f"unknown color '{val}' (use: {', '.join(_GRAPH_COLORS)})")
        elif piece[0] in "\"'" and piece[-1] == piece[0]:
            opts["label"] = piece[1:-1]
        elif low in _GRAPH_COLORS:
            opts["color"] = _GRAPH_COLORS[low]
        elif low in ("dashed", "dotted", "solid"):
            opts["style"] = low
        elif low in ("thick", "thin"):
            opts["weight"] = low
        elif low in ("noshade", "no shade", "unshaded", "nofill", "no fill"):
            opts["shade"] = False
        else:
            extra = ("ticks: n, arcs: n, parallel: n, flip, fill, hidden, nolabel, lengths, "
                     "angles, reflex, right, nodots, " if figure else "")
            raise WorksheetError(
                f"unknown option '{piece}' (colors, dashed, dotted, thick, thin, "
                f"noshade, {extra}label: ...)"
            )
    return body, opts


def _match_paren(s: str, i: int) -> int:
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "(":
            depth += 1
        elif s[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise WorksheetError(f"unbalanced parentheses in '{s}'")


def _scan_coords(s: str):
    """'A(1,2) B (3, 4) to (5,6)' -> [('A',1,2), ('B',3,4), (None,5,6)], leftovers."""
    coords, words, name, i = [], [], None, 0
    while i < len(s):
        c = s[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if c == "(":
            j = _match_paren(s, i)
            parts = _split_top(s[i + 1 : j], ",")
            if len(parts) != 2:
                raise WorksheetError(f"expected a point like (2, -3), got '{s[i:j + 1]}'")
            coords.append((name, const_value(parts[0]), const_value(parts[1])))
            name, i = None, j + 1
            continue
        m = re.match(r"\$[^$]*\$|[A-Za-z][A-Za-z0-9_']*", s[i:])
        if m:
            if name:
                words.append(name)
            w = m.group()
            name = None if w.lower() in _FILLER else w
            i += m.end()
            continue
        m = re.match(r"[^\sA-Za-z(,$]+", s[i:])
        if name:
            words.append(name)
            name = None
        if m.group() not in ("--", "-", "->"):
            words.append(m.group())
        i += m.end()
    if name:
        words.append(name)
    return coords, words


def _parse_domain(src: str):
    """'-2 <= x < 3' -> (lo, lo_closed, hi, hi_closed); missing side -> None."""
    toks = [t.strip() for t in re.split(_REL, src)]
    if len(toks) not in (3, 5) or "x" not in toks[::2]:
        raise WorksheetError(f"domain '{src}' should look like '-2 <= x < 3' or 'x >= 0'")
    lo = hi = None
    lo_c = hi_c = True
    pos = toks.index("x")
    for side in (-1, 1):
        k = pos + 2 * side
        if not 0 <= k < len(toks):
            continue
        op = _norm_rel(toks[pos + side])
        val = const_value(toks[k])
        # read as "x OP val" (flip the operator when val is on the left)
        if side == -1:
            op = {"<": ">", "<=": ">=", ">": "<", ">=": "<="}.get(op, op)
        if op in (">", ">="):
            lo, lo_c = val, op == ">="
        elif op in ("<", "<="):
            hi, hi_c = val, op == "<="
        else:
            raise WorksheetError(f"domain '{src}' uses '=' — use <, <=, > or >=")
    return lo, lo_c, hi, hi_c


def parse_graph(lines: list) -> dict:
    g = {"x": (-10.0, 10.0), "y": (-10.0, 10.0), "xstep": None, "ystep": None,
         "grid": True, "numbers": True, "xlabel": "$x$", "ylabel": "$y$",
         "size": _SIZE_IN["medium"], "title": None, "items": []}
    step_both = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            sm = _SETTING_RE.match(line)
            if sm:
                key, val = sm.group(1).lower(), sm.group(2).strip()
                if key in ("x", "y"):
                    g[key] = _parse_range(val, key)
                elif key in ("xstep", "ystep", "step"):
                    v = const_value(val)
                    if v <= 0:
                        raise WorksheetError("step must be positive")
                    if key == "step":
                        step_both = v
                    else:
                        g[key] = v
                elif key in ("grid", "numbers"):
                    g[key] = _on_off(val, key)
                elif key in ("xlabel", "ylabel"):
                    g[key] = None if val.lower() in ("none", "off", "") else val
                elif key == "size":
                    low = val.lower()
                    if low in _SIZE_IN:
                        g["size"] = _SIZE_IN[low]
                    else:
                        m = re.match(r"^([\d.]+)\s*(in|cm)?$", low)
                        if not m:
                            raise WorksheetError("size: use small, medium, large, or e.g. 3.5in")
                        g["size"] = float(m.group(1)) / (2.54 if m.group(2) == "cm" else 1)
                else:
                    g["title"] = val
                continue

            body, opts = _split_opts(line)
            item = {"opts": opts, "src": line}

            cm = _CURVE_RE.match(body)
            vm = _VLINE_RE.match(body)
            shp = _SHAPE_RE.match(body)
            if cm and not shp:
                expr = cm.group(3)
                dom = re.split(r"\s+for\s+", expr, maxsplit=1)
                item.update(kind="curve", rel=_norm_rel(cm.group(2)),
                            f=compile_expr(dom[0]), domain=None)
                if len(dom) == 2:
                    item["domain"] = _parse_domain(dom[1])
            elif vm and not shp:
                item.update(kind="vline", rel=_norm_rel(vm.group(1)), c=const_value(vm.group(2)))
            elif shp:
                kind = re.sub(r"\s+", " ", shp.group(1).lower())
                rest = shp.group(2)
                radius = None
                if kind == "circle":
                    rm = re.search(r"\bradius\s*[:=]?\s*(.+)$", rest, re.IGNORECASE)
                    if not rm:
                        raise WorksheetError("circle needs a radius: circle (0, 0) radius 3")
                    radius = const_value(rm.group(1))
                    rest = rest[: rm.start()]
                coords, words = _scan_coords(rest)
                need = {"point": 1, "open point": 1, "points": 1, "segment": 2, "vector": 2,
                        "line": 2, "polygon": 3, "circle": 1, "text": 1}[kind]
                if len(coords) < need or (kind in ("point", "open point", "text", "circle",
                                                   "segment", "vector", "line") and len(coords) > need):
                    raise WorksheetError(f"'{kind}' takes {need} point(s), found {len(coords)}")
                item.update(kind=kind, coords=coords, radius=radius)
                if kind == "text":
                    txt = re.sub(r"^\s*[A-Za-z]?\s*\([^)]*\)\s*:?\s*", "", rest, count=1)
                    item["text"] = txt.strip().strip("\"'")
                    if not item["text"]:
                        raise WorksheetError("text needs something to write: text (2, 3) Hello")
                elif words:
                    raise WorksheetError(f"didn't understand '{' '.join(words)}'")
            else:
                raise WorksheetError(
                    "expected a setting (x: -5 to 5), an equation (y = 2x + 1), "
                    "or point / points / segment / vector / line / polygon / circle / text"
                )
            g["items"].append(item)
        except WorksheetError as e:
            raise WorksheetError(f"[graph] line '{line}': {e}") from None

    if step_both:
        g["xstep"] = g["xstep"] or step_both
        g["ystep"] = g["ystep"] or step_both
    return g


# ---------------------------------------------------------------------
# Axis ticks
# ---------------------------------------------------------------------

def _pi_frac(v: float, maxden: int = 24):
    fr = Fraction(v / math.pi).limit_denominator(maxden)
    return fr if abs(float(fr) * math.pi - v) < 1e-9 * max(1.0, abs(v)) else None


def _auto_step(lo: float, hi: float) -> float:
    span = hi - lo
    # bounds like -2pi..2pi get pi-fraction ticks instead of integer ticks
    if _pi_frac(lo) is not None and _pi_frac(hi) is not None and (abs(lo) % 1 or abs(hi) % 1):
        for q in (6, 4, 2, 1, 0.5):
            if span / (math.pi / q) <= 16:
                return math.pi / q
    # classic worksheet grids (-10..10, -5..5, 0..8) get one line per unit
    if lo == int(lo) and hi == int(hi) and 4 <= span <= 24:
        return 1.0
    raw = span / 12
    k = math.floor(math.log10(raw))
    mults = (1, 2, 5, 10) if k >= 0 else (1, 2.5, 5, 10)
    for mult in mults:
        if mult * 10**k >= raw * (1 - 1e-9):
            return mult * 10**k
    return 10 ** (k + 1)


def _tick_label(v: float, step: float, pi_mode: bool, commas: bool = False) -> str:
    if pi_mode:
        fr = Fraction(v / math.pi).limit_denominator(48)
        p, q = fr.numerator, fr.denominator
        if p == 0:
            return "$0$"
        sign = "-" if p < 0 else ""
        num = r"\pi" if abs(p) == 1 else rf"{abs(p)}\pi"
        return f"${sign}{num}$" if q == 1 else f"${sign}{num}/{q}$"
    step_txt = f"{step:.10f}".rstrip("0")
    decimals = len(step_txt.split(".")[1]) if "." in step_txt else 0
    s = f"{v:.{decimals}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", ""):
        s = "0"
    neg = s.startswith("-")
    digits = s.lstrip("-")
    intpart, _, frac = digits.partition(".")
    if commas and len(intpart) > 3:
        intpart = "{,}".join(
            [intpart[max(0, i - 3) : i] for i in range(len(intpart), 0, -3)][::-1]
        )
    s = ("-" if neg else "") + intpart + ("." + frac if frac else "")
    return f"${s}$"


def _ticks(lo, hi, step, length_in, horizontal, hide_zero, show_numbers):
    """Tick positions + labels. Labels are thinned (every 2nd, 5th, ...) only
    as much as needed to keep them from colliding along an axis length_in long."""
    k0 = math.ceil(lo / step - 1e-9)
    k1 = math.floor(hi / step + 1e-9)
    ks = list(range(k0, k1 + 1))
    pi_mode = _pi_frac(step, 12) is not None and abs(step - round(step)) > 1e-9
    if horizontal:
        chars = max(len(re.sub(r"[${}\\]|pi", "x", _tick_label(k * step, step, pi_mode)))
                    for k in (k0, k1))
        need = 0.055 * chars + 0.1
    else:
        need = 0.2
    commas = max(abs(lo), abs(hi)) >= 10000  # then 9{,}000 matches 12{,}000
    spacing = length_in * step / (hi - lo)
    every = 1
    for every in (1, 2, 4, 5, 10, 20, 50, 100):
        if spacing * every >= need:
            break
    vals, labels = [], []
    for k in ks:
        v = k * step
        vals.append(_num(v))
        show = show_numbers and k % every == 0 and not (hide_zero and k == 0)
        labels.append("{" + (_tick_label(v, step, pi_mode, commas) if show else "") + "}")
    return ",".join(vals), ",".join(labels)


# ---------------------------------------------------------------------
# Graph -> pgfplots
# ---------------------------------------------------------------------

def _line_style(opts: dict, color: str, dashed: bool = False) -> str:
    width = {"thick": "1.6pt", "thin": "0.7pt"}.get(opts.get("weight"), "1.1pt")
    style = opts.get("style") or ("dashed" if dashed else "solid")
    dash = {"dashed": ", dash pattern=on 4pt off 2.5pt",
            "dotted": ", dash pattern=on 0.9pt off 2.2pt"}.get(style, "")
    return f"draw={color}, line width={width}, line cap=round, line join=round{dash}"


def _label_node(x, y, text, color, anchor, extra=""):
    return (
        rf"\node[anchor={anchor}, font=\small, text={color}, fill=white, "
        rf"fill opacity=0.85, text opacity=1, inner sep=1.5pt, outer sep=2pt{extra}] "
        f"at ({_num(x)},{_num(y)}) {{{inline_format(text)}}};"
    )


def _dot(x, y, color, open_=False, r="2.1pt"):
    if open_:
        return (rf"\filldraw[fill=white, draw={color}, line width=0.9pt] "
                f"({_num(x)},{_num(y)}) circle[radius={r}];")
    return rf"\fill[{color}] ({_num(x)},{_num(y)}) circle[radius={r}];"


def _point_name(name: str) -> str:
    return f"${name}$" if len(name.rstrip("'")) <= 2 and not name.startswith("$") else name


def render_graph(lines: list) -> str:
    """Render the lines of one [graph] block to a tikzpicture (pgfplots)."""
    g = parse_graph(lines)
    xmin, xmax = g["x"]
    ymin, ymax = g["y"]
    xr, yr = xmax - xmin, ymax - ymin
    W = g["size"]
    ratio = yr / xr
    H = W * ratio if 0.5 <= ratio <= 1.5 else W * 0.75

    xstep = g["xstep"] or _auto_step(xmin, xmax)
    ystep = g["ystep"] or _auto_step(ymin, ymax)
    x_mid = ymin < 0 < ymax  # x-axis drawn through the interior
    y_mid = xmin < 0 < xmax
    x_axis_at_0 = ymin <= 0 <= ymax
    y_axis_at_0 = xmin <= 0 <= xmax
    origin = x_axis_at_0 and y_axis_at_0
    hide_x0 = origin and x_mid
    hide_y0 = origin
    xt, xtl = _ticks(xmin, xmax, xstep, W, True, hide_x0, g["numbers"])
    yt, ytl = _ticks(ymin, ymax, ystep, H, False, hide_y0, g["numbers"])

    def axis_style(neg):
        arrows = "<->, shorten <=-6pt, shorten >=-6pt" if neg else "->, shorten >=-6pt"
        return "{line width=0.7pt, draw=black!85, >={Stealth[length=4.5pt, width=3.6pt]}, " + arrows + "}"

    xlabel, ylabel = g["xlabel"], g["ylabel"]
    conventional = (not x_mid and not y_mid) and any(
        lab and len(re.sub(r"\$|\\[A-Za-z]+|[{}]", "", lab)) > 3 for lab in (xlabel, ylabel)
    )
    opts = [
        "set layers", "scale only axis", f"width={W:.3f}in", f"height={H:.3f}in",
        f"xmin={_num(xmin)}", f"xmax={_num(xmax)}", f"ymin={_num(ymin)}", f"ymax={_num(ymax)}",
        f"axis x line={'middle' if x_mid else 'bottom'}",
        f"axis y line={'middle' if y_mid else 'left'}",
        f"x axis line style={axis_style(xmin < 0)}",
        f"y axis line style={axis_style(ymin < 0)}",
        f"xtick={{{xt}}}", f"xticklabels={{{xtl}}}",
        f"ytick={{{yt}}}", f"yticklabels={{{ytl}}}",
        "scaled ticks=false", "enlargelimits=false", "clip=false",
        r"tick style={draw=black!70, line width=0.5pt}", "major tick length=3pt",
        r"ticklabel style={font=\scriptsize, fill=white, fill opacity=0.9, text opacity=1, inner sep=1pt}",
        "grid=" + ("major" if g["grid"] else "none"),
        "major grid style={draw=black!17, line width=0.35pt}",
    ]
    if xlabel:
        opts.append(f"xlabel={{{inline_format(xlabel)}}}")
    if ylabel:
        opts.append(f"ylabel={{{inline_format(ylabel)}}}")
    if conventional:
        opts += [r"xlabel style={font=\small, at={(ticklabel cs:0.5)}, anchor=near ticklabel}",
                 r"ylabel style={font=\small, at={(ticklabel cs:0.5)}, anchor=near ticklabel}"]
    else:
        # labels sit just past the arrow tips; an edge axis (bottom/left) anchors
        # to the plot corner so the label clears that axis's tick labels
        xat = "ticklabel* cs:1" if x_mid else "axis description cs:1,0"
        yat = "ticklabel* cs:1" if y_mid else "axis description cs:0,1"
        # "every axis ... label/.style" replaces pgfplots' default (which rotates
        # an edge y-label 90 degrees) instead of appending to it
        opts += [rf"every axis x label/.style={{font=\small, at={{({xat})}}, anchor=west, xshift=7pt, yshift=-0.5pt}}",
                 rf"every axis y label/.style={{font=\small, at={{({yat})}}, anchor=south, yshift=6pt}}"]
    if g["title"]:
        shift = 16 if (ylabel and not conventional) else 3
        opts += [f"title={{{inline_format(g['title'])}}}",
                 rf"title style={{font=\small\bfseries, text=wsnavy, yshift={shift}pt}}"]

    fills, draws, dots, nodes = [], [], [], []
    cycle = iter(_COLOR_CYCLE * 20)
    band = (ymin - 0.01 * yr, ymax + 0.01 * yr)

    pending_labels = []  # placed after every item is drawn, so all dots are known

    def curve_label(pieces, color, text):
        if pieces and text:
            pending_labels.append((pieces, color, text))

    def place_labels():
        dot_pts = [(float(a), float(b)) for a, b in
                   re.findall(r"\((-?[\d.]+),(-?[\d.]+)\) circle", "\n".join(dots))]
        taken = []  # label spots already used
        for pieces, color, text in pending_labels:
            best = max(pieces, key=lambda p: math.hypot((p[-1][0] - p[0][0]) / xr,
                                                       (p[-1][1] - p[0][1]) / yr))
            seg = [math.hypot((b[0] - a[0]) / xr, (b[1] - a[1]) / yr) for a, b in zip(best, best[1:])]
            total = sum(seg) or 1e-9

            def at(frac):
                target, acc = frac * total, 0.0
                for (a, b), d in zip(zip(best, best[1:]), seg):
                    if acc + d >= target and d > 0:
                        t = (target - acc) / d
                        return (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]),
                                (b[1] - a[1]) / yr * xr / ((b[0] - a[0]) or 1e-12))
                    acc += d
                a, b = best[-2], best[-1]
                return b[0], b[1], (b[1] - a[1]) / yr * xr / ((b[0] - a[0]) or 1e-12)

            def clearance(px, py):
                # normalised distance to the nearest thing a label shouldn't cover
                d = [min(px - xmin, xmax - px) / xr * 1.5, min(py - ymin, ymax - py) / yr * 1.5]
                d += [math.hypot((px - qx) / xr, (py - qy) / yr) for qx, qy in dot_pts + taken]
                if y_axis_at_0:
                    d.append(abs(px) / xr)
                if x_axis_at_0:
                    d.append(abs(py) / yr)
                return min(d)

            px, py, slope = max((at(fr) for fr in (0.5, 0.4, 0.6, 0.3, 0.7, 0.25, 0.75, 0.85)),
                                key=lambda c: clearance(c[0], c[1]))
            taken.append((px, py))
            # put the text on the open side of the curve: up-left of a rising
            # curve, up-right of a falling one (flipped below near the top edge)
            h = "east" if slope > 0 else "west"
            v = "south"
            if py > ymin + 0.85 * yr:
                v, h = "north", ("west" if slope > 0 else "east")
            nodes.append(_label_node(px, py, text, color, f"{v} {h}"))

    def add_curve(f, rel, domain, o, color):
        lo, hi = xmin, xmax
        lo_c = hi_c = None
        if domain:
            dlo, dlc, dhi, dhc = domain
            if dlo is not None:
                lo, lo_c = max(lo, dlo), (dlc if dlo >= xmin else None)
            if dhi is not None:
                hi, hi_c = min(hi, dhi), (dhc if dhi <= xmax else None)
            if lo >= hi:
                return
        runs = _sample_runs(f, lo, hi, ymin, ymax)
        strict = rel in ("<", ">")
        if rel != "=" and o.get("shade", True):
            top = ymax if rel in (">", ">=") else ymin
            for run in runs:
                clamped = [(x, min(max(y, ymin), ymax)) for x, y in run]
                poly = _simplify(clamped, xr, yr) + [(run[-1][0], top), (run[0][0], top)]
                fills.append(rf"\fill[{color}, fill opacity=0.16] {_path(poly)} -- cycle;")
        pieces = []
        for run in runs:
            pieces += [_simplify(p, xr, yr) for p in _clip_y(run, *band)]
        for p in pieces:
            draws.append(rf"\draw[{_line_style(o, color, dashed=strict)}] {_path(p)};")
        for xe, closed in ((lo, lo_c), (hi, hi_c)):
            if closed is None:
                continue
            ye = f(xe)
            if ye is None:
                ye = f(xe + (1e-9 if xe == lo else -1e-9))
            if ye is not None and ymin <= ye <= ymax:
                dots.append(_dot(xe, ye, color, open_=not closed))
        curve_label(pieces, color, o.get("label"))

    prev_piece = None  # color of the previous "for"-restricted curve, if it was one
    for it in g["items"]:
        o, kind = it["opts"], it["kind"]
        is_piece = kind == "curve" and it["domain"] is not None
        if kind in ("point", "open point", "points", "text"):
            color = o.get("color", "black")
        elif is_piece and prev_piece and "color" not in o:
            # consecutive "y = ... for ..." lines are pieces of ONE piecewise
            # function, so they share a color instead of advancing the cycle
            color = prev_piece
        else:
            color = o.get("color") or next(cycle)
        prev_piece = color if is_piece else None

        if kind == "curve":
            add_curve(it["f"], it["rel"], it["domain"], o, color)

        elif kind == "vline" or (kind == "line" and abs(it["coords"][0][1] - it["coords"][1][1]) < 1e-12):
            c = it["c"] if kind == "vline" else it["coords"][0][1]
            rel = it.get("rel", "=")
            if xmin <= c <= xmax:
                draws.append(rf"\draw[{_line_style(o, color, dashed=rel in ('<', '>'))}] "
                             f"({_num(c)},{_num(ymin)}) -- ({_num(c)},{_num(ymax)});")
            if rel != "=" and o.get("shade", True):
                a, b = (max(c, xmin), xmax) if rel in (">", ">=") else (xmin, min(c, xmax))
                if a < b:
                    fills.append(rf"\fill[{color}, fill opacity=0.16] ({_num(a)},{_num(ymin)}) "
                                 f"rectangle ({_num(b)},{_num(ymax)});")
            if o.get("label") and xmin <= c <= xmax:
                nodes.append(_label_node(c, ymin + 0.9 * yr, o["label"], color, "west"))

        elif kind == "line":
            (_, x1, y1), (_, x2, y2) = it["coords"]
            m = (y2 - y1) / (x2 - x1)
            add_curve(lambda xv, m=m, x1=x1, y1=y1: m * (xv - x1) + y1, "=", None, o, color)

        elif kind in ("point", "open point", "points"):
            for name, px, py in it["coords"]:
                if not (xmin <= px <= xmax and ymin <= py <= ymax):
                    continue
                dots.append(_dot(px, py, color, open_=kind == "open point"))
                text = o.get("label") if kind != "points" else None
                text = text or (_point_name(name) if name else None)
                if text:
                    h = "east" if px > xmin + 0.8 * xr else "west"
                    v = "north" if py > ymin + 0.88 * yr else "south"
                    nodes.append(_label_node(px, py, text, "black", f"{v} {h}"))

        elif kind in ("segment", "vector"):
            (_, x1, y1), (_, x2, y2) = it["coords"]
            tip = ", -{Stealth[length=6pt, width=4.5pt]}" if kind == "vector" else ""
            draws.append(rf"\draw[{_line_style(o, color)}{tip}] {_path([(x1, y1), (x2, y2)])};")
            for name, px, py in it["coords"]:
                if kind == "segment":
                    dots.append(_dot(px, py, color, r="1.7pt"))
                if name:
                    ox, oy = (px - x2, py - y2) if (px, py) == (x1, y1) else (px - x1, py - y1)
                    ang = math.degrees(math.atan2(oy / yr * H, ox / xr * W))
                    anchor = ["west", "south west", "south", "south east", "east",
                              "north east", "north", "north west"][int(round(ang / 45.0)) % 8]
                    nodes.append(_label_node(px, py, _point_name(name), "black", anchor))
            if o.get("label"):
                curve_label([[(x1, y1), (x2, y2)]], color, o["label"])

        elif kind == "polygon":
            pts = [(px, py) for _, px, py in it["coords"]]
            if o.get("shade", True):
                fills.append(rf"\filldraw[{_line_style(o, color)}, fill={color}, fill opacity=0.10] "
                             f"{_path(pts)} -- cycle;")
            else:
                draws.append(rf"\draw[{_line_style(o, color)}] {_path(pts)} -- cycle;")
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            for name, px, py in it["coords"]:
                if not name:
                    continue
                dots.append(_dot(px, py, color, r="1.6pt"))
                ang = math.degrees(math.atan2((py - cy) / yr * H, (px - cx) / xr * W))
                sector = int(round(ang / 45.0)) % 8
                anchor = ["west", "south west", "south", "south east",
                          "east", "north east", "north", "north west"][sector]
                nodes.append(_label_node(px, py, _point_name(name), "black", anchor))
            if o.get("label"):
                nodes.append(_label_node(cx, cy, o["label"], color, "center"))

        elif kind == "circle":
            _, hx, hy = it["coords"][0]
            r = it["radius"]
            pts = [(hx + r * math.cos(2 * math.pi * k / 180), hy + r * math.sin(2 * math.pi * k / 180))
                   for k in range(180)]
            draws.append(rf"\draw[{_line_style(o, color)}] {_path(pts)} -- cycle;")
            if o.get("label"):
                a = math.radians(40)
                nodes.append(_label_node(hx + r * math.cos(a), hy + r * math.sin(a),
                                         o["label"], color, "south west"))

        elif kind == "text":
            _, tx, ty = it["coords"][0]
            nodes.append(rf"\node[font=\small, text={color}] at ({_num(tx)},{_num(ty)}) "
                         f"{{{inline_format(it['text'])}}};")

    body = []
    if fills or draws:
        body.append(r"\begin{scope}")
        body.append(f"\\clip ({_num(xmin)},{_num(ymin)}) rectangle ({_num(xmax)},{_num(ymax)});")
        body += fills + draws
        body.append(r"\end{scope}")
    place_labels()
    dots.sort(key=lambda d: 0 if d.startswith("\\filldraw[fill=white") else 1)
    body += dots + nodes
    return (
        "\\begin{tikzpicture}[baseline=(current bounding box.north)]\n"
        "\\begin{axis}[\n  " + ",\n  ".join(opts) + "\n]\n"
        + "\n".join(body)
        + "\n\\end{axis}\n\\end{tikzpicture}"
    )


# ---------------------------------------------------------------------
# Geometry figures: [figure] ... [/figure]
#
# Unlike [graph] (a coordinate plane with axes), a figure is a plain
# geometry diagram: shapes are placed by exact coordinates or *constructed
# from measurements* (SSS / SAS / ASA triangles, rectangles by width and
# height, ...), every point is computed in a true-to-scale coordinate frame,
# and the whole drawing is then scaled uniformly to the requested size — so
# a 3-4-5 triangle really is a 3-4-5 triangle and a 60-degree angle really
# looks like 60 degrees. Annotations (side lengths / names, angle measures,
# congruence tick marks, congruent-angle arcs, right-angle squares,
# parallel arrows) are computed in paper units so they look identical at
# any figure size.
# ---------------------------------------------------------------------

FIGURE_OPEN_RE = re.compile(r"^\s*\[figure\]\s*$", re.IGNORECASE)
FIGURE_CLOSE_RE = re.compile(r"^\s*\[/figure\]\s*$", re.IGNORECASE)

_FIG_SIZE_IN = {"small": 1.6, "medium": 2.5, "large": 3.5}
_PT_IN = 1 / 72.27  # inches per TeX point

_PNAME = r"[A-Z](?:_?\d+)?'*"                     # A, B', P1, P_1, A1'
_PNAME_RUN_RE = re.compile(rf"(?:{_PNAME})+(?![A-Za-z])")
_PNAME_ONE_RE = re.compile(_PNAME)
_REF_FILLER = {"to", "and", "from", "through", "of", "on"}
_NUMTOK = r"[^\s,\]]+"
_FIG_SETTING_RE = re.compile(r"^(size|title|labels|grid|rotate|rotation)\s*:\s*(.*)$", re.IGNORECASE)
_FIG_KINDS = (
    "points", "point", "right triangle", "triangle", "rectangle", "square", "parallelogram",
    "rhombus", "trapezoid", "trapezium", "regular", "polygon", "quadrilateral", "pentagon",
    "hexagon", "circle", "arc", "sector", "segment", "line", "ray", "vector", "side",
    "right angle", "angle", "text",
)
_FIG_KIND_RE = re.compile(
    r"^(" + "|".join(k.replace(" ", r"\s+") for k in _FIG_KINDS) + r")\b\s*(.*)$", re.IGNORECASE
)


def _fig_name_tex(name: str) -> str:
    """'A' -> $A$, "A'" -> $A'$, 'P1' / 'P_1' -> $P_{1}$."""
    m = re.match(r"^([A-Z])_?(\d*)('*)$", name)
    if not m:
        return name
    sub = f"_{{{m.group(2)}}}" if m.group(2) else ""
    return f"${m.group(1)}{sub}{m.group(3)}$"


def _ang(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(dy, dx))


def _fmt_len(v: float) -> str:
    """Pretty exact-ish length: 5, 4.5, $\\sqrt{34}$, $3\\sqrt{2}$, else 2 decimals."""
    if abs(v - round(v)) < 1e-6:
        return str(int(round(v)))
    sq = v * v
    if abs(sq - round(sq)) < 1e-6 and 1 < round(sq) <= 2000:
        n = int(round(sq))
        k = 1
        for f in range(int(math.isqrt(n)), 1, -1):
            if n % (f * f) == 0:
                k = f
                break
        m = n // (k * k)
        return f"${k if k > 1 else ''}\\sqrt{{{m}}}$"
    if abs(v * 10 - round(v * 10)) < 1e-6:
        return f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def _fmt_deg(v: float) -> str:
    s = str(int(round(v))) if abs(v - round(v)) < 0.05 else f"{v:.1f}"
    return f"${s}^{{\\circ}}$"


class _Figure:
    def __init__(self):
        self.pts = {}            # name -> (x, y)
        self.internal = set()    # auto-named anonymous coordinates (never labeled)
        self.hidden = set()
        self.dot = set()         # points that get a dot
        self.declared = set()    # points made with a 'point' line (always dotted)
        self.shown = []          # points that get a name label (in order)
        self.name_text = {}      # custom label text for a point
        self.nolabel = set()
        self.items = []
        self.polys = []          # vertex-name lists of every drawn polygon
        self.size = _FIG_SIZE_IN["medium"]
        self.title = None
        self.labels = True
        self.grid = None
        self.rotate = 0.0
        self._anon = 0

    # -- points ---------------------------------------------------------
    def define(self, name, x, y):
        old = self.pts.get(name)
        if old is not None and math.dist(old, (x, y)) > 1e-7 * max(1.0, abs(x), abs(y)):
            raise WorksheetError(
                f"point {name} already exists at ({_num(old[0])}, {_num(old[1])}) — "
                f"use a new name (shapes that share a point must agree on where it is)")
        self.pts[name] = (float(x), float(y))

    def anon(self, x, y):
        self._anon += 1
        name = f"#{self._anon}"
        self.define(name, x, y)
        self.internal.add(name)
        return name

    def get(self, name):
        if name not in self.pts:
            raise WorksheetError(f"point {name} hasn't been defined yet — give it coordinates, "
                                 f"e.g. {name}(0, 0), or define it on an earlier line")
        return self.pts[name]

    def show(self, name, dot=False):
        if name in self.internal or name in self.hidden:
            return
        if name not in self.shown:
            self.shown.append(name)
        if dot:
            self.dot.add(name)

    # -- reference scanning ---------------------------------------------
    def refs(self, s: str):
        """Read leading point references: 'A(0,0) B (3, 4)', 'ABC', '(1, 2) to (3, 4)'.

        Returns (names, rest). Names given with coordinates are (re)defined;
        bare names may still be undefined (the caller decides). Stops at the
        first token that isn't a point reference ('AB=6', 'radius', '40°', ...).
        """
        names, i = [], 0
        while True:
            while i < len(s) and (s[i].isspace() or s[i] == ","):
                i += 1
            if i >= len(s):
                break
            if s[i] == "(":
                j = _match_paren(s, i)
                if len(_split_top(s[i + 1:j], ",")) != 2:
                    break  # '(2x+10)°' is label text, not a point
                x, y = self._pair(s[i + 1:j], s[i:j + 1])
                names.append(self.anon(x, y))
                i = j + 1
                continue
            w = re.match(r"[a-z]+\b", s[i:])
            if w and w.group() in _REF_FILLER:
                # only skip a filler word if a point reference follows it
                k = i + w.end()
                nxt = s[k:].lstrip()
                if nxt[:1] == "(" or _PNAME_RUN_RE.match(nxt):
                    i = k
                    continue
                break
            m = _PNAME_RUN_RE.match(s, i)
            if not m:
                break
            after = s[m.end():].lstrip()
            if after.startswith("=") and names:
                break  # 'AB=6' is a measurement, not a reference
            run = m.group()
            if after.startswith("("):
                if not re.fullmatch(_PNAME, run):
                    raise WorksheetError(f"'{run}(...)': give coordinates to one point at a time")
                k = s.index("(", m.end())
                j = _match_paren(s, k)
                self.define(run, *self._pair(s[k + 1:j], s[k:j + 1]))
                names.append(run)
                i = j + 1
                continue
            names += _PNAME_ONE_RE.findall(run)
            i = m.end()
        return names, s[i:].strip()

    @staticmethod
    def _pair(inner, whole):
        parts = _split_top(inner, ",")
        if len(parts) != 2:
            raise WorksheetError(f"expected a point like (2, -3), got '{whole}'")
        return const_value(parts[0]), const_value(parts[1])


def _kw(rest: str, *keys):
    """Value after a keyword: 'width 6', 'height: 4', 'radius=3' -> float or None."""
    for key in keys:
        m = re.search(rf"(?<![A-Za-z]){key}(?![A-Za-z])\s*[:=]?\s*({_NUMTOK})", rest, re.IGNORECASE)
        if m:
            return const_value(m.group(1).rstrip("°"))
    return None


def _solve_triangle(names, rest):
    """Solve a triangle from any determining set of sides/angles.

    names = [P, Q, R]; sides are written 'PQ=5', angles 'angle P=40'
    (also '∠P=40', 'm∠P = 40°'). 'sides 5 6 7' means PQ, QR, RP.
    Returns local coordinates with P at the origin and Q on the +x axis.
    """
    P, Q, R = names
    ang = {}
    rest2 = rest
    for m in re.finditer(rf"(?:\bangle\s*|m?∠\s*|\\angle\s*)({_PNAME})\s*[:=]\s*({_NUMTOK})", rest):
        if m.group(1) not in names:
            raise WorksheetError(f"angle {m.group(1)} isn't a vertex of triangle {P}{Q}{R}")
        ang[m.group(1)] = const_value(m.group(2).rstrip("°"))
        rest2 = rest2.replace(m.group(0), " ")
    opp = {frozenset((Q, R)): P, frozenset((P, R)): Q, frozenset((P, Q)): R}
    side = {}  # keyed by the OPPOSITE vertex
    for m in re.finditer(rf"\b({_PNAME})({_PNAME})\s*[:=]\s*({_NUMTOK})", rest2):
        key = frozenset((m.group(1), m.group(2)))
        if key not in opp:
            raise WorksheetError(f"{m.group(1)}{m.group(2)} isn't a side of triangle {P}{Q}{R}")
        side[opp[key]] = const_value(m.group(3))
    sm = re.search(rf"\bsides?\s*[:=]?\s*({_NUMTOK})[\s,]+({_NUMTOK})[\s,]+({_NUMTOK})", rest2)
    if sm:
        side[R], side[P], side[Q] = (const_value(sm.group(k)) for k in (1, 2, 3))
    if any(v <= 0 for v in side.values()) or any(not 0 < v < 180 for v in ang.values()):
        raise WorksheetError("side lengths must be positive and angles between 0 and 180")

    V = [P, Q, R]
    rad = math.radians
    for _ in range(6):
        if len(ang) == 2:
            missing = next(v for v in V if v not in ang)
            ang[missing] = 180 - sum(ang.values())
            if ang[missing] <= 0:
                raise WorksheetError("those angles add up to 180° or more")
        for v in V:  # SAS: angle with both adjacent sides -> opposite side
            o1, o2 = [w for w in V if w != v]
            if v in ang and v not in side and o1 in side and o2 in side:
                side[v] = math.sqrt(side[o1] ** 2 + side[o2] ** 2
                                    - 2 * side[o1] * side[o2] * math.cos(rad(ang[v])))
        if len(side) == 3:
            a, b, c = side[P], side[Q], side[R]
            if a + b <= c or a + c <= b or b + c <= a:
                raise WorksheetError(f"sides {_fmt_len(c)}, {_fmt_len(a)}, {_fmt_len(b)} "
                                     f"can't form a triangle (triangle inequality)")
            for v in V:
                o1, o2 = [w for w in V if w != v]
                cosv = (side[o1] ** 2 + side[o2] ** 2 - side[v] ** 2) / (2 * side[o1] * side[o2])
                ang.setdefault(v, math.degrees(math.acos(max(-1, min(1, cosv)))))
            break
        pair = next((v for v in V if v in ang and v in side), None)
        if pair:  # law of sines
            k = side[pair] / math.sin(rad(ang[pair]))
            for v in V:
                if v in ang and v not in side:
                    side[v] = k * math.sin(rad(ang[v]))
                elif v in side and v not in ang:
                    s = side[v] / k
                    if s > 1 + 1e-9:
                        raise WorksheetError("no triangle has those measurements (SSA with no solution)")
                    ang[v] = math.degrees(math.asin(min(1.0, s)))  # SSA: the acute choice
    if len(side) < 3 or len(ang) < 3:
        raise WorksheetError(
            f"not enough to pin down triangle {P}{Q}{R} — give 3 sides ({P}{Q}=5 {Q}{R}=6 {R}{P}=7), "
            f"2 sides and the angle between them, or 2 angles and a side "
            f"(or coordinates: triangle {P}(0,0) {Q}(4,0) {R}(1,3))")
    c, b, A = side[R], side[Q], rad(ang[P])
    return [(0.0, 0.0), (c, 0.0), (b * math.cos(A), b * math.sin(A))]


def _shape_local(kind, names, rest):
    """Local coordinates for a measured shape: first vertex at the origin,
    second on the +x axis, the rest above (counterclockwise)."""
    n = len(names)
    if kind == "triangle":
        return _solve_triangle(names, rest)
    if kind == "right triangle":
        legs = re.search(rf"\blegs?\s*[:=]?\s*({_NUMTOK})[\s,]+({_NUMTOK})", rest)
        if not legs:
            raise WorksheetError("right triangle needs its legs, e.g. right triangle ABC legs 3 4 "
                                 "(right angle at B: AB = 3 along the base, BC = 4 up)")
        a, b = const_value(legs.group(1)), const_value(legs.group(2))
        return [(0.0, 0.0), (a, 0.0), (a, b)]
    if kind == "rectangle":
        by = re.search(rf"({_NUMTOK})\s*(?:by|x|×)\s*({_NUMTOK})", rest)
        w = _kw(rest, "width", "length", "base")
        h = _kw(rest, "height")
        if by:
            w, h = const_value(by.group(1)), const_value(by.group(2))
        if w is None or h is None:
            raise WorksheetError("rectangle needs a size: rectangle ABCD width 6 height 4  (or 6 by 4)")
        return [(0, 0), (w, 0), (w, h), (0, h)]
    if kind == "square":
        s = _kw(rest, "side", "length")
        if s is None:
            raise WorksheetError("square needs a side: square ABCD side 4")
        return [(0, 0), (s, 0), (s, s), (0, s)]
    if kind in ("parallelogram", "rhombus"):
        b = _kw(rest, "base", "side", "length")
        s = b if kind == "rhombus" else _kw(rest, "slant", "side2", "leg", "other")
        a = _kw(rest, "angle")
        a = 60.0 if a is None else a
        h = _kw(rest, "height")
        if kind == "parallelogram" and s is None and h is not None:
            s = h / math.sin(math.radians(a))
        if kind == "parallelogram" and s is None:
            m = re.search(rf"\bside\s*[:=]?\s*({_NUMTOK})", rest)
            b2 = _kw(rest, "base")
            if m and b2 is not None:
                b, s = b2, const_value(m.group(1))
        if b is None or s is None:
            raise WorksheetError(f"{kind} needs its size, e.g. parallelogram ABCD base 6 side 4 angle 60 "
                                 f"(or rhombus ABCD side 5 angle 60)")
        dx, dy = s * math.cos(math.radians(a)), s * math.sin(math.radians(a))
        return [(0, 0), (b, 0), (b + dx, dy), (dx, dy)]
    if kind in ("trapezoid", "trapezium"):
        b = _kw(rest, "bottom", "base")
        t = _kw(rest, "top")
        h = _kw(rest, "height")
        if None in (b, t, h):
            raise WorksheetError("trapezoid needs bottom, top and height: trapezoid ABCD bottom 8 top 4 height 3")
        o = _kw(rest, "offset", "shift")
        o = (b - t) / 2 if o is None else o
        return [(0, 0), (b, 0), (o + t, h), (o, h)]
    if kind == "regular":
        if n < 3:
            raise WorksheetError("a regular polygon needs at least 3 vertex names: regular ABCDEF side 3")
        s = _kw(rest, "side", "length")
        r = _kw(rest, "radius", "circumradius")
        if s is None and r is not None:
            s = 2 * r * math.sin(math.pi / n)
        if s is None:
            raise WorksheetError("regular polygon needs a side (or radius): regular ABCDEF side 3")
        pts, x, y, h = [], 0.0, 0.0, 0.0
        for _ in range(n):
            pts.append((x, y))
            x, y = x + s * math.cos(h), y + s * math.sin(h)
            h += 2 * math.pi / n
        return pts
    raise WorksheetError(f"can't build a {kind} from measurements")


_MEASURED_COUNT = {"triangle": 3, "right triangle": 3, "rectangle": 4, "square": 4,
                   "parallelogram": 4, "rhombus": 4, "trapezoid": 4, "trapezium": 4}


def parse_figure(lines: list) -> _Figure:
    fig = _Figure()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            _parse_figure_line(fig, line)
        except WorksheetError as e:
            raise WorksheetError(f"[figure] line '{line}': {e}") from None
    return fig


def _parse_figure_line(fig, line):
    sm = _FIG_SETTING_RE.match(line)
    if sm:
        key, val = sm.group(1).lower(), sm.group(2).strip()
        if key == "size":
            low = val.lower()
            if low in _FIG_SIZE_IN:
                fig.size = _FIG_SIZE_IN[low]
            else:
                m = re.match(r"^([\d.]+)\s*(in|cm)?$", low)
                if not m:
                    raise WorksheetError("size: use small, medium, large, or e.g. 3in")
                fig.size = float(m.group(1)) / (2.54 if m.group(2) == "cm" else 1)
        elif key == "title":
            fig.title = val
        elif key == "labels":
            fig.labels = _on_off(val, "labels")
        elif key == "grid":
            if re.match(r"^[\d.]+$", val):
                fig.grid = float(val)
            else:
                fig.grid = 1.0 if _on_off(val, "grid") else None
        else:
            fig.rotate = const_value(val.rstrip("°"))
        return

    body, opts = _split_opts(line, figure=True)
    # 'A = (1, 2)' shorthand for a point
    pm = re.match(rf"^({_PNAME})\s*=\s*(\(.*\))\s*$", body)
    if pm:
        body = f"point {pm.group(1)} {pm.group(2)}"
    km = _FIG_KIND_RE.match(body)
    if not km:
        raise WorksheetError(
            "expected a setting (size: small) or one of: point, triangle, right triangle, rectangle, "
            "square, parallelogram, rhombus, trapezoid, regular, polygon, circle, arc, sector, "
            "segment, line, ray, vector, side, angle, right angle, text")
    kind = re.sub(r"\s+", " ", km.group(1).lower())
    rest = km.group(2).strip()
    item = {"kind": kind, "opts": opts}

    if kind in ("point", "points"):
        _parse_point(fig, kind, rest, opts)
        return

    if kind == "text":
        tm = re.match(rf"^\s*(\([^)]*\)|{_PNAME}(?:\s*\([^)]*\))?)\s*:?\s*(.*)$", rest)
        names, txt = (fig.refs(tm.group(1))[0], tm.group(2)) if tm else ([], "")
        if len(names) != 1 or not txt:
            raise WorksheetError("text needs a position and something to write: text (2, 3) Region $R$")
        item.update(at=names[0], text=txt.strip("\"'"))
        fig.items.append(item)
        return

    if kind in ("triangle", "right triangle", "rectangle", "square", "parallelogram", "rhombus",
                "trapezoid", "trapezium", "regular", "polygon", "quadrilateral", "pentagon", "hexagon"):
        names, rest = fig.refs(rest)
        names = list(dict.fromkeys(names))  # 'triangle XYZ X(0,0) Y(5,0) Z(1,3)' names each once
        if kind in ("polygon", "quadrilateral", "pentagon", "hexagon") or (
                kind == "triangle" and all(nm in fig.pts for nm in names) and not
                re.search(r"=|\bsides?\b", rest)):
            need = {"quadrilateral": 4, "pentagon": 5, "hexagon": 6, "triangle": 3}.get(kind)
            if (need and len(names) != need) or len(names) < 3:
                raise WorksheetError(f"{kind} needs {need or 'at least 3'} vertices, found {len(names)}")
            for nm in names:
                fig.get(nm)
            if rest:
                raise WorksheetError(f"didn't understand '{rest}'")
        else:
            need = _MEASURED_COUNT.get(kind)
            if need and len(names) != need:
                raise WorksheetError(f"{kind} needs {need} vertex names (e.g. {'ABCD'[:need]}), "
                                     f"found {len(names)}")
            _place_measured(fig, kind, names, rest, opts)
        fig.polys.append(names)
        item.update(kind="poly", names=names)
        fig.items.append(item)
        for nm in names:
            fig.show(nm)
        if opts.get("lengths"):
            for a, b in zip(names, names[1:] + names[:1]):
                fig.items.append({"kind": "side", "p": a, "q": b, "text": None, "measure": True,
                                  "opts": {}, "auto": True})
        if opts.get("angles"):
            for nm in names:
                fig.items.append({"kind": "angle", "abc": None, "vertex": nm, "text": None,
                                  "measure": True, "opts": {}, "auto": True})
        return

    if kind in ("circle", "arc", "sector"):
        names, rest = fig.refs(rest)
        if not names:
            raise WorksheetError(f"{kind} needs a center: {kind} O radius 3  (or {kind} (0, 0) radius 3)")
        c = names[0]
        if c not in fig.pts:
            fig.define(c, 0, 0)
        r = _kw(rest, "radius", "r")
        d = _kw(rest, "diameter", "d")
        if r is None and d is not None:
            r = d / 2
        if kind == "circle":
            through = names[1] if len(names) == 2 else None
            if r is None and through:
                r = math.dist(fig.pts[c], fig.get(through))
            if r is None or r <= 0:
                raise WorksheetError("circle needs a radius: circle O radius 3 (or diameter 6, or circle O through A)")
            item.update(center=c, r=r)
        else:
            if len(names) == 3:  # arc O from A to B
                cx, cy = fig.pts[c]
                (ax, ay), (bx, by) = fig.get(names[1]), fig.get(names[2])
                r = r or math.dist((cx, cy), (ax, ay))
                a0, a1 = _ang(ax - cx, ay - cy), _ang(bx - cx, by - cy)
                if a1 <= a0:
                    a1 += 360
            else:
                fm = re.search(rf"\bfrom\s*({_NUMTOK})\s*(?:to|-)\s*({_NUMTOK})", rest)
                if r is None or not fm:
                    raise WorksheetError(f"{kind} needs a radius and angles: {kind} O radius 3 from 0 to 120 "
                                         f"(or {kind} O from A to B)")
                a0, a1 = const_value(fm.group(1).rstrip("°")), const_value(fm.group(2).rstrip("°"))
            item.update(center=c, r=r, a0=a0, a1=a1)
        if not opts.get("nocenter"):
            fig.show(c, dot=True)
        fig.items.append(item)
        return

    if kind in ("segment", "line", "ray", "vector"):
        names, rest = fig.refs(rest)
        if len(names) != 2 or rest:
            raise WorksheetError(f"{kind} takes two points: {kind} A B  or  {kind} (0, 0) to (3, 4)"
                                 + (f" — didn't understand '{rest}'" if rest else ""))
        for nm in names:
            fig.get(nm)
        item.update(p=names[0], q=names[1])
        fig.items.append(item)
        dots = kind in ("segment", "line", "ray") and not opts.get("nodots")
        fig.show(names[0], dot=dots)
        fig.show(names[1], dot=dots and kind != "vector")
        if opts.get("lengths"):
            fig.items.append({"kind": "side", "p": names[0], "q": names[1], "text": None,
                              "measure": True, "opts": {}, "auto": True})
        return

    if kind == "side":
        names, txt = fig.refs(rest)
        if len(names) != 2:
            raise WorksheetError("side takes two points: side AB 5  or  side AB $x$ [ticks: 1]")
        for nm in names:
            fig.get(nm)
        txt = re.sub(r"^[=:]\s*", "", txt).strip()
        measure = txt.lower() in ("measure", "length", "?=", "auto")
        item.update(p=names[0], q=names[1], text=None if measure or not txt else txt.strip("\"'"),
                    measure=measure)
        fig.items.append(item)
        return

    if kind in ("angle", "right angle"):
        names, txt = fig.refs(rest)
        txt = re.sub(r"^[=:]\s*", "", txt).strip()
        right = kind == "right angle" or txt.lower() == "right"
        if txt.lower() == "right":
            txt = ""
        if len(names) == 3:
            for nm in names:
                fig.get(nm)
            item.update(abc=names, vertex=names[1])
        elif len(names) == 1:
            fig.get(names[0])
            item.update(abc=None, vertex=names[0])
        else:
            raise WorksheetError("angle takes three points with the vertex in the middle (angle ABC 40°), "
                                 "or one vertex of a shape (angle B 40°)")
        measure = txt.lower() in ("measure", "auto", "?=")
        item.update(kind="angle", right=right or opts.get("right", False),
                    text=None if measure or not txt else txt.strip("\"'"), measure=measure)
        fig.items.append(item)
        return


def _place_measured(fig, kind, names, rest, opts):
    at = re.search(r"\bat\s*(\([^)]*\)|" + _PNAME + r")", rest)
    rot = _kw(rest, "rotate", "rotated", "turn", "turned")
    if at:
        rest = rest[: at.start()] + rest[at.end():]
    if rot is not None:
        rest = re.sub(rf"\b(?:rotate|rotated|turn|turned)\s*[:=]?\s*{_NUMTOK}", " ", rest)
    n0, n1 = names[0], names[1]
    if not at and n0 in fig.pts and n1 in fig.pts:
        have = math.dist(fig.pts[n0], fig.pts[n1])
        hv = f"{have:.12f}"
        if kind == "triangle" and not re.search(rf"\b(?:{n0}{n1}|{n1}{n0})\s*[:=]|\bsides?\b", rest):
            rest += f" {n0}{n1}={hv}"
        elif kind in ("square", "rhombus", "regular") and _kw(rest, "side", "length", "radius") is None:
            rest += f" side {hv}"
        elif kind == "rectangle" and _kw(rest, "width", "length", "base") is None and not re.search(r"\bby\b", rest):
            rest += f" width {hv}"
        elif kind == "parallelogram" and _kw(rest, "base") is None:
            rest += f" base {hv}"
        elif kind in ("trapezoid", "trapezium") and _kw(rest, "bottom", "base") is None:
            rest += f" bottom {hv}"
    local = _shape_local(kind, names, rest)
    if opts.get("flip"):
        local = [(x, -y) for x, y in local]
    origin, theta = (0.0, 0.0), 0.0
    if at:
        tok = at.group(1)
        origin = fig._pair(tok[1:-1], tok) if tok.startswith("(") else fig.get(tok)
    elif names[0] in fig.pts:
        origin = fig.pts[names[0]]
        if names[1] in fig.pts:
            (x0, y0), (x1, y1) = origin, fig.pts[names[1]]
            theta = math.atan2(y1 - y0, x1 - x0)
            built = math.dist(local[0], local[1])
            have = math.dist((x0, y0), (x1, y1))
            if abs(built - have) > 1e-6 * max(1, have):
                raise WorksheetError(f"{names[0]}{names[1]} is already {_fmt_len(have)} long, "
                                     f"but this {kind} makes it {_fmt_len(built)}")
    if rot is not None:
        theta += math.radians(rot)
    ct, st = math.cos(theta), math.sin(theta)
    for nm, (x, y) in zip(names, local):
        fig.define(nm, origin[0] + x * ct - y * st, origin[1] + x * st + y * ct)


def _parse_point(fig, kind, rest, opts):
    if kind == "points":
        names, left = fig.refs(rest)
        if left or not names:
            raise WorksheetError("points takes named coordinates: points A(0, 0) B(2, 3)")
        for nm in names:
            if opts.get("hidden"):
                fig.hidden.add(nm)
            else:
                fig.show(nm, dot=not opts.get("nodots"))
                fig.declared.add(nm)
        return
    m = re.match(rf"^({_PNAME})\s*(.*)$", rest)
    if not m:
        raise WorksheetError("point needs a capital-letter name: point A (2, 3)")
    name, how = m.group(1), m.group(2).strip()
    low = how.lower()
    if how.startswith("("):
        fig.refs(f"{name}{how}")
    elif low.startswith("midpoint"):
        ns, _ = fig.refs(re.sub(r"^midpoint\s*(of)?", "", how, flags=re.I))
        if len(ns) != 2:
            raise WorksheetError("point M midpoint A B")
        (ax, ay), (bx, by) = fig.get(ns[0]), fig.get(ns[1])
        fig.define(name, (ax + bx) / 2, (ay + by) / 2)
    elif low.startswith("foot"):
        ns, _ = fig.refs(re.sub(r"^foot\s*(of)?", "", how, flags=re.I))
        if len(ns) != 3:
            raise WorksheetError("point D foot C A B  (foot of the perpendicular from C to line AB)")
        (px, py), (ax, ay), (bx, by) = (fig.get(n) for n in ns)
        dx, dy = bx - ax, by - ay
        t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
        fig.define(name, ax + t * dx, ay + t * dy)
    elif low.startswith("from"):
        fm = re.match(rf"^from\s*({_PNAME})\s*(?:distance\s*)?({_NUMTOK})\s*(?:at|angle|direction|heading)?\s*({_NUMTOK})",
                      how, re.I)
        if not fm:
            raise WorksheetError("point P from A 5 at 30  (5 units from A in the 30° direction)")
        ax, ay = fig.get(fm.group(1))
        d, a = const_value(fm.group(2)), math.radians(const_value(fm.group(3).rstrip("°")))
        fig.define(name, ax + d * math.cos(a), ay + d * math.sin(a))
    elif low.startswith("on"):
        om = re.match(rf"^on\s*(.*?)\s*(?:at\s*)?({_NUMTOK})$", how, re.I)
        ns = fig.refs(om.group(1))[0] if om else []
        if len(ns) != 2:
            raise WorksheetError("point P on A B 0.25  (a quarter of the way from A to B)")
        t = const_value(om.group(2))
        (ax, ay), (bx, by) = fig.get(ns[0]), fig.get(ns[1])
        fig.define(name, ax + t * (bx - ax), ay + t * (by - ay))
    elif low.startswith("intersect"):
        ns, _ = fig.refs(re.sub(r"^intersect(ion)?\s*(of)?", "", how, flags=re.I))
        if len(ns) != 4:
            raise WorksheetError("point X intersect A B C D  (where line AB meets line CD)")
        (x1, y1), (x2, y2), (x3, y3), (x4, y4) = (fig.get(n) for n in ns)
        den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(den) < 1e-12:
            raise WorksheetError("those lines are parallel — they don't intersect")
        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / den
        fig.define(name, x1 + t * (x2 - x1), y1 + t * (y2 - y1))
    elif not how:
        fig.get(name)
    else:
        raise WorksheetError("point A (2, 3) | point M midpoint A B | point D foot C A B | "
                             "point P from A 5 at 30 | point P on A B 0.25 | point X intersect A B C D")
    if opts.get("hidden"):
        fig.hidden.add(name)
        if name in fig.shown:
            fig.shown.remove(name)
        return
    fig.show(name, dot=not opts.get("nodots"))
    if not opts.get("nodots"):
        fig.declared.add(name)
    if opts.get("label"):
        fig.name_text[name] = opts["label"]
    if opts.get("nolabel"):
        fig.nolabel.add(name)


_LBL_BG = "fill=white, fill opacity=0.85, text opacity=1"  # keeps lines from striking through labels


def _fig_style(opts, color, base="0.9pt"):
    width = {"thick": "1.4pt", "thin": "0.55pt"}.get(opts.get("weight"), base)
    dash = {"dashed": ", dash pattern=on 4pt off 2.5pt",
            "dotted": ", dash pattern=on 0.9pt off 2.2pt"}.get(opts.get("style"), "")
    return f"draw={color}, line width={width}, line cap=round, line join=round{dash}"


def _est_label_pt(text: str):
    """Rough (half-width, half-height) of a \\small label, in pt."""
    vis = re.sub(r"\\[A-Za-z]+|[{}$^_\\]", "", text)
    w = 5.0 * max(1, len(vis)) + 2
    h = 12.0 if ("\\frac" in text or "\\sqrt" in text) else 9.0
    return w / 2, h / 2


def render_figure(lines: list) -> str:
    fig = parse_figure(lines)
    th = math.radians(fig.rotate)
    if th:
        ct, st = math.cos(th), math.sin(th)
        fig.pts = {k: (x * ct - y * st, x * st + y * ct) for k, (x, y) in fig.pts.items()}
        for it in fig.items:
            if "a0" in it:
                it["a0"] += fig.rotate
                it["a1"] += fig.rotate
    P = fig.pts

    # ---- bounding box (in figure units) -------------------------------
    bb = []
    for it in fig.items:
        k = it["kind"]
        if k == "poly":
            bb += [P[n] for n in it["names"]]
        elif k in ("segment", "vector", "side"):
            bb += [P[it["p"]], P[it["q"]]]
        elif k in ("line", "ray"):
            (ax, ay), (bx, by) = P[it["p"]], P[it["q"]]
            ext = 0.22 if k == "line" else 0.35
            it["ends"] = ((ax - ext * (bx - ax), ay - ext * (by - ay)) if k == "line" else (ax, ay),
                          (bx + ext * (bx - ax), by + ext * (by - ay)))
            bb += list(it["ends"])
        elif k == "circle":
            (cx, cy), r = P[it["center"]], it["r"]
            bb += [(cx - r, cy - r), (cx + r, cy + r)]
        elif k in ("arc", "sector"):
            (cx, cy), r = P[it["center"]], it["r"]
            steps = max(8, int(abs(it["a1"] - it["a0"]) / 3))
            bb += [(cx + r * math.cos(math.radians(it["a0"] + (it["a1"] - it["a0"]) * j / steps)),
                    cy + r * math.sin(math.radians(it["a0"] + (it["a1"] - it["a0"]) * j / steps)))
                   for j in range(steps + 1)]
            if k == "sector":
                bb.append((cx, cy))
        elif k == "text":
            bb.append(P[it["at"]])
    bb += [P[n] for n in fig.shown]
    if not bb:
        raise WorksheetError("[figure] block has nothing to draw")
    xs, ys = [p[0] for p in bb], [p[1] for p in bb]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if fig.grid:
        g = fig.grid
        x0, y0 = math.floor(x0 / g - 1e-9) * g, math.floor(y0 / g - 1e-9) * g
        x1, y1 = math.ceil(x1 / g + 1e-9) * g, math.ceil(y1 / g + 1e-9) * g
    bw, bh = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    W = fig.size
    if bw < 1e-6 and bh < 1e-6:
        bw = bh = 1.0
    s = min(W / bw if bw > 1e-6 else 1e9, W / bh if bh > 1e-6 else 1e9)  # inches per unit

    def u(pt):  # TeX points -> figure units
        return pt * _PT_IN / s

    def xy(p):
        return f"({_num(p[0])},{_num(p[1])})"

    grid, fills, strokes, marks, dots, nodes = [], [], [], [], [], []
    if fig.grid:
        grid.append(rf"\draw[black!16, line width=0.35pt, step={_num(fig.grid)}] "
                    f"({_num(x0)},{_num(y0)}) grid ({_num(x1)},{_num(y1)});")

    # edges for label placement: name -> list of directions (degrees) to neighbours
    incident = {}

    straight = []  # every drawn straight piece, for points lying in the middle of one
    sweeps = {}    # vertex -> [(start, delta, outer radius)] of its angle marks

    def add_edge(a, b):
        (ax, ay), (bx, by) = P[a], P[b]
        straight.append(((ax, ay), (bx, by)))
        if (ax, ay) != (bx, by):
            incident.setdefault(a, []).append(_ang(bx - ax, by - ay))
            incident.setdefault(b, []).append(_ang(ax - bx, ay - by))

    edges = set()
    circles = []
    all_poly_pts = [P[n] for poly in fig.polys for n in poly]
    fig_cx = sum(x for x, _ in (all_poly_pts or bb)) / len(all_poly_pts or bb)
    fig_cy = sum(y for _, y in (all_poly_pts or bb)) / len(all_poly_pts or bb)

    for it in fig.items:
        k, o = it["kind"], it["opts"]
        color = o.get("color", "black")
        if k == "poly":
            ns = it["names"]
            path = " -- ".join(xy(P[n]) for n in ns) + " -- cycle"
            if o.get("shade"):
                fills.append(rf"\fill[{color}, fill opacity=0.13] {path};")
            strokes.append(rf"\draw[{_fig_style(o, color)}] {path};")
            for a, b in zip(ns, ns[1:] + ns[:1]):
                add_edge(a, b)
                edges.add(frozenset((a, b)))
            if o.get("label"):
                cx = sum(P[n][0] for n in ns) / len(ns)
                cy = sum(P[n][1] for n in ns) / len(ns)
                nodes.append(rf"\node[font=\small, text={color}] at {xy((cx, cy))} {{{inline_format(o['label'])}}};")
        elif k in ("segment", "line", "ray", "vector"):
            a, b = P[it["p"]], P[it["q"]]
            if k == "segment":
                strokes.append(rf"\draw[{_fig_style(o, color)}] {xy(a)} -- {xy(b)};")
            elif k == "vector":
                strokes.append(rf"\draw[{_fig_style(o, color, '1.1pt')}, -{{Stealth[length=6pt, width=4.5pt]}}] "
                               f"{xy(a)} -- {xy(b)};")
            else:
                tips = "<->" if k == "line" else "->"
                e0, e1 = it["ends"]
                strokes.append(rf"\draw[{_fig_style(o, color)}, >={{Stealth[length=5pt, width=4pt]}}, {tips}] "
                               f"{xy(e0)} -- {xy(e1)};")
            add_edge(it["p"], it["q"])
            edges.add(frozenset((it["p"], it["q"])))
            fwd = _ang(b[0] - a[0], b[1] - a[1])
            if k in ("line", "ray"):
                incident.setdefault(it["q"], []).append(fwd)
            if k == "line":
                incident.setdefault(it["p"], []).append(fwd + 180)
            if o.get("label"):
                it2 = {"kind": "side", "p": it["p"], "q": it["q"], "text": o["label"], "measure": False,
                       "opts": {"color": color}, "auto": True, "vec": k == "vector"}
                fig.items.append(it2)
        elif k == "circle":
            c, r = P[it["center"]], it["r"]
            if o.get("shade"):
                fills.append(rf"\fill[{color}, fill opacity=0.13] {xy(c)} circle[radius={_num(r)}];")
            strokes.append(rf"\draw[{_fig_style(o, color)}] {xy(c)} circle[radius={_num(r)}];")
            circles.append((it["center"], c, r))
            if o.get("label"):
                a = math.radians(45)
                nodes.append(rf"\node[font=\small, text={color}, anchor=south west, inner sep=1.5pt] at "
                             f"{xy((c[0] + r * math.cos(a), c[1] + r * math.sin(a)))} {{{inline_format(o['label'])}}};")
        elif k in ("arc", "sector"):
            (cx, cy), r = P[it["center"]], it["r"]
            a0, a1 = it["a0"], it["a1"]
            start = (cx + r * math.cos(math.radians(a0)), cy + r * math.sin(math.radians(a0)))
            arc = f"arc[start angle={_num(a0)}, end angle={_num(a1)}, radius={_num(r)}]"
            if k == "sector":
                path = f"{xy((cx, cy))} -- {xy(start)} {arc} -- cycle"
                if o.get("shade", True):
                    fills.append(rf"\fill[{color}, fill opacity=0.13] {path};")
                strokes.append(rf"\draw[{_fig_style(o, color)}] {path};")
                end = (cx + r * math.cos(math.radians(a1)), cy + r * math.sin(math.radians(a1)))
                for e in (start, end):
                    incident.setdefault(it["center"], []).append(_ang(e[0] - cx, e[1] - cy))
            else:
                strokes.append(rf"\draw[{_fig_style(o, color)}] {xy(start)} {arc};")
            circles.append((it["center"], (cx, cy), r))
            if o.get("label"):
                am = math.radians((a0 + a1) / 2)
                nodes.append(rf"\node[font=\small, text={color}, anchor={_num((a0 + a1) / 2 + 180)}, "
                             f"inner sep=1.5pt] at {xy((cx + (r + u(3)) * math.cos(am), cy + (r + u(3)) * math.sin(am)))} "
                             f"{{{inline_format(o['label'])}}};")
        elif k == "text":
            nodes.append(rf"\node[font=\small, text={color}] at {xy(P[it['at']])} {{{inline_format(it['text'])}}};")

    # ---- side annotations: ticks, parallel arrows, length / name labels
    explicit_sides = {frozenset((it["p"], it["q"])) for it in fig.items
                      if it["kind"] == "side" and not it.get("auto")}
    label_spots = []
    for it in fig.items:
        if it["kind"] != "side":
            continue
        key = frozenset((it["p"], it["q"]))
        if it.get("auto") and it.get("measure") and key in explicit_sides:
            continue
        o = it["opts"]
        color = o.get("color", "black")
        (ax, ay), (bx, by) = P[it["p"]], P[it["q"]]
        L = math.hypot(bx - ax, by - ay)
        if L < 1e-12:
            raise WorksheetError(f"side {it['p']}{it['q']}: the two points are the same")
        if key not in edges and not it.get("auto"):
            strokes.append(rf"\draw[{_fig_style(o, color)}] {xy((ax, ay))} -- {xy((bx, by))};")
            edges.add(key)
            add_edge(it["p"], it["q"])
        dx, dy = (bx - ax) / L, (by - ay) / L
        mx, my = (ax + bx) / 2, (ay + by) / 2
        nx, ny = -dy, dx
        # outward = away from the polygon that owns this edge (else the figure centre)
        owner = next((poly for poly in fig.polys
                      if any(frozenset((p1, p2)) == key for p1, p2 in zip(poly, poly[1:] + poly[:1]))), None)
        if owner:
            # true outward normal (works for concave shapes too): the right-hand
            # normal of the edge as traversed counterclockwise
            ring = list(zip(owner, owner[1:] + owner[:1]))
            p1, p2 = next(e for e in ring if frozenset(e) == key)
            area = sum(P[p][0] * P[q][1] - P[q][0] * P[p][1] for p, q in ring)
            ex, ey = P[p2][0] - P[p1][0], P[p2][1] - P[p1][1]
            el = math.hypot(ex, ey)
            nx, ny = (ey / el, -ex / el) if area > 0 else (-ey / el, ex / el)
        else:
            if (mx - fig_cx) * nx + (my - fig_cy) * ny < -1e-9 or (
                    abs((mx - fig_cx) * nx + (my - fig_cy) * ny) <= 1e-9 and ny < 0):
                nx, ny = -nx, -ny
        if it.get("vec") and not o.get("flip"):
            nx, ny = -dy, dx  # vector names sit on the left of the arrow
        if o.get("flip"):
            nx, ny = -nx, -ny
        nt = int(o.get("ticks", 0))
        npar = int(o.get("parallel", 0))
        for i in range(nt):
            off = (i - (nt - 1) / 2) * u(2.6)
            tx, ty = mx + dx * off, my + dy * off
            marks.append(rf"\draw[draw={color}, line width=0.7pt] "
                         f"{xy((tx - nx * u(3.8), ty - ny * u(3.8)))} -- {xy((tx + nx * u(3.8), ty + ny * u(3.8)))};")
        if npar:
            shift = u(5 + 2.6 * nt) if nt else 0.0
            for i in range(npar):
                off = shift + (i - (npar - 1) / 2) * u(3.2) + u(1.8)
                tx, ty = mx + dx * off, my + dy * off
                bxp, byp = tx - dx * u(3.6), ty - dy * u(3.6)
                marks.append(rf"\draw[draw={color}, line width=0.7pt, line cap=round, line join=round] "
                             f"{xy((bxp + nx * u(2.6), byp + ny * u(2.6)))} -- {xy((tx, ty))} -- "
                             f"{xy((bxp - nx * u(2.6), byp - ny * u(2.6)))};")
        text = _fmt_len(L) if it.get("measure") else it.get("text")
        if text:
            gap = u(3.5 + (3.8 if nt else 0) + (2.6 if npar and not nt else 0))
            lx, ly = mx + nx * gap, my + ny * gap
            label_spots.append((lx, ly))
            nodes.append(rf"\node[font=\small, text={color}, anchor={_num(_ang(nx, ny) + 180)}, inner sep=1.2pt, {_LBL_BG}] "
                         f"at {xy((lx, ly))} {{{inline_format(text)}}};")

    # ---- angle annotations: arcs, right-angle squares, measures --------
    for it in fig.items:
        if it["kind"] != "angle":
            continue
        o = it["opts"]
        color = o.get("color", "black")
        B = it["vertex"]
        if it["abc"]:
            A, _, C = it["abc"]
            (bx, by), (ax, ay), (cx2, cy2) = P[B], P[A], P[C]
            a1, a2 = _ang(ax - bx, ay - by), _ang(cx2 - bx, cy2 - by)
            start, delta = a1, (a2 - a1) % 360
            if delta > 180:
                start, delta = a2, 360 - delta
            if o.get("reflex"):
                start, delta = (start + delta) % 360, 360 - delta
        else:
            poly = next((pl for pl in fig.polys if B in pl), None)
            if not poly:
                raise WorksheetError(f"angle {B}: {B} isn't a vertex of any shape — "
                                     f"name all three points instead (angle A{B}C)")
            i = poly.index(B)
            A, C = poly[i - 1], poly[(i + 1) % len(poly)]
            (bx, by), (ax, ay), (cx2, cy2) = P[B], P[A], P[C]
            area = sum(P[p][0] * P[q][1] - P[q][0] * P[p][1] for p, q in zip(poly, poly[1:] + poly[:1]))
            a_prev, a_next = _ang(ax - bx, ay - by), _ang(cx2 - bx, cy2 - by)
            if area > 0:  # counterclockwise: interior runs from next round to prev
                start, delta = a_next, (a_prev - a_next) % 360
            else:
                start, delta = a_prev, (a_next - a_prev) % 360
        la = math.hypot(ax - bx, ay - by)
        lc = math.hypot(cx2 - bx, cy2 - by)
        if min(la, lc) < 1e-12:
            raise WorksheetError(f"angle at {B}: one of its sides has zero length")
        short = min(la, lc)
        right = it.get("right")
        narcs = int(o.get("arcs", 0 if right else 1))
        r = min(u(12 if narcs <= 1 else 10), 0.38 * short)
        r_out = r
        if right:
            sq = min(u(7.5), 0.3 * short)
            e1 = (math.cos(math.radians(start)), math.sin(math.radians(start)))
            e2 = (math.cos(math.radians(start + delta)), math.sin(math.radians(start + delta)))
            p1 = (bx + e1[0] * sq, by + e1[1] * sq)
            p2 = (bx + (e1[0] + e2[0]) * sq, by + (e1[1] + e2[1]) * sq)
            p3 = (bx + e2[0] * sq, by + e2[1] * sq)
            marks.append(rf"\draw[draw={color}, line width=0.65pt] {xy(p1)} -- {xy(p2)} -- {xy(p3)};")
            r_out = sq * 1.42
        for j in range(narcs):
            rj = r + j * u(2.4)
            sx = bx + rj * math.cos(math.radians(start))
            sy = by + rj * math.sin(math.radians(start))
            marks.append(rf"\draw[draw={color}, line width=0.65pt] ({_num(sx)},{_num(sy)}) "
                         f"arc[start angle={_num(start)}, end angle={_num(start + delta)}, radius={_num(rj)}];")
            r_out = max(r_out, rj)
        text = _fmt_deg(delta) if it.get("measure") else it.get("text")
        if text:
            hw, hh = _est_label_pt(text)
            half = math.radians(min(delta, 180) / 2)
            need = u(max(hw, hh) + 1) / max(math.tan(half), 0.05)
            d = max(r_out + u(2.5), min(need, u(42)))
            bis = math.radians(start + delta / 2)
            lx, ly = bx + d * math.cos(bis), by + d * math.sin(bis)
            nodes.append(rf"\node[font=\small, text={color}, anchor={_num(math.degrees(bis) + 180)}, "
                         f"inner sep=1pt, {_LBL_BG}] at {xy((lx, ly))} {{{inline_format(text)}}};")
        if text or narcs or right:
            sweeps.setdefault(B, []).append((start, delta, r_out))
            # the vertex name must stay clear of the whole marked angle
            steps = max(1, math.ceil(delta / 30))
            incident.setdefault(B, []).extend(start + delta * j / steps for j in range(steps + 1))

    # ---- point dots and names ------------------------------------------
    for name in fig.shown:
        if name in fig.hidden:
            continue
        px, py = P[name]
        corner = any(name in poly for poly in fig.polys)
        if name in fig.dot and (not corner or name in fig.declared):  # no dots on shape corners
            dots.append(rf"\fill {xy((px, py))} circle[radius={_num(u(1.7))}];")
        if not fig.labels or name in fig.nolabel:
            continue
        dirs = list(incident.get(name, []))
        for cname, (cx, cy), r in circles:  # a point on a circle: label outside it
            if cname != name and abs(math.hypot(px - cx, py - cy) - r) < 1e-6 * max(1, r):
                dirs.append(_ang(cx - px, cy - py))
        for (ax, ay), (bx, by) in straight:  # a point partway along a drawn line
            L2 = (bx - ax) ** 2 + (by - ay) ** 2
            if L2 < 1e-18:
                continue
            t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / L2
            if 1e-6 < t < 1 - 1e-6 and abs((px - ax) * (by - ay) - (py - ay) * (bx - ax)) / math.sqrt(L2) < 1e-6:
                dirs += [_ang(bx - ax, by - ay), _ang(ax - bx, ay - by)]
        ds = sorted({round(d % 360, 6) % 360 for d in dirs})
        if not ds:
            vx, vy = px - fig_cx, py - fig_cy
            best = _ang(vx, vy) if math.hypot(vx, vy) > 1e-9 else 45.0
        elif len(ds) == 1:
            best = ds[0] + 180
        else:  # the middle of the widest empty wedge around the point
            gaps = [((ds[(i + 1) % len(ds)] - ds[i]) % 360, ds[i]) for i in range(len(ds))]
            gap, a = max(gaps)
            best = a + gap / 2
        best %= 360
        rad = math.radians(best)
        off = u(2.2 if name in fig.dot else 1.6)
        for st0, dl, ro in sweeps.get(name, []):  # boxed in by an angle mark: go past it
            if (best - st0) % 360 <= dl:
                off = max(off, ro + u(1.5))
        text = fig.name_text.get(name) or _fig_name_tex(name)
        nodes.append(rf"\node[font=\small, anchor={_num(best + 180)}, inner sep=1.4pt] at "
                     f"{xy((px + off * math.cos(rad), py + off * math.sin(rad)))} {{{inline_format(text)}}};")

    body = grid + fills + strokes + marks + dots + nodes
    if fig.title:
        body.append(rf"\node[anchor=south, font=\small\bfseries, text=wsnavy, inner sep=0pt] "
                    rf"at ([yshift=7pt]current bounding box.north) {{{inline_format(fig.title)}}};")
    return (
        f"\\begin{{tikzpicture}}[x={s:.4f}in, y={s:.4f}in, baseline=(current bounding box.north)]\n"
        + "\n".join(body)
        + "\n\\end{tikzpicture}"
    )


def render_graph_row(graphs: list) -> str:
    """Consecutive [graph] / [figure] blocks sit side by side, wrapping to new
    rows as needed. Each picture is shrunk (never enlarged) to the current
    line width, so a large graph still fits inside a narrow column or cell."""
    pics = [r"\wsfit{" + (render_figure(b) if kind == "figure" else render_graph(b)) + "}"
            for kind, b in graphs]
    return (
        "\\begin{center}\\setlength{\\lineskip}{14pt}\n"
        + "\\hspace{1em}\n".join(pics)
        + "\n\\end{center}"
    )


# ---------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------

_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _split_row(line: str) -> list:
    """Split '| a | $|x|$ | b |' into cells; pipes inside $...$ or as \\| are kept."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, buf, in_math, i = [], [], False, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] in "|$":
            buf.append(s[i : i + 2] if s[i + 1] == "$" else "|")
            i += 2
            continue
        if c == "$":
            in_math = not in_math
        if c == "|" and not in_math:
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(c)
        i += 1
    cells.append("".join(buf).strip())
    return cells


def _visible_len(cell: str) -> int:
    return len(re.sub(r"\\[A-Za-z]+|[{}$*\\]", "", cell))


def render_table(rows: list) -> str:
    """Render consecutive '| a | b |' lines as a boxed, centered table.

    A second row made of dashes (|---|:--:|---:|) makes the first row a
    shaded header and sets each column's alignment (left/center/right);
    without it every row is a body row and columns are centered. Empty body
    cells are widened so students have room to write in them.
    """
    grid = [_split_row(r) for r in rows]
    header, aligns = None, None
    if len(grid) >= 2 and all(_SEP_CELL_RE.match(c.replace(" ", "")) for c in grid[1] if c) \
            and any(grid[1]):
        header = grid[0]
        aligns = []
        for c in grid[1]:
            c = c.replace(" ", "")
            aligns.append("c" if (c.startswith(":") and c.endswith(":")) or not c
                          else "r" if c.endswith(":") else "l" if c.startswith(":") else "c")
        body = grid[2:]
    else:
        body = grid
    ncols = max(len(r) for r in ([header] if header else []) + body)
    aligns = (aligns or []) + ["c"] * ncols
    aligns = aligns[:ncols]
    if header:
        header += [""] * (ncols - len(header))
    body = [r + [""] * (ncols - len(r)) for r in body]

    widths = [max([_visible_len(r[i]) for r in ([header] if header else []) + body] + [3])
              for i in range(ncols)]
    wide = sum(widths) + 3 * ncols > 88
    specs = []
    for i in range(ncols):
        if wide and (widths[i] > 14 or all(w <= 14 for w in widths)):
            ragged = {"l": r"\raggedright", "r": r"\raggedleft", "c": r"\centering"}[aligns[i]]
            if aligns[i] == "c" and widths[i] > 14:
                ragged = r"\raggedright"
            specs.append(rf">{{{ragged}\arraybackslash}}X")
        else:
            specs.append(aligns[i])
    colspec = "|" + "|".join(specs) + "|"

    def cell(txt, is_header):
        if is_header:
            return rf"\textcolor{{wsnavy}}{{\textbf{{{inline_format(txt)}}}}}" if txt else ""
        return inline_format(txt) if txt else r"\rule{0pt}{15pt}\hspace{0.55in}"

    lines = [r"\hline"]
    if header:
        lines.append(r"\rowcolor{wsboxbg}" + " & ".join(cell(c, True) for c in header)
                     + r" \\ \hline")
    for r in body:
        lines.append(" & ".join(cell(c, False) for c in r) + r" \\ \hline")
    env = "tabularx" if wide else "tabular"
    width_arg = r"{\linewidth}" if wide else ""
    table = (f"\\begin{{{env}}}{width_arg}{{{colspec}}}\n" + "\n".join(lines)
             + f"\n\\end{{{env}}}")
    if not wide:  # a plain tabular can be shrunk to fit a narrow column/cell
        table = "\\wsfit{" + table + "}"
    return (
        "\\begin{center}\n\\renewcommand{\\arraystretch}{1.45}\\arrayrulecolor{wsnavy!55}%\n"
        + table
        + "\n\\end{center}"
    )



def _join_text_lines(lines: list, hard: bool = False) -> str:
    """Join source lines into one paragraph's LaTeX.

    - A line that is only $$...$$ becomes a centered display (even mid-paragraph).
    - A line ending in \\ forces a line break before the next line.
    - hard=True (list items with indented continuation lines): every source
      line starts on a new line.
    """
    pieces, pending = [], ""
    for ln in lines:
        d = is_bare_display_math(ln)
        if d is not None:
            pieces.append(f"\n\\[\n{d}\n\\]\n")
            pending = ""
            continue
        s = ln.strip()
        brk = s.endswith("\\\\")
        if brk:
            s = s[:-2].rstrip()
        if pieces:
            pieces.append(pending)
        pieces.append(inline_format(s))
        pending = "\\par\n" if hard else ("\\\\\n" if brk else " ")
    return "".join(pieces)


BOX_COLORS = {
    "navy": "wsnavy", "key": "wsnavy", "note": "wsnavy", "info": "wsnavy",
    "blue": "wsblue", "gold": "wsgold", "tip": "wsgold", "idea": "wsgold",
    "yellow": "wsgold", "red": "wsred", "warning": "wsred", "caution": "wsred",
    "watch": "wsred", "green": "wsgreen", "check": "wsgreen", "rule": "wsgreen",
    "gray": "wsgray", "grey": "wsgray", "purple": "wspurple", "orange": "wsorange",
    "outline": None, "plain": None,
}


def render_box(spec: str, inner: list) -> str:
    """[box], [box: Title], [box gold], [box red: Title] -> a callout box."""
    spec = (spec or "").strip()
    style, title = "navy", ""
    if ":" in spec:
        left, title = spec.split(":", 1)
        left = left.strip().lower()
        if left:
            if left not in BOX_COLORS:
                raise WorksheetError(
                    f"[box {spec}]: unknown style '{left}' (navy, gold, red, green, gray, "
                    "blue, purple, orange, outline)")
            style = left
    elif spec.lower() in BOX_COLORS:
        style = spec.lower()
    else:
        title = spec  # "[box Key idea]" — no colon, not a style word: it's the title
    title = title.strip()
    color = BOX_COLORS[style]
    if color is None:
        opts = ("colback=white, colframe=wsnavy!45, boxrule=0.6pt, arc=2pt")
        title_color = "wsnavy"
    else:
        opts = (f"colback={color}!7!white, colframe={color}, boxrule=0pt, leftrule=2.6pt, "
                "sharp corners")
        title_color = f"{color}!85!black"
    body = render_body(inner, inner_mode=True)
    head = (f"{{\\bfseries\\color{{{title_color}}}{inline_format(title)}}}\\par\\nopagebreak\n"
            if title else "")
    return (
        f"\\begin{{tcolorbox}}[{opts}, left=9pt, right=8pt, top=5pt, bottom=6pt, "
        "before skip=\\wsboxskip, after skip=\\wsboxskip, "
        "before upper={\\setlength{\\parskip}{\\wsparskip}}]\n"
        + head + (body or "\\mbox{}") + "\n\\end{tcolorbox}"
    )


def _writing_lines(n: int) -> str:
    rule = r"\noindent\textcolor{wsmuted!55}{\rule{\linewidth}{0.4pt}}\par"
    return ("{\\setlength{\\parskip}{0pt}\\par"
            + "".join(f"\\vspace{{0.32in}}{rule}" for _ in range(n)) + "}")


def render_body(lines: list, part_space: str = "0in", info: dict | None = None,
                inner_mode: bool = False) -> str:
    """Port of View.renderBody: interleaved paragraphs and (a)(b) part lists,
    plus [graph] blocks and | pipe | tables (LaTeX-only extensions), plus the
    formatting/spacing extensions: (1) steps, - bullets, ## headings, [box],
    > callouts, indented continuation lines, \\ line breaks, [space],
    [lines N], [newpage], --- rules.

    part_space: workspace left under each (a)/(b) sub-part.
    info: if given, info["ended_with_parts"] is set (the body's last block is
          a sub-part list) so the caller can merge part and problem workspace.
    inner_mode: inside a box / grid cell — [newpage] is ignored there.
    """
    blocks = []  # (kind, tex)
    lst = None   # current list: {"kind": parts|steps|stepwords|bullets, "items": [[key, [lines]]]}
    para = []
    graphs = []  # consecutive [graph] blocks are laid out side by side

    def add(kind, tex):
        blocks.append((kind, tex))

    def flush_para():
        nonlocal para
        if para:
            if len(para) == 1:
                display_body = is_bare_display_math(para[0])
                if display_body is not None:
                    add("math", f"\\[\n{display_body}\n\\]")
                    para = []
                    return
            text = _join_text_lines(para)
            if LEAD_RE.match(para[0]) and blocks and blocks[-1][0] != "head":
                text = "\\wslead\n" + text
            add("para", text)
            para = []

    def flush_list(explicit_space_next: bool = False):
        nonlocal lst
        if not lst:
            return
        kind, items = lst["kind"], lst["items"]
        work = (f"\\par\\vspace{{{part_space}}}"
                if kind == "parts" and _len_in(part_space) != 0 else "")
        if kind == "parts":
            # continue lettering if a graph/table/paragraph interrupted the list
            start = ord(items[0][0].lower()) - ord("a") + 1
            start_opt = f", start={start}" if start > 1 else ""
            # a [space] / [lines] line right under a part replaces its part workspace
            body = "\n".join(
                f"  \\item {_join_text_lines(ls, True)}"
                + ("" if explicit_space_next and k == len(items) - 1 else work)
                for k, (_, ls) in enumerate(items))
            add("parts", "\\begin{enumerate}[label=(\\alph*), leftmargin=1.6em, "
                f"itemsep=\\wsitemsep, topsep=\\wsitemsep{start_opt}]\n" + body + "\n\\end{enumerate}")
        elif kind == "steps":
            body = "\n".join(f"  \\item[\\wsstep{{{k}}}] {_join_text_lines(ls, True)}"
                             for k, ls in items)
            add("list", "\\begin{itemize}[leftmargin=2.05em, labelwidth=1.45em, labelsep=0.6em, "
                "align=left, itemsep=\\wsitemsep, parsep=1pt, topsep=\\wsitemsep]\n" + body
                + "\n\\end{itemize}")
        elif kind == "stepwords":
            body = "\n".join(
                f"  \\item[\\textbf{{\\color{{wsnavy}}{escape_latex(k.capitalize())}.}}] "
                f"{_join_text_lines(ls, True)}" for k, ls in items)
            add("list", "\\begin{itemize}[leftmargin=3.7em, labelwidth=3.1em, labelsep=0.6em, "
                "align=left, itemsep=\\wsitemsep, parsep=1pt, topsep=\\wsitemsep]\n" + body
                + "\n\\end{itemize}")
        else:  # bullets
            body = "\n".join(f"  \\item {_join_text_lines(ls, True)}" for _, ls in items)
            add("list", "\\begin{itemize}[label={\\color{wsnavy}\\textbullet}, leftmargin=1.5em, "
                "itemsep=\\wsitemsep, parsep=1pt, topsep=\\wsitemsep]\n" + body + "\n\\end{itemize}")
        lst = None

    def flush_graphs():
        nonlocal graphs
        if graphs:
            add("graph", render_graph_row(graphs))
            graphs = []

    def flush_all():
        flush_graphs()
        flush_list()
        flush_para()

    def list_item(kind, key, text):
        nonlocal lst
        flush_para()
        if lst is None or lst["kind"] != kind:
            flush_list()
            lst = {"kind": kind, "items": []}
        lst["items"].append([key, [text]])

    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if GRAPH_OPEN_RE.match(ln) or FIGURE_OPEN_RE.match(ln):
            kind = "graph" if GRAPH_OPEN_RE.match(ln) else "figure"
            close_re = GRAPH_CLOSE_RE if kind == "graph" else FIGURE_CLOSE_RE
            flush_list()
            flush_para()
            j = i + 1
            while j < n and not close_re.match(lines[j]):
                j += 1
            if j >= n:
                raise WorksheetError(f"a [{kind}] block is missing its closing [/{kind}] line")
            graphs.append((kind, lines[i + 1 : j]))
            i = j + 1
            continue
        if GRAPH_CLOSE_RE.match(ln) or FIGURE_CLOSE_RE.match(ln):
            kind = "graph" if GRAPH_CLOSE_RE.match(ln) else "figure"
            raise WorksheetError(f"found [/{kind}] without a matching [{kind}] above it")
        bm = BOX_OPEN_RE.match(ln)
        if bm:
            flush_all()
            depth, j = 1, i + 1
            while j < n:
                if BOX_OPEN_RE.match(lines[j]):
                    depth += 1
                elif BOX_CLOSE_RE.match(lines[j]):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= n:
                raise WorksheetError("a [box] block is missing its closing [/box] line")
            add("box", render_box(bm.group(1), lines[i + 1 : j]))
            i = j + 1
            continue
        if BOX_CLOSE_RE.match(ln):
            raise WorksheetError("found [/box] without a matching [box] above it")
        if QUOTE_RE.match(ln):
            flush_all()
            q = []
            while i < n and QUOTE_RE.match(lines[i]):
                q.append(QUOTE_RE.match(lines[i]).group(1) or "")
                i += 1
            add("box", render_box("", q))
            continue
        if TABLE_ROW_RE.match(ln):
            flush_all()
            rows = []
            while i < n and TABLE_ROW_RE.match(lines[i]):
                rows.append(lines[i])
                i += 1
            add("table", render_table(rows))
            continue

        # spacing directives
        m = SPACE_RE.match(ln)
        if m:
            flush_graphs()
            flush_list(explicit_space_next=True)
            flush_para()
            add("space", f"\\vspace{{{_parse_space(m.group(1), '[space]')}}}")
            i += 1
            continue
        m = LINES_RE.match(ln)
        if m:
            flush_graphs()
            flush_list(explicit_space_next=True)
            flush_para()
            add("space", _writing_lines(int(m.group(1))))
            i += 1
            continue
        if NEWPAGE_RE.match(ln):
            flush_all()
            if not inner_mode:
                add("space", "\\newpage")
            i += 1
            continue
        if WORKSPACE_RE.match(ln) or SETTINGS_RE.match(ln):
            i += 1  # handled by build_problem_block / only valid in the header
            continue
        if RULE_RE.match(ln):
            flush_all()
            add("rule", "\\par\\vspace{2pt}\\noindent\\textcolor{wsnavy!30}"
                "{\\rule{\\linewidth}{0.5pt}}\\par")
            i += 1
            continue
        hm = HEADING_RE.match(ln)
        if hm:
            flush_all()
            size = "" if len(hm.group(1)) == 2 else "\\small"
            text = f"{{{size}\\bfseries\\color{{wsnavy}}{inline_format(hm.group(2))}}}"
            add("head", f"\\wshead{{{text}}}" if blocks else f"{text}\\par\\nopagebreak")
            i += 1
            continue

        if ln.strip():
            flush_graphs()
        # an indented line directly under a list item continues that item on a new line
        if (lst is not None and CONT_RE.match(ln) and not PART_RE.match(ln)
                and not STEP_RE.match(ln) and not STEPWORD_RE.match(ln)
                and not BULLET_RE.match(ln)):
            lst["items"][-1][1].append(ln.strip())
            i += 1
            continue

        pm = PART_RE.match(ln)
        sm = STEP_RE.match(ln)
        wm = STEPWORD_RE.match(ln)
        bu = BULLET_RE.match(ln)
        if pm:
            list_item("parts", pm.group(1), pm.group(2))
        elif sm:
            list_item("steps", sm.group(1), sm.group(2))
        elif wm:
            list_item("stepwords", re.sub(r"\s+", " ", wm.group(1)), wm.group(2))
        elif bu:
            list_item("bullets", "", bu.group(1))
        elif not ln.strip():
            flush_list()
            flush_para()
        else:
            flush_list()
            para.append(ln.strip())
        i += 1

    flush_all()
    if info is not None:
        info["ended_with_parts"] = bool(blocks) and blocks[-1][0] == "parts"
    return "\n\n".join(tex for _, tex in blocks)


def _problem_header(p: dict, title: str | None) -> str:
    star = r" {\color{wsgold}$\bigstar$}" if p["star"] else ""
    if title:
        return (rf"\noindent{{\large\bfseries\color{{wsnavy}}Problem {p['num']}.\enspace "
                rf"{inline_format(title)}}}{star}\par\nopagebreak\vspace{{3pt}}"
                r"{\color{wsnavy!30}\hrule height 0.6pt}\nopagebreak\vspace{2pt}")
    return rf"\noindent\textbf{{\color{{wsnavy}}Problem {p['num']}.}}{star}\par\nopagebreak"


def build_problem_block(p: dict, spacing_in: str, part_space: str = "0in") -> str:
    lines = []
    ws = spacing_in
    for ln in p["lines"]:
        wm = WORKSPACE_RE.match(ln)
        if wm:  # [workspace large] inside a problem overrides --spacing for it
            ws = _parse_space(wm.group(1), "[workspace]")
            continue
        lines.append(ln)
    # a trailing [newpage] goes after the problem's workspace
    newpage_after = False
    while lines and (not lines[-1].strip() or NEWPAGE_RE.match(lines[-1])):
        newpage_after = newpage_after or bool(NEWPAGE_RE.match(lines[-1]))
        lines.pop()

    title = None
    if lines and TITLE_LINE_RE.match(lines[0]):
        title = TITLE_LINE_RE.match(lines[0]).group(1).strip()
        lines = lines[1:]
    header = _problem_header(p, title)

    info = {}
    body = render_body(lines, part_space, info)
    # the last sub-part's workspace counts toward the problem's own workspace
    ws_in = _len_in(ws)
    if info.get("ended_with_parts") and _len_in(part_space) > 0 and ws_in > 0:
        ws_in = max(0.0, ws_in - _len_in(part_space))
        ws = f"{ws_in:.3f}in"
    workspace = f"\n\\vspace{{{ws}}}" if ws_in != 0 else ""
    tail = "\n\\newpage" if newpage_after else ""
    parts = [header]
    if body:
        parts.append(body)
    if _has_block(lines):
        # samepage can't stop a break between paragraphs, and a tall graph
        # makes that break likely; an unbreakable minipage keeps the whole
        # problem (header, text, graph/table) on one page.
        inner = "\\setlength{\\parskip}{\\wsparskip}\n" + "\n\n".join(parts)
        # TutorAssist: measure the minipage first. A box taller than a page would be cut
        # off at the page edge, so such a problem flows like any other problem instead.
        need = "\\needspace{6\\baselineskip}\n" if title else ""
        return ("\\setbox\\wsprobbox=\\hbox{\\begin{minipage}{\\linewidth}\n" + inner
                + "\n\\end{minipage}}%\n"
                "\\ifdim\\dimexpr\\ht\\wsprobbox+\\dp\\wsprobbox\\relax>"
                "\\dimexpr\\textheight-2\\baselineskip\\relax\n"
                + need + "\\begin{samepage}\n" + "\n\n".join(parts) + "\n\\end{samepage}\n"
                "\\else\n\\noindent\\usebox\\wsprobbox\n\\fi" + workspace + tail)
    block = "\n\n".join(parts) + workspace
    # keep the header from being stranded at the bottom of a page
    need = "\\needspace{6\\baselineskip}\n" if title else ""
    return need + "\\begin{samepage}\n" + block + "\n\\end{samepage}" + tail


def _has_block(lines: list) -> bool:
    return any(GRAPH_OPEN_RE.match(ln) or FIGURE_OPEN_RE.match(ln) or TABLE_ROW_RE.match(ln)
               for ln in lines)


def build_answer_item(a: dict) -> str:
    body = render_body(a["lines"], inner_mode=False)
    label = rf"\textbf{{\color{{wsnavy}}{a['num']}.}}"
    if _has_block(a["lines"]):
        # [t] aligns the minipage's first line with the label; if the answer
        # opens with a graph/table, an empty first line keeps the label on top.
        lead = "\\mbox{}\n\n" if body.startswith("\\begin{center}") else ""
        body = ("\\begin{minipage}[t]{\\linewidth}\\setlength{\\parskip}{\\wsparskip}\n"
                + lead + body + "\n\\end{minipage}")
    return f"\\item[{{{label}}}] {body}"


# ---------------------------------------------------------------------
# Compact layouts: [grid N] (row-by-row boxed table of problems) and
# [columns N] (problems flow down N newspaper-style columns)
# ---------------------------------------------------------------------

def _compact_item(num, star: bool, body: str) -> str:
    """'12.' hanging-number item used inside grids and columns (no 'Problem' header)."""
    star_tex = r"{\color{wsgold}$\bigstar$}\ " if star else ""
    label = rf"\textbf{{\color{{wsnavy}}{num}.}}"
    # short drill problems read better with full-size fractions: \frac acts like \dfrac here
    return (
        "\\begin{itemize}[before=\\everymath{\\displaystyle}, label={" + label + "}, "
        "leftmargin=1.75em, labelsep=0.4em, "
        "topsep=0pt, partopsep=0pt, itemsep=0pt, parsep=6pt]\n"
        "\\item " + star_tex + (body or "\\mbox{}") + "\n\\end{itemize}"
    )


def render_compact_grid(cells: list, n: int, boxed: bool, space_in: str, pad: bool = True) -> str:
    """Cells laid out left-to-right, n per row; each row is its own table so
    the grid can break across pages between rows (never inside one)."""
    rule = "|" if boxed else ""
    col = r">{\raggedright\arraybackslash}X"
    colspec = rule + rule.join([col] * n) + rule
    work = rf"\par\vspace*{{{space_in}}}" if space_in != "0in" else ""
    top = r"\vspace*{3pt}" if pad else ""
    out = ["{\\setlength{\\parskip}{0pt}\\renewcommand{\\arraystretch}{1}%",
           r"\arrayrulecolor{wsnavy!45}\setlength{\tabcolsep}{6pt}\setlength{\extrarowheight}{0pt}%"]
    rows = [cells[i:i + n] for i in range(0, len(cells), n)]
    for ri, row in enumerate(rows):
        filled = [f"{top}{c}{work}\\vspace*{{4pt}}" for c in row]
        k = len(filled)
        if k < n:
            filled.append(f"\\multicolumn{{{n - k}}}{{c}}{{}}")
        bottom = (r"\hline" if k == n else rf"\cline{{1-{k}}}") if boxed else ""
        out.append(
            ("\\par\\nointerlineskip\n" if ri else "")
            + f"\\noindent\\begin{{tabularx}}{{\\linewidth}}{{{colspec}}}\n"
            + (r"\hline" + "\n" if boxed and ri == 0 else "")
            + " &\n".join(filled) + f" \\\\ {bottom}\n"
            + "\\end{tabularx}"
        )
    out.append("\\par}")
    return "\n".join(out)


def render_compact_columns(items: list, n: int, rule: bool, space_in: str) -> str:
    """Problems flow down n balanced columns (multicol), each kept in one piece."""
    sep = (r"\setlength{\columnseprule}{0.4pt}\renewcommand{\columnseprulecolor}{\color{wsnavy!30}}"
           if rule else r"\setlength{\columnseprule}{0pt}")
    work = f"\\par\\vspace{{{space_in}}}" if space_in != "0in" else ""
    blocks = [f"\\noindent\\begin{{minipage}}{{\\linewidth}}\n{it}\n\\end{{minipage}}{work}\\par\\medskip"
              for it in items]
    return ("{" + sep + r"\setlength{\columnsep}{18pt}" + "\n"
            + f"\\begin{{multicols}}{{{n}}}\\raggedcolumns\n"
            + "\n".join(blocks)
            + "\n\\end{multicols}}")


def build_problems(problems: list, spacing_in: str, part_space: str = "0in") -> str:
    """Problems in source order; consecutive problems from one [grid]/[columns]
    block are rendered together in that layout, the rest as normal blocks."""
    segs = []
    for p in problems:
        lay = p.get("layout")
        if lay and segs and segs[-1][0] and segs[-1][0]["id"] == lay["id"]:
            segs[-1][1].append(p)
        else:
            segs.append((lay, [p]))
    out = []
    for lay, ps in segs:
        if not lay:
            out += [build_problem_block(p, spacing_in, part_space) for p in ps]
            continue
        space = SPACING_MAP[lay["space"]] if lay["space"] else spacing_in
        items = [_compact_item(p["num"], p["star"], render_body(p["lines"], inner_mode=True))
                 for p in ps]
        if lay["kind"] == "grid":
            out.append(render_compact_grid(items, lay["n"], lay["boxed"], space))
        else:
            out.append(render_compact_columns(items, lay["n"], lay["rule"], space))
    return "\n\n\\vspace{\\wsproblemsep}\n\n".join(out)


def build_answer_key(answers: list, problems: list) -> str:
    """Answer key box. Answers to problems that sat in a [grid]/[columns]
    block (or answers written inside their own [grid]/[columns] block) are
    packed into a compact grid with the same number of columns."""
    prob_layout = {p["num"]: p.get("layout") for p in problems}
    segs = []
    for a in sorted(answers, key=lambda a: a["num"]):
        lay = a.get("layout") or prob_layout.get(a["num"])
        key = (("a" if a.get("layout") else "p"), lay["id"]) if lay else None
        if key and segs and segs[-1][0] == key:
            segs[-1][2].append(a)
        else:
            segs.append((key, lay, [a]))
    out = []
    for key, lay, ans in segs:
        if not lay:
            items = "\n\n".join(build_answer_item(a) for a in ans)
            out.append("\\begin{itemize}[leftmargin=2.6em, itemsep=10pt, topsep=2pt, label={}]\n"
                       + items + "\n\\end{itemize}")
        else:
            cells = [_compact_item(a["num"], False, render_body(a["lines"], inner_mode=True))
                     for a in ans]
            out.append(render_compact_grid(cells, lay["n"], False, "0in", pad=False))
    return "\n\\medskip\n".join(out)


DOC_TEMPLATE = r"""\documentclass[11pt]{article}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage[letterpaper, top=0.46in, bottom=0.55in, left=0.75in, right=0.75in, headheight=14pt, headsep=14pt]{geometry}
\usepackage{mathpazo}
\usepackage{amsmath,amssymb}
\usepackage[protrusion=true,expansion=false]{microtype}
\usepackage{enumitem}
\usepackage[most]{tcolorbox}
\usepackage{fancyhdr}
\usepackage{xcolor}
\usepackage{needspace}
\newbox\wsprobbox
\usepackage{array,tabularx,colortbl}
\usepackage{multicol}
\usepackage{graphicx}
\usepackage{pgfplots}
\pgfplotsset{compat=1.18}
\usetikzlibrary{arrows.meta}

\definecolor{wsnavy}{HTML}{19468C}
\definecolor{wsnavydark}{HTML}{123468}
\definecolor{wsgold}{HTML}{B8892B}
\definecolor{wsboxbg}{HTML}{F2F5FB}
\definecolor{wsmuted}{HTML}{6B7482}
\definecolor{wsred}{HTML}{C0392B}
\definecolor{wsblue}{HTML}{2E86DE}
\definecolor{wsgreen}{HTML}{1E8449}
\definecolor{wsorange}{HTML}{D35400}
\definecolor{wspurple}{HTML}{7D3C98}
\definecolor{wsgray}{HTML}{7F8C8D}

% shrink a picture/table to the line width only if it's wider (never enlarge)
\newsavebox{\wsfitbox}
\newcommand{\wsfit}[1]{\sbox{\wsfitbox}{#1}\ifdim\wd\wsfitbox>\linewidth\resizebox{\linewidth}{!}{\usebox{\wsfitbox}}\else\usebox{\wsfitbox}\fi}

% spacing (set by --density / [settings density: ...])
\newlength{\wsparskip}
\newlength{\wsleadsep}
\newlength{\wsitemsep}
\newlength{\wsboxskip}
\newlength{\wsproblemsep}
__DENSITY__
% extra space above a labeled paragraph / heading, and keep it off the bottom of a page
\makeatletter
\newcommand{\wslead}{\par\ifinner\addvspace{\wsleadsep}\else\addvspace{\wsleadsep}\needspace{4\baselineskip}\fi}
\makeatother
\newcommand{\wshead}[1]{\wslead#1\par\nopagebreak}
% navy number badge for (1) (2) step lists
\newcommand{\wsstep}[1]{\tikz[baseline=(wsn.base)]\node[circle, fill=wsnavy, text=white, inner sep=0pt, minimum size=1.3em, font=\footnotesize\bfseries] (wsn) {#1};}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.8pt}
\renewcommand{\footrulewidth}{0pt}
\lhead{\small\color{wsmuted} __HEADER_TITLE__}
\rhead{\small\color{wsmuted} Page \thepage}

\setlength{\parindent}{0pt}
\setlength{\parskip}{\wsparskip}

\begin{document}

\begin{center}
{\Large\bfseries\color{wsnavy} __DOC_TITLE__}\\[2pt]
__SUBTITLE_LINE__
\vspace{4pt}
\hrule height 1.2pt width 0.94\linewidth
\vspace{6pt}
\end{center}

\noindent
\begin{minipage}[t]{0.5\linewidth}
Name: \rule{2.4in}{0.5pt}
\end{minipage}%
\begin{minipage}[t]{0.5\linewidth}\raggedleft
Date: \rule{1.6in}{0.5pt}
\end{minipage}

\vspace{14pt}

__PROBLEMS__

__ANSWER_KEY__

\end{document}
"""


def _density_tex(density: str) -> str:
    d = DENSITY_PRESETS[density]
    return (f"\\linespread{{{d['spread']}}}\n"
            f"\\setlength{{\\wsparskip}}{{{d['parskip']}}}\n"
            f"\\setlength{{\\wsleadsep}}{{{d['lead']}}}\n"
            f"\\setlength{{\\wsitemsep}}{{{d['item']}}}\n"
            f"\\setlength{{\\wsboxskip}}{{{d['box']}}}\n"
            f"\\setlength{{\\wsproblemsep}}{{{d['problem']}}}")


def build_latex_document(data: dict, spacing: str = "standard", density: str = "normal",
                         part_spacing: str = "none") -> str:
    """spacing / part_spacing: a preset name (none, compact, standard, large,
    xlarge) or a length ("1.2in"); density: compact | normal | relaxed | spacious."""
    spacing_in = _parse_space(spacing, "--spacing")
    part_in = _parse_space(part_spacing, "--part-spacing", default="none")
    density = _norm_density(density, "--density")

    title_tex = inline_format(data["title"])
    subtitle_line = ""
    if data["subtitle"]:
        subtitle_line = (
            rf"{{\normalsize\color{{wsmuted}} {inline_format(data['subtitle'])}}}\\[2pt]"
        )

    problems_tex = build_problems(data["problems"], spacing_in, part_in)

    answer_key_tex = ""
    if data["answers"]:
        key_body = build_answer_key(data["answers"], data["problems"])
        answer_key_tex = (
            "\\clearpage\n"
            "\\begin{center}\n"
            "{\\Large\\bfseries\\color{wsnavy} Answer Key}\n"
            "\\end{center}\n"
            "\\vspace{6pt}\n"
            "\\begin{tcolorbox}[breakable, colback=wsboxbg, colframe=wsnavy, "
            "boxrule=1.1pt, arc=3pt, left=14pt, right=14pt, top=10pt, bottom=10pt]\n"
            + key_body
            + "\n\\end{tcolorbox}"
        )

    doc = DOC_TEMPLATE
    doc = doc.replace("__DENSITY__", _density_tex(density))
    doc = doc.replace("__HEADER_TITLE__", title_tex)
    doc = doc.replace("__DOC_TITLE__", title_tex)
    doc = doc.replace("__SUBTITLE_LINE__", subtitle_line)
    doc = doc.replace("__PROBLEMS__", problems_tex)
    doc = doc.replace("__ANSWER_KEY__", answer_key_tex)
    return doc


# =====================================================================
# CONTROLLER — CLI + pdflatex compile
# =====================================================================


def compile_pdf(tex_path: Path, workdir: Path) -> Path:
    for _ in range(2):  # run twice for stable layout / any cross-references
        result = subprocess.run(
            [
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                tex_path.name,
            ],
            cwd=workdir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            log_path = tex_path.with_suffix(".log")
            tail = ""
            if log_path.exists():
                tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-60:])
            raise RuntimeError(
                f"pdflatex failed (exit {result.returncode}).\n"
                f"--- last 60 lines of {log_path.name} ---\n{tail}"
            )
    pdf_path = tex_path.with_suffix(".pdf")
    if not pdf_path.exists():
        raise RuntimeError("pdflatex reported success but no PDF was produced.")
    return pdf_path


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", help="Path to a .txt file in Worksheet Studio format")
    ap.add_argument("-o", "--output", help="Output slug (no extension); default: derived from title")
    ap.add_argument(
        "--spacing",
        choices=list(SPACING_MAP),
        default=None,
        help="Blank workspace left under each problem (default: standard)",
    )
    ap.add_argument(
        "--part-spacing",
        choices=list(SPACING_MAP),
        default=None,
        help="Blank workspace under each (a)/(b) sub-part (default: none)",
    )
    ap.add_argument(
        "--density",
        choices=list(DENSITY_PRESETS),
        default=None,
        help="Overall vertical rhythm: line spacing, paragraph/list/box gaps, "
             "gap between problems (default: normal)",
    )
    ap.add_argument(
        "--outdir",
        default=".",
        help="Directory to write the .tex/.pdf into (default: current directory)",
    )
    args = ap.parse_args()

    src_path = Path(args.input)
    text = src_path.read_text(encoding="utf-8")
    try:
        data = parse_worksheet(text)
    except WorksheetError as e:
        print(f"Input error: {e}", file=sys.stderr)
        sys.exit(1)

    if not data["problems"]:
        print(
            'No problems found — start lines with "Problem 1." or "1."',
            file=sys.stderr,
        )
        sys.exit(1)

    slug = args.output or slugify(data["title"])
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    # command-line flags win over a [settings ...] line in the file, which wins over defaults
    st = data.get("settings", {})
    spacing = args.spacing or st.get("spacing") or "standard"
    part_spacing = args.part_spacing or st.get("part_spacing") or "none"
    density = args.density or st.get("density") or "normal"
    try:
        tex_content = build_latex_document(data, spacing, density, part_spacing)
    except WorksheetError as e:
        print(f"Input error: {e}", file=sys.stderr)
        sys.exit(1)
    tex_path = outdir / f"{slug}.tex"
    tex_path.write_text(tex_content, encoding="utf-8")

    pdf_path = compile_pdf(tex_path, outdir)

    n_problems = len(data["problems"])
    n_answers = len(data["answers"])
    print(
        f"{n_problems} problem{'s' if n_problems != 1 else ''}"
        + (f" · answer key ({n_answers})" if n_answers else "")
        + f" -> {pdf_path}"
    )


if __name__ == "__main__":
    main()
