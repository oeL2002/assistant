"""Runtime configuration, read from the environment.

Secrets are delivered by systemd `LoadCredential` as files, so every secret is
read from `<NAME>_FILE` first (a path) and only then from `<NAME>` directly. That
keeps tokens out of the process's argv/env dumps on the server while staying easy
to run from a shell in development (just export the plain var).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read(name: str) -> str | None:
    """Value of `name`, preferring a `<name>_FILE` path over the plain var."""
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_text().strip()
    val = os.environ.get(name)
    return val.strip() if val else None


@dataclass(frozen=True)
class Config:
    telegram_token: str
    allowed_chat_id: int | None
    db_path: Path
    files_dir: Path  # the shared drive; Telegram-received files land here
    calendar_ics_url: str | None
    # Migadu mail. One mailbox password (smtp_password) works for both SMTP (send)
    # and IMAP (read). Optional: the mail tools report "not set up" if unset.
    smtp_host: str
    smtp_port: int
    imap_host: str
    invite_from: str | None  # the assistant's own address; also the IMAP/SMTP login
    invite_to: str | None  # where work invites go (Leo's work calendar)
    smtp_password: str | None

    @property
    def mail_ready(self) -> bool:
        """Can send a work invite (needs a recipient too)."""
        return bool(self.invite_from and self.invite_to and self.smtp_password)

    @property
    def mailbox_ready(self) -> bool:
        """Can read the assistant's own mailbox."""
        return bool(self.invite_from and self.smtp_password)

    @classmethod
    def from_env(cls) -> Config:
        token = _read("TELEGRAM_BOT_TOKEN")
        if not token:
            raise SystemExit("TELEGRAM_BOT_TOKEN (or TELEGRAM_BOT_TOKEN_FILE) is required")
        chat = os.environ.get("TELEGRAM_ALLOWED_CHAT_ID")
        return cls(
            telegram_token=token,
            allowed_chat_id=int(chat) if chat else None,
            db_path=Path(os.environ.get("ASSISTANT_DB", "./assistant.db")),
            files_dir=Path(os.environ.get("ASSISTANT_FILES", "./files")),
            calendar_ics_url=_read("CALENDAR_WORK_ICS_URL"),
            smtp_host=os.environ.get("SMTP_HOST", "smtp.migadu.com"),
            smtp_port=int(os.environ.get("SMTP_PORT", "465")),
            imap_host=os.environ.get("IMAP_HOST", "imap.migadu.com"),
            invite_from=_read("SMTP_FROM"),
            invite_to=_read("INVITE_TO"),
            smtp_password=_read("SMTP_PASSWORD"),
        )
