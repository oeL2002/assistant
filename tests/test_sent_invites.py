from assistant.db import (
    add_sent_invite,
    bump_sent_invite,
    cancel_sent_invite,
    connect,
    get_sent_invite,
    list_sent_invites,
)


def test_sent_invite_update_and_cancel(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = add_sent_invite(conn, "u1@example.com", "standup", "2026-11-03T09:00+01:00", "2026-11-03T09:15+01:00", "busy")

    (row,) = list_sent_invites(conn)
    assert row["id"] == a
    assert row["sequence"] == 0
    assert row["show_as"] == "busy"

    # update: higher sequence, changed fields, preserved uid
    seq = bump_sent_invite(conn, a, "standup", "2026-11-03T10:00+01:00", "2026-11-03T10:15+01:00", "free")
    assert seq == 1
    row = get_sent_invite(conn, a)
    assert row["uid"] == "u1@example.com"  # uid never changes -> Outlook updates in place
    assert row["show_as"] == "free"

    # cancel removes it from the active list and bumps the sequence again
    assert cancel_sent_invite(conn, a)
    assert list_sent_invites(conn) == []
    assert get_sent_invite(conn, a)["sequence"] == 2
    assert not cancel_sent_invite(conn, a)  # already cancelled
    assert bump_sent_invite(conn, a, "x", "2026-11-03T10:00+01:00", "2026-11-03T10:15+01:00", "busy") == -1


def test_sent_invite_stores_recurrence(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = add_sent_invite(
        conn, "u2@example.com", "sync", "2026-11-06T09:00+01:00", "2026-11-06T10:00+01:00",
        "busy", "FREQ=WEEKLY;BYDAY=FR;COUNT=12",
    )
    assert get_sent_invite(conn, a)["rrule"] == "FREQ=WEEKLY;BYDAY=FR;COUNT=12"
    # update preserves it when not overridden (the tool passes the stored value back)
    bump_sent_invite(
        conn, a, "sync", "2026-11-06T11:00+01:00", "2026-11-06T12:00+01:00",
        "busy", "FREQ=WEEKLY;BYDAY=FR;COUNT=12",
    )
    assert get_sent_invite(conn, a)["rrule"] == "FREQ=WEEKLY;BYDAY=FR;COUNT=12"
