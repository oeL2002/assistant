import datetime as dt

from assistant.db import (
    add_reminder,
    cancel_reminder,
    connect,
    mark_reminder_done,
    pending_reminders,
)


def test_reminder_lifecycle(tmp_path):
    conn = connect(tmp_path / "t.db")
    past = "2020-01-01T00:00:00+00:00"
    future = "2999-01-01T00:00:00+00:00"
    a = add_reminder(conn, past, "ask about next meeting", 42)
    b = add_reminder(conn, future, "much later", 42)

    rows = pending_reminders(conn)
    assert [r["id"] for r in rows] == [a, b]  # earliest first
    assert rows[0]["chat_id"] == 42

    # the daemon's due test: ordered, so stop at the first future one
    now = dt.datetime.now(dt.UTC)
    due = [r["id"] for r in rows if dt.datetime.fromisoformat(r["fire_at"]) <= now]
    assert due == [a]

    mark_reminder_done(conn, a)
    assert [r["id"] for r in pending_reminders(conn)] == [b]

    assert cancel_reminder(conn, b)
    assert pending_reminders(conn) == []
    assert not cancel_reminder(conn, a)  # already fired -> not cancellable
