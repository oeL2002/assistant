from assistant.db import (
    add_memory,
    connect,
    delete_memory,
    get_meta,
    list_memories,
    set_meta,
)


def test_memory_crud(tmp_path):
    conn = connect(tmp_path / "t.db")
    a = add_memory(conn, "prefers deep work before noon")
    add_memory(conn, "thesis due 2027-03-14")

    assert [r["text"] for r in list_memories(conn)] == [
        "prefers deep work before noon",
        "thesis due 2027-03-14",
    ]
    assert delete_memory(conn, a)
    assert [r["text"] for r in list_memories(conn)] == ["thesis due 2027-03-14"]
    assert not delete_memory(conn, 999)


def test_meta_upsert(tmp_path):
    conn = connect(tmp_path / "t.db")
    assert get_meta(conn, "claude_session_id") is None
    set_meta(conn, "claude_session_id", "abc")
    assert get_meta(conn, "claude_session_id") == "abc"
    set_meta(conn, "claude_session_id", "def")  # upsert, not duplicate
    assert get_meta(conn, "claude_session_id") == "def"
