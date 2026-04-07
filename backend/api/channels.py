import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from database import get_db
from config import settings
from channels.sender import test_channel

router = APIRouter(prefix="/channels")


class ChannelCreate(BaseModel):
    type: str
    name: str
    config: dict


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None


@router.get("")
def list_channels():
    db = get_db(settings.db_path)
    rows = db.execute("SELECT * FROM channels ORDER BY created_at DESC").fetchall()
    db.close()
    result = []
    for r in rows:
        result.append({
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "config": json.loads(r["config_json"]),
            "enabled": bool(r["enabled"]),
            "created_at": r["created_at"],
        })
    return result


@router.post("")
def create_channel(body: ChannelCreate):
    if body.type not in ("telegram", "discord", "webhook"):
        raise HTTPException(400, f"Unsupported type: {body.type}")

    db = get_db(settings.db_path)
    cursor = db.execute(
        "INSERT INTO channels (type, name, config_json) VALUES (?, ?, ?)",
        (body.type, body.name, json.dumps(body.config)),
    )
    db.commit()
    ch_id = cursor.lastrowid
    row = db.execute("SELECT * FROM channels WHERE id = ?", (ch_id,)).fetchone()
    db.close()
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


@router.put("/{channel_id}")
def update_channel(channel_id: int, body: ChannelUpdate):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Channel not found")

    updates = []
    params = []
    if body.name is not None:
        updates.append("name = ?")
        params.append(body.name)
    if body.config is not None:
        updates.append("config_json = ?")
        params.append(json.dumps(body.config))
    if body.enabled is not None:
        updates.append("enabled = ?")
        params.append(int(body.enabled))

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(channel_id)
        db.execute(f"UPDATE channels SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()

    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    db.close()
    return {
        "id": row["id"],
        "type": row["type"],
        "name": row["name"],
        "config": json.loads(row["config_json"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


@router.delete("/{channel_id}")
def delete_channel(channel_id: int):
    db = get_db(settings.db_path)
    db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    db.commit()
    db.close()
    return {"ok": True}


@router.get("/{channel_id}/history")
def channel_history(channel_id: int, limit: int = 50):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    if not row:
        db.close()
        raise HTTPException(404, "Channel not found")

    logs = db.execute("""
        SELECT p.*, t.name as task_name
        FROM push_logs p
        LEFT JOIN tasks t ON t.id = p.task_id
        WHERE p.channel_id = ?
        ORDER BY p.pushed_at DESC LIMIT ?
    """, (channel_id, limit)).fetchall()
    db.close()

    return [
        {
            "id": r["id"],
            "task_name": r["task_name"] or "-",
            "content": (r["content_text"] or "")[:200],
            "status": r["status"],
            "error": r["error_message"],
            "pushed_at": r["pushed_at"],
        }
        for r in logs
    ]


@router.post("/{channel_id}/test")
def test_push(channel_id: int):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
    db.close()
    if not row:
        raise HTTPException(404, "Channel not found")

    config = json.loads(row["config_json"])
    result = test_channel(row["type"], config)
    return result
