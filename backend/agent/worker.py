"""Agent worker: single daemon thread draining agent_runs. Never runs in
the APScheduler pool; own short-lived DB connections; clean shutdown."""
import json
import sys
import threading
from app_logger import log as applog
from agent.queue import claim_next, fail_run

_stop = threading.Event()
_thread = None


def _process_run(run_row):
    from agent.config import load_config
    from agent.agent_decider import run_agent_on_context
    cfg = load_config()
    if not cfg.enabled or not cfg.api_key:
        fail_run(run_row["id"], "agent disabled or api_key missing")
        return
    context = json.loads(run_row["context_json"] or "{}")
    out = run_agent_on_context(run_row["id"], context, cfg)
    applog("agent", "info",
           f"run #{run_row['id']}: {out['decisions']} decisions, {out['reused']} reused")
    _maybe_push_verdict(run_row, cfg)


def _maybe_push_verdict(run_row, cfg):
    """可选跟随消息：非 skip 裁决摘要。经 sender 抽象（channel 无关）+ HTML 转义。失败仅记日志。"""
    if not cfg.push_verdict or not run_row["task_id"]:
        return
    try:
        import html
        from database import get_db
        from config import settings
        from channels.sender import send_text
        db = get_db(settings.db_path)
        try:
            task = db.execute(
                """SELECT c.type, c.config_json FROM tasks t JOIN channels c ON c.id = t.channel_id
                   WHERE t.id = ?""", (run_row["task_id"],)).fetchone()
            ds = db.execute(
                "SELECT symbol, timeframe, direction, confidence FROM agent_decisions "
                "WHERE run_id = ? AND direction != 'skip'", (run_row["id"],)).fetchall()
        finally:
            db.close()
        if not task or not ds:
            return
        arrow = {"long": "📈 做多", "short": "📉 做空"}
        lines = [f"🤖 Agent 裁决（run #{run_row['id']}）:"]
        for d in ds:
            lines.append(html.escape(
                f"  {d['symbol']} @{d['timeframe']} → {arrow[d['direction']]}"
                f"（置信 {d['confidence']:.2f}）"))
        send_text(task["type"], json.loads(task["config_json"]), "\n".join(lines))
    except Exception as e:
        applog("agent", "warn", f"verdict push failed: {e}")


def _loop(interval):
    mod = sys.modules[__name__]
    while not _stop.wait(interval):
        try:
            row = claim_next()
            if row is None:
                continue
            try:
                mod._process_run(row)     # 模块属性调用，测试可 patch
            except Exception as e:
                applog("agent", "error", f"run #{row['id']} crashed: {e}")
                fail_run(row["id"], repr(e))
        except Exception as e:
            applog("agent", "error", f"worker loop: {e}")


def start_worker(interval=2.0):
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(interval,), daemon=True, name="agent-worker")
    _thread.start()


def stop_worker():
    _stop.set()
    if _thread:
        _thread.join(timeout=10)
