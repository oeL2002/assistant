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


def render_plan_ics(rows: list[sqlite3.Row]) -> bytes:
    cal = icalendar.Calendar()
    cal.add("prodid", "-//Leo's assistant//plan//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", "Assistant plan")
    for r in rows:
        ev = icalendar.Event()
        ev.add("uid", f"plan-{r['id']}@assistant.invalid")
        ev.add("summary", r["summary"])
        ev.add("dtstart", parse_local(r["start_at"]))
        ev.add("dtend", parse_local(r["end_at"]))
        if r["notes"]:
            ev.add("description", r["notes"])
        cal.add_component(ev)
    return cal.to_ical()


class _Handler(http.server.BaseHTTPRequestHandler):
    db_path = ""  # set on the subclass in serve()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/plan.ics":
            self.send_error(404)
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            body = render_plan_ics(db.list_events(conn))
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
