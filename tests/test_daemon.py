"""The daemon's message handling against a fake Telegram and brain."""

import asyncio
import datetime as dt
from pathlib import Path

import pytest

from assistant import __main__ as daemon
from assistant import tools
from assistant.db import add_sent_invite, connect, get_meta, set_meta


class FakeTG:
    def __init__(self, batches):
        self.batches = list(batches)
        self.sent: list[tuple[int, str]] = []

    async def get_updates(self, offset, timeout=60):
        return self.batches.pop(0) if self.batches else []

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


def _msg(uid, text):
    return {"update_id": uid, "message": {"chat": {"id": 1}, "text": text}}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(daemon, "_SETTLE_S", 0)


def test_split_message_is_collected_into_one_burst():
    tg = FakeTG([[_msg(1, "part one")], [_msg(2, "part two")], [_msg(3, "part three")]])
    updates, offset = asyncio.run(daemon._collect(tg, None))
    assert [u["message"]["text"] for u in updates] == ["part one", "part two", "part three"]
    assert offset == 4


def test_burst_becomes_one_brain_turn(tmp_path, monkeypatch):
    prompts = []

    async def fake_reply(prompt, conn):
        prompts.append(prompt)
        return "ok"

    monkeypatch.setattr(daemon, "reply", fake_reply)
    conn = connect(tmp_path / "t.db")
    tg = FakeTG([])
    msgs = [{"chat": {"id": 1}, "text": "part one"}, {"chat": {"id": 1}, "text": "part two"}]
    asyncio.run(daemon._handle(tg, None, conn, msgs))
    assert prompts == ["part one\n\npart two"]
    assert tg.sent == [(1, "ok")]


class _Cfg:
    allowed_chat_id = 1


def test_outage_is_reported_after_a_long_gap(tmp_path):
    conn = connect(tmp_path / "t.db")
    tg = FakeTG([])
    asyncio.run(daemon._report_outage(tg, conn, _Cfg()))
    assert tg.sent == []  # first ever start: nothing to compare
    then = dt.datetime.now(dt.UTC) - dt.timedelta(hours=46)
    set_meta(conn, daemon._HEARTBEAT_KEY, then.isoformat())
    asyncio.run(daemon._report_outage(tg, conn, _Cfg()))
    assert "offline" in tg.sent[0][1] and "(46.0 h)" in tg.sent[0][1]
    assert get_meta(conn, daemon._HEARTBEAT_KEY) == then.isoformat()  # the loop updates it


def test_cancel_one_occurrence_of_a_recurring_invite(tmp_path, monkeypatch):
    sent = []

    async def fake_send(cfg, summary, start, end, notes=None, show_as="busy", **kw):
        sent.append((summary, start, kw))
        return kw.get("uid")

    monkeypatch.setattr(tools._mail, "send_invite", fake_send)

    class Cfg:
        mail_ready = True
        files_dir = Path("/nonexistent")

    conn = connect(tmp_path / "t.db")
    tools.init(conn, calendar=None, cfg=Cfg())
    add_sent_invite(conn, "u@x", "Im Büro", "2026-11-23T08:00+01:00", "2026-11-23T17:00+01:00",
                    "busy", "FREQ=WEEKLY;BYDAY=MO,WE")
    out = asyncio.run(tools.cancel_work_invite.handler({"id": 1, "occurrence": "2026-11-25"}))
    assert "on Wed 25 Nov only" in out["content"][0]["text"]
    (_, _, kw), = sent
    assert kw["method"] == "CANCEL" and kw["uid"] == "u@x" and kw["sequence"] == 1
    assert kw["recurrence_id"].isoformat() == "2026-11-25T08:00:00+01:00"
    listing = asyncio.run(tools.list_work_invites.handler({}))["content"][0]["text"]
    assert "Wed 25 Nov: cancelled" in listing
