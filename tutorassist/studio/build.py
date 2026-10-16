"""Worksheet Studio, built into the app: a session's `.txt` source → its PDFs.

The agent writes plain text to `.work/sources/YYYY-MM-DD/{Student}_{Type}.txt` and the
app does the rest (a Claude Code hook calls `build_source` the moment the file is saved,
and every run ends with a catch-up pass):

    {Student}_Guided.txt    → material/DATE/{Student}_{DATE}_Guided.pdf          (key at the end)
    {Student}_Eval.txt      → …_Eval.pdf (questions, no key) + …_Eval_Answers.pdf (key only)
    {Student}_Homework.txt  → …_Homework.pdf + …_Homework_Answers.pdf             (same split)

Older sessions may have separate `_Eval_Answers.txt` / `_Homework_Answers.txt` sources;
those still build as-is. Instead of the agent rendering pages and looking at them, the
build report says which problem sits on which page and flags blank pages, problems that
are missing from the PDF, and nearly empty last pages.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import worksheet_gen as wg

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SOURCE_RE = re.compile(r"^(?P<student>.+?)_(?:\d{4}-\d{2}-\d{2}_)?"
                       r"(?P<type>Guided|Eval_Answers|Eval|Homework_Answers|Homework)\.txt$")
OUTPUTS = {
    "Guided": [("Guided", "full")],
    "Eval": [("Eval", "questions"), ("Eval_Answers", "key")],
    "Homework": [("Homework", "questions"), ("Homework_Answers", "key")],
    "Eval_Answers": [("Eval_Answers", "full")],
    "Homework_Answers": [("Homework_Answers", "full")],
}
HEADER_BAND_PT = 22  # the running header (title · Page N) sits above this on every page
_LOCK = threading.Lock()  # key-only builds swap a generator function for a moment
_NAME_DATE_RE = re.compile(r"\\noindent\n\\begin\{minipage\}\[t\]\{0\.5\\linewidth\}\nName:.*?\\vspace\{14pt\}\n", re.S)


@dataclass
class Output:
    type: str
    pdf: str          # workspace-relative
    pages: int
    layout: str = ""  # "p1: Problem 1 · p2: Problem 1 (cont.), Problem 2 · p3: Answer Key"


@dataclass
class BuildResult:
    source: str                     # workspace-relative
    ok: bool = False
    outputs: list[Output] = field(default_factory=list)
    error: str = ""
    warnings: list[str] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def message(self) -> str:
        """The build report, written for the agent (it's also shown in the app)."""
        name = Path(self.source).name
        lines = []
        if self.outputs:
            made = ", ".join(f"{o.pdf} ({o.pages} page{'s' if o.pages != 1 else ''})" for o in self.outputs)
            lines.append(f"Built {name} → {made}.")
            for o in self.outputs:
                if o.layout:
                    lines.append(f"Layout of {o.type}: {o.layout}.")
        if self.error:
            lines.append(f"Couldn't build {name}: {self.error}")
            lines.append("Fix the .txt and save it again (it rebuilds on save).")
        if self.warnings:
            lines.append("Check: " + " ".join(self.warnings))
        if self.previews:
            lines.append("This source has [figure] blocks; page previews to check labels and spacing: "
                         + ", ".join(self.previews))
        return "\n".join(lines)


# --------------------------------------------------------------------------- paths
def source_info(ws: Path, src: Path) -> dict | None:
    """{student, date, type} for a session source under .work/sources/DATE/, else None."""
    try:
        rel = src.resolve().relative_to(ws.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if len(parts) != 4 or parts[:2] != (".work", "sources") or not DATE_RE.match(parts[2]):
        return None
    m = SOURCE_RE.match(parts[3])
    if not m:
        return None
    return {"student": m.group("student").strip(), "date": parts[2], "type": m.group("type")}


def output_paths(ws: Path, src: Path) -> list[Path]:
    info = source_info(ws, src)
    if not info:
        return []
    day = ws / "material" / info["date"]
    return [day / f"{info['student']}_{info['date']}_{t}.pdf" for t, _ in OUTPUTS[info["type"]]]


def needs_build(ws: Path, src: Path) -> bool:
    outs = output_paths(ws, src)
    if not outs:
        return False
    mtime = src.stat().st_mtime
    return any(not p.exists() or p.stat().st_mtime < mtime for p in outs)


# --------------------------------------------------------------------------- build
def build_source(ws: Path, src: Path, *, previews: bool = False) -> BuildResult:
    ws = ws.resolve()
    src = src if src.is_absolute() else ws / src
    rel = _rel(ws, src)
    res = BuildResult(source=rel)
    info = source_info(ws, src)
    if not info:
        res.error = ("this isn't a session source. Name it .work/sources/YYYY-MM-DD/"
                     "{Student}_{Guided|Eval|Homework}.txt.")
        return res
    try:
        text = src.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        res.error = f"can't read it ({e})."
        return res
    try:
        data = wg.parse_worksheet(text)
    except wg.WorksheetError as e:
        res.error = str(e)
        return res
    if not data["problems"]:
        res.error = 'no problems found. Line 1 is the title; start each problem with "Problem 1." etc.'
        return res

    st = data.get("settings") or {}
    spacing = st.get("spacing") or "standard"
    part_spacing = st.get("part_spacing") or "none"
    density = st.get("density") or "normal"
    day = ws / "material" / info["date"]
    day.mkdir(parents=True, exist_ok=True)

    plans = OUTPUTS[info["type"]]
    if any(v == "key" for _, v in plans) and not data["answers"]:
        res.error = (f"it has no Answer Key section, so {info['type']}_Answers.pdf can't be made. "
                     "Add a line reading `Answer Key` after the last problem, then the numbered answers.")
        plans = [p for p in plans if p[1] != "key"]
    problem_nums = {p["num"] for p in data["problems"]}
    stray = sorted(a["num"] for a in data["answers"] if a["num"] not in problem_nums)
    if stray:
        res.warnings.append(f"The Answer Key has entries for problems that don't exist: {', '.join(map(str, stray))}.")
    unanswered = sorted(problem_nums - {a["num"] for a in data["answers"]})
    if data["answers"] and unanswered and info["type"] != "Guided":
        res.warnings.append(f"No answer for problem {', '.join(map(str, unanswered))}.")

    for out_type, variant in plans:
        try:
            tex = _document(data, variant, spacing, density, part_spacing)
            pdf_bytes = _compile(tex)
        except wg.WorksheetError as e:
            res.error = str(e)
            return res
        except RuntimeError as e:
            res.error = _latex_error(str(e), text)
            return res
        dest = day / f"{info['student']}_{info['date']}_{out_type}.pdf"
        tmp = dest.with_name(f".{dest.name}.tmp")
        tmp.write_bytes(pdf_bytes)
        os.replace(tmp, dest)
        pages, layout, warnings = _inspect(pdf_bytes, data, variant)
        res.outputs.append(Output(type=out_type, pdf=_rel(ws, dest), pages=pages, layout=layout))
        res.warnings += [f"{out_type}: {w}" for w in warnings]
        if previews and variant != "key" and re.search(r"^\s*\[figure\]", text, re.M | re.I):
            res.previews = _previews(ws, pdf_bytes, dest.stem)
    res.ok = not res.error
    return res


def build_text(ws: Path, rel_source: str, text: str) -> BuildResult:
    """Save new text for a source (from the app's editor) and build it."""
    src = (ws / rel_source).resolve()
    if not source_info(ws, src):
        return BuildResult(source=rel_source, error="that file isn't a session source.")
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text(text, encoding="utf-8")
    return build_source(ws, src)


# --------------------------------------------------------------------------- internals
def _rel(ws: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ws.resolve()))
    except ValueError:
        return str(p)


def _document(data: dict, variant: str, spacing: str, density: str, part_spacing: str) -> str:
    if variant == "questions":
        return wg.build_latex_document({**data, "answers": []}, spacing, density, part_spacing)
    if variant == "full":
        return wg.build_latex_document(data, spacing, density, part_spacing)
    # key only: the same document without the problems (the answer key still sees them,
    # for compact-layout answers), the key starting on page 1, no Name/Date line.
    with _LOCK:
        original = wg.build_problems
        wg.build_problems = lambda *a, **k: ""
        try:
            doc = wg.build_latex_document(data, spacing, density, part_spacing)
        finally:
            wg.build_problems = original
    doc = doc.replace("\\clearpage\n\\begin{center}\n{\\Large\\bfseries\\color{wsnavy} Answer Key}",
                      "\\begin{center}\n{\\Large\\bfseries\\color{wsnavy} Answer Key}", 1)
    return _NAME_DATE_RE.sub("", doc, count=1)


def _compile(tex: str) -> bytes:
    with tempfile.TemporaryDirectory(prefix="tutorassist-build-") as d:
        path = Path(d) / "worksheet.tex"
        path.write_text(tex, encoding="utf-8")
        return wg.compile_pdf(path, Path(d)).read_bytes()


def _latex_error(raw: str, source: str) -> str:
    """Boil pdflatex's log tail down to the error and the .txt line that caused it."""
    lines = raw.splitlines()
    errors = [ln[2:].strip() for ln in lines if ln.startswith("! ")]
    before = after = ""
    for i, ln in enumerate(lines):
        m = re.match(r"^l\.\d+\s?(.*)$", ln)
        if m:
            before = m.group(1)
            after = lines[i + 1].strip() if i + 1 < len(lines) else ""
            break
    first = errors[0].rstrip(".") if errors else "an error"
    if "Missing $" in raw or "Display math should end with $$" in raw:
        bad = _unmatched_dollar_lines(source)
        if bad:
            return (f"line {', '.join(map(str, bad))} has an unmatched $. Write \\$ for a dollar sign, "
                    "and close every $...$ on the same line.")
    if "Undefined control sequence" in raw:
        cs = re.findall(r"\\[A-Za-z]+", before)
        if cs:
            hits = [n for n, ln in enumerate(source.splitlines(), 1) if cs[-1] in ln]
            where = f"line {', '.join(map(str, hits[:3]))} uses" if hits else "the source uses"
            return f"{where} {cs[-1]}, which isn't a LaTeX command pdflatex knows. Use a standard one."
    near = (before + after).strip()
    msg = f"LaTeX couldn't typeset it ({first})"
    if near and not near.startswith("\\end{"):
        msg += f" near: {near[-80:]}"
        hint = _find_line(source, before)
        if hint:
            msg += f" (source line {hint})"
    return msg + "."


def _unmatched_dollar_lines(source: str) -> list[int]:
    bad = []
    for n, ln in enumerate(source.splitlines(), 1):
        bare = re.sub(r"\\\$", "", ln)  # \$ is a literal dollar sign
        if bare.count("$") % 2:
            bad.append(n)
    return bad[:5]


def _find_line(source: str, fragment: str) -> int | None:
    frag = fragment.strip()
    for n in (30, 20, 12):
        piece = frag[-n:].strip()
        if len(piece) < 6:
            continue
        hits = [i for i, ln in enumerate(source.splitlines(), 1) if piece in ln]
        if len(hits) == 1:
            return hits[0]
    return None


def _inspect(pdf_bytes: bytes, data: dict, variant: str) -> tuple[int, str, list[str]]:
    """Page count, a one-line layout ("p2: Problem 1 (cont.), Problem 2"), and warnings."""
    try:
        import pymupdf
    except ImportError:  # pragma: no cover — a dependency of the app
        return 0, "", []
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    warnings: list[str] = []
    expected = [p["num"] for p in data["problems"] if not p.get("layout")] if variant != "key" else []
    found: dict[int, int] = {}
    pages_desc = []
    key_page = None
    for i, page in enumerate(doc):
        words = [w for w in page.get_text("words") if w[1] > HEADER_BAND_PT]
        words.sort(key=lambda w: (round(w[1]), w[0]))
        heads = []  # (y, label)
        for j, w in enumerate(words[:-1]):
            if w[4] == "Problem" and re.fullmatch(r"\d+\.", words[j + 1][4]):
                n = int(words[j + 1][4][:-1])
                if n not in found:
                    found[n] = i
                    heads.append((w[1], f"Problem {n}"))
            elif w[4] == "Answer" and words[j + 1][4] == "Key" and key_page is None:
                key_page = i
                heads.append((w[1], "Answer Key"))
        first_head_y = heads[0][0] if heads else float("inf")
        before = [w for w in words if w[1] < first_head_y - 2]
        labels = []
        if before and i > 0 and len(before) > 3:
            labels.append(f"{_last_label(pages_desc)} (cont.)" if pages_desc else "(cont.)")
        labels += [lbl for _, lbl in heads]
        if not words:
            warnings.append(f"page {i + 1} is blank.")
        elif not labels and i == 0:
            labels.append("title")
        elif not labels:
            labels.append(f"{_last_label(pages_desc)} (cont.)")
        pages_desc.append((i + 1, labels))
    missing = [n for n in expected if n not in found]
    if missing:
        warnings.append("Not in the PDF: " + ", ".join(f"Problem {n}" for n in missing) + ".")
    last = doc[-1] if doc.page_count else None
    if last is not None and doc.page_count > 1 and variant != "key" and key_page != doc.page_count - 1:
        n_words = len([w for w in last.get_text("words") if w[1] > HEADER_BAND_PT])
        if 0 < n_words < 25:
            warnings.append(f"the last page holds only a few lines; `density: compact` or "
                            f"`spacing: none` in [settings] would save the page.")
    layout = " · ".join(f"p{n}: {', '.join(_compress(lbls))}" for n, lbls in pages_desc)
    return doc.page_count, layout, warnings


def _last_label(pages_desc: list) -> str:
    for _, labels in reversed(pages_desc):
        for lbl in reversed(labels):
            if lbl not in ("title",):
                return lbl.replace(" (cont.)", "")
    return ""


def _compress(labels: list[str]) -> list[str]:
    """Problem 1, Problem 2, Problem 3 → Problems 1–3 (keeps cont./Answer Key as is)."""
    out: list[str] = []
    run: list[int] = []

    def flush() -> None:
        if run:
            out.append(f"Problem {run[0]}" if len(run) == 1 else f"Problems {run[0]}–{run[-1]}")
            run.clear()
    for lbl in labels:
        m = re.fullmatch(r"Problem (\d+)", lbl)
        if m and (not run or int(m.group(1)) == run[-1] + 1):
            run.append(int(m.group(1)))
            continue
        flush()
        if m:
            run.append(int(m.group(1)))
        else:
            out.append(lbl)
    flush()
    return out


def _previews(ws: Path, pdf_bytes: bytes, stem: str) -> list[str]:
    import pymupdf

    out_dir = ws / ".work" / "scratch" / "previews"
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    paths = []
    for i, page in enumerate(doc):
        p = out_dir / f"{stem}-p{i + 1}.png"
        page.get_pixmap(dpi=60).save(p)
        paths.append(_rel(ws, p))
    return paths
