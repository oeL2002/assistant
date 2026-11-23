"""SQLite state. One file, stdlib only, no ORM. Add columns as needed (see
_ADDED_COLUMNS) — this is personal, single-writer, and small forever.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id      INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    chat_id INTEGER,
    role    TEXT NOT NULL,                    -- 'user' | 'assistant'
    text    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY,
    created      TEXT NOT NULL DEFAULT (datetime('now')),
    title        TEXT NOT NULL,
    project      TEXT,
    due          TEXT,                          -- ISO 8601, nullable
    scheduled    TEXT,                          -- ISO 8601, nullable
    estimate_min INTEGER,
    status       TEXT NOT NULL DEFAULT 'open',  -- open | done | dropped
    scope        TEXT NOT NULL DEFAULT 'personal'  -- personal | work
);

-- The assistant's own planning layer: the time blocks it schedules, published
-- as an ICS feed Leo subscribes to. Separate from the (read-only) work
-- calendar, which stays the source of truth for existing commitments.
CREATE TABLE IF NOT EXISTS plan_events (
    id       INTEGER PRIMARY KEY,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    summary  TEXT NOT NULL,
    start_at TEXT NOT NULL,   -- ISO 8601 with offset
    end_at   TEXT NOT NULL,   -- ISO 8601 with offset
    notes    TEXT
);

-- Durable facts the assistant chooses to remember about Leo, independent of the
-- conversation session (which the Agent SDK compacts over time). Injected into
-- the system prompt every turn, so they survive compaction and restarts.
CREATE TABLE IF NOT EXISTS memories (
    id      INTEGER PRIMARY KEY,
    created TEXT NOT NULL DEFAULT (datetime('now')),
    text    TEXT NOT NULL
);

-- Self-set triggers: the assistant schedules itself to message Leo later. The
-- daemon fires due ones through the brain. fire_at is UTC ISO 8601 so the due
-- check can't be fooled by a DST change.
CREATE TABLE IF NOT EXISTS reminders (
    id      INTEGER PRIMARY KEY,
    created TEXT NOT NULL DEFAULT (datetime('now')),
    fire_at TEXT NOT NULL,                  -- UTC ISO 8601
    prompt  TEXT NOT NULL,                  -- instruction to future-self
    chat_id INTEGER NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0
);

-- Work-calendar invites the assistant sent, so it can later UPDATE (same uid,
-- higher sequence) or CANCEL (METHOD:CANCEL) them. Without the stored uid an
-- invite can't be referenced again.
CREATE TABLE IF NOT EXISTS sent_invites (
    id       INTEGER PRIMARY KEY,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    uid      TEXT NOT NULL,
    summary  TEXT NOT NULL,
    start_at TEXT NOT NULL,
    end_at   TEXT NOT NULL,
    show_as  TEXT NOT NULL DEFAULT 'busy',
    rrule    TEXT,                          -- RRULE string if recurring, else NULL
    sequence INTEGER NOT NULL DEFAULT 0,
    status   TEXT NOT NULL DEFAULT 'sent'   -- sent | cancelled
);

-- One occurrence of a recurring series changed on its own: moved/renamed, or
-- cancelled. `occurrence` is that occurrence's ORIGINAL start (ISO with offset),
-- which is what iCalendar's RECURRENCE-ID / EXDATE name it by. `kind` says which
-- series table event_id points into.
CREATE TABLE IF NOT EXISTS overrides (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,                -- plan | invite
    event_id   INTEGER NOT NULL,
    occurrence TEXT NOT NULL,
    start_at   TEXT,                         -- NULL when cancelled
    end_at     TEXT,
    summary    TEXT,                         -- NULL = keep the series' summary
    cancelled  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (kind, event_id, occurrence)
);

-- Things to bring up when Leo next talks about a topic, not at a time. Pending
-- ones are listed in the system prompt; the agent clears one once acted on.
CREATE TABLE IF NOT EXISTS topic_triggers (
    id      INTEGER PRIMARY KEY,
    created TEXT NOT NULL DEFAULT (datetime('now')),
    topic   TEXT NOT NULL,
    prompt  TEXT NOT NULL,
    done    INTEGER NOT NULL DEFAULT 0
);

-- What Leo actually did (sleep, study, gym, ...), for plan-vs-actual and stats.
CREATE TABLE IF NOT EXISTS activity_log (
    id       INTEGER PRIMARY KEY,
    created  TEXT NOT NULL DEFAULT (datetime('now')),
    kind     TEXT NOT NULL,          -- lower-case: sleep, study, work, gym, ...
    start_at TEXT NOT NULL,          -- ISO 8601 with offset
    end_at   TEXT NOT NULL,
    note     TEXT
);
"""


# Columns added to a table after its first release. `CREATE TABLE IF NOT EXISTS`
# leaves a pre-existing table untouched, so a database created by an older
# version is missing them — patch them in on connect. Idempotent: each is added
# only if absent, so fresh databases (which already have them) are unaffected.
# ADD COLUMN with NOT NULL needs a DEFAULT, which these have.
_ADDED_COLUMNS = [
    ("sent_invites", "show_as", "TEXT NOT NULL DEFAULT 'busy'"),
    ("sent_invites", "rrule", "TEXT"),
    ("plan_events", "rrule", "TEXT"),
    # Fire only if Leo hasn't written since the reminder was set (a nudge that
    # is moot once he's replied, e.g. a wake-up check).
    ("reminders", "only_if_silent", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


# --- meta (small key/value: e.g. the Claude session id to resume) -----------


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def log_message(conn: sqlite3.Connection, chat_id: int, role: str, text: str) -> None:
    conn.execute(
        "INSERT INTO messages (chat_id, role, text) VALUES (?, ?, ?)",
        (chat_id, role, text),
    )
    conn.commit()


def recent_messages(conn: sqlite3.Connection, limit: int = 12) -> list[sqlite3.Row]:
    """The last `limit` messages, oldest first — the agent's short-term memory."""
    rows = conn.execute(
        "SELECT role, text FROM messages ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return list(reversed(rows))


# --- tasks -------------------------------------------------------------------

_UPDATABLE = {"title", "due", "scheduled", "project", "estimate_min", "scope", "status"}


def add_task(
    conn: sqlite3.Connection,
    title: str,
    due: str | None = None,
    project: str | None = None,
    estimate_min: int | None = None,
    scope: str = "personal",
) -> int:
    cur = conn.execute(
        "INSERT INTO tasks (title, due, project, estimate_min, scope) VALUES (?, ?, ?, ?, ?)",
        (title, due, project, estimate_min, scope),
    )
    conn.commit()
    return cur.lastrowid


def list_tasks(conn: sqlite3.Connection, status: str = "open") -> list[sqlite3.Row]:
    order = "ORDER BY (due IS NULL), due, id"
    if status == "all":
        return conn.execute(f"SELECT * FROM tasks {order}").fetchall()
    return conn.execute(f"SELECT * FROM tasks WHERE status = ? {order}", (status,)).fetchall()


def set_status(conn: sqlite3.Connection, task_id: int, status: str) -> bool:
    cur = conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
    conn.commit()
    return cur.rowcount > 0


def update_task(conn: sqlite3.Connection, task_id: int, fields: dict) -> bool:
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return False
    assignments = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE tasks SET {assignments} WHERE id = ?", (*fields.values(), task_id)
    )
    conn.commit()
    return cur.rowcount > 0


# --- plan events (the assistant's published calendar) ------------------------


def add_event(
    conn: sqlite3.Connection,
    summary: str,
    start_at: str,
    end_at: str,
    notes: str | None = None,
    rrule: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO plan_events (summary, start_at, end_at, notes, rrule) VALUES (?, ?, ?, ?, ?)",
        (summary, start_at, end_at, notes, rrule),
    )
    conn.commit()
    return cur.lastrowid


def get_event(conn: sqlite3.Connection, event_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM plan_events WHERE id = ?", (event_id,)).fetchone()


_EVENT_FIELDS = {"summary", "start_at", "end_at", "notes", "rrule"}


def update_event(conn: sqlite3.Connection, event_id: int, fields: dict) -> bool:
    """Edit a block (or a whole series) in place; the id stays the same."""
    fields = {k: v for k, v in fields.items() if k in _EVENT_FIELDS}
    if not fields:
        return False
    assignments = ", ".join(f"{k} = ?" for k in fields)
    cur = conn.execute(
        f"UPDATE plan_events SET {assignments} WHERE id = ?", (*fields.values(), event_id)
    )
    conn.commit()
    return cur.rowcount > 0


def list_events(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All planning blocks, earliest first — for the ICS feed and the plan view."""
    return conn.execute("SELECT * FROM plan_events ORDER BY start_at, id").fetchall()


def delete_event(conn: sqlite3.Connection, event_id: int) -> bool:
    cur = conn.execute("DELETE FROM plan_events WHERE id = ?", (event_id,))
    conn.execute("DELETE FROM overrides WHERE kind = 'plan' AND event_id = ?", (event_id,))
    conn.commit()
    return cur.rowcount > 0


# --- per-occurrence overrides of recurring series ----------------------------


def set_override(
    conn: sqlite3.Connection,
    kind: str,
    event_id: int,
    occurrence: str,
    start_at: str | None = None,
    end_at: str | None = None,
    summary: str | None = None,
    cancelled: bool = False,
) -> None:
    """Move/rename or cancel one occurrence. A later call for the same occurrence
    replaces the earlier one (move it twice and only the last move counts)."""
    conn.execute(
        "INSERT INTO overrides (kind, event_id, occurrence, start_at, end_at, summary, cancelled) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(kind, event_id, occurrence) DO UPDATE SET "
        "start_at = excluded.start_at, end_at = excluded.end_at, summary = excluded.summary, "
        "cancelled = excluded.cancelled",
        (kind, event_id, occurrence, start_at, end_at, summary, int(cancelled)),
    )
    conn.commit()


def list_overrides(conn: sqlite3.Connection, kind: str, event_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM overrides WHERE kind = ? AND event_id = ? ORDER BY occurrence",
        (kind, event_id),
    ).fetchall()


# --- memories (durable facts, injected into the system prompt) ---------------


def add_memory(conn: sqlite3.Connection, text: str) -> int:
    cur = conn.execute("INSERT INTO memories (text) VALUES (?)", (text,))
    conn.commit()
    return cur.lastrowid


def list_memories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, text FROM memories ORDER BY id").fetchall()


def delete_memory(conn: sqlite3.Connection, mem_id: int) -> bool:
    cur = conn.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
    conn.commit()
    return cur.rowcount > 0


# --- reminders (self-set triggers) -------------------------------------------


def add_reminder(
    conn: sqlite3.Connection, fire_at: str, prompt: str, chat_id: int, only_if_silent: bool = False
) -> int:
    cur = conn.execute(
        "INSERT INTO reminders (fire_at, prompt, chat_id, only_if_silent) VALUES (?, ?, ?, ?)",
        (fire_at, prompt, chat_id, int(only_if_silent)),
    )
    conn.commit()
    return cur.lastrowid


def pending_reminders(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Not-yet-fired reminders, earliest first. The caller compares fire_at to now."""
    return conn.execute(
        "SELECT * FROM reminders WHERE done = 0 ORDER BY fire_at, id"
    ).fetchall()


def mark_reminder_done(conn: sqlite3.Connection, rid: int) -> None:
    conn.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
    conn.commit()


def cancel_reminder(conn: sqlite3.Connection, rid: int) -> bool:
    cur = conn.execute("DELETE FROM reminders WHERE id = ? AND done = 0", (rid,))
    conn.commit()
    return cur.rowcount > 0


# --- sent work-calendar invites (to allow update / cancel) -------------------


def add_sent_invite(
    conn: sqlite3.Connection,
    uid: str,
    summary: str,
    start_at: str,
    end_at: str,
    show_as: str,
    rrule: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO sent_invites (uid, summary, start_at, end_at, show_as, rrule) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uid, summary, start_at, end_at, show_as, rrule),
    )
    conn.commit()
    return cur.lastrowid


def list_sent_invites(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM sent_invites WHERE status = 'sent' ORDER BY start_at, id"
    ).fetchall()


def get_sent_invite(conn: sqlite3.Connection, invite_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM sent_invites WHERE id = ?", (invite_id,)).fetchone()


def bump_sent_invite(
    conn: sqlite3.Connection,
    invite_id: int,
    summary: str,
    start_at: str,
    end_at: str,
    show_as: str,
    rrule: str | None = None,
) -> int:
    """Record an update: higher SEQUENCE + new fields. Returns the new sequence, or -1."""
    cur = conn.execute(
        "UPDATE sent_invites SET sequence = sequence + 1, summary = ?, start_at = ?, "
        "end_at = ?, show_as = ?, rrule = ? WHERE id = ? AND status = 'sent'",
        (summary, start_at, end_at, show_as, rrule, invite_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return -1
    row = get_sent_invite(conn, invite_id)
    return row["sequence"]


def cancel_sent_invite(conn: sqlite3.Connection, invite_id: int) -> bool:
    cur = conn.execute(
        "UPDATE sent_invites SET sequence = sequence + 1, status = 'cancelled' "
        "WHERE id = ? AND status = 'sent'",
        (invite_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def leo_wrote_since(conn: sqlite3.Connection, since: str) -> bool:
    """Has Leo sent a message after `since` (a sqlite datetime('now') string)?"""
    row = conn.execute(
        "SELECT 1 FROM messages WHERE role = 'user' AND ts > ? LIMIT 1", (since,)
    ).fetchone()
    return row is not None


# --- topic triggers ----------------------------------------------------------


def add_topic_trigger(conn: sqlite3.Connection, topic: str, prompt: str) -> int:
    cur = conn.execute("INSERT INTO topic_triggers (topic, prompt) VALUES (?, ?)", (topic, prompt))
    conn.commit()
    return cur.lastrowid


def pending_topic_triggers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM topic_triggers WHERE done = 0 ORDER BY id").fetchall()


def clear_topic_trigger(conn: sqlite3.Connection, tid: int) -> bool:
    cur = conn.execute("UPDATE topic_triggers SET done = 1 WHERE id = ? AND done = 0", (tid,))
    conn.commit()
    return cur.rowcount > 0


# --- activity log ------------------------------------------------------------


def add_activity(
    conn: sqlite3.Connection, kind: str, start_at: str, end_at: str, note: str | None = None
) -> int:
    cur = conn.execute(
        "INSERT INTO activity_log (kind, start_at, end_at, note) VALUES (?, ?, ?, ?)",
        (kind.strip().lower(), start_at, end_at, note),
    )
    conn.commit()
    return cur.lastrowid


def list_activity(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """All entries, earliest first; callers window them (ISO strings with mixed
    offsets don't compare correctly in SQL)."""
    return conn.execute("SELECT * FROM activity_log ORDER BY start_at, id").fetchall()


def delete_activity(conn: sqlite3.Connection, aid: int) -> bool:
    cur = conn.execute("DELETE FROM activity_log WHERE id = ?", (aid,))
    conn.commit()
    return cur.rowcount > 0
