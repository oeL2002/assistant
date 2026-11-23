"""Publish the assistant's planning layer as an ICS feed Leo subscribes to.

`render_plan_ics` is pure (rows -> bytes). `serve` runs a tiny read-only HTTP
server in a background thread; it is reachable only over the tailnet, because
the NixOS module opens the port on the `tailscale0` interface alone. No auth:
the tailnet is the trust boundary, and a calendar subscription can't send
credentials anyway.
"""

from __future__ import annotations

import http.server
import sqlite3
import threading
from pathlib import Path

import icalendar

from . import db
from .calendar import parse_local

PLAN_ICS_PORT = 8737  # must match the firewall line in the NixOS assistant module


def plan_uid(event_id: int) -> str:
    return f"plan-{event_id}@assistant.invalid"


def plan_id(uid: str) -> int | None:
    """Inverse of plan_uid; None for anything else."""
    if uid.startswith("plan-") and uid.endswith("@assistant.invalid"):
        return int(uid[5 : -len("@assistant.invalid")])
    return None


def render_plan_ics(
    rows: list[sqlite3.Row], overrides: dict[int, list[sqlite3.Row]] | None = None
) -> bytes:
    """Rows (+ per-occurrence overrides of recurring rows) -> ICS. A recurring
    block is one VEVENT with an RRULE; a cancelled occurrence is an EXDATE on it;
    a moved/renamed one is a second VEVENT with the same UID and a RECURRENCE-ID
    naming the occurrence it replaces. That is the standard shape, so phones and
    recurring_ical_events both read it."""
    overrides = overrides or {}
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Leo's assistant//plan//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Assistant plan")
    for r in rows:
        ev = icalendar.Event()
        ev.add("uid", plan_uid(r["id"]))
        ev.add("summary", r["summary"])
        ev.add("dtstart", parse_local(r["start_at"]))
        ev.add("dtend", parse_local(r["end_at"]))
        if r["notes"]:
            ev.add("description", r["notes"])
        if r["rrule"]:
            ev.add("rrule", icalendar.vRecur.from_ical(r["rrule"]))
        own = overrides.get(r["id"], []) if r["rrule"] else []
        exdates = [parse_local(o["occurrence"]) for o in own if o["cancelled"]]
        if exdates:
            ev.add("exdate", exdates)
        cal.add_component(ev)
        for o in own:
            if o["cancelled"]:
                continue
            moved = icalendar.Event()
            moved.add("uid", plan_uid(r["id"]))
            moved.add("recurrence-id", parse_local(o["occurrence"]))
            moved.add("summary", o["summary"] or r["summary"])
            moved.add("dtstart", parse_local(o["start_at"]))
            moved.add("dtend", parse_local(o["end_at"]))
            if r["notes"]:
                moved.add("description", r["notes"])
            cal.add_component(moved)
    return cal.to_ical()


def plan_ics(conn: sqlite3.Connection) -> bytes:
    """The whole plan layer as ICS, overrides applied."""
    rows = db.list_events(conn)
    overrides = {r["id"]: db.list_overrides(conn, "plan", r["id"]) for r in rows if r["rrule"]}
    return render_plan_ics(rows, overrides)


class _Handler(http.server.BaseHTTPRequestHandler):
    db_path = ""  # set on the subclass in serve()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/plan.ics":
            self.send_error(404)
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            body = plan_ics(conn)
        finally:
            conn.close()
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:  # don't spam the journal per poll
        pass


def serve(db_path: Path, port: int = PLAN_ICS_PORT) -> None:
    """Start the ICS server in a daemon thread. Binds all interfaces; the
    firewall restricts reachability to tailscale0."""
    handler = type("Handler", (_Handler,), {"db_path": str(db_path)})
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
