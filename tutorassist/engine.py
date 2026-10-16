"""Runs the agent: one headless Claude Code process per run.

The app never talks to the Anthropic API itself. Each run launches the tutor's
own, unmodified ``claude`` CLI in non-interactive mode (``claude -p``) with the
workspace as its working directory, so Claude Code loads ``CLAUDE.md`` and the
bundled ``.claude/skills`` exactly as it would in a terminal. Authentication is
whatever that CLI is signed into — the tutor's Claude subscription, or an
``ANTHROPIC_API_KEY`` if one is set — and the app never sees or stores it.

``stream-json`` output is translated into small UI events (``t`` = type).
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

# Tools the agent may use without a person approving them. Nobody is there to
# approve anything during a headless run, so everything the workflows need must be
# pre-approved here. (Passed on the command line: a `-p` session doesn't apply the
# allow rules in an untrusted folder's .claude/settings.json.)
BASE_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Skill", "TodoWrite"]
# Shell commands the skills and workflows use. "Standard" shell access allows only
# these; "full" access allows any command.
SHELL_COMMANDS = [
    "python3", "python", "ocrmypdf",
    "mkdir", "ls", "cp", "mv", "rm", "cat", "head", "tail", "wc", "grep", "find", "sort", "uniq",
    "cut", "tr", "diff", "date", "echo", "printf", "cd", "pwd", "test", "file", "which", "sed",
    "awk", "touch", "basename", "dirname", "stat", "du", "true",
]
DISALLOWED = [
    "WebFetch", "WebSearch", "AskUserQuestion",
    "Bash(rm -rf *)", "Bash(rm -r *)", "Bash(rm -fr *)", "Bash(sudo *)", "Bash(curl *)", "Bash(wget *)",
    "Bash(git push *)", "Bash(ssh *)", "Bash(scp *)",
]


def allowed_tools(full_shell: bool) -> list[str]:
    if full_shell:
        return BASE_TOOLS + ["Bash"]
    return BASE_TOOLS + [r for c in SHELL_COMMANDS for r in (f"Bash({c})", f"Bash({c} *)")]


def find_claude() -> str | None:
    override = os.environ.get("TUTOR_CLAUDE_BIN")
    if override:
        return override
    found = shutil.which("claude")
    if found:
        return found
    for cand in (Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if cand.exists():
            return str(cand)
    return None


@dataclass
class RunSpec:
    workspace: Path
    prompt: str
    system_append: str
    resume_session: str | None = None
    model: str = ""
    full_shell: bool = False
    extra_env: dict[str, str] = field(default_factory=dict)


def build_command(claude: str, spec: RunSpec) -> list[str]:
    cmd = [
        claude, "-p",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode", "acceptEdits",
        # Project settings carry the shell allow/deny list; the tutor's personal
        # ~/.claude settings and MCP servers stay out of agent runs.
        "--setting-sources", "project,local",
        "--strict-mcp-config",
        "--allowedTools", ",".join(allowed_tools(spec.full_shell)),
        "--disallowedTools", ",".join(DISALLOWED),
        "--append-system-prompt", spec.system_append,
        "--settings", hook_settings(),
    ]
    if spec.model:
        cmd += ["--model", spec.model]
    if spec.resume_session:
        cmd += ["--resume", spec.resume_session]
    return cmd


def build_env(spec: RunSpec) -> dict[str, str]:
    env = dict(os.environ)
    # The skills run `python3`; put this app's Python (which has PyMuPDF, sympy)
    # first so they find their libraries regardless of the system Python.
    py_bin = str(Path(sys.executable).parent)
    env["PATH"] = py_bin + os.pathsep + env.get("PATH", "")
    env["TEXTBOOK_INDEX_DIR"] = str(spec.workspace / ".textbook_index")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Variables set when the app itself is launched from inside another Claude
    # Code session would make the child join that session; drop them.
    for k in list(env):
        if k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID",
                 "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_SESSION_ORIGIN") or k.startswith(
                ("CLAUDE_CODE_REMOTE", "CLAUDE_CODE_MESSAGING")):
            env.pop(k)
    env.update(spec.extra_env)
    return env


# --------------------------------------------------------------------------- event translation
def _short(s: str, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def describe_tool(name: str, inp: dict, workspace: Path | None = None) -> str:
    def rel(p: str) -> str:
        if workspace and p.startswith(str(workspace)):
            return p[len(str(workspace)):].lstrip("/") or "."
        return p

    if name == "Bash":
        return inp.get("description") or _short(inp.get("command", ""))
    if name in ("Read", "Write", "Edit", "NotebookEdit"):
        verb = {"Read": "Read", "Write": "Wrote", "Edit": "Edited"}.get(name, name)
        return f"{verb} {rel(inp.get('file_path', ''))}"
    if name == "Skill":
        return f"Using skill: {inp.get('skill') or inp.get('name') or ''}".strip()
    if name in ("Glob", "Grep"):
        return f"Searched for {_short(inp.get('pattern', ''), 80)}"
    if name == "TodoWrite":
        return "Updated plan"
    if name in ("Task", "Agent"):
        return f"Subtask: {_short(inp.get('description', ''), 80)}"
    return name


def _build_report(output: str) -> tuple[bool, str] | None:
    """The app's own PDF-build hook answers in JSON; turn it into a UI event."""
    try:
        data = json.loads(output.strip().splitlines()[-1]) if output.strip() else None
    except (json.JSONDecodeError, IndexError):
        return None
    if not isinstance(data, dict):
        return None
    if data.get("decision") == "block":
        return False, str(data.get("reason", ""))
    ctx = (data.get("hookSpecificOutput") or {}).get("additionalContext")
    return (True, str(ctx)) if ctx else None


def hook_settings() -> str:
    """--settings JSON that registers the PDF-build hook: whenever the agent saves a
    session source (.work/sources/DATE/{Student}_{Type}.txt), the app builds its PDFs and
    hands the agent the build report."""
    command = f"{shlex.quote(sys.executable)} -m tutorassist.studio.hook"
    return json.dumps({"hooks": {"PostToolUse": [{
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [{"type": "command", "command": command, "timeout": 300}],
    }]}})


def translate(raw: dict, workspace: Path | None = None) -> list[dict]:
    """One stream-json line → zero or more UI events."""
    t = raw.get("type")
    sub = raw.get("parent_tool_use_id") is not None
    out: list[dict] = []
    if t == "system":
        st = raw.get("subtype")
        if st == "init":
            out.append({"t": "init", "sessionId": raw.get("session_id"), "model": raw.get("model"),
                        "skills": raw.get("skills", []), "version": raw.get("claude_code_version")})
        elif st == "api_retry":
            out.append({"t": "status", "text": f"Claude API busy ({raw.get('error')}); retrying "
                                               f"(attempt {raw.get('attempt')}/{raw.get('max_retries')})…"})
        elif st == "permission_denied":
            out.append({"t": "status", "text": "A tool call was blocked by the workspace's permissions."})
        elif st == "hook_response" and str(raw.get("hook_event", "")) == "PostToolUse":
            report = _build_report(raw.get("output") or raw.get("stdout") or "")
            if report:
                out.append({"t": "build", "ok": report[0], "text": report[1]})
    elif t == "stream_event" and not sub:
        ev = raw.get("event", {})
        if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
            out.append({"t": "delta", "text": ev["delta"].get("text", "")})
    elif t == "assistant":
        for block in raw.get("message", {}).get("content", []):
            bt = block.get("type")
            if bt == "text" and block.get("text", "").strip():
                out.append({"t": "text", "text": block["text"], "sub": sub})
            elif bt == "tool_use":
                name, inp = block.get("name", ""), block.get("input", {}) or {}
                ev = {"t": "tool", "id": block.get("id"), "name": name,
                      "summary": describe_tool(name, inp, workspace), "sub": sub}
                if name in ("Write", "Edit") and inp.get("file_path"):
                    ev["path"] = inp["file_path"]
                out.append(ev)
                if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    out.append({"t": "todos", "items": [
                        {"text": td.get("content") or td.get("activeForm", ""), "status": td.get("status", "pending")}
                        for td in inp["todos"]]})
    elif t == "user":
        content = raw.get("message", {}).get("content", [])
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, list):
                        c = " ".join(x.get("text", "") for x in c if isinstance(x, dict))
                    out.append({"t": "tool_result", "id": block.get("tool_use_id"),
                                "ok": not block.get("is_error", False), "preview": _short(c or "", 300), "sub": sub})
    elif t == "result":
        out.append({
            "t": "result",
            "ok": not raw.get("is_error", False) and raw.get("subtype") == "success",
            "text": raw.get("result", ""),
            "sessionId": raw.get("session_id"),
            "costUsd": raw.get("total_cost_usd"),
            "durationMs": raw.get("duration_ms"),
            "turns": raw.get("num_turns"),
            "denials": len(raw.get("permission_denials") or []),
            "subtype": raw.get("subtype"),
            "errors": [str(e) for e in raw.get("errors") or []],
        })
    return out


# --------------------------------------------------------------------------- process
class AgentProcess:
    def __init__(self, spec: RunSpec, claude: str | None = None):
        self.spec = spec
        self.claude = claude or find_claude()
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_tail: list[str] = []
        self.cancelled = False

    async def events(self) -> AsyncIterator[dict]:
        if not self.claude:
            yield {"t": "error", "text": "Claude Code isn't installed (no `claude` command found). "
                                        "Install it, sign in once by running `claude`, then try again."}
            return
        cmd = build_command(self.claude, self.spec)
        self.proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(self.spec.workspace),
            env=build_env(self.spec),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,  # tool results can make single JSON lines large
            start_new_session=True,
        )
        assert self.proc.stdin and self.proc.stdout and self.proc.stderr
        # The prompt goes in on stdin: no argv length limits, no shell quoting.
        self.proc.stdin.write(self.spec.prompt.encode("utf-8"))
        await self.proc.stdin.drain()
        self.proc.stdin.close()

        stderr_task = asyncio.create_task(self._drain_stderr())
        saw_result = False
        async for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            for ev in translate(raw, self.spec.workspace):
                if ev["t"] == "result":
                    saw_result = True
                yield ev
        code = await self.proc.wait()
        await stderr_task
        if self.cancelled:
            yield {"t": "error", "text": "Run stopped."}
        elif not saw_result:
            tail = "\n".join(self.stderr_tail[-15:]).strip()
            yield {"t": "error", "text": f"Claude Code exited (code {code}) without finishing."
                                         + (f"\n\n{tail}" if tail else "")}

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        async for line in self.proc.stderr:
            self.stderr_tail.append(line.decode("utf-8", "replace").rstrip())
            del self.stderr_tail[:-50]

    async def cancel(self) -> None:
        """SIGINT first so Claude Code ends the turn cleanly; escalate if needed."""
        self.cancelled = True
        if not self.proc or self.proc.returncode is not None:
            return
        for sig, wait in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 0)):
            try:
                os.killpg(self.proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                return
            try:
                await asyncio.wait_for(self.proc.wait(), timeout=wait or 1)
                return
            except asyncio.TimeoutError:
                continue
