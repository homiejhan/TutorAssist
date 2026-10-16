"""Calendar → schedule.json.

Reads the tutor's calendar through its private iCal (ICS) link — Google
Calendar's "Secret address in iCal format", an iCloud public-calendar link, or
an Outlook "ICS" publish link all work — expands recurring events for today
plus the look-ahead window, merges sessions the tutor added by hand in the app,
and writes ``schedule.json`` for the agent.

The agent never talks to the calendar itself: this module is the only reader,
which keeps runs deterministic and means no Google Cloud project or OAuth
client is needed.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.request
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import WorkspaceConfig, write_json_atomic

USER_AGENT = "TutorAssist/0.2 (+calendar sync)"


class CalendarError(RuntimeError):
    pass


def schedule_path(ws: Path) -> Path:
    return ws / "schedule.json"


def manual_path(ws: Path) -> Path:
    return ws / ".tutor" / "manual_sessions.json"


# --------------------------------------------------------------------------- fetch + parse
def normalize_url(url: str) -> str:
    url = url.strip()
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    return url


def fetch_ics(url: str, timeout: float = 20.0) -> bytes:
    url = normalize_url(url)
    if not url.lower().startswith(("https://", "http://")):
        raise CalendarError("The calendar link should start with https:// (or webcal://).")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — user-supplied feed URL is the point
            data = resp.read()
    except Exception as e:  # noqa: BLE001
        raise CalendarError(f"Couldn't download the calendar: {e}") from e
    if b"BEGIN:VCALENDAR" not in data[:2048]:
        raise CalendarError(
            "That link didn't return a calendar file. For Google Calendar, use the "
            "'Secret address in iCal format' (it ends in .ics), not the calendar's web page."
        )
    return data


def parse_events(ics: bytes, tz: ZoneInfo, start: dt.datetime, end: dt.datetime) -> list[dict]:
    import icalendar
    import recurring_ical_events

    try:
        cal = icalendar.Calendar.from_ical(ics)
    except Exception as e:  # noqa: BLE001
        raise CalendarError(f"Couldn't read the calendar file: {e}") from e

    out = []
    for ev in recurring_ical_events.of(cal).between(start, end):
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        summary = str(ev.get("SUMMARY", "")).strip()
        s = ev.get("DTSTART").dt
        e_prop = ev.get("DTEND")
        if e_prop is not None:
            e = e_prop.dt
        elif ev.get("DURATION") is not None:
            e = s + ev.get("DURATION").dt
        else:
            e = s
        item: dict = {"summary": summary, "source": "calendar"}
        if isinstance(s, dt.datetime):
            s = s.replace(tzinfo=tz) if s.tzinfo is None else s.astimezone(tz)
            e = e.replace(tzinfo=tz) if isinstance(e, dt.datetime) and e.tzinfo is None else e
            if isinstance(e, dt.datetime):
                e = e.astimezone(tz)
            item["start"] = {"dateTime": s.isoformat()}
            item["end"] = {"dateTime": e.isoformat()} if isinstance(e, dt.datetime) else {"date": e.isoformat()}
        else:  # all-day
            item["start"] = {"date": s.isoformat()}
            item["end"] = {"date": e.isoformat()}
        uid = str(ev.get("UID", "")) or uuid.uuid4().hex
        item["id"] = f"{uid}@{item['start'].get('dateTime') or item['start'].get('date')}"
        out.append(item)
    return out


# --------------------------------------------------------------------------- matching
def match_student(summary: str, names: list[str]) -> str | None:
    """Whole-word, case-insensitive match of exactly one roster name in a title."""
    s = summary.strip().lower()
    exact = [n for n in names if n.strip().lower() == s]
    if len(exact) == 1:
        return exact[0]
    hits = [n for n in names if n.strip() and re.search(rf"(?<!\w){re.escape(n.strip())}(?!\w)", summary, re.I)]
    # Prefer the longest name when one is contained in another ("Ana" vs "Ana Lee").
    hits.sort(key=len, reverse=True)
    if len(hits) == 1 or (len(hits) > 1 and all(h.lower() in hits[0].lower() for h in hits[1:])):
        return hits[0]
    return None


def event_start(ev: dict, tz: ZoneInfo) -> dt.datetime:
    st = ev["start"]
    if "dateTime" in st:
        return dt.datetime.fromisoformat(st["dateTime"]).astimezone(tz)
    return dt.datetime.combine(dt.date.fromisoformat(st["date"]), dt.time(0, 0), tz)


def event_minutes(ev: dict) -> int | None:
    if "dateTime" not in ev["start"] or "dateTime" not in ev.get("end", {}):
        return None
    a = dt.datetime.fromisoformat(ev["start"]["dateTime"])
    b = dt.datetime.fromisoformat(ev["end"]["dateTime"])
    return int((b - a).total_seconds() // 60)


# --------------------------------------------------------------------------- manual sessions
def load_manual(ws: Path) -> list[dict]:
    try:
        return json.loads(manual_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_manual(ws: Path, items: list[dict]) -> None:
    write_json_atomic(manual_path(ws), items)


def add_manual(ws: Path, student: str, date: str, start: str, end: str) -> dict:
    dt.date.fromisoformat(date)
    for t in (start, end):
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            raise ValueError("times must be HH:MM")
    item = {"id": uuid.uuid4().hex[:10], "student": student.strip(), "date": date, "start": start, "end": end}
    items = load_manual(ws)
    items.append(item)
    save_manual(ws, items)
    return item


def remove_manual(ws: Path, item_id: str) -> None:
    save_manual(ws, [i for i in load_manual(ws) if i.get("id") != item_id])


def manual_events(ws: Path, tz: ZoneInfo, start: dt.date, end: dt.date) -> list[dict]:
    out = []
    for m in load_manual(ws):
        try:
            d = dt.date.fromisoformat(m["date"])
        except (KeyError, ValueError):
            continue
        if not (start <= d <= end):
            continue
        sh, sm = map(int, m["start"].split(":"))
        eh, em = map(int, m["end"].split(":"))
        s = dt.datetime.combine(d, dt.time(sh, sm), tz)
        e = dt.datetime.combine(d, dt.time(eh, em), tz)
        if e <= s:
            e += dt.timedelta(days=1)
        out.append({
            "id": f"manual:{m['id']}", "summary": m["student"], "student": m["student"],
            "source": "manual", "start": {"dateTime": s.isoformat()}, "end": {"dateTime": e.isoformat()},
        })
    return out


# --------------------------------------------------------------------------- refresh
def refresh(ws: Path, cfg: WorkspaceConfig, roster_names: list[str], now: dt.datetime | None = None,
            fetcher=fetch_ics) -> dict:
    """Rebuild schedule.json. Never raises: problems are recorded in the file."""
    tz = ZoneInfo(cfg.timezone or "UTC")
    now = (now or dt.datetime.now(tz)).astimezone(tz)
    today = now.date()
    last = today + dt.timedelta(days=max(1, cfg.calendar.lookaheadDays))
    win_start = dt.datetime.combine(today, dt.time(0, 0), tz)
    win_end = dt.datetime.combine(last + dt.timedelta(days=1), dt.time(0, 0), tz)

    previous = {}
    try:
        previous = json.loads(schedule_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    doc: dict = {
        "_comment": "Written by TutorAssist from the tutor's calendar before every run. Read-only for the agent.",
        "fetchedAt": today.isoformat(),
        "refreshedAt": now.isoformat(timespec="seconds"),
        "timezone": cfg.timezone,
        "window": {"from": today.isoformat(), "to": last.isoformat()},
        "source": "ics" if cfg.calendar.icsUrl else "manual-only",
        "events": [],
    }

    cal_events: list[dict] = []
    if cfg.calendar.icsUrl:
        try:
            cal_events = parse_events(fetcher(cfg.calendar.icsUrl), tz, win_start, win_end)
            flt = cfg.calendar.filter.strip().lower()
            if flt:
                cal_events = [e for e in cal_events if flt in e["summary"].lower()]
        except CalendarError as e:
            prev_ok = previous.get("fetchedAt") == today.isoformat() and not previous.get("error")
            if prev_ok:
                cal_events = [e for e in previous.get("events", []) if e.get("source") == "calendar"]
                doc["warning"] = f"Calendar refresh failed ({e}); using today's earlier copy."
            else:
                doc["error"] = str(e)

    events = cal_events + manual_events(ws, tz, today, last)
    for ev in events:
        if "student" not in ev:
            hit = match_student(ev["summary"], roster_names)
            if hit:
                ev["student"] = hit
        mins = event_minutes(ev)
        if mins is not None:
            ev["minutes"] = mins
    events.sort(key=lambda e: event_start(e, tz))
    doc["events"] = events
    write_json_atomic(schedule_path(ws), doc)
    return doc


def load_schedule(ws: Path) -> dict:
    try:
        return json.loads(schedule_path(ws).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"events": []}


def sessions_on(schedule: dict, day: dt.date, tz: ZoneInfo) -> list[dict]:
    return [e for e in schedule.get("events", []) if event_start(e, tz).date() == day]
