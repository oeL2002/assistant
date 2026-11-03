from assistant.config import _read


def test_file_env_preferred(tmp_path, monkeypatch):
    f = tmp_path / "tok"
    f.write_text("  secret\n")
    monkeypatch.setenv("X_FILE", str(f))
    monkeypatch.setenv("X", "plain")
    assert _read("X") == "secret"  # _FILE wins over plain var


def test_plain_env_fallback(monkeypatch):
    monkeypatch.delenv("Y_FILE", raising=False)
    monkeypatch.setenv("Y", " val ")
    assert _read("Y") == "val"


def test_missing(monkeypatch):
    monkeypatch.delenv("Z_FILE", raising=False)
    monkeypatch.delenv("Z", raising=False)
    assert _read("Z") is None
