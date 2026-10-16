"""App-level and workspace-level configuration.

Two places hold settings:

* ``~/.tutorassist/app.json`` (override with ``TUTORASSIST_HOME``) — which
  workspace the app opens by default and the per-install access token that
  protects the local API from other websites.
* ``<workspace>/.tutor/config.json`` — everything about one tutor's setup:
  name, timezone, calendar feed, auto-prep schedule, model, shell access.
"""
from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


def app_home() -> Path:
    override = os.environ.get("TUTORASSIST_HOME") or os.environ.get("TUTOR_AGENT_HOME")
    return Path(override).expanduser() if override else Path.home() / ".tutorassist"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- app.json
@dataclass
class AppState:
    workspace: str = ""
    token: str = ""
    source: str = ""  # where the app was installed from, for `tutor update`

    @classmethod
    def load(cls) -> "AppState":
        raw = _read_json(app_home() / "app.json", {})
        st = cls(workspace=raw.get("workspace", ""), token=raw.get("token", ""), source=raw.get("source", ""))
        if not st.token:
            st.token = secrets.token_urlsafe(24)
            st.save()
        return st

    def save(self) -> None:
        write_json_atomic(app_home() / "app.json", asdict(self))


# --------------------------------------------------------------------------- workspace config
@dataclass
class CalendarConfig:
    icsUrl: str = ""
    # Only events whose title contains this text (case-insensitive) count as
    # tutoring sessions. Empty = every event on the calendar.
    filter: str = ""
    lookaheadDays: int = 7


@dataclass
class AutoPrepConfig:
    enabled: bool = False
    time: str = "06:30"  # HH:MM in the tutor's timezone
    days: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5, 6])  # Monday = 0
    # Only start the scheduled run when today actually has sessions.
    onlyWhenSessions: bool = True


@dataclass
class WorkspaceConfig:
    tutorName: str = ""
    timezone: str = "America/Chicago"
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    autoPrep: AutoPrepConfig = field(default_factory=AutoPrepConfig)
    # Claude model alias or full name passed to `claude --model`; "" = Claude Code's default.
    model: str = ""
    # "standard": the allow-list in .claude/settings.json. "full": any shell command.
    shellAccess: str = "standard"
    setupComplete: bool = False

    @classmethod
    def path(cls, ws: Path) -> Path:
        return ws / ".tutor" / "config.json"

    @classmethod
    def load(cls, ws: Path) -> "WorkspaceConfig":
        return cls.from_dict(_read_json(cls.path(ws), {}))

    @classmethod
    def from_dict(cls, raw: dict) -> "WorkspaceConfig":
        cfg = cls()
        for k in ("tutorName", "timezone", "model", "shellAccess", "setupComplete"):
            if k in raw:
                setattr(cfg, k, raw[k])
        cal = raw.get("calendar") or {}
        cfg.calendar = CalendarConfig(
            icsUrl=str(cal.get("icsUrl", "")).strip(),
            filter=str(cal.get("filter", "")),
            lookaheadDays=int(cal.get("lookaheadDays", 7) or 7),
        )
        ap = raw.get("autoPrep") or {}
        cfg.autoPrep = AutoPrepConfig(
            enabled=bool(ap.get("enabled", False)),
            time=str(ap.get("time", "06:30")),
            days=[int(d) for d in ap.get("days", [0, 1, 2, 3, 4, 5, 6])],
            onlyWhenSessions=bool(ap.get("onlyWhenSessions", True)),
        )
        if cfg.shellAccess not in ("standard", "full"):
            cfg.shellAccess = "standard"
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, ws: Path) -> None:
        write_json_atomic(self.path(ws), self.to_dict())
