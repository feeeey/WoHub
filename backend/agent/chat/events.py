"""Append-only event log per turn. Global autoincrement id doubles as the
SSE resume cursor; per-turn seq guarantees intra-turn ordering."""
import json
from database import get_db
from config import settings

MAX_PAYLOAD_CHARS = 4000   # 事件是流式明细，超长截断（完整结果在消息 trace_json 里）


def append_event(turn_id: int, type: str, payload: dict) -> int:
    raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) > MAX_PAYLOAD_CHARS:
        raw = json.dumps({"_truncated": True, "preview": raw[:MAX_PAYLOAD_CHARS]},
                         ensure_ascii=False)
    db = get_db(settings.db_path)
    try:
        cur = db.execute(
            """INSERT INTO chat_events (turn_id, seq, type, payload_json)
               VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_events
                           WHERE turn_id = ?), ?, ?)""",
            (turn_id, turn_id, type, raw))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def _rows_to_dicts(rows):
    out = []
    for r in rows:
        d = {"id": r["id"], "turn_id": r["turn_id"], "seq": r["seq"], "type": r["type"]}
        try:
            d["payload"] = json.loads(r["payload_json"])
        except json.JSONDecodeError:
            d["payload"] = {"_parse_error": True}
        out.append(d)
    return out


def events_after(session_id: int, after_id: int, limit: int = 500) -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            """SELECT e.id, e.turn_id, e.seq, e.type, e.payload_json
               FROM chat_events e JOIN chat_turns t ON t.id = e.turn_id
               WHERE t.session_id = ? AND e.id > ? ORDER BY e.id LIMIT ?""",
            (session_id, after_id, limit)).fetchall()
    finally:
        db.close()
    return _rows_to_dicts(rows)


def turn_events(turn_id: int) -> list[dict]:
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            "SELECT id, turn_id, seq, type, payload_json FROM chat_events "
            "WHERE turn_id = ? ORDER BY seq", (turn_id,)).fetchall()
    finally:
        db.close()
    return _rows_to_dicts(rows)


def last_event_id() -> int:
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT COALESCE(MAX(id), 0) AS m FROM chat_events").fetchone()
        return row["m"]
    finally:
        db.close()
