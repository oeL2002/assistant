import datetime as dt
from zoneinfo import ZoneInfo

from assistant.calendar import expand, overlaps

UTC = ZoneInfo("UTC")


def test_overlaps_window():
    ws = dt.datetime(2027, 2, 1, tzinfo=UTC)
    we = dt.datetime(2027, 2, 28, tzinfo=UTC)
    assert overlaps("2027-02-10T09:00+00:00", "2027-02-10T10:00+00:00", ws, we)  # inside
    assert not overlaps("2027-01-01T09:00+00:00", "2027-01-01T10:00+00:00", ws, we)  # before
    assert not overlaps("2027-03-05T09:00+00:00", "2027-03-05T10:00+00:00", ws, we)  # after
    assert overlaps("2027-01-31T23:00+00:00", "2027-02-01T01:00+00:00", ws, we)  # straddles start

ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:x@test
DTSTART:20300101T100000Z
DTEND:20300101T110000Z
SUMMARY:Test event
LOCATION:Room 1
END:VEVENT
END:VCALENDAR
"""


def test_expand_in_window():
    events = expand(ICS, dt.datetime(2030, 1, 1, tzinfo=UTC), dt.datetime(2030, 1, 2, tzinfo=UTC))
    assert len(events) == 1
    assert events[0].summary == "Test event"
    assert events[0].location == "Room 1"


def test_expand_outside_window_is_empty():
    events = expand(ICS, dt.datetime(2031, 1, 1, tzinfo=UTC), dt.datetime(2031, 1, 2, tzinfo=UTC))
    assert events == []
