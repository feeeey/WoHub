"""agent_runs doubles as the work queue (single worker, restart-safe)."""
import json
from database import get_db
from config import settings


def enqueue_run(task_id, context: dict) -> int:
    db = get_db(settings.db_path)
    try:
        cur = db.execute(
            "INSERT INTO agent_runs (task_id, decider, status, context_json) VALUES (?, 'agent', 'queued', ?)",
            (task_id, json.dumps(context, ensure_ascii=False)))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


# SINGLE-WORKER: 仅供单一 worker 线程调用；多线程领取虽被 SQLite 写锁串行化，但会阻塞而非失败
def claim_next():
    """Atomically claim the oldest queued run; None when empty. Requires SQLite >= 3.35 (RETURNING)."""
    db = get_db(settings.db_path)
    try:
        row = db.execute(
            """UPDATE agent_runs SET status = 'running', started_at = datetime('now')
               WHERE id = (SELECT id FROM agent_runs WHERE status = 'queued' ORDER BY id LIMIT 1)
               RETURNING *""").fetchone()
        db.commit()
        return row
    finally:
        db.close()


def finish_run(run_id, *, model, prompt_version, trace, input_tokens, output_tokens):
    db = get_db(settings.db_path)
    try:
        raw = json.dumps(trace, ensure_ascii=False)
        if len(raw) > 200_000:
            # 截断必须保持合法 JSON——丢弃明细，保留结构化占位
            raw = json.dumps({"_truncated": True, "char_limit": 200_000,
                              "reused": trace.get("reused", []) if isinstance(trace, dict) else []},
                             ensure_ascii=False)
        db.execute(
            """UPDATE agent_runs SET status='done', finished_at=datetime('now'), model=?,
               prompt_version=?, trace_json=?, input_tokens=?, output_tokens=? WHERE id=?""",
            (model, prompt_version, raw,
             input_tokens, output_tokens, run_id))
        db.commit()
    finally:
        db.close()


def fail_run(run_id, error: str):
    db = get_db(settings.db_path)
    try:
        db.execute("UPDATE agent_runs SET status='failed', finished_at=datetime('now'), error=? WHERE id=?",
                   (str(error)[:2000], run_id))
        db.commit()
    finally:
        db.close()


def get_run(run_id):
    db = get_db(settings.db_path)
    try:
        return db.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    finally:
        db.close()
