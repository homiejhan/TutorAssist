#!/usr/bin/env python3
"""Stand-in for the `claude` CLI in tests: speaks just enough stream-json."""
import json
import os
import sys
import uuid
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["--version"]:
    print("9.9.9 (Claude Code)"); sys.exit(0)
if args[:2] == ["auth", "status"]:
    print(json.dumps({"loggedIn": True, "authMethod": "oauth_token"})); sys.exit(0)

prompt = sys.stdin.read()
sid = args[args.index("--resume") + 1] if "--resume" in args else str(uuid.uuid4())
if "--resume" in args and os.environ.get("FAKE_FORGET_SESSIONS"):
    # What Claude Code does when it has no saved session for this folder.
    msg = f"No conversation found with session ID: {sid}"
    print(msg, file=sys.stderr)
    print(json.dumps({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 0,
                      "session_id": sid, "total_cost_usd": 0, "errors": [msg]}), flush=True)
    sys.exit(1)
(Path(".fake_last_args.json")).write_text(json.dumps({"args": args, "prompt": prompt}))

def out(d):
    print(json.dumps(d), flush=True)

out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake", "skills": ["worksheet-studio-pdf"], "claude_code_version": "9.9.9"})
out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Working"}}, "parent_tool_use_id": None})
d = Path("material/2026-10-16"); d.mkdir(parents=True, exist_ok=True)
(d / "Ana_2026-10-16_Guided.pdf").write_bytes(b"%PDF-1.4 fake")
(d / "Ana_2026-10-16_Guided.tex").write_text("junk")
(d / "Ana_2026-10-16_Guided.pdf.tex").write_text("junk from a bad -o")
# A misbehaving agent: source and an extra variant in material/ (the app must move them out).
(d / "Ana_Guided.txt").write_text("Title\n\nProblem 1. x")
(d / "Ana_2026-10-16_Extra_Practice.pdf").write_bytes(b"%PDF-1.4 extra")
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": str(Path.cwd() / d / "Ana_Guided.txt")}}]}, "parent_tool_use_id": None})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": False}]}, "parent_tool_use_id": None})
if "write the eval" in prompt.lower():
    # an agent that only writes the source (hooks don't run under the fake CLI, so the
    # app's end-of-run catch-up builds it)
    src = Path(".work/sources/2026-10-16"); src.mkdir(parents=True, exist_ok=True)
    (src / "Ana_Eval.txt").write_text("Ana — Eval\nAlgebra I\n\nProblem 1. Factor $x^2 - 9$.\n\n"
                                      "Problem 2. Solve $2x + 3 = 7$.\n\nAnswer Key\n1. $(x-3)(x+3)$\n2. $x = 2$\n")
if "feedback" in prompt.lower():
    r = json.loads(Path("roster.json").read_text())
    for s in r["students"]:
        if s["name"] == "Ana":
            s["strengths"] = ["Now solid on Holt-A1 §5-2: Solving Systems by Substitution."]
    Path("roster.json").write_text(json.dumps(r, indent=2))
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "**Generated**\nmaterial/2026-10-16/Ana_2026-10-16_Guided.pdf\n**Needs your input**\n- Nothing."}]}, "parent_tool_use_id": None})
out({"type": "result", "subtype": "success", "is_error": False, "result": "**Generated**\nmaterial/2026-10-16/Ana_2026-10-16_Guided.pdf", "session_id": sid, "total_cost_usd": 0.01, "duration_ms": 10, "num_turns": 2, "permission_denials": []})
