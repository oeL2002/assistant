"""Recurring plan blocks with single-occurrence overrides, through the tools the
agent calls."""

import asyncio
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant import tools
from assistant.calendar import expand
from assistant.db import connect
from assistant.publish import plan_ics

TZ = ZoneInfo("Europe/Zurich")


class _Cfg:
    files_dir = Path("/nonexistent")


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "t.db")
    tools.init(c, calendar=None, cfg=_Cfg())
    return c


def call(name: str, **args) -> str:
    return asyncio.run(getattr(tools, name).handler(args))["content"][0]["text"]


def week(conn, day="2026-11-23"):
    start = dt.datetime.fromisoformat(day).replace(tzinfo=TZ)
    return [(e.start, e.summary) for e in expand(plan_ics(conn), start, start + dt.timedelta(days=14))]


def test_weekly_gym_moved_once_and_cancelled_once(conn):
    # Gym every Tuesday 18-19, from 24 Nov.
    assert "Scheduled #1" in call(
        "schedule_block", summary="gym", start="2026-11-24T18:00", end="2026-11-24T19:00",
        recurrence="FREQ=WEEKLY;BYDAY=TU",
    )
    # This week only: Wednesday instead.
    out = call("update_block", id=1, occurrence="2026-11-24", start="2026-11-25T07:00")
    assert "this occurrence only" in out
    # Next week: skip.
    assert "only" in call("unschedule", id=1, occurrence="2026-12-01")

    got = week(conn)
    assert (dt.datetime(2026, 11, 25, 7, tzinfo=TZ), "gym") in got  # moved, kept its length
    assert all(s.date() != dt.date(2026, 11, 24) for s, _ in got)  # Tuesday gone
    assert all(s.date() != dt.date(2026, 12, 1) for s, _ in got)  # cancelled
    assert len(got) == 1  # 24 Nov -> 25 Nov; 1 Dec cancelled; window ends 7 Dec

    # list_plan shows the occurrence the agent must name to change it again
    listing = call("list_plan", start="2026-11-23", days=14)
    assert "#1 Wed 25 Nov 07:00–08:00  gym  [recurring, occurrence 2026-11-24]" in listing


def test_moving_the_same_occurrence_twice_keeps_the_last(conn):
    call("schedule_block", summary="gym", start="2026-11-24T18:00", end="2026-11-24T19:00",
         recurrence="FREQ=WEEKLY")
    call("update_block", id=1, occurrence="2026-11-24", start="2026-11-25T07:00")
    call("update_block", id=1, occurrence="2026-11-25", start="2026-11-26T07:00")  # by current date
    starts = [s for s, _ in week(conn, "2026-11-23")]
    assert dt.datetime(2026, 11, 26, 7, tzinfo=TZ) in starts
    assert dt.datetime(2026, 11, 25, 7, tzinfo=TZ) not in starts


def test_series_edit_and_one_off_move_keep_the_id(conn):
    call("schedule_block", summary="thesis", start="2026-11-24T09:00", end="2026-11-24T11:00")
    assert "Updated #1" in call("update_block", id=1, start="2026-11-24T13:00")
    assert week(conn) == [(dt.datetime(2026, 11, 24, 13, tzinfo=TZ), "thesis")]  # 2h kept
    assert "isn't recurring" in call("update_block", id=1, occurrence="2026-11-24")


def test_unschedule_many_at_once(conn):
    for d in (24, 25, 26):
        call("schedule_block", summary="x", start=f"2026-11-{d}T09:00", end=f"2026-11-{d}T10:00")
    assert call("unschedule", ids=[1, 3, 9]) == "Removed #1, #3. Not found: [9]."
    assert len(week(conn)) == 1


def test_recurring_series_keeps_wall_clock_across_dst(conn):
    # Weekly 18:00 from October (CEST) into November (CET): still 18:00 local.
    call("schedule_block", summary="club", start="2026-10-22T18:00", end="2026-10-22T19:00",
         recurrence="FREQ=WEEKLY")
    starts = [s for s, _ in week(conn, "2026-10-20")]
    assert [s.hour for s in starts] == [18, 18]


def test_tz_for_travel(conn):
    call("schedule_block", summary="talk", start="2026-05-21T10:00", end="2026-05-21T11:00",
         tz="America/Toronto")
    (start, _), = week(conn, "2026-05-18")
    assert start == dt.datetime(2026, 5, 21, 16, tzinfo=TZ)  # 10:00 Toronto = 16:00 Zurich
    assert "Thu 21 May 10:00–11:00" in call("list_plan", start="2026-05-18", tz="America/Toronto")
