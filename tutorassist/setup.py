"""`tutor setup` — everything a new install needs, in one idempotent pass.

    1. Claude Code: installs it with Anthropic's official installer if missing, then runs
       `claude auth login` (Anthropic's own sign-in page) if it isn't signed in yet.
    2. LaTeX: uses an existing TeX if it already compiles worksheets. Otherwise installs
       TinyTeX into the home folder (no admin password), then adds whatever packages a test
       compile still lacks.
    3. Workspace: creates ~/TutorAssist, importing an existing Cowork/Claude Code
       tutoring folder if one is found next to the app (or given with --import).
    4. Starts at login: a macOS LaunchAgent (or a systemd user service on Linux), plus a
       "TutorAssist" app in ~/Applications that opens the page.
    5. Opens the app in the browser for the one remaining question: the calendar link.

Safe to re-run any time: every step checks first and only fixes what's missing.
"""
from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import migrate, workspace
from .config import AppState
from .engine import find_claude

MAC = platform.system() == "Darwin"
URL = "http://127.0.0.1:8787/"
TINYTEX_PARENT = Path.home() / "Library" if MAC else Path.home()
TINYTEX_GLOB = str(TINYTEX_PARENT / ("TinyTeX" if MAC else ".TinyTeX") / "bin" / "*")
LATEX_PACKAGES = (
    "palatino mathpazo fpl psnfss microtype enumitem tcolorbox fancyhdr needspace colortbl tools "
    "pgf pgfplots environ trimspaces pdfcol tikzfill listings listingsutf8 xcolor amsmath amsfonts etoolbox"
).split()


def say(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    say(f"\n\033[1m{msg}\033[0m")


def tinytex_bin() -> str | None:
    hits = sorted(glob.glob(TINYTEX_GLOB))
    return hits[0] if hits else None


# --------------------------------------------------------------------------- Claude Code
def ensure_claude(interactive: bool) -> bool:
    step("Claude Code")
    claude = find_claude()
    if not claude:
        say("  Installing Claude Code (Anthropic's official installer)…")
        r = subprocess.run("curl -fsSL https://claude.ai/install.sh | bash", shell=True)
        os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ.get("PATH", "")
        claude = find_claude()
        if r.returncode != 0 or not claude:
            say("  ✗ Couldn't install Claude Code automatically. Install it from https://claude.com/claude-code, then re-run `tutor setup`.")
            return False
    ver = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip()
    say(f"  ✓ {ver}")
    if os.environ.get("ANTHROPIC_API_KEY"):
        say("  ✓ Using ANTHROPIC_API_KEY (billed per use to that key)")
        return True
    if _signed_in(claude):
        say("  ✓ Signed in")
        return True
    if not interactive:
        say("  ✗ Not signed in. Run `claude auth login`, then re-run `tutor setup`.")
        return False
    say("  Sign in with your Claude account (Pro or Max) in the browser window that opens…")
    subprocess.run([claude, "auth", "login"])
    if _signed_in(claude):
        say("  ✓ Signed in")
        return True
    say("  ✗ Still not signed in. Run `claude auth login`, then re-run `tutor setup`.")
    return False


def _signed_in(claude: str) -> bool:
    try:
        out = subprocess.run([claude, "auth", "status"], capture_output=True, text=True, timeout=30).stdout
        return bool(json.loads(out or "{}").get("loggedIn"))
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- LaTeX
def _sample_dir() -> Path:
    return workspace.template_root() / "claude_dir" / "skills" / "worksheet-studio-pdf"


def compile_samples() -> tuple[bool, str]:
    """Compile every bundled Worksheet Studio example (graphs, figures, tables, layouts)."""
    gen = Path(__file__).parent / "studio" / "worksheet_gen.py"
    samples = sorted(_sample_dir().glob("example*.txt")) + sorted(_sample_dir().glob("guided_template.txt"))
    with tempfile.TemporaryDirectory() as td:
        for s in samples:
            r = subprocess.run([sys.executable, str(gen), str(s), "-o", s.stem, "--outdir", td],
                               capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                log = Path(td) / f"{s.stem}.log"
                text = log.read_text(errors="replace") if log.exists() else ""
                return False, text + "\n" + r.stdout + r.stderr
    return True, ""


def missing_from_log(text: str) -> list[str]:
    """Files a failed LaTeX run couldn't find: `x.sty`, `y.tfm`, …"""
    found = re.findall(r"File [`']([^`']+?\.(?:sty|cls|tex|def|cfg|code\.tex))' not found", text)
    found += [f"{m}.tfm" for m in re.findall(r"Font \S+=(\S+?) at \S+ not (?:found|loadable)", text)]
    found += [f"{m}.tfm" for m in re.findall(r"mktextfm (\S+)", text)]
    seen, out = set(), []
    for f in found:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


def _tlmgr(bin_dir: str, *args: str) -> subprocess.CompletedProcess:
    r = subprocess.run([os.path.join(bin_dir, "tlmgr"), *args], capture_output=True, text=True)
    if "tlmgr itself needs to be updated" in (r.stdout + r.stderr):
        subprocess.run([os.path.join(bin_dir, "tlmgr"), "update", "--self"], capture_output=True, text=True)
        r = subprocess.run([os.path.join(bin_dir, "tlmgr"), *args], capture_output=True, text=True)
    return r


def _package_for(bin_dir: str, filename: str) -> str | None:
    r = _tlmgr(bin_dir, "search", "--global", "--file", f"/{filename}")
    for line in r.stdout.splitlines():
        m = re.match(r"^([\w.\-]+):\s*$", line.strip())
        if m:
            return m.group(1)
    return None


def install_tinytex() -> str | None:
    arch = platform.machine().lower()
    if MAC:
        suffix = "-darwin"
    elif arch in ("aarch64", "arm64"):
        suffix = "-linux-arm64"
    else:
        suffix = "-linux-x86_64"
    url = f"https://github.com/rstudio/tinytex-releases/releases/download/daily/TinyTeX-1{suffix}.tar.xz"
    say("  Downloading TinyTeX (about 55 MB; it installs in your home folder, no password needed)…")
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "tinytex.tar.xz"
        try:
            with urllib.request.urlopen(url, timeout=120) as resp, open(dest, "wb") as out:  # noqa: S310
                shutil.copyfileobj(resp, out)
        except Exception as e:  # noqa: BLE001
            say(f"  ✗ Download failed: {e}")
            return None
        TINYTEX_PARENT.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["tar", "xf", str(dest), "-C", str(TINYTEX_PARENT)], capture_output=True, text=True)
        if r.returncode != 0:
            say(f"  ✗ Couldn't unpack TinyTeX: {r.stderr.strip()}")
            return None
    return tinytex_bin()


def ensure_latex() -> bool:
    step("LaTeX (typesets the worksheets)")
    from .cli import augment_path
    augment_path()
    ok, log = compile_samples() if shutil.which("pdflatex") else (False, "")
    if ok:
        say(f"  ✓ {shutil.which('pdflatex')} compiles every worksheet feature")
        return True

    bin_dir = tinytex_bin()
    if not bin_dir:
        if shutil.which("pdflatex"):
            say("  Your existing LaTeX is missing packages; installing a private TinyTeX alongside it instead…")
        bin_dir = install_tinytex()
        if not bin_dir:
            return False
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
    say("  Installing worksheet packages…")
    r = _tlmgr(bin_dir, "install", *LATEX_PACKAGES)
    if r.returncode != 0:  # one bad name fails the batch; fall back to one at a time
        for pkg in LATEX_PACKAGES:
            _tlmgr(bin_dir, "install", pkg)

    for _ in range(12):
        ok, log = compile_samples()
        if ok:
            say(f"  ✓ TinyTeX compiles every worksheet feature ({bin_dir})")
            return True
        missing = missing_from_log(log)
        pkgs = [p for p in (_package_for(bin_dir, f) for f in missing) if p]
        if not pkgs:
            break
        say(f"  Adding {', '.join(pkgs)}…")
        _tlmgr(bin_dir, "install", *pkgs)
    say("  ✗ LaTeX still can't compile a worksheet. Last error:")
    say("    " + "\n    ".join([ln for ln in log.splitlines() if ln.startswith("!")][:5] or log.splitlines()[-5:]))
    return False


# --------------------------------------------------------------------------- workspace
def find_legacy_folder(source: str | None) -> Path | None:
    """A Cowork-era tutoring folder (CLAUDE.md + roster.json) the app was unpacked into."""
    candidates = []
    if source and not source.startswith(("git+", "http")):
        candidates.append(Path(source).expanduser().resolve().parent)
    candidates.append(Path.cwd())
    for c in candidates:
        if (c / "roster.json").exists() and (c / "CLAUDE.md").exists() and not (c / ".tutor").exists():
            return c
    return None


def ensure_workspace(ws: Path, import_from: str | None, source: str | None) -> Path:
    step("Workspace")
    st = AppState.load()
    existing = Path(st.workspace) if st.workspace and workspace.is_workspace(Path(st.workspace)) else None
    if not existing and workspace.is_workspace(ws):
        existing = ws
    if import_from and existing and _has_students(existing):
        # Re-running setup must never overwrite the live roster with an old copy.
        target = existing
        workspace.sync_managed(target)
        say(f"  Skipped importing {import_from}: {target} already has your students.")
        say("  (To deliberately replace them, run `tutor import FOLDER WORKSPACE`.)")
    elif import_from:
        target = existing or ws
        say(f"  Importing {import_from} (the original stays untouched)…")
        for n in workspace.import_legacy(Path(import_from), target):
            say(f"    • {n}")
    elif existing:
        target = existing
        workspace.sync_managed(target)
    else:
        target = ws
        legacy = find_legacy_folder(source)
        if legacy:
            say(f"  Found your existing tutoring folder {legacy}; importing it (it stays untouched)…")
            for n in workspace.import_legacy(legacy, target):
                say(f"    • {n}")
        else:
            workspace.create(target, sample=True)
    st = AppState.load()
    st.workspace = str(target)
    st.save()
    say(f"  ✓ {target}")
    return target


def _has_students(ws: Path) -> bool:
    """Real students, that is: the two example students don't count."""
    return not workspace.only_sample_students(ws)


# --------------------------------------------------------------------------- start at login
def ensure_service() -> bool:
    step("Start at login")
    from .cli import cmd_service

    class A:  # minimal argparse stand-in
        action = "install"

    try:
        cmd_service(A())
    except Exception as e:  # noqa: BLE001
        say(f"  ! Couldn't set up start-at-login ({e}). Run `tutor` whenever you want the app.")
        return False
    return True


def ensure_launcher() -> None:
    """A "TutorAssist" app in ~/Applications (Spotlight) that opens the page."""
    if not MAC:
        return
    apps = Path.home() / "Applications"
    apps.mkdir(exist_ok=True)
    target = apps / "TutorAssist.app"
    if shutil.which("osacompile") and not target.exists():
        subprocess.run(["osacompile", "-o", str(target), "-e", f'open location "{URL}"'],
                       capture_output=True)
        say("  ✓ Added “TutorAssist” to your Applications (find it with Spotlight)")


def wait_for_server(seconds: int = 20) -> bool:
    for _ in range(seconds * 2):
        try:
            with urllib.request.urlopen(URL + "api/state", timeout=1):  # noqa: S310
                return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


# --------------------------------------------------------------------------- entry
def run(a) -> int:
    from .cli import DEFAULT_WS, augment_path

    augment_path()
    interactive = sys.stdin.isatty() and not a.yes
    moved = migrate.run(stop_service=True)  # an install from when the app was "Tutoring Agent"
    if moved:
        step("Moving over from “Tutoring Agent”")
        for n in moved:
            say(f"  ✓ {n}")
    if a.source:
        st = AppState.load()
        st.source = a.source
        st.save()

    results = {
        "claude": ensure_claude(interactive),
        "latex": ensure_latex(),
    }
    ws = ensure_workspace(Path(a.workspace).expanduser() if a.workspace else DEFAULT_WS, a.import_from, a.source)
    service = False if a.no_service else ensure_service()
    if service:
        ensure_launcher()

    step("Done" if all(results.values()) else "Almost done")
    for name, ok in results.items():
        if not ok:
            say(f"  ✗ {name}: fix the message above, then run `tutor setup` again (it only redoes what's missing).")
    if service and wait_for_server():
        say(f"  The app is running at {URL} and starts by itself whenever you log in.")
        if not a.no_browser:
            webbrowser.open(URL)
    else:
        say(f"  Start the app with `tutor` (workspace: {ws}).")
    return 0 if all(results.values()) else 1
