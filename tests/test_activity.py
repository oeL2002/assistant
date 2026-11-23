import datetime as dt
from zoneinfo import ZoneInfo

from assistant.activity import stats
from assistant.db import add_activity, connect, list_activity

TZ = ZoneInfo("Europe/Zurich")


def test_stats_totals_and_sleep_times_across_midnight(tmp_path):
    conn = connect(tmp_path / "t.db")
    add_activity(conn, "Sleep", "2026-11-20T23:30+01:00", "2026-11-21T07:30+01:00")
    add_activity(conn, "sleep", "2026-11-22T00:30+01:00", "2026-11-22T08:30+01:00")
    add_activity(conn, "study", "2026-11-21T10:00+01:00", "2026-11-21T13:00+01:00")
    start = dt.datetime(2026, 11, 20, 12, tzinfo=TZ)
    out = stats(list_activity(conn), start, start + dt.timedelta(days=2))
    assert "sleep: 16.0 h total, 8.0 h/day, 2×" in out  # kind lower-cased on insert
    assert "study: 3.0 h total" in out
    assert "to bed ~00:00, up ~08:00" in out  # 23:30 and 00:30 average to midnight


def test_stats_clips_to_window(tmp_path):
    conn = connect(tmp_path / "t.db")
    add_activity(conn, "work", "2026-11-20T08:00+01:00", "2026-11-20T18:00+01:00")
    start = dt.datetime(2026, 11, 20, 12, tzinfo=TZ)
    assert "work: 6.0 h total" in stats(list_activity(conn), start, start + dt.timedelta(days=1))
