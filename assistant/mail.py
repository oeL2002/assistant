"""Send a time block to Leo's WORK calendar as an email invite (iMIP).

The assistant is the ORGANIZER and Leo the ATTENDEE; Outlook reads the
`METHOD:REQUEST` calendar part natively and shows Accept/Decline, so accepting
puts the block on the work calendar. The assistant never logs into that
system — this is plain outbound mail from its own Migadu mailbox. The email's
From must equal the ORGANIZER or Outlook distrusts the invite.

Reusing a UID with a higher SEQUENCE would later update (REQUEST) or cancel
(CANCEL) a block already sent; v1 only creates.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import imaplib
import smtplib
import ssl
import uuid
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr

import icalendar

from .calendar import TZ
from .config import Config

# How the block shows on the calendar. TRANSP is the RFC 5545 standard (OPAQUE =
# blocks time = busy; TRANSPARENT = free); the X-MICROSOFT-CDO-* values give
# Outlook the finer Free/Tentative/Busy/OutOfOffice "Show As".
_SHOW_AS = {
    "busy": ("OPAQUE", "BUSY"),
    "free": ("TRANSPARENT", "FREE"),
    "tentative": ("OPAQUE", "TENTATIVE"),
    "outofoffice": ("OPAQUE", "OOF"),
}


def build_invite(
    summary: str,
    start: dt.datetime,
    end: dt.datetime,
    organizer: str,
    attendee: str,
    uid: str,
    notes: str | None = None,
    sequence: int = 0,
    method: str = "REQUEST",
    show_as: str = "busy",
    rrule: str | None = None,
) -> bytes:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Leo's assistant//invite//EN")
    cal.add("version", "2.0")
    cal.add("method", method)

    ev = icalendar.Event()
    ev.add("uid", uid)
    ev.add("summary", summary)
    ev.add("dtstart", start)
    ev.add("dtend", end)
    ev.add("dtstamp", dt.datetime.now(TZ))
    ev.add("sequence", sequence)
    if notes:
        ev.add("description", notes)
    if method == "CANCEL":
        ev.add("status", "CANCELLED")
    if rrule:
        ev.add("rrule", icalendar.vRecur.from_ical(rrule))  # recurring series

    transp, cdo = _SHOW_AS.get(show_as, _SHOW_AS["busy"])
    ev.add("transp", transp)
    # INTENDEDSTATUS sets how it shows on the attendee's (Leo's) calendar;
    # BUSYSTATUS is the organizer's own. Set both so it's consistent.
    ev.add("x-microsoft-cdo-intendedstatus", cdo)
    ev.add("x-microsoft-cdo-busystatus", cdo)

    org = icalendar.vCalAddress(f"mailto:{organizer}")
    org.params["cn"] = icalendar.vText("Leo's AI assistant")  # shown as the organizer
    ev["organizer"] = org

    att = icalendar.vCalAddress(f"mailto:{attendee}")
    att.params["role"] = icalendar.vText("REQ-PARTICIPANT")
    att.params["partstat"] = icalendar.vText("NEEDS-ACTION")
    att.params["rsvp"] = icalendar.vText("TRUE")
    ev.add("attendee", att, encode=0)

    cal.add_component(ev)
    return cal.to_ical()


def normalize_rrule(rrule: str) -> str:
    """Validate an RRULE string (raises ValueError if malformed); returns it as-is."""
    icalendar.vRecur.from_ical(rrule)
    return rrule


def _send_sync(cfg: Config, msg: EmailMessage) -> None:
    with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context()) as s:
        s.login(cfg.invite_from, cfg.smtp_password)
        s.send_message(msg)


async def send_invite(
    cfg: Config,
    summary: str,
    start: dt.datetime,
    end: dt.datetime,
    notes: str | None = None,
    show_as: str = "busy",
    uid: str | None = None,
    sequence: int = 0,
    method: str = "REQUEST",
    rrule: str | None = None,
) -> str:
    """Send an invite and return its UID. New invite: omit uid (one is generated).
    Update: pass the original uid with a higher sequence. Cancel: method='CANCEL'.
    rrule (an RRULE string) makes it a recurring series. Blocking SMTP runs off-loop."""
    if uid is None:
        domain = cfg.invite_from.split("@")[-1] if cfg.invite_from else "localhost"
        uid = f"{uuid.uuid4()}@{domain}"
    ics = build_invite(
        summary, start, end, cfg.invite_from, cfg.invite_to, uid, notes, sequence, method,
        show_as, rrule,
    )

    msg = EmailMessage()
    msg["From"] = formataddr(("Leo's AI assistant", cfg.invite_from))
    msg["To"] = cfg.invite_to
    msg["Subject"] = ("Cancelled: " if method == "CANCEL" else "") + summary
    verb = "Cancelled" if method == "CANCEL" else "Calendar invite"
    msg.set_content(f"{verb}: {summary}\n{start:%a %d %b %H:%M}–{end:%H:%M}")
    msg.add_alternative(
        ics.decode("utf-8"), subtype="calendar", charset="utf-8", params={"method": method}
    )

    await asyncio.to_thread(_send_sync, cfg, msg)
    return uid


# --- receiving (read the assistant's own mailbox; never sends, never deletes) ---


def parse_message(raw: bytes, snippet_len: int = 800) -> dict:
    """Pure: raw RFC822 bytes -> from/subject/date/snippet. Headers decoded; the
    snippet is the plain-text body (or HTML if that's all there is), truncated."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    body = msg.get_body(preferencelist=("plain", "html"))
    snippet = body.get_content().strip()[:snippet_len] if body is not None else ""
    return {
        "from": str(msg["From"] or ""),
        "subject": str(msg["Subject"] or ""),
        "date": str(msg["Date"] or ""),
        "snippet": snippet,
    }


def fetch_recent(cfg: Config, limit: int = 10) -> list[dict]:
    """The newest `limit` messages in INBOX, newest first. readonly + BODY.PEEK
    so reading never marks anything seen."""
    with imaplib.IMAP4_SSL(cfg.imap_host) as m:
        m.login(cfg.invite_from, cfg.smtp_password)
        m.select("INBOX", readonly=True)
        _, data = m.search(None, "ALL")
        ids = data[0].split()
        out = []
        for i in reversed(ids[-limit:]):
            _, msgdata = m.fetch(i, "(BODY.PEEK[])")
            out.append(parse_message(msgdata[0][1]))
        return out
