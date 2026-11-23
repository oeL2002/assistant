"""Entry points.

  assistant            -> the always-on daemon: long-poll Telegram, answer, persist,
                          and publish the plan calendar over the tailnet.
  assistant briefing   -> one-shot: send Leo a morning briefing (run by a timer).
  assistant weekly     -> one-shot: Sunday check of next week's work calendar and plan.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sqlite3
import sys

import httpx

from . import files, gym, publish, tools
from .brain import TZ, reply
from .calendar import Calendar, expand
from .config import Config
from .db import (
    connect,
    get_meta,
    get_plan,
    leo_wrote_since,
    log_message,
    mark_reminder_done,
    pending_reminders,
    set_meta,
)
from .telegram import Telegram

# Prompts the timers fire into the brain, by subcommand name. The NixOS module
# has one timer per name.
JOBS = {
    "briefing": (
        "Give me a brief morning briefing: today's schedule and the 1-3 tasks to focus "
        "on. If I have an early commitment today, also set one or two `only_if_silent` "
        "reminders shortly before I need to leave or start, to check I'm up."
    ),
    "weekly": (
        "[weekly check, Sunday evening] Look at the coming week. 1) Work calendar: are "
        "my office and away days for next week on it (list_work_invites, "
        "upcoming_events)? Fix gaps and changes, using per-occurrence changes on the "
        "standing series. If you don't know a day, ask me one short question. 2) Plan: "
        "do deadlines and open tasks for the week have realistic blocks? Add or move "
        "them. 3) One or two lines on last week: activity_stats against the plan. "
        "Keep the message short."
    ),
}

_HEARTBEAT_KEY = "heartbeat"
_OUTAGE = dt.timedelta(minutes=10)  # a gap in heartbeats longer than this was an outage
_STALE = dt.timedelta(hours=2)  # a reminder this late gets flagged as possibly moot
_SETTLE_S = 1.5  # wait this long for the rest of a message Telegram split up
_GYM_LEAD = dt.timedelta(hours=2)  # plan the workout this long before a gym block


def _local(utc_iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(utc_iso).astimezone(TZ)


async def _save_upload(tg: Telegram, cfg: Config, file_id: str, name: str) -> str:
    info = await tg.get_file(file_id)
    data = await tg.download_file(info["file_path"])
    return str(files.save(cfg.files_dir, name, data).resolve())


async def _fire_due_reminders(tg: Telegram, conn: sqlite3.Connection) -> None:
    """Fire any reminder whose time has passed, through the brain. Marked done
    before firing so a crash can't turn it into a resend loop."""
    now = dt.datetime.now(dt.UTC)
    for r in pending_reminders(conn):
        due = dt.datetime.fromisoformat(r["fire_at"])
        if due > now:
            break  # ordered by fire_at: the first future one ends the scan
        mark_reminder_done(conn, r["id"])
        if r["only_if_silent"] and leo_wrote_since(conn, r["created"]):
            continue  # he has written since: the nudge is moot
        prompt = f"[scheduled reminder you set earlier] {r['prompt']}"
        if now - due > _STALE:
            prompt = (
                f"[scheduled reminder you set earlier, due {due.astimezone(TZ):%a %d %b %H:%M} "
                f"but delivered {(now - due).total_seconds() / 3600:.0f} h late because you "
                f"were offline — it may be moot; only bring it up if it still matters] "
                f"{r['prompt']}"
            )
        try:
            answer = await reply(prompt, conn)
        except Exception as exc:  # keep the daemon alive on any brain error
            answer = f"(reminder error: {type(exc).__name__})"
        log_message(conn, r["chat_id"], "assistant", answer)
        await tg.send_message(r["chat_id"], answer)


async def _prepare_gym(tg: Telegram, conn: sqlite3.Connection, cfg: Config) -> None:
    """About _GYM_LEAD before a gym block on the plan, have the brain plan the
    workout and send it. Once per occurrence; skipped if a plan already exists."""
    if cfg.allowed_chat_id is None:
        return
    now = dt.datetime.now(TZ)
    for e in expand(publish.plan_ics(conn), now, now + _GYM_LEAD):
        if e.start <= now or not gym.is_gym(e.summary):
            continue
        key = f"gym_prepared:{e.start.isoformat()}"
        if get_meta(conn, key):
            continue
        set_meta(conn, key, "1")
        if get_plan(conn, e.start.date().isoformat()):
            continue  # already planned (e.g. he asked earlier)
        prompt = (
            f"[gym session on the plan at {e.start:%H:%M} ({e.summary})] Plan this workout: "
            "check gym_history and how he's doing, call plan_workout for today, then send "
            "the plan in a few lines (exercise: sets×reps @ weight)."
        )
        try:
            answer = await reply(prompt, conn)
        except Exception as exc:  # keep the daemon alive on any brain error
            answer = f"(gym plan error: {type(exc).__name__})"
        log_message(conn, cfg.allowed_chat_id, "assistant", answer)
        await tg.send_message(cfg.allowed_chat_id, answer)


async def _report_outage(tg: Telegram, conn: sqlite3.Connection, cfg: Config) -> None:
    """On start: if the last heartbeat is old, tell Leo how long the bot was down,
    so a silence is never mistaken for 'nothing to say'."""
    last = get_meta(conn, _HEARTBEAT_KEY)
    now = dt.datetime.now(dt.UTC)
    if last is None or cfg.allowed_chat_id is None:
        return
    gap = now - dt.datetime.fromisoformat(last)
    if gap > _OUTAGE:
        await tg.send_message(
            cfg.allowed_chat_id,
            f"I was offline from {_local(last):%a %d %b %H:%M} to {now.astimezone(TZ):%a %H:%M} "
            f"({gap.total_seconds() / 3600:.1f} h). Messages you sent then may be lost; "
            "missed reminders follow.",
        )


async def _collect(tg: Telegram, offset: int | None) -> tuple[list[dict], int | None]:
    """One long-poll, then keep taking updates while they still arrive: Telegram
    splits a long message into several, sent a moment apart, and they must reach
    the brain as one."""
    updates = await tg.get_updates(offset)
    while updates:
        offset = updates[-1]["update_id"] + 1
        await asyncio.sleep(_SETTLE_S)
        more = await tg.get_updates(offset, timeout=0)
        if not more:
            break
        updates += more
    return updates, offset


async def _handle(tg: Telegram, cfg: Config, conn: sqlite3.Connection, msgs: list[dict]) -> None:
    """One turn for everything Leo sent in one burst: texts joined, files saved
    to the drive and named in the prompt so the brain can Read them."""
    chat_id = msgs[0]["chat"]["id"]
    texts: list[str] = []
    saved: list[str] = []
    for msg in msgs:
        try:
            if doc := msg.get("document"):
                name = doc.get("file_name") or f"{doc['file_id']}.bin"
                saved.append(await _save_upload(tg, cfg, doc["file_id"], name))
            elif photo := msg.get("photo"):
                name = f"photo-{dt.datetime.now(TZ):%Y%m%d-%H%M%S}.jpg"
                saved.append(await _save_upload(tg, cfg, photo[-1]["file_id"], name))  # largest
        except (httpx.HTTPError, RuntimeError, OSError, KeyError) as exc:
            texts.append(f"[a file I sent couldn't be saved: {type(exc).__name__}]")
        if t := msg.get("text") or msg.get("caption"):
            texts.append(t)
    if not texts and not saved:
        return  # voice, stickers: not handled yet
    if not texts:  # files without a word: just file them
        log_message(conn, chat_id, "user", "[files: " + ", ".join(saved) + "]")
        answer = "Saved to your drive: " + ", ".join(p.rsplit("/", 1)[-1] for p in saved)
    else:
        prompt = "\n\n".join(texts)
        if saved:
            prompt += "\n\n[I sent files, saved to the drive: " + ", ".join(saved) + "]"
        log_message(conn, chat_id, "user", prompt)
        try:
            answer = await reply(prompt, conn)
        except Exception as exc:  # keep the loop alive on any brain error
            answer = f"(error: {type(exc).__name__})"
    log_message(conn, chat_id, "assistant", answer)
    await tg.send_message(chat_id, answer)


async def run() -> None:
    cfg = Config.from_env()
    conn = connect(cfg.db_path)
    publish.serve(cfg.db_path)  # plan.ics over the tailnet; own read connections
    async with httpx.AsyncClient() as client:
        tools.init(conn, Calendar(cfg.calendar_ics_url, client), cfg)
        tg = Telegram(cfg.telegram_token, client)
        try:
            await _report_outage(tg, conn, cfg)
        except (httpx.HTTPError, RuntimeError):
            pass  # no network yet; the gap is still visible in the reminders
        offset: int | None = None
        while True:
            set_meta(conn, _HEARTBEAT_KEY, dt.datetime.now(dt.UTC).isoformat())
            try:
                updates, offset = await _collect(tg, offset)
            except (httpx.HTTPError, RuntimeError):
                await asyncio.sleep(5)
                continue
            await _fire_due_reminders(tg, conn)  # due within ~1 poll (≤60s)
            await _prepare_gym(tg, conn, cfg)
            mine = [
                u["message"]
                for u in updates
                if (u.get("message") or {}).get("chat", {}).get("id") is not None
                and (
                    cfg.allowed_chat_id is None
                    or u["message"]["chat"]["id"] == cfg.allowed_chat_id
                )  # ignore everyone but Leo
            ]
            by_chat: dict[int, list[dict]] = {}
            for m in mine:
                by_chat.setdefault(m["chat"]["id"], []).append(m)
            for msgs in by_chat.values():
                await _handle(tg, cfg, conn, msgs)


async def run_job(name: str) -> None:
    cfg = Config.from_env()
    if cfg.allowed_chat_id is None:
        return  # nowhere to send it
    conn = connect(cfg.db_path)
    async with httpx.AsyncClient() as client:
        tools.init(conn, Calendar(cfg.calendar_ics_url, client), cfg)
        tg = Telegram(cfg.telegram_token, client)
        prompt = JOBS[name]
        log_message(conn, cfg.allowed_chat_id, "user", prompt)
        try:
            answer = await reply(prompt, conn)  # the brain lock queues it behind a live turn
        except Exception as exc:
            answer = f"({name} error: {type(exc).__name__})"
        log_message(conn, cfg.allowed_chat_id, "assistant", answer)
        await tg.send_message(cfg.allowed_chat_id, answer)


def main() -> None:
    if len(sys.argv) > 1:
        if sys.argv[1] not in JOBS:
            raise SystemExit(f"unknown job {sys.argv[1]!r}; known: {', '.join(JOBS)}")
        asyncio.run(run_job(sys.argv[1]))
    else:
        asyncio.run(run())


if __name__ == "__main__":
    main()
