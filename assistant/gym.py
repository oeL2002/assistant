"""Gym: what the page shows and what the assistant reasons over.

Progression is double progression, per exercise: keep the weight until every
set of a session reaches rep_high, then add increment_kg and drop back to
rep_low. `next_step` applies that rule to the last session so the agent's
suggestion starts from a fact, not a guess.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3

from . import db

# A plan block counts as a gym session if its summary says so.
_GYM = re.compile(r"\b(gym|workout|training)\b", re.IGNORECASE)


def is_gym(summary: str) -> bool:
    return bool(_GYM.search(summary))


def _fmt(w: float) -> str:
    return f"{w:g}"


def _range(ex: sqlite3.Row) -> str:
    if ex["rep_low"] is None or ex["rep_high"] is None:
        return "no rep range set"
    step = f", +{_fmt(ex['increment_kg'])} kg" if ex["increment_kg"] else ", no weight step set"
    return f"{ex['rep_low']}–{ex['rep_high']} reps{step}"


def next_step(ex: sqlite3.Row, last: list[tuple[float, int]]) -> str:
    """The double-progression suggestion from the last session's sets, or what
    the coach must decide first."""
    if ex["rep_low"] is None or ex["rep_high"] is None or not ex["increment_kg"]:
        return "set a rep range and weight step first (save_exercise)"
    if not last:
        return f"no history: pick a weight for {ex['rep_low']}–{ex['rep_high']} reps"
    top = max(w for w, _ in last)
    at_top = [r for w, r in last if w == top]
    if all(r >= ex["rep_high"] for r in at_top):
        return (
            f"all sets reached {ex['rep_high']}: go to {_fmt(top + ex['increment_kg'])} kg "
            f"for {ex['rep_low']} reps"
        )
    return f"stay at {_fmt(top)} kg and add reps (aim {min(at_top) + 1}+ on the weakest set)"


def history_text(conn: sqlite3.Connection, names: list[str] | None = None, limit: int = 4) -> str:
    rows = [db.get_exercise(conn, n) for n in names] if names else db.list_exercises(conn, True)
    unknown = [n for n, r in zip(names or [], rows, strict=False) if r is None]
    out = []
    for ex in (r for r in rows if r is not None):
        sess = db.sessions(conn, ex["id"], limit=limit)
        head = f"{ex['name']} ({_range(ex)})"
        if ex["notes"]:
            head += f" — {ex['notes']}"
        lines = [head]
        for d, sets in sess:
            day = dt.date.fromisoformat(d)
            lines.append(f"  {day:%a %d %b}: " + ", ".join(f"{_fmt(w)}×{r}" for w, r in sets))
        lines.append("  → " + next_step(ex, sess[0][1] if sess else []))
        out.append("\n".join(lines))
    if unknown:
        out.append("Unknown exercises: " + ", ".join(unknown))
    return "\n\n".join(out) if out else "No exercises yet."


def state(conn: sqlite3.Connection, date: str) -> dict:
    """Everything the gym page needs for one day, as plain JSON."""
    exercises = db.list_exercises(conn)
    return {
        "date": date,
        "exercises": [
            {
                "id": e["id"],
                "name": e["name"],
                "active": bool(e["active"]),
                "rep_low": e["rep_low"],
                "rep_high": e["rep_high"],
                "notes": e["notes"],
            }
            for e in exercises
        ],
        "plan": [
            {
                "exercise_id": p["exercise_id"],
                "sets": p["sets"],
                "reps": p["reps"],
                "weight_kg": p["weight_kg"],
                "note": p["note"],
            }
            for p in db.get_plan(conn, date)
        ],
        "sets": [
            {
                "id": s["id"],
                "client_id": s["client_id"],
                "exercise_id": s["exercise_id"],
                "weight_kg": s["weight_kg"],
                "reps": s["reps"],
            }
            for s in db.sets_on(conn, date)
        ],
        "last": {
            str(e["id"]): {"date": sess[0][0], "sets": sess[0][1]}
            for e in exercises
            if (sess := db.sessions(conn, e["id"], before=date, limit=1))
        },
    }

