"""评测运行管理：Settings 页触发、后台执行、结果落 eval_runs 表。

live 金标评测在**子进程**里跑（复用 `python -m evals --live` CLI）而不是
线程内：fixtures 会把 agent.tools 的数据函数 patch 成假数据，且 patch 是
进程级的——若在服务进程内跑，评测期间用户的任何 chat 轮次都会静默拿到
fixture 行情。子进程隔离让两者互不可见。

offline 报告零 LLM 调用、毫秒级，直接进程内同步跑。
"""
import json
import os
import subprocess
import sys
import tempfile
import threading

from app_logger import log as applog
from config import settings
from database import get_db

LIVE_TIMEOUT = 900          # 12 用例 × LLM 延迟，10 分钟兜底
_active_lock = threading.Lock()
_active_run_id: int | None = None    # 进程内唯一并发闸门；重启后自然清零


def _db():
    return get_db(settings.db_path)


def _row_to_dict(row, with_results=False) -> dict:
    d = {"id": row["id"], "kind": row["kind"], "status": row["status"],
         "prompt_version": row["prompt_version"], "model": row["model"],
         "error": row["error"], "created_at": row["created_at"],
         "finished_at": row["finished_at"],
         "summary": json.loads(row["summary_json"]) if row["summary_json"] else None}
    if with_results:
        d["results"] = json.loads(row["results_json"]) if row["results_json"] else None
    return d


def _mark_stale_runs() -> None:
    """running 但不属于当前进程的行 → failed（服务重启会遗留这种行）。"""
    db = _db()
    try:
        with _active_lock:
            active = _active_run_id
        db.execute(
            "UPDATE eval_runs SET status='failed', error='服务重启中断', "
            "finished_at=datetime('now') WHERE status='running' AND id != ?",
            (active if active is not None else -1,))
        db.commit()
    finally:
        db.close()


def list_runs(limit: int = 20) -> list[dict]:
    _mark_stale_runs()
    db = _db()
    try:
        rows = db.execute(
            "SELECT * FROM eval_runs ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 100)),)).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def get_run(run_id: int) -> dict | None:
    _mark_stale_runs()
    db = _db()
    try:
        row = db.execute("SELECT * FROM eval_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row, with_results=True) if row else None
    finally:
        db.close()


def delete_run(run_id: int) -> bool:
    db = _db()
    try:
        cur = db.execute("DELETE FROM eval_runs WHERE id = ? AND status != 'running'",
                         (run_id,))
        db.commit()
        return cur.rowcount > 0
    finally:
        db.close()


def _finish(run_id: int, status: str, *, prompt_version=None, model=None,
            summary=None, results=None, error=None) -> None:
    db = _db()
    try:
        db.execute(
            """UPDATE eval_runs SET status=?, prompt_version=COALESCE(?, prompt_version),
               model=COALESCE(?, model), summary_json=?, results_json=?, error=?,
               finished_at=datetime('now') WHERE id=?""",
            (status, prompt_version, model,
             json.dumps(summary, ensure_ascii=False) if summary is not None else None,
             json.dumps(results, ensure_ascii=False) if results is not None else None,
             error, run_id))
        db.commit()
    finally:
        db.close()


# ---- offline（同步，零费用）----

def run_offline(limit: int = 500) -> dict:
    from evals import report, runner
    from agent.chat.prompts import CHAT_PROMPT_VERSION

    db = _db()
    try:
        cur = db.execute("INSERT INTO eval_runs (kind, prompt_version) VALUES ('offline', ?)",
                         (CHAT_PROMPT_VERSION,))
        db.commit()
        run_id = cur.lastrowid
    finally:
        db.close()

    try:
        rows = runner.score_stored(limit=limit)
        summary = report.summarize_stored(rows)
        # 元组键转字符串供 JSON 序列化
        buckets = [{"prompt_version": k[0], "model": k[1], **v}
                   for k, v in sorted(summary.items())]
        _finish(run_id, "done", summary={"n_traces": len(rows), "buckets": buckets},
                results=rows)
    except Exception as e:
        applog("evals", "error", f"offline run #{run_id} failed: {e!r}")
        _finish(run_id, "failed", error=str(e)[:1000])
    return get_run(run_id)


# ---- live（子进程 + 后台线程）----

def _execute_live(run_id: int) -> None:
    global _active_run_id
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fd, out_path = tempfile.mkstemp(suffix=".json", prefix="eval-live-")
    os.close(fd)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "evals", "--live", "--json", out_path],
            cwd=backend_dir, capture_output=True, text=True, timeout=LIVE_TIMEOUT)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[-800:]
            _finish(run_id, "failed", error=f"exit {proc.returncode}: {err}")
            return
        with open(out_path, encoding="utf-8") as f:
            data = json.load(f)
        _finish(run_id, "done", prompt_version=data.get("prompt_version"),
                model=data.get("model"), summary=data.get("summary"),
                results=data.get("results"))
    except subprocess.TimeoutExpired:
        _finish(run_id, "failed", error=f"评测超时（>{LIVE_TIMEOUT}s）")
    except Exception as e:
        applog("evals", "error", f"live run #{run_id} failed: {e!r}")
        _finish(run_id, "failed", error=str(e)[:1000])
    finally:
        try:
            os.remove(out_path)
        except OSError:
            pass
        with _active_lock:
            _active_run_id = None


def start_live_run() -> tuple[int | None, str | None]:
    """启动金标实跑。返回 (run_id, None) 或 (None, 拒绝原因)。"""
    global _active_run_id
    from agent.config import load_config
    cfg = load_config()
    if not cfg.main_channel or not cfg.main_channel.api_key:
        return None, "未配置 LLM 渠道，先到上方 Agent 配置里设置"

    with _active_lock:
        if _active_run_id is not None:
            return None, f"已有评测在运行（#{_active_run_id}），请等它结束"
        db = _db()
        try:
            cur = db.execute("INSERT INTO eval_runs (kind, model) VALUES ('live', ?)",
                             (cfg.model,))
            db.commit()
            run_id = cur.lastrowid
        finally:
            db.close()
        _active_run_id = run_id

    threading.Thread(target=_execute_live, args=(run_id,), daemon=True,
                     name=f"eval-live-{run_id}").start()
    return run_id, None
