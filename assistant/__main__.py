"""Entry points.

  assistant            -> the always-on daemon: long-poll Telegram, answer, persist,
                          and publish the plan calendar over the tailnet.
  assistant briefing   -> one-shot: send Leo a morning briefing (run by a timer).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import sys

import httpx

from . import files, publish, tools
from .brain import reply
from .calendar import Calendar
from .config import Config
from .db import (
    connect,
    log_message,
    mark_reminder_done,
    pending_reminders,
)
from .telegram import Telegram


async def _receive_file(tg: Telegram, cfg: Config, doc: dict) -> str:
    name = doc.get("file_name") or f"{doc['file_id']}.bin"
    info = await tg.get_file(doc["file_id"])
    data = await tg.download_file(info["file_path"])
    dest = files.save(cfg.files_dir, name, data)
    return f"Saved {dest.name} to your drive ({max(1, len(data) // 1024)} KB)."


async def _fire_due_reminders(tg: Telegram, conn: sqlite3.Connection) -> None:
    """Fire any reminder whose time has passed, through the brain. Marked done
    before firing so a crash can't turn it into a resend loop."""
    now = dt.datetime.now(dt.UTC)
    for r in pending_reminders(conn):
        if dt.datetime.fromisoformat(r["fire_at"]) > now:
            break  # ordered by fire_at: the first future one ends the scan
        mark_reminder_done(conn, r["id"])
        try:
            answer = await reply(f"[scheduled reminder you set earlier] {r['prompt']}", conn)
        except Exception as exc:  # keep the daemon alive on any brain error
            answer = f"(reminder error: {type(exc).__name__})"
        log_message(conn, r["chat_id"], "assistant", answer)
        await tg.send_message(r["chat_id"], answer)


async def run() -> None:
    cfg = Config.from_env()
    conn = connect(cfg.db_path)
    publish.serve(cfg.db_path)  # plan.ics over the tailnet; own read connections
    async with httpx.AsyncClient() as client:
        tools.init(conn, Calendar(cfg.calendar_ics_url, client), cfg)
        tg = Telegram(cfg.telegram_token, client)
        offset: int | None = None
        while True:
            try:
                updates = await tg.get_updates(offset)
            except (httpx.HTTPError, RuntimeError):
                await asyncio.sleep(5)
                continue
            await _fire_due_reminders(tg, conn)  # due within ~1 poll (≤60s)
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                chat_id = (msg.get("chat") or {}).get("id")
                if chat_id is None:
                    continue
                if cfg.allowed_chat_id is not None and chat_id != cfg.allowed_chat_id:
                    continue  # ignore everyone but Leo
                text = msg.get("text")
                doc = msg.get("document")
                if text:
                    log_message(conn, chat_id, "user", text)
                    try:
                        answer = await reply(text, conn)
                    except Exception as exc:  # keep the loop alive on any brain error
                        answer = f"(error: {type(exc).__name__})"
                elif doc:
                    log_message(conn, chat_id, "user", f"[file: {doc.get('file_name', '?')}]")
                    try:
                        answer = await _receive_file(tg, cfg, doc)
                    except (httpx.HTTPError, RuntimeError, OSError, KeyError) as exc:
                        answer = f"(couldn't save that file: {type(exc).__name__})"
                else:
                    continue  # voice, photos, stickers: not handled yet
                log_message(conn, chat_id, "assistant", answer)
                await tg.send_message(chat_id, answer)


async def briefing() -> None:
    cfg = Config.from_env()
    if cfg.allowed_chat_id is None:
        return  # nowhere to send it
    conn = connect(cfg.db_path)
    async with httpx.AsyncClient() as client:
        tools.init(conn, Calendar(cfg.calendar_ics_url, client), cfg)
        tg = Telegram(cfg.telegram_token, client)
        prompt = "Give me a brief morning briefing: today's schedule and the 1-3 tasks to focus on."
        log_message(conn, cfg.allowed_chat_id, "user", prompt)
        # ponytail: shares the chat session for continuity; a collision with an
        # in-flight daemon reply is possible but ~seconds/day. Separate session
        # key if that ever bites.
        try:
            answer = await reply(prompt, conn)
        except Exception as exc:
            answer = f"(briefing error: {type(exc).__name__})"
        log_message(conn, cfg.allowed_chat_id, "assistant", answer)
        await tg.send_message(cfg.allowed_chat_id, answer)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "briefing":
        asyncio.run(briefing())
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
