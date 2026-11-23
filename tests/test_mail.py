import datetime as dt
from zoneinfo import ZoneInfo

import icalendar

from assistant.mail import build_invite, parse_message, read_full

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


def _pdf(text: str) -> bytes:
    """A minimal valid one-page PDF whose page draws `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _forward_with_pdf() -> bytes:
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "Leo <leo@example.com>"
    msg["Subject"] = "Fwd: Programm"
    msg.set_content("---------- Forwarded message ----------\nFrom: Club\nSee the programme.")
    msg.add_attachment(_pdf("Meeting on 4 December"), maintype="application",
                       subtype="pdf", filename="programm.pdf")
    return msg.as_bytes()


def test_parse_message_lists_attachments():
    m = parse_message(_forward_with_pdf())
    assert m["attachments"] == ["programm.pdf"]
    assert "See the programme." in m["snippet"]


def test_read_full_saves_attachments_for_native_reading(tmp_path):
    out = read_full(_forward_with_pdf(), tmp_path / "mail" / "7")
    assert "Subject: Fwd: Programm" in out
    assert "See the programme." in out  # inline forward body
    saved = tmp_path / "mail" / "7" / "programm.pdf"
    assert f"[attachment saved: {saved.resolve()} (application/pdf" in out
    assert saved.read_bytes().startswith(b"%PDF")
    read_full(_forward_with_pdf(), tmp_path / "mail" / "7")  # read again: no programm-1.pdf
    assert [p.name for p in saved.parent.iterdir()] == ["programm.pdf"]


def test_read_full_without_drive_only_names_attachments():
    assert "[attachment programm.pdf," in read_full(_forward_with_pdf())


def test_attachment_name_cannot_escape(tmp_path):
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["Subject"] = "x"
    msg.set_content("x")
    msg.add_attachment(b"x", maintype="text", subtype="plain", filename="../../evil.txt")
    read_full(msg.as_bytes(), tmp_path / "d")
    assert (tmp_path / "d" / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()


def test_read_full_renders_forward_as_attachment():
    from email.message import EmailMessage

    inner = EmailMessage()
    inner["From"] = "HR <hr@example.com>"
    inner["Subject"] = "Hotel booking"
    inner.set_content("Room booked for 20-24 May.")
    outer = EmailMessage()
    outer["From"] = "Leo <leo@example.com>"
    outer["Subject"] = "FW: Hotel booking"
    outer.set_content("fyi")
    outer.add_attachment(inner)  # message/rfc822, Outlook's "forward as attachment"

    out = read_full(outer.as_bytes())
    assert "[attached message]" in out
    assert "Subject: Hotel booking" in out
    assert "Room booked for 20-24 May." in out


def test_html_only_body_becomes_text():
    raw = (
        b"From: a@example.com\r\nSubject: x\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
        b"<html><style>p{color:red}</style><p>Hello</p><p>World</p></html>"
    )
    out = read_full(raw)
    assert "Hello" in out and "World" in out
    assert "<p>" not in out and "color:red" not in out


def test_build_invite_single_occurrence_has_recurrence_id_and_no_rrule():
    occ = dt.datetime(2026, 11, 25, 8, tzinfo=TZ)
    cal = icalendar.Calendar.from_ical(
        build_invite(
            "Im Büro", occ, occ + dt.timedelta(hours=9), "a@example.com", "l@example.com",
            "u@example.com", sequence=3, method="CANCEL", rrule="FREQ=WEEKLY", recurrence_id=occ,
        )
    )
    ev = cal.walk("VEVENT")[0]
    assert ev["uid"] == "u@example.com"  # same series
    assert ev["recurrence-id"].dt == occ  # ...this one occurrence
    assert "rrule" not in ev
    assert cal["method"] == "CANCEL"
