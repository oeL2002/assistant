from assistant.db import (
    add_task,
    connect,
    list_tasks,
    log_message,
    recent_messages,
    set_status,
    update_task,
)


def test_task_crud(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = add_task(conn, "write intro", due="2026-11-10", estimate_min=90)
    add_task(conn, "email advisor", scope="work")

    open_tasks = list_tasks(conn)
    assert {r["title"] for r in open_tasks} == {"write intro", "email advisor"}
    assert open_tasks[0]["title"] == "write intro"  # dated task sorts before null-due

    assert set_status(conn, a, "done")
    assert [r["title"] for r in list_tasks(conn)] == ["email advisor"]

    (b,) = [r["id"] for r in list_tasks(conn)]
    assert update_task(conn, b, {"project": "thesis", "estimate_min": 15})
    r = list_tasks(conn)[0]
    assert r["project"] == "thesis"
    assert r["estimate_min"] == 15

    # unknown ids are no-ops, not errors
    assert not update_task(conn, 999, {"title": "nope"})
    assert not set_status(conn, 999, "done")
    # a field not in the allowlist is ignored
    assert not update_task(conn, b, {"not_a_column": "x"})


def test_recent_messages_order(tmp_path):
    conn = connect(tmp_path / "t.db")
    for i in range(20):
        log_message(conn, 1, "user" if i % 2 == 0 else "assistant", f"m{i}")
    recent = recent_messages(conn, limit=5)
    assert [r["text"] for r in recent] == ["m15", "m16", "m17", "m18", "m19"]  # oldest-first
