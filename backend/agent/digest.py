"""有护栏的主动性：任务信号触发后的 AI 简评。

这是被删掉的「批量裁决层」的谨慎重建，但形态完全不同：不再有平行的
worker/队列/decider——简评就是在专用会话里自动创建的一个**普通 chat 轮次**，
预算、只读工具、红线、trace、评测全部自动继承。用户在 Chat 页能看到
简评的完整工具轨迹，跟人问的轮次一模一样。

护栏（每一条都可测）：
- agent 未启用 / 无渠道 → 跳过（任务本体的文字/截图推送不受影响）
- 同任务冷却窗口内不重复简评（复用 agent_config.cooldown_minutes）
- 简评会话已有排队/运行中的轮次 → 跳过，不堆积
- 信号列表截断进 prompt，简评要求引用后验统计、200 字内、不确定就明说
"""
from app_logger import log as applog
from config import settings
from database import get_db

DIGEST_SESSION_TITLE = "信号简评（自动）"
DIGEST_PREFIX = "[自动简评]"
MAX_TRIGGER_CHARS = 1200


def _db():
    return get_db(settings.db_path)


def _digest_session_id() -> int:
    from agent.chat import store
    db = _db()
    try:
        row = db.execute("SELECT id FROM chat_sessions WHERE title = ? LIMIT 1",
                         (DIGEST_SESSION_TITLE,)).fetchone()
    finally:
        db.close()
    return row["id"] if row else store.create_session(DIGEST_SESSION_TITLE)


def _in_cooldown(session_id: int, task_id: int, cooldown_minutes: int) -> bool:
    """同任务冷却：查简评会话里该任务最近一条触发消息的时间。"""
    if cooldown_minutes <= 0:
        return False
    db = _db()
    try:
        row = db.execute(
            """SELECT 1 FROM chat_messages
               WHERE session_id = ? AND role = 'user' AND content LIKE ?
                 AND created_at >= datetime('now', ?)
               LIMIT 1""",
            (session_id, f"{DIGEST_PREFIX} 任务#{task_id}%",
             f"-{cooldown_minutes} minutes")).fetchone()
        return row is not None
    finally:
        db.close()


def _task_name(task_id: int) -> str:
    db = _db()
    try:
        row = db.execute("SELECT name FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row["name"] if row else f"任务{task_id}"
    finally:
        db.close()


def enqueue_digest(task_id: int, summary_text: str, channel: dict | None) -> dict:
    """为一次任务触发排一个简评轮次。返回 {queued: turn_id} 或 {skipped: 原因}。

    永不抛异常——简评失败绝不能影响任务本体的推送流程。
    """
    try:
        from agent.config import load_config
        from agent.chat import store

        cfg = load_config()
        if not cfg.enabled or not cfg.main_channel or not cfg.main_channel.api_key:
            return {"skipped": "agent 未启用或未配置 LLM 渠道"}

        sid = _digest_session_id()
        if _in_cooldown(sid, task_id, cfg.cooldown_minutes):
            return {"skipped": f"冷却中（{cfg.cooldown_minutes} 分钟内已简评过该任务）"}
        if store.active_turn(sid):
            return {"skipped": "简评会话已有轮次在排队/运行，不堆积"}

        name = _task_name(task_id)
        content = (
            f"{DIGEST_PREFIX} 任务#{task_id}《{name}》触发信号：\n"
            f"{(summary_text or '').strip()[:MAX_TRIGGER_CHARS]}\n\n"
            "请给出简评：结合语义档案的后验统计评估这批信号的整体可信度，"
            "点出最值得关注的 1~3 个标的并给出可复核的理由；200 字以内；"
            "证据不足就明说，不要硬给方向。")
        mid = store.add_message(sid, "user", content)
        turn_id = store.create_turn(
            sid, mid, push_channel_id=channel.get("id") if channel else None)
        applog("agent", "info", f"简评已排队：任务#{task_id} → turn#{turn_id}")
        return {"queued": turn_id}
    except Exception as e:
        applog("agent", "error", f"简评排队失败（任务#{task_id}）: {e!r}")
        return {"skipped": f"排队失败: {e}"}


def push_digest(push_channel_id: int, content: str, task_hint: str = "") -> bool:
    """把完成的简评推到通知渠道。失败只记日志（轮次本身已成功）。"""
    import json
    from channels.sender import send_text

    if not (content or "").strip():
        return False
    db = _db()
    try:
        row = db.execute("SELECT * FROM channels WHERE id = ?",
                         (push_channel_id,)).fetchone()
        if not row:
            applog("agent", "warn", f"简评推送渠道 {push_channel_id} 不存在")
            return False
        config = json.loads(row["config_json"])
        text = f"🤖 AI 简评{task_hint}\n{content.strip()}"
        send_text(row["type"], config, text)
        db.execute(
            "INSERT INTO push_logs (task_id, channel_id, content_text, status) "
            "VALUES (NULL, ?, ?, 'success')", (push_channel_id, text[:1000]))
        db.commit()
        return True
    except Exception as e:
        applog("agent", "error", f"简评推送失败: {e!r}")
        try:
            db.execute(
                "INSERT INTO push_logs (task_id, channel_id, content_text, status, error_message) "
                "VALUES (NULL, ?, ?, 'failed', ?)",
                (push_channel_id, (content or "")[:1000], str(e)[:500]))
            db.commit()
        except Exception:
            pass
        return False
    finally:
        db.close()
