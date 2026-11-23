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
import re
import smtplib
import ssl
import uuid
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formataddr
from html.parser import HTMLParser
from pathlib import Path

import icalendar

from . import files as _files
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
    recurrence_id: dt.datetime | None = None,
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
    if recurrence_id is not None:
        # One occurrence of a series (same UID): moves or cancels just that one.
        ev.add("recurrence-id", recurrence_id)
    elif rrule:
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
    recurrence_id: dt.datetime | None = None,
) -> str:
    """Send an invite and return its UID. New invite: omit uid (one is generated).
    Update: pass the original uid with a higher sequence. Cancel: method='CANCEL'.
    rrule (an RRULE string) makes it a recurring series. Blocking SMTP runs off-loop."""
    if uid is None:
        domain = cfg.invite_from.split("@")[-1] if cfg.invite_from else "localhost"
        uid = f"{uuid.uuid4()}@{domain}"
    ics = build_invite(
        summary, start, end, cfg.invite_from, cfg.invite_to, uid, notes, sequence, method,
        show_as, rrule, recurrence_id,
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
#
# Leo forwards mail here to have it read. A forward arrives one of two ways: the
# original quoted inline in the body, or attached whole as message/rfc822
# (Outlook's "forward as attachment"). read_full handles both; file attachments
# are saved to the drive and read by the agent natively.

class _HTMLText(HTMLParser):
    """Visible text of an HTML body; enough for HTML-only mail."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in ("br", "p", "div", "tr", "li"):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def _html_text(html: str) -> str:
    p = _HTMLText()
    p.feed(html)
    return re.sub(r"\n\s*\n+", "\n\n", "".join(p.parts)).strip()


def _body_text(msg: EmailMessage) -> str:
    body = msg.get_body(preferencelist=("plain", "html"))
    if body is None:
        return ""
    text = body.get_content()
    return _html_text(text) if body.get_content_type() == "text/html" else text.strip()


def _attachments(msg: EmailMessage) -> list[EmailMessage]:
    """Attachments, plus attached messages (forward-as-attachment) even when
    they are marked inline. The chosen body part is never one of them."""
    body = msg.get_body(preferencelist=("plain", "html"))
    out = list(msg.iter_attachments())
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822" and part not in out:
            out.append(part)
    return [p for p in out if p is not body]


def _name(part: EmailMessage) -> str:
    return part.get_filename() or part.get_content_type()


def parse_message(raw: bytes, snippet_len: int = 800) -> dict:
    """Pure: raw RFC822 bytes -> from/subject/date/snippet/attachment names.
    Headers decoded; the snippet is the body as text, truncated."""
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    return {
        "from": str(msg["From"] or ""),
        "subject": str(msg["Subject"] or ""),
        "date": str(msg["Date"] or ""),
        "snippet": _body_text(msg)[:snippet_len],
        "attachments": [_name(p) for p in _attachments(msg)],
    }


def _render(msg: EmailMessage, save_to: Path | None, depth: int = 0) -> str:
    """Headers + body as text. Attachments are saved under `save_to` for the
    agent to open with its own Read tool (Claude reads PDFs and images natively,
    layout included); attached messages are rendered inline, recursively."""
    head = f"From: {msg['From']}\nTo: {msg['To']}\nDate: {msg['Date']}\nSubject: {msg['Subject']}"
    parts = [head, _body_text(msg)]
    for part in _attachments(msg) if depth < 3 else []:  # stop a crafted nesting loop
        if part.get_content_type() == "message/rfc822":
            inner = part.get_payload()[0] if part.is_multipart() else part.get_content()
            parts.append(f"[attached message]\n{_render(inner, save_to, depth + 1)}")
            continue
        data = part.get_payload(decode=True) or b""
        if save_to is None:
            parts.append(f"[attachment {_name(part)}, {len(data)} bytes]")
            continue
        dest = save_to / _files.safe_name(_name(part))
        if not dest.exists():  # reading the same mail twice doesn't duplicate files
            save_to.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        parts.append(f"[attachment saved: {dest.resolve()} ({part.get_content_type()}, {len(data)} bytes)]")
    return "\n\n".join(p for p in parts if p)


def read_full(raw: bytes, save_to: Path | None = None, max_chars: int = 40000) -> str:
    """One message as text: headers, body, attached messages, and the paths its
    attachments were saved to (or just their names when save_to is None)."""
    text = _render(BytesParser(policy=policy.default).parsebytes(raw), save_to)
    if len(text) > max_chars:
        return f"{text[:max_chars]}\n…(truncated; {len(text)} chars total)"
    return text


def _open(cfg: Config) -> imaplib.IMAP4_SSL:
    m = imaplib.IMAP4_SSL(cfg.imap_host)
    m.login(cfg.invite_from, cfg.smtp_password)
    m.select("INBOX", readonly=True)
    return m


def fetch_recent(cfg: Config, limit: int = 10) -> list[dict]:
    """The newest `limit` messages in INBOX, newest first, each with its IMAP UID
    (stable across sessions, unlike a sequence number). readonly + BODY.PEEK so
    reading never marks anything seen."""
    with _open(cfg) as m:
        _, data = m.uid("SEARCH", None, "ALL")
        uids = data[0].split()
        out = []
        for uid in reversed(uids[-limit:]):
            _, msgdata = m.uid("FETCH", uid, "(BODY.PEEK[])")
            out.append({"uid": uid.decode(), **parse_message(msgdata[0][1])})
        return out


def fetch_one(cfg: Config, uid: str) -> bytes | None:
    """Raw bytes of the message with this UID, or None if there is none."""
    if not uid.isdigit():
        return None
    with _open(cfg) as m:
        _, msgdata = m.uid("FETCH", uid, "(BODY.PEEK[])")
        if not msgdata or not isinstance(msgdata[0], tuple):
            return None
        return msgdata[0][1]
