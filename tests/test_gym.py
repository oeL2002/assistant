import asyncio
import datetime as dt
import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from assistant import __main__ as daemon
from assistant import gym, publish, tools
from assistant.db import add_event, add_set, connect, get_meta, get_plan, save_exercise

TZ = ZoneInfo("Europe/Zurich")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    conn = connect(tmp_path / "a.db")
    port = _port()
    publish.serve(tmp_path / "a.db", port)
    base = f"http://127.0.0.1:{port}"

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()

    return conn, call


def test_page_and_redirect(server):
    _, call = server
    status, body = call("/gym/")
    assert status == 200 and b"Log set" in body
    status, body = call("/gym")  # urllib follows the 301 to /gym/
    assert b"Log set" in body


def test_log_set_is_idempotent_and_shows_in_state(server):
    conn, call = server
    ex = save_exercise(conn, "Pull Up", {"notes": "added weight"})
    s = {"client_id": "abc", "date": "2026-11-24", "exercise_id": ex, "weight_kg": 35, "reps": 8}
    _, first = call("/gym/sets", s)
    _, again = call("/gym/sets", s)  # retried after lost signal
    assert json.loads(first) == json.loads(again)
    _, raw = call("/gym/state?date=2026-11-24")
    st = json.loads(raw)
    assert [(x["weight_kg"], x["reps"]) for x in st["sets"]] == [(35, 8)]
    assert st["exercises"][0]["name"] == "Pull Up"

    _, raw = call("/gym/state?date=2026-11-27")  # a later day sees it as "last"
    assert json.loads(raw)["last"][str(ex)] == {"date": "2026-11-24", "sets": [[35, 8]]}

    call("/gym/sets/delete", {"id": json.loads(first)["id"]})
    _, raw = call("/gym/state?date=2026-11-24")
    assert json.loads(raw)["sets"] == []


@pytest.mark.parametrize("bad", [
    {"client_id": "x", "date": "nope", "exercise_id": 1, "weight_kg": 1, "reps": 1},
    {"client_id": "x", "date": "2026-11-24", "exercise_id": 999, "weight_kg": 1, "reps": 1},
    {"client_id": "x", "date": "2026-11-24", "exercise_id": 1, "weight_kg": 1, "reps": 0},
    {"date": "2026-11-24", "exercise_id": 1, "weight_kg": 1, "reps": 1},
])
def test_bad_sets_are_refused(server, bad):
    conn, call = server
    save_exercise(conn, "Squat", {})
    with pytest.raises(urllib.error.HTTPError) as e:
        call("/gym/sets", bad)
    assert e.value.code == 400


def test_page_can_add_an_exercise(server):
    conn, call = server
    _, raw = call("/gym/exercises", {"name": "  Dips "})
    assert conn.execute("SELECT name FROM exercises WHERE id = ?",
                        (json.loads(raw)["id"],)).fetchone()[0] == "Dips"


def test_double_progression(tmp_path):
    conn = connect(tmp_path / "a.db")
    ex_id = save_exercise(conn, "Bench", {"rep_low": 6, "rep_high": 8, "increment_kg": 2.5})
    ex = conn.execute("SELECT * FROM exercises WHERE id = ?", (ex_id,)).fetchone()
    assert "go to 42.5 kg for 6 reps" in gym.next_step(ex, [(40, 8), (40, 8), (40, 9)])
    assert "stay at 40 kg" in gym.next_step(ex, [(40, 8), (40, 8), (40, 6)])
    assert "aim 7+" in gym.next_step(ex, [(40, 8), (40, 8), (40, 6)])
    # a lighter back-off set doesn't hold progression back
    assert "go to 42.5" in gym.next_step(ex, [(40, 8), (40, 8), (30, 5)])


def test_history_and_plan_tools(tmp_path):
    conn = connect(tmp_path / "a.db")

    class Cfg:
        files_dir = Path("/nonexistent")

    tools.init(conn, calendar=None, cfg=Cfg())
    def run(tool, **a):
        return asyncio.run(getattr(tools, tool).handler(a))["content"][0]["text"]

    run("save_exercise", name="Pull Up", notes="added weight")
    assert "set a rep range and weight step first" in run("gym_history")  # coach decides
    run("save_exercise", name="Pull Up", rep_low=5, rep_high=8, increment_kg=2.5)
    add_set(conn, "a", "2026-11-20", 1, 35, 8)
    add_set(conn, "b", "2026-11-20", 1, 35, 8)
    hist = run("gym_history")
    assert "Pull Up (5–8 reps, +2.5 kg) — added weight" in hist
    assert "Fri 20 Nov: 35×8, 35×8" in hist
    assert "go to 37.5 kg" in hist

    assert "Unknown exercises: Squat" in run(
        "plan_workout", date="2026-11-24",
        items=[{"exercise": "Squat", "sets": 3, "reps": 5, "weight_kg": 60}])
    run("plan_workout", date="2026-11-24",
        items=[{"exercise": "pull up", "sets": 3, "reps": 5, "weight_kg": 37.5}])  # any case
    (p,) = get_plan(conn, "2026-11-24")
    assert (p["name"], p["sets"], p["reps"], p["weight_kg"]) == ("Pull Up", 3, 5, 37.5)

    assert "Saved" in run("save_exercise", name="Pull Up", active=False)
    assert "[inactive]" in run("list_exercises")


class FakeTG:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text):
        self.sent.append(text)


def test_gym_block_triggers_one_plan_two_hours_before(tmp_path, monkeypatch):
    conn = connect(tmp_path / "a.db")
    prompts = []

    async def fake_reply(prompt, conn):
        prompts.append(prompt)
        return "Pull Up: 3×5 @ 37.5 kg"

    monkeypatch.setattr(daemon, "reply", fake_reply)
    start = dt.datetime.now(TZ).replace(microsecond=0) + dt.timedelta(minutes=90)
    add_event(conn, "Gym", start.isoformat(), (start + dt.timedelta(hours=1)).isoformat())
    add_event(conn, "Thesis", start.isoformat(), (start + dt.timedelta(hours=1)).isoformat())

    class Cfg:
        allowed_chat_id = 1

    tg = FakeTG()
    asyncio.run(daemon._prepare_gym(tg, conn, Cfg()))
    asyncio.run(daemon._prepare_gym(tg, conn, Cfg()))  # next poll: no repeat
    assert len(prompts) == 1 and prompts[0].startswith("[gym session on the plan")
    assert tg.sent == ["Pull Up: 3×5 @ 37.5 kg"]
    assert get_meta(conn, f"gym_prepared:{start.isoformat()}") == "1"


def test_gym_block_far_away_does_not_trigger(tmp_path, monkeypatch):
    conn = connect(tmp_path / "a.db")
    monkeypatch.setattr(daemon, "reply", lambda *a: pytest.fail("should not plan yet"))
    start = dt.datetime.now(TZ) + dt.timedelta(hours=5)
    add_event(conn, "Gym", start.isoformat(), (start + dt.timedelta(hours=1)).isoformat())

    class Cfg:
        allowed_chat_id = 1

    asyncio.run(daemon._prepare_gym(FakeTG(), conn, Cfg()))

