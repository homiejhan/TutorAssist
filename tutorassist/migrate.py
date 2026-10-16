"""Carry an install over from the app's old name, "Tutoring Agent", to TutorAssist.

An old install has:
    ~/.tutor-agent/                          app state (app.json) and server.log
    ~/TutoringAgent/                         the default workspace
    ~/Library/LaunchAgents/com.tutoring-agent.server.plist   (Linux: tutoring-agent.service)
    ~/Applications/Tutoring Agent.app        the Spotlight launcher
    the uv tool "tutoring-agent"             (install.sh and `tutor update` uninstall it)

Everything is moved or renamed, never copied or deleted, except the old login item and
launcher, which the app generated itself. A workspace in a custom location stays where
it is. Claude Code keys its saved sessions by folder, so a moved workspace can't resume
older chats directly; runs.py notices and carries the conversation into a new session.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .config import AppState, app_home

OLD_HOME = Path.home() / ".tutor-agent"
OLD_WS = Path.home() / "TutoringAgent"
NEW_WS = Path.home() / "TutorAssist"
OLD_LAUNCHD_LABEL = "com.tutoring-agent.server"
OLD_UNIT = "tutoring-agent"
OLD_LAUNCHER = Path.home() / "Applications" / "Tutoring Agent.app"
OLD_TOOL = "tutoring-agent"


def _old_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{OLD_LAUNCHD_LABEL}.plist"


def _old_unit() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{OLD_UNIT}.service"


def _home_overridden() -> bool:
    return bool(os.environ.get("TUTORASSIST_HOME") or os.environ.get("TUTOR_AGENT_HOME"))


def _run(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def _rename(src: Path, dst: Path) -> bool:
    try:
        src.rename(dst)
        return True
    except OSError:  # e.g. another `tutor` process moved it a moment ago
        return False


def old_service_installed() -> bool:
    return _old_plist().exists() or _old_unit().exists()


def pending() -> bool:
    home = not _home_overridden() and OLD_HOME.is_dir() and not app_home().exists()
    ws = OLD_WS.is_dir() and not NEW_WS.exists()
    return home or ws or old_service_installed() or OLD_LAUNCHER.exists()


def server_running(port: int = 8787) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1):  # noqa: S310
            return True
    except Exception:  # noqa: BLE001
        return False


def stop_old_service() -> bool:
    """Stop and remove the old login item. Returns True if there was one."""
    found = False
    plist = _old_plist()
    if plist.exists():
        _run(["launchctl", "unload", "-w", str(plist)])
        plist.unlink(missing_ok=True)
        found = True
    unit = _old_unit()
    if unit.exists():
        _run(["systemctl", "--user", "disable", "--now", OLD_UNIT])
        unit.unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"])
        found = True
    return found


def uninstall_old_tool() -> None:
    """Remove the old uv tool. Call it before installing the new one: the old tool's
    receipt also lists the `tutor` command, and uninstalling it removes that file."""
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    try:
        r = subprocess.run([uv, "tool", "list"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return
    names = {line.split()[0] for line in r.stdout.splitlines() if line.strip() and not line.startswith(("-", " "))}
    if OLD_TOOL in names:
        _run([uv, "tool", "uninstall", OLD_TOOL])


def run(*, stop_service: bool) -> list[str]:
    """Move an old install to the new names. Returns what changed (empty if nothing).

    With stop_service=False (ordinary commands) nothing moves while an app is still
    serving from the old folders; `tutor setup` and `tutor update` pass True, stop the
    old login item first, and then move everything.
    """
    if not pending():
        return []
    notes: list[str] = []
    if stop_service:
        if stop_old_service():
            notes.append("Stopped the old “Tutoring Agent” login item")
    elif server_running():
        return []

    new_home = app_home()
    if not _home_overridden() and OLD_HOME.is_dir() and not new_home.exists():
        if _rename(OLD_HOME, new_home):
            notes.append(f"Moved app settings {OLD_HOME} → {new_home}")

    st = AppState.load()
    current = Path(st.workspace).expanduser().resolve() if st.workspace else None
    if current is None or current == OLD_WS.resolve():
        if OLD_WS.is_dir() and not NEW_WS.exists() and _rename(OLD_WS, NEW_WS):
            notes.append(f"Moved your workspace {OLD_WS} → {NEW_WS}")
        if current is not None and not OLD_WS.exists() and NEW_WS.is_dir():
            st.workspace = str(NEW_WS)
            st.save()

    if OLD_LAUNCHER.exists():
        shutil.rmtree(OLD_LAUNCHER, ignore_errors=True)
        notes.append("Removed the old “Tutoring Agent” launcher")
    return notes
