"""跨会话长期记忆：用户偏好与稳定事实。

刻意用 SQLite 平表 + 全量注入 prompt，不做向量检索——单用户系统、
条数硬上限 50，全部记忆 ~2k token 以内直接进 system prompt，检索层
在这个规模是纯开销。写入面只有两个受控入口（remember/forget 工具），
内容长度与总条数都有硬上限，agent 写不满也写不爆。
"""
from app_logger import log as applog
from config import settings
from database import get_db

MAX_MEMORIES = 50
MAX_CONTENT_LEN = 200
CATEGORIES = ("preference", "fact")


def _db():
    return get_db(settings.db_path)


def list_memories() -> list[dict]:
    db = _db()
    try:
        rows = db.execute(
            "SELECT id, content, category, created_at FROM agent_memory "
            "ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def save_memory(content: str, category: str = "preference") -> dict:
    """存一条记忆。内容去首尾空白；完全相同的内容幂等返回已有 id。"""
    content = (content or "").strip()
    if not content:
        return {"error": "记忆内容不能为空"}
    if len(content) > MAX_CONTENT_LEN:
        return {"error": f"记忆过长（{len(content)} > {MAX_CONTENT_LEN} 字）——"
                         "长期记忆存结论，不存过程"}
    if category not in CATEGORIES:
        category = "preference"

    db = _db()
    try:
        dup = db.execute("SELECT id FROM agent_memory WHERE content = ?",
                         (content,)).fetchone()
        if dup:
            return {"id": dup["id"], "note": "已存在完全相同的记忆，未重复写入"}
        n = db.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0]
        if n >= MAX_MEMORIES:
            return {"error": f"记忆已满（{MAX_MEMORIES} 条上限）——"
                             "先用 forget 清理过时条目再存新的"}
        cur = db.execute(
            "INSERT INTO agent_memory (content, category) VALUES (?, ?)",
            (content, category))
        db.commit()
        return {"id": cur.lastrowid}
    except Exception as e:
        applog("agent", "error", f"save_memory failed: {e}")
        return {"error": f"记忆写入失败: {e}"}
    finally:
        db.close()


def forget_memory(memory_id) -> dict:
    try:
        memory_id = int(memory_id)
    except (TypeError, ValueError):
        return {"error": f"memory_id 必须是数字，收到 {memory_id!r}"}
    db = _db()
    try:
        cur = db.execute("DELETE FROM agent_memory WHERE id = ?", (memory_id,))
        db.commit()
        if cur.rowcount == 0:
            return {"error": f"记忆 #{memory_id} 不存在"}
        return {"ok": True, "deleted": memory_id}
    finally:
        db.close()


def render_block() -> str:
    """system prompt 的长期记忆段落；无记忆返回空串。"""
    rows = list_memories()
    if not rows:
        return ""
    lines = ["\n【长期记忆】（用户在过往会话里确认过的偏好与事实，回答时主动遵循；"
             "与当前指令冲突时以当前指令为准并提示矛盾）"]
    for r in rows:
        tag = "偏好" if r["category"] == "preference" else "事实"
        lines.append(f"- [#{r['id']}·{tag}] {r['content']}")
    return "\n".join(lines)
