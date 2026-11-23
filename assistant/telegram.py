"""Minimal Telegram Bot API client over httpx — long-poll in, send out.

A single user needs no framework: getUpdates long-polls, sendMessage replies.
"""

from __future__ import annotations

from typing import Any

import httpx

_URL = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096  # Telegram's cap on one message, in characters
_FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"


class Telegram:
    def __init__(self, token: str, client: httpx.AsyncClient) -> None:
        self._token = token
        self._client = client

    async def _call(self, method: str, **params: Any) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        resp = await self._client.post(
            _URL.format(token=self._token, method=method), json=params, timeout=70
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {data}")
        return data["result"]

    async def get_updates(self, offset: int | None, timeout: int = 60) -> list[dict]:
        return await self._call("getUpdates", offset=offset, timeout=timeout)

    async def send_message(self, chat_id: int, text: str) -> None:
        for part in chunks(text):
            await self._call("sendMessage", chat_id=chat_id, text=part)

    async def get_file(self, file_id: str) -> dict:
        """Resolve a file_id to its download path (valid ~1h). Bot API caps at 20 MB."""
        return await self._call("getFile", file_id=file_id)

    async def download_file(self, file_path: str) -> bytes:
        resp = await self._client.get(
            _FILE_URL.format(token=self._token, path=file_path), timeout=120
        )
        resp.raise_for_status()
        return resp.content


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    """Split text into messages of at most `limit` chars, at a paragraph break,
    else a line break, else a space, else hard. Nothing is dropped."""
    out: list[str] = []
    while len(text) > limit:
        # The best break in the back half of the window; a hard cut if none.
        cut = next(
            (c for sep in ("\n\n", "\n", " ") if (c := text.rfind(sep, 0, limit)) > limit // 2),
            limit,
        )
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text or not out:
        out.append(text)
    return out
