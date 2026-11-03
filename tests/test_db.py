from pathlib import Path

from assistant.db import connect, log_message


def test_schema_and_task_roundtrip(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "messages", "tasks"} <= tables

    conn.execute("INSERT INTO tasks (title) VALUES (?)", ("write thesis",))
    conn.commit()
    row = conn.execute("SELECT title, status, scope FROM tasks").fetchone()
    assert row["title"] == "write thesis"
    assert row["status"] == "open"       # default
    assert row["scope"] == "personal"    # default


def test_log_message(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    log_message(conn, 42, "user", "hi")
    log_message(conn, 42, "assistant", "hello")
    rows = conn.execute("SELECT role, text FROM messages ORDER BY id").fetchall()
    assert [(r["role"], r["text"]) for r in rows] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]
