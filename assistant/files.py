"""Save files received over Telegram into the shared drive. Syncthing (set up
in the NixOS config) replicates that folder to Leo's phone and laptop.
"""

from __future__ import annotations

from pathlib import Path


def safe_name(name: str) -> str:
    """Strip any directory parts: a remote-supplied filename must not escape the
    drive (e.g. '../../etc/x' -> 'x')."""
    base = Path(name).name.strip()
    return base or "file"


def _unique(path: Path) -> Path:
    """Don't clobber: foo.pdf -> foo-1.pdf -> foo-2.pdf ..."""
    if not path.exists():
        return path
    i = 1
    while True:
        cand = path.with_name(f"{path.stem}-{i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


def save(files_dir: Path, name: str, data: bytes) -> Path:
    files_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique(files_dir / safe_name(name))
    dest.write_bytes(data)
    return dest


# --- reading (confined to the drive) -----------------------------------------

_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".log", ".org", ".tex", ".bib", ".html", ".xml", ".ini", ".toml",
}


def _resolve(files_dir: Path, name: str) -> Path:
    """Resolve `name` under the drive, refusing anything that escapes it."""
    root = files_dir.resolve()
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise ValueError("outside the drive")
    return target


def list_dir(files_dir: Path) -> list[str]:
    root = files_dir
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def read_document(files_dir: Path, name: str, max_chars: int = 20000) -> str:
    """Return a drive file's text. PDFs are text-extracted; unknown binaries are
    reported, not dumped. Raises ValueError (escape) / FileNotFoundError."""
    target = _resolve(files_dir, name)
    if not target.is_file():
        raise FileNotFoundError(name)
    suffix = target.suffix.lower()
    if suffix == ".pdf":
        try:
            text = _pdf_text(target)
        except Exception as exc:  # noqa: BLE001 -- pypdf raises assorted types on bad PDFs
            return f"(couldn't extract text from the PDF: {type(exc).__name__})"
    elif suffix in _TEXT_SUFFIXES:
        text = target.read_text(errors="replace")
    else:
        return f"({suffix or 'binary'} file, {target.stat().st_size} bytes — not readable as text)"
    text = text.strip()
    if not text:
        return "(empty, or no extractable text — a scanned PDF would need OCR)"
    if len(text) > max_chars:
        return f"{text[:max_chars]}\n…(truncated; {len(text)} chars total)"
    return text
