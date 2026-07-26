"""删除任务必须能真的删掉。

`get_db` 开了 `PRAGMA foreign_keys=ON`，所以 `DELETE FROM signals` 之前必须清空
每一张引用 `signals(id)` 的子表。漏掉 `outcome_checks` 曾导致「凡是产生过信号的
任务都永久删不掉」——而且异常未捕获，用户看到的是 500 而不是可读的错误。
"""
import re

import pytest

from config import settings
from database import SCHEMA, get_db


def _task_with_full_child_rows():
    """造一个把所有子表都写满的任务，模拟真实跑过一段时间的状态。"""
    db = get_db(settings.db_path)
    tid = db.execute(
        "INSERT INTO tasks (name, type) VALUES ('待删任务', 'watchlist_signal')").lastrowid
    sid = db.execute(
        "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) "
        "VALUES (?, 'BTCUSDT', 'Binance', '底背离', '1h')", (tid,)).lastrowid
    db.execute("INSERT INTO snapshots (signal_id, price) VALUES (?, 100.0)", (sid,))
    db.execute("INSERT INTO outcomes (signal_id, change_1h) VALUES (?, 1.5)", (sid,))
    db.execute("INSERT INTO outcome_checks (signal_id, horizon, due_at) "
               "VALUES (?, '4h', datetime('now'))", (sid,))
    db.execute("INSERT INTO screenshots (signal_id, task_id, symbol, timeframe, file_path) "
               "VALUES (?, ?, 'BTCUSDT', '1h', 'a.png')", (sid, tid))
    db.execute("INSERT INTO push_logs (task_id, content_text) VALUES (?, 'x')", (tid,))
    db.commit()
    db.close()
    return tid, sid


def test_every_table_referencing_signals_is_cleaned():
    """结构性护栏：新增引用 signals(id) 的表时，这个用例会提醒你更新 delete_task。"""
    import api.tasks as t
    import inspect

    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)\s*\((.*?)\n\);", SCHEMA, re.S)
    referencing = {name for name, body in tables
                   if re.search(r"REFERENCES\s+signals\s*\(", body)}
    src = inspect.getsource(t.delete_task)
    missing = {tbl for tbl in referencing if tbl not in src}
    assert not missing, f"delete_task 未清理这些引用 signals 的表：{missing}"


@pytest.mark.asyncio
async def test_delete_task_with_signals_succeeds(client):
    tid, _ = _task_with_full_child_rows()
    resp = await client.delete(f"/api/tasks/{tid}")
    assert resp.status_code == 200, f"删除失败：{resp.status_code} {resp.text}"
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_delete_removes_all_child_rows(client):
    tid, sid = _task_with_full_child_rows()
    await client.delete(f"/api/tasks/{tid}")

    db = get_db(settings.db_path)
    try:
        for table in ("outcomes", "snapshots", "screenshots", "outcome_checks"):
            n = db.execute(f"SELECT COUNT(*) c FROM {table} WHERE signal_id = ?",
                           (sid,)).fetchone()["c"]
            assert n == 0, f"{table} 残留 {n} 行"
        assert db.execute("SELECT COUNT(*) c FROM signals WHERE task_id = ?",
                          (tid,)).fetchone()["c"] == 0
        assert db.execute("SELECT COUNT(*) c FROM tasks WHERE id = ?",
                          (tid,)).fetchone()["c"] == 0
    finally:
        db.close()


@pytest.mark.asyncio
async def test_delete_task_without_signals_still_works(client):
    db = get_db(settings.db_path)
    tid = db.execute("INSERT INTO tasks (name, type) VALUES ('空任务', 'market_scan')").lastrowid
    db.commit()
    db.close()
    assert (await client.delete(f"/api/tasks/{tid}")).status_code == 200
