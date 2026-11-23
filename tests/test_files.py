from assistant.files import list_dir, safe_name, save


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


def test_list_dir_gives_absolute_paths_and_skips_syncthing(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "notes.md").write_text("x")
    (tmp_path / ".stfolder").mkdir()
    (tmp_path / ".stfolder" / "marker").write_text("")
    assert list_dir(tmp_path) == [str((tmp_path / "sub" / "notes.md").resolve())]
