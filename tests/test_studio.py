"""The app's built-in Worksheet Studio: the agent writes .txt, the app makes the PDFs."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf
import pytest

from tutorassist import studio, workspace
from tutorassist.engine import build_command, RunSpec, translate

pytestmark = pytest.mark.skipif(not shutil.which("pdflatex"), reason="needs pdflatex")
TEMPLATE = Path(__file__).parent.parent / "scripts" / "skill_extras" / "worksheet-studio-pdf" / "guided_template.txt"
EVAL = ("Ana — Eval: Factoring\nAlgebra I · October 16, 2026\n\n"
        "Problem 1. Factor $6x^2 + 9x$.\n\nProblem 2. A hat costs \\$8. What do 3 hats cost?\n\n"
        "Answer Key\n1. $3x(2x + 3)$\n2. \\$24\n")


def text(pdf: Path) -> str:
    return " ".join(page.get_text() for page in pymupdf.open(pdf))


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    workspace.create(w)
    return w


def write(ws, name, body, date="2026-10-16"):
    p = ws / ".work" / "sources" / date / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def test_guided_template_builds_with_its_key_and_layout(ws):
    src = write(ws, "Jordan_Guided.txt", TEMPLATE.read_text(), "2026-10-14")
    r = studio.build_source(ws, src)
    assert r.ok, r.message()
    pdf = ws / "material/2026-10-14/Jordan_2026-10-14_Guided.pdf"
    assert pdf.exists() and [o.pdf for o in r.outputs] == ["material/2026-10-14/Jordan_2026-10-14_Guided.pdf"]
    t = text(pdf)
    assert "Key idea" in t and "Now you try" in t and "Answer Key" in t
    assert "Problem 3" in r.outputs[0].layout and "Answer Key" in r.outputs[0].layout
    assert not r.warnings
    # nothing but the PDF lands in material/
    assert sorted(p.name for p in (ws / "material/2026-10-14").iterdir()) == ["Jordan_2026-10-14_Guided.pdf"]


def test_one_eval_source_makes_student_copy_and_answers(ws):
    r = studio.build_source(ws, write(ws, "Ana_Eval.txt", EVAL))
    assert r.ok, r.message()
    student = text(ws / "material/2026-10-16/Ana_2026-10-16_Eval.pdf")
    answers = text(ws / "material/2026-10-16/Ana_2026-10-16_Eval_Answers.pdf")
    assert "Problem 2" in student and "Answer Key" not in student and "$24" not in student
    assert "Answer Key" in answers and "$24" in answers and "What do 3 hats cost" not in answers
    assert "Name:" not in answers
    assert not studio.needs_build(ws, ws / ".work/sources/2026-10-16/Ana_Eval.txt")


def test_build_errors_name_the_line(ws):
    r = studio.build_source(ws, write(ws, "Ana_Homework.txt", "Ana — HW\n\nProblem 1. It costs $5 today.\n\nAnswer Key\n1. 5\n"))
    assert not r.ok and "line 3 has an unmatched $" in r.error
    r = studio.build_source(ws, write(ws, "Ana_Homework.txt", "Ana — HW\n\nProblem 1. Factor $x^2 - 9$.\n"))
    assert not r.ok and "no Answer Key" in r.error
    assert (ws / "material/2026-10-16/Ana_2026-10-16_Homework.pdf").exists()  # the student copy still built
    r = studio.build_source(ws, write(ws, "Ana_Homework.txt", "Ana — HW\n\nProblem 1. $\\frac{1}{2}\\nope{x}$\n\nAnswer Key\n1. 2\n"))
    assert not r.ok and "line 3 uses \\nope" in r.error
    assert not studio.build_source(ws, write(ws, "notes.txt", "x")).ok


def test_hook_builds_sources_and_ignores_everything_else(ws):
    src = write(ws, "Ana_Eval.txt", EVAL)
    def hook(path):
        r = subprocess.run([sys.executable, "-m", "tutorassist.studio.hook"], cwd=ws, capture_output=True, text=True,
                           input=json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write", "cwd": str(ws),
                                             "tool_input": {"file_path": str(path)}}))
        return r.stdout.strip()
    out = json.loads(hook(src))
    assert "Built Ana_Eval.txt" in out["hookSpecificOutput"]["additionalContext"]
    assert hook(ws / "roster.json") == "" and hook(ws / ".work/scratch/x.txt") == ""
    src.write_text("Ana — Eval\n\nProblem 1. $x\n")
    out = json.loads(hook(src))
    assert out["decision"] == "block" and "unmatched $" in out["reason"]
    ev = translate({"type": "system", "subtype": "hook_response", "hook_event": "PostToolUse", "output": json.dumps(out)})
    assert ev == [{"t": "build", "ok": False, "text": out["reason"]}]


def test_runs_register_the_build_hook(tmp_path):
    cmd = build_command("claude", RunSpec(workspace=tmp_path, prompt="x", system_append="y"))
    settings = json.loads(cmd[cmd.index("--settings") + 1])
    hook = settings["hooks"]["PostToolUse"][0]
    assert hook["matcher"] == "Write|Edit|MultiEdit" and "tutorassist.studio.hook" in hook["hooks"][0]["command"]


def test_hook_report_reaches_the_run_log(ws):
    import time
    from tutorassist.runs import _hook_report
    from tutorassist.studio.hook import save_report
    res = studio.build_source(ws, write(ws, "Ana_Eval.txt", EVAL))
    t0 = time.time() - 1
    save_report(ws, res)
    rep = _hook_report(ws, ".work/sources/2026-10-16/Ana_Eval.txt", t0)
    assert rep["ok"] and rep["text"].startswith("Built Ana_Eval.txt")
    assert _hook_report(ws, ".work/sources/2026-10-16/Ana_Eval.txt", time.time() + 5) is None  # stale
