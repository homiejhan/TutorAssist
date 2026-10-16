#!/usr/bin/env bash
# TutorAssist — one-command install (macOS / Linux). Safe to re-run.
#
#   bash install.sh                      # from a downloaded copy
#   bash install.sh --import ~/old/folder   # also bring over an existing tutoring folder
#
# Installs the `tutor` app, then `tutor setup` takes care of Claude Code (and sign-in),
# LaTeX, your workspace, and starting at login. No admin password needed.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
SRC="${TUTOR_SOURCE:-$HERE}"
if [ -z "$SRC" ] || { [ ! -f "$SRC/pyproject.toml" ] && [[ "$SRC" != git+* ]]; }; then
  echo "Run this from the TutorAssist-app folder (or set TUTOR_SOURCE=git+https://…)." >&2
  exit 1
fi

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv (Python installer)…"
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
fi

echo "Installing TutorAssist…"
# The app used to be called "Tutoring Agent"; its old tool shares the `tutor` command.
uv tool uninstall tutoring-agent >/dev/null 2>&1 || true
uv tool install --force --reinstall --quiet --python 3.12 "$SRC"

# Interactive steps (Claude sign-in) need the terminal even when piped from curl.
if [ ! -t 0 ] && (: </dev/tty) 2>/dev/null; then
  exec tutor setup --source "$SRC" "$@" </dev/tty
else
  exec tutor setup --source "$SRC" "$@"
fi
