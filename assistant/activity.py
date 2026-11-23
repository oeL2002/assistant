"""Statistics over the activity log. Pure: rows in, text out."""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections import defaultdict

from .calendar import parse_local


def _clip(a: dt.datetime, b: dt.datetime, lo: dt.datetime, hi: dt.datetime) -> float:
    """Hours of [a, b] that fall inside [lo, hi]."""
    return max(0.0, (min(b, hi) - max(a, lo)).total_seconds() / 3600)


def _clock(minutes: float) -> str:
    m = round(minutes) % (24 * 60)
    return f"{m // 60:02d}:{m % 60:02d}"


def _mean_clock(times: list[dt.datetime]) -> str:
    """Average clock time, counted from noon so 23:30 and 00:30 average to
    midnight rather than to noon."""
    noon = [((t.hour * 60 + t.minute) - 12 * 60) % (24 * 60) for t in times]
    return _clock(sum(noon) / len(noon) + 12 * 60)


def stats(rows: list[sqlite3.Row], start: dt.datetime, end: dt.datetime) -> str:
    days = max(1.0, (end - start).total_seconds() / 86400)
    hours: dict[str, float] = defaultdict(float)
    count: dict[str, int] = defaultdict(int)
    sleeps: list[tuple[dt.datetime, dt.datetime]] = []
    for r in rows:
        a, b = parse_local(r["start_at"]), parse_local(r["end_at"])
        h = _clip(a, b, start, end)
        if h <= 0:
            continue
        hours[r["kind"]] += h
        count[r["kind"]] += 1
        if r["kind"] == "sleep":
            sleeps.append((a, b))
    if not hours:
        return "Nothing logged in that window."
    lines = [f"{start:%a %d %b} – {end:%a %d %b} ({days:.0f} days):"]
    for kind in sorted(hours, key=hours.get, reverse=True):
        lines.append(
            f"- {kind}: {hours[kind]:.1f} h total, {hours[kind] / days:.1f} h/day, {count[kind]}×"
        )
    if sleeps:
        lines.append(
            f"- sleep times: to bed ~{_mean_clock([a for a, _ in sleeps])}, "
            f"up ~{_mean_clock([b for _, b in sleeps])} on average"
        )
    return "\n".join(lines)
