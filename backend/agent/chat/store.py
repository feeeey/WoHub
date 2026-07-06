"""Chat sessions/messages/turns persistence. Short-lived connections,
short transactions (SQLite single-writer discipline)."""
import json
from database import get_db
from config import settings


def _db():
    return get_db(settings.db_path)


# ---- sessions ----

def create_session(title: str | None = None) -> int:
    db = _db()
    try:
        cur = db.execute("INSERT INTO chat_sessions (title) VALUES (?)",
                         (title or "新会话",))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def list_sessions() -> list[dict]:
    db = _db()
    try:
        rows = db.execute(
            """SELECT s.*, (SELECT COUNT(*) FROM chat_messages m
                            WHERE m.session_id = s.id) AS message_count
               FROM chat_sessions s ORDER BY s.updated_at DESC, s.id DESC""").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def rename_session(session_id: int, title: str) -> bool:
    db = _db()
    try:
        cur = db.execute(
            "UPDATE chat_sessions SET title = ?, updated_at = datetime('now') WHERE id = ?",
            (title[:80], session_id))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def delete_session(session_id: int) -> None:
    db = _db()
    try:
        db.execute("DELETE FROM chat_events WHERE turn_id IN "
                   "(SELECT id FROM chat_turns WHERE session_id = ?)", (session_id,))
        db.execute("DELETE FROM chat_turns WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        db.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        db.commit()
    finally:
        db.close()


def touch_session(session_id: int) -> None:
    db = _db()
    try:
        db.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                   (session_id,))
        db.commit()
    finally:
        db.close()


# ---- messages ----

def add_message(session_id: int, role: str, content: str, images=None, trace=None,
                model=None, input_tokens=None, output_tokens=None, error=None) -> int:
    db = _db()
    try:
        cur = db.execute(
            """INSERT INTO chat_messages (session_id, role, content, images_json,
               trace_json, model, input_tokens, output_tokens, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, role, content,
             json.dumps(images, ensure_ascii=False) if images else None,
             json.dumps(trace, ensure_ascii=False) if trace else None,
             model, input_tokens, output_tokens, error))
        db.execute("UPDATE chat_sessions SET updated_at = datetime('now') WHERE id = ?",
                   (session_id,))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def _safe_json(s):
    try:
        return json.loads(s) if s else None
    except json.JSONDecodeError:
        return {"_parse_error": True}


def list_messages(session_id: int) -> list[dict]:
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,)).fetchall()
    finally:
        db.close()
    out = []
    for r in rows:
        d = dict(r)
        d["images"] = _safe_json(d.pop("images_json")) or []
        d["trace"] = _safe_json(d.pop("trace_json"))
        out.append(d)
    return out


# ---- turns（chat_turns 兼作工作队列，单 worker）----

def create_turn(session_id: int, user_message_id: int) -> int:
    db = _db()
    try:
        cur = db.execute(
            "INSERT INTO chat_turns (session_id, user_message_id) VALUES (?, ?)",
            (session_id, user_message_id))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


def claim_next_turn():
    """Atomically claim oldest queued turn; None when empty (SQLite >= 3.35 RETURNING)."""
    db = _db()
    try:
        row = db.execute(
            """UPDATE chat_turns SET status = 'running'
               WHERE id = (SELECT id FROM chat_turns WHERE status = 'queued'
                           ORDER BY id LIMIT 1)
               RETURNING *""").fetchone()
        db.commit()
        return row
    finally:
        db.close()


def finish_turn(turn_id: int, status: str) -> None:
    assert status in ("done", "failed", "cancelled")
    db = _db()
    try:
        db.execute("UPDATE chat_turns SET status = ?, finished_at = datetime('now') "
                   "WHERE id = ?", (status, turn_id))
        db.commit()
    finally:
        db.close()


def request_cancel(turn_id: int) -> bool:
    db = _db()
    try:
        cur = db.execute(
            "UPDATE chat_turns SET cancel_requested = 1 WHERE id = ? "
            "AND status IN ('queued', 'running')", (turn_id,))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def cancel_requested(turn_id: int) -> bool:
    db = _db()
    try:
        row = db.execute("SELECT cancel_requested FROM chat_turns WHERE id = ?",
                         (turn_id,)).fetchone()
        return bool(row and row["cancel_requested"])
    finally:
        db.close()


def active_turn(session_id: int) -> dict | None:
    db = _db()
    try:
        row = db.execute(
            """SELECT id, status, user_message_id FROM chat_turns
               WHERE session_id = ? AND status IN ('queued', 'running')
               ORDER BY id DESC LIMIT 1""", (session_id,)).fetchone()
        return dict(row) if row else None
    finally:
        db.close()


def recover_interrupted() -> list[dict]:
    """启动恢复：queued 保留续跑；被重启打断的 running 判 failed。"""
    db = _db()
    try:
        rows = db.execute(
            """UPDATE chat_turns SET status = 'failed', finished_at = datetime('now')
               WHERE status = 'running' RETURNING id, session_id""").fetchall()
        db.commit()
        return [{"id": r["id"], "session_id": r["session_id"]} for r in rows]
    finally:
        db.close()
