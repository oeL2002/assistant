import sqlite3

from assistant.db import add_sent_invite, connect


def test_connect_adds_missing_rrule_column(tmp_path):
    # Reproduce an older database: sent_invites as it was before `rrule` existed.
    p = tmp_path / "old.db"
    raw = sqlite3.connect(p)
    raw.execute(
        "CREATE TABLE sent_invites ("
        "id INTEGER PRIMARY KEY, created TEXT DEFAULT (datetime('now')), uid TEXT NOT NULL, "
        "summary TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL, "
        "show_as TEXT NOT NULL DEFAULT 'busy', sequence INTEGER NOT NULL DEFAULT 0, "
        "status TEXT NOT NULL DEFAULT 'sent')"
    )
    raw.commit()
    raw.close()

    conn = connect(p)  # must ALTER in the missing column, not choke on CREATE IF NOT EXISTS
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sent_invites)")}
    assert "rrule" in cols

    # the insert that failed on the server now works
    rid = add_sent_invite(
        conn, "u@example.com", "x", "2026-11-06T09:00+01:00", "2026-11-06T10:00+01:00",
        "busy", "FREQ=WEEKLY;COUNT=2",
    )
    assert conn.execute("SELECT rrule FROM sent_invites WHERE id=?", (rid,)).fetchone()[0] == (
        "FREQ=WEEKLY;COUNT=2"
    )
