"""`tutor` command line.

    tutor                     open the app (starts it if it isn't already running)
    tutor setup               one-time setup: Claude Code, LaTeX, workspace, start at login
    tutor update              install the latest version of the app and restart it
    tutor init [PATH]         create a workspace (default ~/TutorAssist)
    tutor import OLD [PATH]   copy an existing Cowork/Claude Code tutoring folder into a workspace
    tutor serve               run the web app (opens your browser)
    tutor prep [--date D] [--only A,B]   run prep from the terminal
    tutor doctor [--full]     check Claude Code, LaTeX, Python libraries, calendar
    tutor upgrade             refresh the bundled skills / CLAUDE.md in the workspace
    tutor service install|uninstall|status   start the app automatically at login
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import platform
import shutil
import subprocess
import sys
import textwrap
import threading
import webbrowser
from pathlib import Path
from zoneinfo import ZoneInfo

from . import __version__, doctor, migrate, workspace
from .config import AppState, WorkspaceConfig, app_home

DEFAULT_WS = Path.home() / "TutorAssist"
DEFAULT_PORT = 8787
EXTRA_PATH = ["~/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "/Library/TeX/texbin"]


def augment_path() -> None:
    """Apps started at login (launchd/systemd) get a bare PATH; add the usual tool dirs.

    A TinyTeX that `tutor setup` installed goes first: setup only installs it when the
    system LaTeX couldn't compile worksheets.
    """
    import glob

    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    for d in EXTRA_PATH:
        d = os.path.expanduser(d)
        if os.path.isdir(d) and d not in parts:
            parts.append(d)
    for d in sorted(glob.glob(os.path.expanduser("~/Library/TinyTeX/bin/*")) +
                    glob.glob(os.path.expanduser("~/.TinyTeX/bin/*"))):
        if d in parts:
            parts.remove(d)
        parts.insert(0, d)
    os.environ["PATH"] = os.pathsep.join(parts)


def resolve_ws(arg: str | None) -> Path:
    if arg:
        return Path(arg).expanduser().resolve()
    st = AppState.load()
    if st.workspace:
        return Path(st.workspace)
    return DEFAULT_WS


def remember(ws: Path) -> None:
    st = AppState.load()
    st.workspace = str(ws)
    st.save()


# --------------------------------------------------------------------------- commands
def cmd_init(a) -> int:
    ws = resolve_ws(a.path)
    notes = workspace.create(ws, sample=True)
    remember(ws)
    print(f"Workspace ready at {ws}")
    for n in notes:
        print(f"  • {n}")
    print("\nNext: run `tutor` to open the app and finish setup.")
    return 0


def cmd_import(a) -> int:
    ws = Path(a.into).expanduser().resolve() if a.into else DEFAULT_WS
    print(f"Copying {a.source} → {ws} (the original folder is not modified)…")
    notes = workspace.import_legacy(Path(a.source), ws, with_material=not a.no_material)
    remember(ws)
    for n in notes:
        print(f"  • {n}")
    print("\nNext: run `tutor`, then add your calendar's iCal link in Settings.")
    return 0


def cmd_serve(a) -> int:
    import uvicorn

    from .server import create_app

    host = a.host or os.environ.get("TUTOR_HOST", "127.0.0.1")
    port = a.port or int(os.environ.get("TUTOR_PORT", DEFAULT_PORT))
    running = f"http://127.0.0.1:{port}/"
    if host in ("127.0.0.1", "localhost") and _responds(running + "api/state"):
        # Already running (normally the start-at-login service): just open it, and
        # leave its workspace alone.
        print(f"TutorAssist is running at {running}")
        if not a.no_browser:
            webbrowser.open(running)
        return 0
    ws = resolve_ws(a.workspace)
    if not workspace.is_workspace(ws):
        print(f"Creating a workspace at {ws}")
        workspace.create(ws, sample=True)
    elif workspace.needs_sync(ws):
        for n in workspace.sync_managed(ws):
            print(f"  • {n}")
    remember(ws)
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("TUTOR_ACCESS_TOKEN"):
        print("Refusing to listen beyond this computer without TUTOR_ACCESS_TOKEN set "
              "(the agent can run commands). See README → Hosting.", file=sys.stderr)
        return 2
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
    print(f"TutorAssist {__version__} — workspace {ws}\nOpen {url}  (Ctrl+C to stop)")
    if not a.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(ws), host=host, port=port, log_level="warning")
    return 0


def cmd_prep(a) -> int:
    from . import calendar_ics, roster, scheduler
    from .runs import RunManager

    ws = resolve_ws(a.workspace)
    cfg = WorkspaceConfig.load(ws)
    today = dt.datetime.now(ZoneInfo(cfg.timezone or "UTC")).date()
    day = dt.date.fromisoformat(a.date) if a.date else today
    only = [s.strip() for s in (a.only or "").split(",") if s.strip()]
    prompt = scheduler.prep_prompt(day, today, only)

    async def main() -> int:
        runs = RunManager(ws)
        run = runs.start(kind="prep", prompt=prompt, source="cli")
        q = runs.subscribe(run.id)
        while True:
            ev = await q.get()
            t = ev["t"]
            if t == "delta":
                print(ev["text"], end="", flush=True)
            elif t == "text":
                print()
            elif t == "tool":
                print(f"\n  · {ev['summary']}", flush=True)
            elif t in ("status", "error"):
                print(f"\n[{ev['text']}]", flush=True)
            elif t == "files":
                print("\nFiles:\n" + "\n".join(f"  {f}" for f in ev["files"]))
            elif t == "done":
                print(f"\n— {ev['status']}")
                return 0 if ev["status"] == "done" else 1

    _ = calendar_ics, roster  # imported for side-effect-free availability in tracebacks
    return asyncio.run(main())


def cmd_doctor(a) -> int:
    ws = resolve_ws(a.workspace)
    checks = doctor.run_all(ws if workspace.is_workspace(ws) else None, full=a.full)
    icon = {"ok": "✓", "warn": "!", "fail": "✗"}
    for c in checks:
        print(f" {icon[c['status']]} {c['label']}: {c['detail']}")
        if c["status"] != "ok" and c["fix"]:
            print(textwrap.indent(textwrap.fill("fix: " + c["fix"], 96), "     "))
    return 1 if any(c["status"] == "fail" for c in checks) else 0


def cmd_upgrade(a) -> int:
    ws = resolve_ws(a.workspace)
    notes = workspace.sync_managed(ws, force_spec=a.force_spec)
    print("Workspace is up to date." if not notes else "\n".join(f"  • {n}" for n in notes))
    return 0


def _responds(url: str) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=1):  # noqa: S310 — loopback only
            return True
    except Exception:  # noqa: BLE001
        return False


def cmd_setup(a) -> int:
    from . import setup

    return setup.run(a)


def cmd_update(a) -> int:
    st = AppState.load()
    if not st.source:
        print("Don't know where this copy was installed from. Re-run install.sh from the new version instead.")
        return 1
    if not st.source.startswith(("git+", "http")) and not Path(st.source).expanduser().exists():
        print(f"The app was installed from {st.source}, which isn't there any more.\n"
              "Run install.sh from the app's folder instead; it records the new location.")
        return 1
    uv = shutil.which("uv") or os.path.expanduser("~/.local/bin/uv")
    print(f"Updating from {st.source}…")
    migrate.uninstall_old_tool()  # before installing: it shares the `tutor` command
    r = subprocess.run([uv, "tool", "install", "--force", "--reinstall", "--python", "3.12", st.source])
    if r.returncode != 0:
        return r.returncode
    # Coming from the old name ("Tutoring Agent"): move its folders and swap its login
    # item and launcher for the new ones.
    had_service, had_launcher = migrate.old_service_installed(), migrate.OLD_LAUNCHER.exists()
    for n in migrate.run(stop_service=True):
        print(f"  • {n}")
    from .setup import ensure_launcher, ensure_service
    if had_service:
        ensure_service()
    else:
        # The new code syncs the workspace's skills/spec on start; restart the background app.
        restart_service()
    if had_launcher:
        ensure_launcher()
    print("Updated. Your workspace, settings and students are unchanged.")
    return 0


def restart_service() -> None:
    if platform.system() == "Darwin":
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"], capture_output=True)
    elif platform.system() == "Linux":
        subprocess.run(["systemctl", "--user", "restart", SYSTEMD_UNIT], capture_output=True)


# --------------------------------------------------------------------------- login service
LAUNCHD_LABEL = "com.tutorassist.server"
SYSTEMD_UNIT = "tutorassist"


def _service_cmd() -> list[str]:
    exe = shutil.which("tutor") or sys.argv[0]
    return [exe, "serve", "--no-browser"]


def cmd_service(a) -> int:
    system = platform.system()
    path_env = os.environ.get("PATH", "")
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
        if a.action == "install":
            args = "".join(f"<string>{x}</string>" for x in _service_cmd())
            log = app_home() / "server.log"
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCHD_LABEL}</string>
  <key>ProgramArguments</key><array>{args}</array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path_env}</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict></plist>
""")
            subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
            r = subprocess.run(["launchctl", "load", "-w", str(plist)], capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "launchctl load failed")
            print(f"  ✓ Starts at login (log: {log})")
        elif a.action == "uninstall":
            subprocess.run(["launchctl", "unload", "-w", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
            print("Removed the login item.")
        else:
            r = subprocess.run(["launchctl", "list", LAUNCHD_LABEL], capture_output=True, text=True)
            print("running" if r.returncode == 0 else "not installed")
        return 0
    if system == "Linux":
        unit = Path.home() / ".config" / "systemd" / "user" / f"{SYSTEMD_UNIT}.service"
        if a.action == "install":
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(f"""[Unit]
Description=TutorAssist

[Service]
ExecStart={' '.join(_service_cmd())}
Environment=PATH={path_env}
Restart=on-failure

[Install]
WantedBy=default.target
""")
            if not shutil.which("systemctl"):
                raise RuntimeError("systemd isn't available here")
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
            r = subprocess.run(["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(r.stderr.strip() or "systemctl failed")
            print("  ✓ Starts at login")
        elif a.action == "uninstall":
            subprocess.run(["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT], check=False)
            unit.unlink(missing_ok=True)
            print("Removed.")
        else:
            subprocess.run(["systemctl", "--user", "status", SYSTEMD_UNIT, "--no-pager"], check=False)
        return 0
    print("Login service setup is available on macOS and Linux.")
    return 1


# --------------------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> int:
    augment_path()
    p = argparse.ArgumentParser(prog="tutor", description="TutorAssist — powered by Claude Code.")
    p.add_argument("--version", action="version", version=f"tutor {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("setup", help="one-time setup (safe to re-run)")
    s.add_argument("--import", dest="import_from", metavar="FOLDER",
                   help="copy an existing tutoring folder (CLAUDE.md + roster.json) into the workspace")
    s.add_argument("--workspace", help="where to keep your students and materials (default ~/TutorAssist)")
    s.add_argument("--source", help=argparse.SUPPRESS)
    s.add_argument("--no-service", action="store_true", help="don't start the app at login")
    s.add_argument("--no-browser", action="store_true")
    s.add_argument("--yes", action="store_true", help="never prompt (skips Claude sign-in)")
    s.set_defaults(fn=cmd_setup)

    s = sub.add_parser("update", help="install the latest version and restart")
    s.set_defaults(fn=cmd_update)

    s = sub.add_parser("init", help="create a workspace")
    s.add_argument("path", nargs="?")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("import", help="copy an existing tutoring folder into a new workspace")
    s.add_argument("source")
    s.add_argument("into", nargs="?")
    s.add_argument("--no-material", action="store_true", help="skip the old material/ folder")
    s.set_defaults(fn=cmd_import)

    for name in ("serve",):
        s = sub.add_parser(name, help="run the web app")
        s.add_argument("--workspace")
        s.add_argument("--host")
        s.add_argument("--port", type=int)
        s.add_argument("--no-browser", action="store_true")
        s.set_defaults(fn=cmd_serve)

    s = sub.add_parser("prep", help="run prep in the terminal")
    s.add_argument("--workspace")
    s.add_argument("--date")
    s.add_argument("--only", help="comma-separated student names")
    s.set_defaults(fn=cmd_prep)

    s = sub.add_parser("doctor", help="check the setup")
    s.add_argument("--workspace")
    s.add_argument("--full", action="store_true", help="also compile a test worksheet")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("upgrade", help="refresh bundled skills and CLAUDE.md")
    s.add_argument("--workspace")
    s.add_argument("--force-spec", action="store_true", help="replace CLAUDE.md even if you edited it")
    s.set_defaults(fn=cmd_upgrade)

    s = sub.add_parser("service", help="start the app at login")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.set_defaults(fn=cmd_service)

    a = p.parse_args(argv)
    if not a.cmd:
        a = p.parse_args(["serve", *(argv or [])])
    if a.cmd not in ("setup", "update"):  # those stop the old login item first, then move
        for n in migrate.run(stop_service=False):
            print(f"  • {n}")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
