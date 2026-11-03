import pytest

from assistant.files import list_dir, read_document, safe_name, save


def test_safe_name_strips_directory_traversal():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("notes.pdf") == "notes.pdf"
    assert safe_name("") == "file"
    assert safe_name("/abs/path/x.txt") == "x.txt"


def test_save_writes_and_avoids_clobber(tmp_path):
    a = save(tmp_path, "notes.pdf", b"one")
    b = save(tmp_path, "notes.pdf", b"two")  # same name -> must not overwrite
    assert a.name == "notes.pdf"
    assert b.name == "notes-1.pdf"
    assert a.read_bytes() == b"one"
    assert b.read_bytes() == b"two"

    # a traversal name is contained inside the drive
    c = save(tmp_path, "../escape.txt", b"x")
    assert c.parent == tmp_path
    assert c.name == "escape.txt"


def test_read_document_and_confinement(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.md").write_text("# Lecture 1\nkey idea")
    (tmp_path / "pic.png").write_bytes(b"\x89PNG\r\n")

    assert "key idea" in read_document(tmp_path, "sub/notes.md")
    assert "not readable as text" in read_document(tmp_path, "pic.png")  # binary reported
    assert list_dir(tmp_path) == ["pic.png", "sub/notes.md"]

    with pytest.raises(FileNotFoundError):
        read_document(tmp_path, "nope.txt")
    # must not escape the drive, even via traversal
    with pytest.raises(ValueError):
        read_document(tmp_path, "../../../etc/passwd")


def test_read_pdf_failure_is_graceful(tmp_path):
    # Not a real PDF. Must return a message, never raise (this is the path that
    # crashed in production on a bad pypdf import).
    (tmp_path / "broken.pdf").write_bytes(b"not actually a pdf")
    out = read_document(tmp_path, "broken.pdf")
    assert "couldn't extract" in out.lower()
