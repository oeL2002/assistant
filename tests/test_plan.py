import icalendar

from assistant.db import add_event, connect, delete_event, list_events
from assistant.publish import render_plan_ics


def test_plan_crud(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = add_event(conn, "deep work", "2026-11-03T09:00+01:00", "2026-11-03T11:00+01:00", "thesis")
    add_event(conn, "gym", "2026-11-02T18:00+01:00", "2026-11-02T19:00+01:00")

    rows = list_events(conn)
    assert [r["summary"] for r in rows] == ["gym", "deep work"]  # earliest first

    assert delete_event(conn, a)
    assert [r["summary"] for r in list_events(conn)] == ["gym"]
    assert not delete_event(conn, 999)  # unknown id is a no-op


def test_render_plan_ics_roundtrips(tmp_path):
    conn = connect(tmp_path / "t.db")
    add_event(conn, "deep work", "2026-11-03T09:00+01:00", "2026-11-03T11:00+01:00", "thesis")

    cal = icalendar.Calendar.from_ical(render_plan_ics(list_events(conn)))
    events = [c for c in cal.walk("VEVENT")]
    assert len(events) == 1
    assert str(events[0]["SUMMARY"]) == "deep work"
    assert str(events[0]["DESCRIPTION"]) == "thesis"
    assert events[0]["DTSTART"].dt.hour == 9
