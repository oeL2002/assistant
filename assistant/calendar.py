"""Read-only view of the work calendar: fetch the published ICS, expand
recurrences, return the next N days of events. A short TTL cache keeps repeated
tool calls in one turn from re-hitting the endpoint.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

TZ = ZoneInfo("Europe/Zurich")


@dataclass
class Event:
    start: dt.datetime
    end: dt.datetime
    summary: str
    location: str


def parse_local(s: str) -> dt.datetime:
    """Parse an ISO datetime the agent supplies; a naive one is read as local."""
    parsed = dt.datetime.fromisoformat(s.strip())
    return parsed.replace(tzinfo=TZ) if parsed.tzinfo is None else parsed.astimezone(TZ)


def overlaps(start_at: str, end_at: str, start: dt.datetime, end: dt.datetime) -> bool:
    """True if a stored event [start_at, end_at] (ISO) intersects the window [start, end]."""
    return parse_local(start_at) <= end and parse_local(end_at) >= start


def _as_dt(x: object) -> dt.datetime:
    """Normalise an icalendar DTSTART/DTEND (date or datetime) to a TZ-aware dt."""
    if isinstance(x, dt.datetime):
        return x.astimezone(TZ) if x.tzinfo else x.replace(tzinfo=TZ)
    if isinstance(x, dt.date):  # all-day event -> local midnight
        return dt.datetime(x.year, x.month, x.day, tzinfo=TZ)
    raise TypeError(f"unexpected date value: {x!r}")


def expand(raw: bytes, start: dt.datetime, end: dt.datetime) -> list[Event]:
    """Pure: parse an ICS blob and return events occurring in [start, end]."""
    cal = icalendar.Calendar.from_ical(raw)
    events: list[Event] = []
    for e in recurring_ical_events.of(cal).between(start, end):
        s = _as_dt(e["DTSTART"].dt)
        end_prop = e.get("DTEND")
        en = _as_dt(end_prop.dt) if end_prop is not None else s
        events.append(Event(s, en, str(e.get("SUMMARY", "")), str(e.get("LOCATION", ""))))
    events.sort(key=lambda ev: ev.start)
    return events


class Calendar:
    def __init__(self, url: str | None, client: httpx.AsyncClient, ttl_s: int = 300):
        self._url = url
        self._client = client
        self._ttl = ttl_s
        self._cache: tuple[float, bytes] | None = None

    async def _raw(self) -> bytes | None:
        if not self._url:
            return None
        now = time.monotonic()
        if self._cache and now - self._cache[0] < self._ttl:
            return self._cache[1]
        resp = await self._client.get(self._url, timeout=30)
        resp.raise_for_status()
        self._cache = (now, resp.content)
        return resp.content

    async def between(self, start: dt.datetime, end: dt.datetime) -> list[Event]:
        raw = await self._raw()
        if raw is None:
            return []
        return expand(raw, start, end)

    async def upcoming(self, days: int) -> list[Event]:
        start = dt.datetime.now(TZ)
        return await self.between(start, start + dt.timedelta(days=days))
