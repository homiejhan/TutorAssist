"""Health checks shown on the Settings page and by `tutor doctor`."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import WorkspaceConfig
from .engine import find_claude

MAC = platform.system() == "Darwin"
# What worksheet_gen.py \usepackage's directly; their own dependencies are caught by the test compile.
LATEX_PACKAGES = ["mathpazo.sty", "amsmath.sty", "microtype.sty", "enumitem.sty", "tcolorbox.sty", "fancyhdr.sty",
                  "xcolor.sty", "needspace.sty", "tabularx.sty", "colortbl.sty", "multicol.sty", "pgfplots.sty"]


def _check(id_: str, label: str, status: str, detail: str = "", fix: str = "") -> dict:
    return {"id": id_, "label": label, "status": status, "detail": detail, "fix": fix}


def check_claude() -> list[dict]:
    claude = find_claude()
    if not claude:
        return [_check("claude", "Claude Code", "fail", "The `claude` command isn't installed.",
                       "tutor setup")]
    out = []
    try:
        ver = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return [_check("claude", "Claude Code", "fail", f"`claude --version` failed: {e}", "tutor setup")]
    out.append(_check("claude", "Claude Code", "ok", ver or claude))
    try:
        res = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=20)
        st = json.loads(res.stdout or "{}")
    except Exception:  # noqa: BLE001
        st = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append(_check("auth", "Claude sign-in", "ok",
                          "Using ANTHROPIC_API_KEY from the environment — usage is billed per token to that key."))
    elif st.get("loggedIn"):
        method = st.get("authMethod", "")
        detail = "Signed in with your Claude account — runs count toward your plan's usage limits." \
            if "oauth" in method else f"Signed in ({method})."
        out.append(_check("auth", "Claude sign-in", "ok", detail))
    elif st:
        out.append(_check("auth", "Claude sign-in", "fail", "Claude Code isn't signed in.",
                          "claude auth login"))
    else:
        out.append(_check("auth", "Claude sign-in", "warn", "Couldn't read the sign-in status.",
                          "Run `claude` in a terminal once to make sure you're signed in."))
    return out


def check_python() -> list[dict]:
    out = []
    for mod, why in (("pymupdf", "textbook indexing"), ("sympy", "answer verification"),
                     ("icalendar", "calendar sync"), ("recurring_ical_events", "repeating calendar events")):
        try:
            __import__(mod)
            out.append(_check(f"py-{mod}", f"Python: {mod}", "ok", why))
        except ImportError:
            out.append(_check(f"py-{mod}", f"Python: {mod}", "fail", f"missing ({why})", "tutor update"))
    return out


def check_latex(full: bool = False) -> list[dict]:
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        return [_check("latex", "LaTeX (pdflatex)", "fail", "Needed to typeset worksheets as PDFs.", "tutor setup")]
    out = [_check("latex", "LaTeX (pdflatex)", "ok", pdflatex)]
    kpse = shutil.which("kpsewhich")
    if kpse:
        missing = []
        for pkg in LATEX_PACKAGES:
            r = subprocess.run([kpse, pkg], capture_output=True, text=True)
            if not r.stdout.strip():
                missing.append(pkg)
        if missing:
            out.append(_check("latex-pkgs", "LaTeX packages", "fail", "missing: " + ", ".join(missing), "tutor setup"))
        else:
            out.append(_check("latex-pkgs", "LaTeX packages", "ok", "all worksheet packages found"))
    if full:
        out.append(compile_test())
    return out


def compile_test() -> dict:
    gen = Path(__file__).parent / "studio" / "worksheet_gen.py"
    sample = ("Doctor Check\nTutorAssist test page\n\nProblem 1. Solve $2x + 3 = 7$.\n\n"
              "[graph]\nx: -3 to 3\ny: -3 to 5\ny = x^2\n[/graph]\n\nAnswer Key\n1. $x = 2$\n")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "t.txt").write_text(sample)
        r = subprocess.run([sys.executable, str(gen), "t.txt", "-o", "t", "--outdir", td],
                           cwd=td, capture_output=True, text=True, timeout=180)
        if r.returncode == 0 and (Path(td) / "t.pdf").exists():
            return _check("latex-compile", "Worksheet test compile", "ok", "a sample worksheet with a graph compiled")
        tail = (r.stderr or r.stdout)[-600:]
        return _check("latex-compile", "Worksheet test compile", "fail", tail, "tutor setup")


def check_optional() -> list[dict]:
    out = []
    if shutil.which("ocrmypdf"):
        out.append(_check("ocr", "OCRmyPDF", "ok", "can add a text layer to scanned textbooks"))
    else:
        out.append(_check("ocr", "OCRmyPDF", "warn", "optional — only needed for scanned books with no text layer",
                          "brew install ocrmypdf" if MAC else "sudo apt install ocrmypdf"))
    return out


def check_workspace(ws: Path) -> list[dict]:
    from . import books

    cfg = WorkspaceConfig.load(ws)
    out = []
    if cfg.calendar.icsUrl:
        try:
            sched = json.loads((ws / "schedule.json").read_text())
        except Exception:  # noqa: BLE001
            sched = {}
        if sched.get("error"):
            out.append(_check("calendar", "Calendar", "fail", sched["error"], "Check the link in Settings → Calendar."))
        else:
            out.append(_check("calendar", "Calendar", "ok",
                              f"{len(sched.get('events', []))} sessions in the next {cfg.calendar.lookaheadDays} days"))
    else:
        out.append(_check("calendar", "Calendar", "warn", "No calendar link yet — add sessions by hand or connect one.",
                          "Settings → Calendar"))
    lib = books.library(ws)
    if not lib:
        out.append(_check("textbooks", "Textbooks", "warn", "No textbooks uploaded yet.", "Library → Upload"))
    else:
        unkeyed = [b["file"] for b in lib if not b["key"]]
        detail = f"{len(lib)} PDFs, {sum(b['indexed'] for b in lib)} indexed"
        if unkeyed:
            detail += f"; {len(unkeyed)} without a book key yet (the agent adds keys when it first cites a book)"
        out.append(_check("textbooks", "Textbooks", "ok", detail))
    return out


def run_all(ws: Path | None, full: bool = False) -> list[dict]:
    checks = check_claude() + check_python() + check_latex(full) + check_optional()
    if ws:
        checks += check_workspace(ws)
    return checks
