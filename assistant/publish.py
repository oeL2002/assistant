"""The assistant's small HTTP server: the planning layer as an ICS feed Leo
subscribes to, and the gym page (gym_page.py) with its JSON API.

`render_plan_ics` is pure (rows -> bytes). `serve` runs the server in a
background thread; it is reachable only over the tailnet, because the NixOS
module opens the port on the `tailscale0` interface alone. No auth: the tailnet
is the trust boundary, as for the calendar feed, which can't send credentials
anyway. The gym API only adds and deletes sets and adds exercises.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import http.server
import json
import sqlite3
import threading
import urllib.parse
from pathlib import Path

import icalendar

from . import db, gym
from .calendar import parse_local
from .gym_page import PAGE

PLAN_ICS_PORT = 8737  # must match the firewall line in the NixOS assistant module
_MAX_BODY = 4096  # the gym API's requests are a few fields


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
    """plan.ics (read-only) and the gym page with its small JSON API. Every
    request opens its own connection: this runs in server threads, beside the
    daemon's connection; WAL lets them share the file."""

    db_path = ""  # set on the subclass in serve()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: object) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/plan.ics":
            with contextlib.closing(self._conn()) as conn:
                self._send(200, plan_ics(conn), "text/calendar; charset=utf-8")
        elif url.path == "/gym":
            self.send_response(301)  # the page's relative API paths need the slash
            self.send_header("Location", "/gym/")
            self.end_headers()
        elif url.path == "/gym/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif url.path == "/gym/state":
            date = urllib.parse.parse_qs(url.query).get("date", [""])[0]
            try:
                dt.date.fromisoformat(date)
            except ValueError:
                self._json(400, {"error": "date must be YYYY-MM-DD"})
                return
            with contextlib.closing(self._conn()) as conn:
                self._json(200, gym.state(conn, date))
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > _MAX_BODY:
            self._json(413, {"error": "too large"})
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise TypeError
        except (TypeError, ValueError):
            self._json(400, {"error": "body must be a JSON object"})
            return
        handler = {
            "/gym/sets": self._add_set,
            "/gym/sets/delete": self._delete_set,
            "/gym/exercises": self._add_exercise,
        }.get(urllib.parse.urlsplit(self.path).path)
        if handler is None:
            self.send_error(404)
            return
        with contextlib.closing(self._conn()) as conn:
            try:
                handler(conn, body)
            except (KeyError, TypeError, ValueError):
                self._json(400, {"error": "bad request"})

    def _add_set(self, conn: sqlite3.Connection, b: dict) -> None:
        dt.date.fromisoformat(b["date"])
        cid, ex_id = str(b["client_id"])[:64], int(b["exercise_id"])
        weight, reps = float(b["weight_kg"]), int(b["reps"])
        if not cid or not (1 <= reps <= 1000) or not (-500 <= weight <= 1000):
            raise ValueError
        if conn.execute("SELECT 1 FROM exercises WHERE id = ?", (ex_id,)).fetchone() is None:
            raise ValueError
        row = db.add_set(conn, cid, b["date"], ex_id, weight, reps)
        self._json(200, {k: row[k] for k in ("id", "client_id", "exercise_id", "weight_kg", "reps")})

    def _delete_set(self, conn: sqlite3.Connection, b: dict) -> None:
        self._json(200, {"deleted": db.delete_set(conn, int(b["id"]))})

    def _add_exercise(self, conn: sqlite3.Connection, b: dict) -> None:
        name = str(b["name"]).strip()[:80]
        if not name:
            raise ValueError
        self._json(200, {"id": db.save_exercise(conn, name, {})})

    def log_message(self, *_args) -> None:  # don't spam the journal per poll
        pass


def serve(db_path: Path, port: int = PLAN_ICS_PORT) -> None:
    """Start the HTTP server in a daemon thread. Binds all interfaces; the
    firewall restricts reachability to tailscale0."""
    handler = type("Handler", (_Handler,), {"db_path": str(db_path)})
    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
