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


# --- listing ------------------------------------------------------------------


def list_dir(files_dir: Path) -> list[str]:
    """Absolute paths, so the agent can hand them straight to its Read tool."""
    if not files_dir.exists():
        return []
    return sorted(str(p.resolve()) for p in files_dir.rglob("*") if p.is_file() and ".stfolder" not in p.parts)
