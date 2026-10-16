"""Background jobs while the server runs: calendar refresh and the daily auto-prep."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from . import calendar_ics, roster
from .config import WorkspaceConfig, write_json_atomic
from .runs import RunManager, clear_scratch

log = logging.getLogger("tutorassist.scheduler")
CALENDAR_EVERY = dt.timedelta(minutes=15)
# If the computer was asleep at prep time, still run when it wakes — up to this late.
CATCH_UP = dt.timedelta(hours=10)


def _state_path(ws: Path) -> Path:
    return ws / ".tutor" / "state.json"


def load_state(ws: Path) -> dict:
    try:
        return json.loads(_state_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(ws: Path, st: dict) -> None:
    write_json_atomic(_state_path(ws), st)


def roster_names(ws: Path) -> list[str]:
    try:
        return [s.get("name", "") for s in roster.load(ws).get("students", [])]
    except roster.RosterError:
        return []


def prep_prompt(day: dt.date, today: dt.date, only: list[str] | None = None) -> str:
    p = "prep today" if day == today else f"prep {day.isoformat()}"
    if only:
        p += " — only for: " + ", ".join(only)
    return p


def due_auto_prep(cfg: WorkspaceConfig, state: dict, now: dt.datetime) -> bool:
    ap = cfg.autoPrep
    if not ap.enabled or now.weekday() not in ap.days:
        return False
    if state.get("lastAutoPrep") == now.date().isoformat():
        return False
    try:
        hh, mm = (int(x) for x in ap.time.split(":"))
    except ValueError:
        return False
    at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return at <= now <= at + CATCH_UP


async def loop(ws: Path, runs: RunManager, stop: asyncio.Event) -> None:
    last_cal: dt.datetime | None = None
    while not stop.is_set():
        try:
            cfg = WorkspaceConfig.load(ws)
            tz = ZoneInfo(cfg.timezone or "UTC")
            now = dt.datetime.now(tz)
            state = load_state(ws)

            if state.get("scratchCleared") != now.date().isoformat() and not runs.busy():
                clear_scratch(ws)
                state["scratchCleared"] = now.date().isoformat()
                save_state(ws, state)

            if last_cal is None or now - last_cal >= CALENDAR_EVERY:
                if not runs.busy():
                    await asyncio.to_thread(calendar_ics.refresh, ws, cfg, roster_names(ws))
                last_cal = now

            if due_auto_prep(cfg, state, now) and not runs.busy():
                sched = await asyncio.to_thread(calendar_ics.refresh, ws, cfg, roster_names(ws))
                last_cal = now
                todays = calendar_ics.sessions_on(sched, now.date(), tz)
                state["lastAutoPrep"] = now.date().isoformat()
                save_state(ws, state)
                if todays or not cfg.autoPrep.onlyWhenSessions:
                    names = ", ".join(e.get("student") or e["summary"] for e in todays) or "no sessions found"
                    runs.start(kind="prep", prompt="prep today", title=f"Daily prep: {names}",
                               source="schedule")
                    log.info("auto-prep started for %s", now.date())
                else:
                    log.info("auto-prep skipped: no sessions today")
        except Exception:  # noqa: BLE001 — a bad config must never kill the loop
            log.exception("scheduler tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except asyncio.TimeoutError:
            pass
