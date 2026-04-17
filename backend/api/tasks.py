import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from tasks.scheduler import (
    add_task_job, remove_task_job, is_task_running,
    get_shortest_resolution, SCHEDULE_DESC,
)
from tasks.executor import execute_task, get_last_result
from sources.pine_screener import list_screeners

router = APIRouter(prefix="/tasks")

VALID_TYPES = {"watchlist_signal", "market_scan", "anomaly_watch", "scheduled_shot"}


class TaskCreate(BaseModel):
    name: str
    type: str
    config: dict
    actions: list
    schedule: str
    channel_id: Optional[int] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    actions: Optional[list] = None
    schedule: Optional[str] = None
    channel_id: Optional[int] = None
    enabled: Optional[bool] = None


def _row_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": json.loads(row["config_json"]),
        "actions": json.loads(row["actions_json"]),
        "channel_id": row["channel_id"],
        "schedule": row["schedule"],
        "enabled": bool(row["enabled"]),
        "running": is_task_running(row["id"]),
        "schedule_desc": SCHEDULE_DESC.get(row["schedule"], row["schedule"]),
        "created_at": row["created_at"],
    }


@router.get("/screeners")
def get_screeners():
    return list_screeners()


@router.get("/watchlists")
def get_watchlists():
    try:
        from sources.pine_screener import fetch_watchlists
        return {"ok": True, "watchlists": fetch_watchlists()}
    except Exception as e:
        return {"ok": False, "watchlists": {}, "error": str(e)}


@router.get("")
def list_tasks():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    db.close()
    return [_row_to_dict(r) for r in rows]


@router.post("")
def create_task(body: TaskCreate):
    if body.type not in VALID_TYPES:
        raise HTTPException(400, f"Invalid type: {body.type}")
    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO tasks (name, type, config_json, actions_json, channel_id, schedule) VALUES (?, ?, ?, ?, ?, ?)",
        (body.name, body.type, json.dumps(body.config), json.dumps(body.actions), body.channel_id, body.schedule),
    )
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (cursor.lastrowid,)).fetchone()
    db.close()
    return _row_to_dict(row)


@router.put("/{task_id}")
def update_task(task_id: int, body: TaskUpdate):
    try:
        db = get_db(settings.db_path)
        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            db.close()
            raise HTTPException(404, "Task not found")

        old_schedule = row["schedule"]
        old_enabled = bool(row["enabled"])
        old_config = row["config_json"]

        updates, params = [], []
        if body.name is not None:
            updates.append("name = ?"); params.append(body.name)
        if body.config is not None:
            updates.append("config_json = ?"); params.append(json.dumps(body.config))
        if body.actions is not None:
            updates.append("actions_json = ?"); params.append(json.dumps(body.actions))
        if body.schedule is not None:
            updates.append("schedule = ?"); params.append(body.schedule)
        if body.channel_id is not None:
            updates.append("channel_id = ?"); params.append(body.channel_id)
        if body.enabled is not None:
            updates.append("enabled = ?"); params.append(int(body.enabled))

        if updates:
            updates.append("updated_at = datetime('now')")
            params.append(task_id)
            db.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params)
            db.commit()

        row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        db.close()

        # Only reschedule if enabled/schedule/config actually changed
        new_enabled = bool(row["enabled"])
        schedule_changed = (body.schedule is not None and body.schedule != old_schedule)
        enabled_changed = (body.enabled is not None and new_enabled != old_enabled)
        config_changed = (body.config is not None and json.dumps(body.config) != old_config)

        try:
            if enabled_changed or schedule_changed or config_changed:
                if new_enabled:
                    _start_job(row)
                else:
                    remove_task_job(task_id)
        except Exception as e:
            print(f"[tasks] Reschedule failed: {e}")

        return _row_to_dict(row)
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(500, detail=f"Update failed: {e}")


@router.delete("/{task_id}")
def delete_task(task_id: int):
    try:
        remove_task_job(task_id)
    except Exception as e:
        print(f"[tasks] remove_task_job failed: {e}")
    db = get_db(settings.db_path)
    # Delete in FK order: children first, then parent
    signal_ids = [r["id"] for r in db.execute("SELECT id FROM signals WHERE task_id = ?", (task_id,)).fetchall()]
    if signal_ids:
        placeholders = ",".join("?" * len(signal_ids))
        db.execute(f"DELETE FROM outcomes WHERE signal_id IN ({placeholders})", signal_ids)
        db.execute(f"DELETE FROM snapshots WHERE signal_id IN ({placeholders})", signal_ids)
        db.execute(f"DELETE FROM ai_analyses WHERE signal_id IN ({placeholders})", signal_ids)
        db.execute(f"DELETE FROM signals WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM push_logs WHERE task_id = ?", (task_id,))
    db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.post("/{task_id}/start")
def start_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")
    db.execute("UPDATE tasks SET enabled = 1 WHERE id = ?", (task_id,))
    db.commit()
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    _start_job(row)
    return _row_to_dict(row)


@router.post("/{task_id}/stop")
def stop_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")
    db.execute("UPDATE tasks SET enabled = 0 WHERE id = ?", (task_id,))
    db.commit()
    remove_task_job(task_id)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    return _row_to_dict(row)


@router.post("/{task_id}/test")
def test_task(task_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Task not found")
    try:
        execute_task(task_id)
        result = get_last_result(task_id)
        return {"ok": True, "detail": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/{task_id}/history")
def task_history(task_id: int, limit: int = 50):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Task not found")

    signals = db.execute("""
        SELECT s.*, snap.price, snap.change_24h, snap.funding_rate,
               a.analysis_text, a.sentiment,
               o.change_1h, o.change_4h, o.change_24h
        FROM signals s
        LEFT JOIN snapshots snap ON snap.signal_id = s.id
        LEFT JOIN ai_analyses a ON a.signal_id = s.id
        LEFT JOIN outcomes o ON o.signal_id = s.id
        WHERE s.task_id = ?
        ORDER BY s.triggered_at DESC LIMIT ?
    """, (task_id, limit)).fetchall()

    push_logs = db.execute("""
        SELECT * FROM push_logs WHERE task_id = ?
        ORDER BY pushed_at DESC LIMIT ?
    """, (task_id, limit)).fetchall()

    db.close()

    return {
        "signals": [dict(s) for s in signals],
        "push_logs": [dict(p) for p in push_logs],
    }


def _start_job(row):
    schedule = row["schedule"]
    config = json.loads(row["config_json"])
    resolutions = config.get("resolutions", [schedule])
    if isinstance(resolutions, list) and len(resolutions) > 1:
        schedule = get_shortest_resolution(resolutions)
    add_task_job(row["id"], execute_task, schedule)


def start_all_enabled():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM tasks WHERE enabled = 1").fetchall()
    db.close()
    for row in rows:
        try:
            _start_job(row)
        except Exception as e:
            print(f"[scheduler] Failed to start task {row['id']}: {e}")
