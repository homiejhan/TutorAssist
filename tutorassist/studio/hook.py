"""Claude Code PostToolUse hook: build a session source the moment the agent saves it.

engine.py registers it for Write/Edit/MultiEdit on every run (via --settings). Claude
Code passes the tool call as JSON on stdin; for anything that isn't a session source
under .work/sources/ the hook exits silently. Otherwise it builds the PDFs and answers
with the build report: as extra context when the build worked, or as a blocking
message (which Claude Code shows the agent right away) when it didn't.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path


def report_path(ws: Path, source_rel: str) -> Path:
    """Where the hook leaves its latest report for a source, for the app's run log
    (Claude Code doesn't put hook output in the stream the app reads)."""
    return ws / ".tutor" / "builds" / (source_rel.replace("/", "__") + ".json")


def save_report(ws: Path, res) -> None:
    p = report_path(ws, res.source)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ok": res.ok, "text": res.message(), "at": time.time()}))
    except OSError:
        pass


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    path = (event.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".txt"):
        return 0
    ws = Path(os.environ.get("TUTORASSIST_WORKSPACE") or event.get("cwd") or os.getcwd()).resolve()
    src = Path(path)
    if not src.is_absolute():
        src = ws / src

    from .build import build_source, source_info

    if not source_info(ws, src):
        return 0
    res = build_source(ws, src, previews=True)
    save_report(ws, res)
    if res.ok:
        out = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": res.message()}}
    else:
        out = {"decision": "block", "reason": res.message()}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
