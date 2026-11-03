import datetime as dt
from zoneinfo import ZoneInfo

import icalendar

from assistant.mail import build_invite, parse_message

TZ = ZoneInfo("Europe/Zurich")


def test_build_invite_is_a_request_with_organizer_and_attendee():
    start = dt.datetime(2026, 11, 3, 9, 0, tzinfo=TZ)
    end = dt.datetime(2026, 11, 3, 11, 0, tzinfo=TZ)
    raw = build_invite(
        "deep work", start, end, "assistant@example.com", "leo@example.com", "u1@example.com", "thesis"
    )

    cal = icalendar.Calendar.from_ical(raw)
    assert cal["method"] == "REQUEST"  # Outlook renders REQUEST as an invite
    (ev,) = cal.walk("VEVENT")
    assert str(ev["summary"]) == "deep work"
    assert ev["uid"] == "u1@example.com"
    assert str(ev["organizer"]) == "mailto:assistant@example.com"
    assert str(ev["organizer"].params["cn"]) == "Leo's AI assistant"  # shown as organizer
    assert str(ev["attendee"]) == "mailto:leo@example.com"
    assert ev["dtstart"].dt.hour == 9
    assert str(ev["transp"]) == "OPAQUE"  # default: busy


def test_build_invite_recurring():
    s = dt.datetime(2026, 11, 6, 9, tzinfo=TZ)
    e = dt.datetime(2026, 11, 6, 10, tzinfo=TZ)
    cal = icalendar.Calendar.from_ical(
        build_invite(
            "weekly sync", s, e, "a@example.com", "l@example.com", "u@example.com",
            rrule="FREQ=WEEKLY;BYDAY=FR;COUNT=12",
        )
    )
    ev = cal.walk("VEVENT")[0]
    assert ev["rrule"]["FREQ"] == ["WEEKLY"]
    assert ev["rrule"]["COUNT"] == [12]


def test_build_invite_cancel():
    s = dt.datetime(2026, 11, 3, 9, tzinfo=TZ)
    e = dt.datetime(2026, 11, 3, 10, tzinfo=TZ)
    cal = icalendar.Calendar.from_ical(
        build_invite("x", s, e, "a@example.com", "l@example.com", "u@example.com", sequence=1, method="CANCEL")
    )
    assert cal["method"] == "CANCEL"
    ev = cal.walk("VEVENT")[0]
    assert str(ev["status"]) == "CANCELLED"
    assert int(ev["sequence"]) == 1  # higher sequence => Outlook supersedes the original
    assert ev["uid"] == "u@example.com"  # same uid is what ties it to the sent invite


def test_build_invite_free_busy():
    s = dt.datetime(2026, 11, 3, 9, tzinfo=TZ)
    e = dt.datetime(2026, 11, 3, 10, tzinfo=TZ)
    free = icalendar.Calendar.from_ical(
        build_invite("x", s, e, "a@example.com", "l@example.com", "u@example.com", show_as="free")
    ).walk("VEVENT")[0]
    assert str(free["transp"]) == "TRANSPARENT"  # free = doesn't block time
    assert str(free["x-microsoft-cdo-intendedstatus"]) == "FREE"

    busy = icalendar.Calendar.from_ical(
        build_invite("x", s, e, "a@example.com", "l@example.com", "u@example.com", show_as="busy")
    ).walk("VEVENT")[0]
    assert str(busy["transp"]) == "OPAQUE"
    assert str(busy["x-microsoft-cdo-intendedstatus"]) == "BUSY"


def test_parse_message_decodes_headers_and_body():
    # Subject is RFC 2047 encoded ("Grüezi") to prove header decoding.
    raw = (
        b"From: Boss <boss@example.com>\r\n"
        b"Subject: =?utf-8?b?R3LDvGV6aQ==?=\r\n"
        b"Date: Mon, 03 Nov 2026 10:00:00 +0100\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"\r\n"
        b"Accepted: thesis block.\r\n"
    )
    m = parse_message(raw)
    assert m["from"] == "Boss <boss@example.com>"
    assert m["subject"] == "Grüezi"
    assert "Accepted: thesis block." in m["snippet"]
