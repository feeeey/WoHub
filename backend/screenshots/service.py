"""截图服务层 —— 内部统一入口。

`capture(symbol, ...)` 是全应用唯一的截图入口：归一化标的、校验周期、调
ChartShot、落库、返回结构化结果。executor、chat agent、REST 接口都走这里，
不再各自拼装 chartshot_client 的裸响应。

配套的 list_shots / get_shot / delete_shot 给了 `screenshots` 表读取面 ——
在此之前该表只写不读，捕获的截图在 UI 里无法访问。
"""

import os
import re

from app_logger import log as applog
from config import settings
from database import get_db
from screenshots.client import chartshot_client

# 与 services/chartshot/config.py 的 TIMEFRAME_MAP 保持一致。
# 两个服务跑在不同容器里，无法共享模块，所以这里是必要的重复 —— 改动需同步两处。
VALID_TIMEFRAMES = ("1m", "3m", "5m", "15m", "30m", "1h", "4h", "8h", "1d", "1w")
DEFAULT_TIMEFRAMES = ("1h",)

# ChartShot 产出的文件名形如 {SYMBOL}_{tf}_{YYYYmmdd}_{HHMMSS}.png
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def normalize_symbol(raw: str) -> str:
    """Pine 筛选返回 'BINANCE:BTCUSDT.P'，交易所前缀和永续后缀都要剥掉；
    非 BINANCE 前缀（如 'OANDA:XAUUSD'）保留原样交给 ChartShot 解析。"""
    s = (raw or "").strip().upper()
    if s.startswith("BINANCE:"):
        s = s[len("BINANCE:"):]
    if s.endswith(".P"):
        s = s[:-2]
    return s


def normalize_timeframes(raw) -> list:
    """去重保序 + 小写归一。空输入回落到默认周期。非法周期抛 ValueError。"""
    if not raw:
        return list(DEFAULT_TIMEFRAMES)
    out = []
    for tf in raw:
        t = str(tf).strip().lower()
        if not t:
            continue
        if t not in VALID_TIMEFRAMES:
            raise ValueError(f"不支持的周期: {tf}（可选: {', '.join(VALID_TIMEFRAMES)}）")
        if t not in out:
            out.append(t)
    if not out:
        return list(DEFAULT_TIMEFRAMES)
    return out


def file_url(filename: str) -> str:
    return f"/api/screenshots/file/{filename}"


def local_path(filename: str) -> str:
    return os.path.join(settings.screenshots_dir, filename)


def capture(symbol: str, timeframes=None, task_id=None, signal_id=None,
            source="manual") -> dict:
    """截图并落库。返回 {ok, symbol, timeframes, shots, errors, busy}。

    - 部分周期失败不影响其余：失败的周期进 errors，成功的照常返回。
    - ok 表示「至少拿到一张图」；调用方要判断完整性就比对 len(shots) 与周期数。
    - signal_id 为 None 时按 (task_id, symbol, timeframe) 反查最近一条信号；
      查不到就留空，此时 task_id 仍会写入，记录不会变成无归属的孤儿。
    - source="task" 的截图享有优先权；其余来源在定时任务截图进行中会被
      ChartShot 直接拒绝（busy=True），不会排队干等。
    """
    sym = normalize_symbol(symbol)
    if not sym:
        raise ValueError("symbol 不能为空")
    tfs = normalize_timeframes(timeframes)

    result = chartshot_client.screenshot(sym, tfs, source=source)
    if not result.get("ok"):
        err = result.get("error") or "unknown error"
        busy = bool(result.get("busy"))
        # 被定时任务挡下是预期内的调度行为，不是故障，按 warn 记
        applog("screenshots", "warn" if busy else "error",
               f"截图未完成 {sym} {tfs}: {err}")
        return {"ok": False, "symbol": sym, "timeframes": tfs, "shots": [],
                "errors": [err], "busy": busy}

    files = result.get("files") or []
    shots, errors = [], []

    for filename in files:
        tf = _parse_tf_from_filename(filename, tfs)
        path = local_path(filename)
        if not os.path.isfile(path):
            msg = f"截图文件不可读: {path}"
            applog("screenshots", "error", msg)
            errors.append(msg)
            continue
        row_id = _record(task_id, signal_id, sym, tf, path)
        if row_id is None:
            # 图已经拍出来了，不因落库失败丢掉它 —— 但要让调用方看见这条记录不可检索
            errors.append(f"{sym} {tf} 截图已生成但未能落库")
        shots.append({
            "id": row_id,
            "task_id": task_id,
            "symbol": sym,
            "timeframe": tf,
            "filename": filename,
            "file_path": path,
            "url": file_url(filename),
        })

    got = {s["timeframe"] for s in shots}
    for tf in tfs:
        if tf not in got:
            errors.append(f"{sym} {tf} 未产出截图")

    return {"ok": bool(shots), "symbol": sym, "timeframes": tfs,
            "shots": shots, "errors": errors, "busy": False}


def list_shots(symbol=None, task_id=None, timeframe=None, limit=50) -> list:
    """按条件倒序列出截图记录。"""
    where, params = [], []
    if symbol:
        where.append("symbol = ?")
        params.append(normalize_symbol(symbol))
    if task_id is not None:
        where.append("task_id = ?")
        params.append(task_id)
    if timeframe:
        where.append("timeframe = ?")
        params.append(str(timeframe).strip().lower())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    params.append(max(1, min(int(limit), 500)))

    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            f"SELECT * FROM screenshots {clause} ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_shot(r) for r in rows]
    finally:
        db.close()


def get_shot(shot_id: int):
    db = get_db(settings.db_path)
    try:
        row = db.execute("SELECT * FROM screenshots WHERE id = ?", (shot_id,)).fetchone()
        return _row_to_shot(row) if row else None
    finally:
        db.close()


def get_screenshot_for_signal(signal_id: int):
    """该信号最近一张截图的绝对路径，无则 None。"""
    db = get_db(settings.db_path)
    try:
        row = db.execute(
            "SELECT file_path FROM screenshots WHERE signal_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        return row["file_path"] if row else None
    except Exception as e:
        applog("screenshots", "error", f"读取信号 {signal_id} 的截图失败: {e}")
        return None
    finally:
        db.close()


def delete_shot(shot_id: int, remove_file: bool = True) -> bool:
    """删除记录，默认连磁盘文件一起删。记录不存在返回 False。"""
    shot = get_shot(shot_id)
    if not shot:
        return False
    db = get_db(settings.db_path)
    try:
        db.execute("DELETE FROM screenshots WHERE id = ?", (shot_id,))
        db.commit()
    finally:
        db.close()
    if remove_file and shot["file_path"]:
        try:
            os.remove(shot["file_path"])
        except OSError:
            pass  # 文件已不在或无权限：记录已删，不阻塞
    return True


def _row_to_shot(row) -> dict:
    filename = os.path.basename(row["file_path"] or "")
    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "signal_id": row["signal_id"],
        "symbol": row["symbol"],
        "timeframe": row["timeframe"],
        "filename": filename,
        "file_path": row["file_path"],
        "url": file_url(filename),
        "created_at": row["created_at"],
    }


def _parse_tf_from_filename(filename: str, candidates) -> str:
    """从 '{SYMBOL}_{tf}_{ts}.png' 里取周期。"""
    name = filename.rsplit(".", 1)[0]
    for part in name.split("_"):
        if part in candidates:
            return part
    return candidates[0] if candidates else "?"


def _resolve_signal_id(db, task_id, symbol, timeframe):
    if task_id is None:
        return None
    row = db.execute(
        "SELECT id FROM signals WHERE task_id = ? AND symbol = ? AND timeframe = ? "
        "ORDER BY triggered_at DESC LIMIT 1",
        (task_id, symbol, timeframe),
    ).fetchone()
    return row["id"] if row else None


def _record(task_id, signal_id, symbol, timeframe, file_path):
    """写入 screenshots 行，返回主键；失败返回 None（截图本身已成功，不该因落库失败丢掉）。"""
    db = get_db(settings.db_path)
    try:
        sid = signal_id if signal_id is not None else _resolve_signal_id(db, task_id, symbol, timeframe)
        cur = db.execute(
            "INSERT INTO screenshots (task_id, signal_id, symbol, timeframe, file_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, sid, symbol, timeframe, file_path),
        )
        db.commit()
        return cur.lastrowid
    except Exception as e:
        applog("screenshots", "error", f"截图落库失败 {symbol} {timeframe}: {e}")
        return None
    finally:
        db.close()
