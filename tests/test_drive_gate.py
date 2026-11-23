import asyncio

from assistant.brain import _drive_gate


def decide(gate, path):
    out = asyncio.run(gate({"tool_input": {"file_path": str(path)}}, None, None))
    return out["hookSpecificOutput"]["permissionDecision"]


def test_read_is_confined_to_the_drive(tmp_path):
    drive = tmp_path / "files"
    (drive / "mail").mkdir(parents=True)
    (drive / "mail" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "assistant.db").write_text("state")
    (drive / "link").symlink_to(tmp_path / "assistant.db")  # e.g. synced in by Syncthing
    gate = _drive_gate(drive)

    assert decide(gate, drive / "mail" / "a.pdf") == "allow"
    assert decide(gate, tmp_path / "assistant.db") == "deny"  # the state dir next door
    assert decide(gate, drive / ".." / "assistant.db") == "deny"  # traversal
    assert decide(gate, drive / "link") == "deny"  # symlink pointing out
    assert decide(gate, "/etc/passwd") == "deny"
