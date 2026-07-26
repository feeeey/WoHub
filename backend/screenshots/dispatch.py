"""截图推送模块 —— 把截图分发到一个或多个通知渠道。

与 channels/sender.py 的分工：sender 是「一张图 → 一个渠道」的传输原语，
这里负责「一批图 → 一批渠道」的编排：逐渠道隔离失败、渲染 caption、
汇总写 push_logs。telegram 和 discord 都经由 sender.send_photo，
两者的差异（Bot API vs REST multipart）在 channels/ 层已经抹平。
"""

import json

from app_logger import log as applog
from channels.sender import send_photo
from config import settings
from database import get_db

DEFAULT_CAPTION = "📸 {symbol} {timeframe}"


def resolve_channels(channel_ids) -> tuple:
    """按 id 取渠道配置。返回 (channels, missing_ids)。

    只解析 telegram / discord —— webhook 类型没有图片上传语义，直接算作不可用。
    """
    if not channel_ids:
        return [], []
    ids = [int(c) for c in channel_ids]
    placeholders = ",".join("?" * len(ids))
    db = get_db(settings.db_path)
    try:
        rows = db.execute(
            f"SELECT id, type, name, config_json, enabled FROM channels WHERE id IN ({placeholders})",
            ids,
        ).fetchall()
    finally:
        db.close()

    found = {}
    for r in rows:
        if r["type"] not in ("telegram", "discord"):
            continue
        found[r["id"]] = {
            "id": r["id"],
            "type": r["type"],
            "name": r["name"],
            "config": json.loads(r["config_json"]),
            "enabled": bool(r["enabled"]),
        }
    # 保持调用方给定的顺序
    channels = [found[i] for i in ids if i in found]
    missing = [i for i in ids if i not in found]
    return channels, missing


def push_shots(shots, channels, caption=None, task_id=None) -> list:
    """把 shots 推到每个 channel。返回逐渠道的结果列表。

    失败是分层隔离的：某张图失败不影响同渠道其余图，某渠道失败不影响其他渠道。
    每个渠道汇总写一条 push_logs（image_paths 存该渠道实际送达的文件名 JSON）。
    """
    results = []
    if not shots or not channels:
        return results

    for ch in channels:
        sent, errors, message_ids = [], [], []
        for shot in shots:
            try:
                mid = send_photo(ch["type"], ch["config"], shot["file_path"],
                                 caption=_render_caption(caption, shot))
                sent.append(shot["filename"])
                message_ids.append(mid)
            except Exception as e:
                msg = f"{shot['symbol']} {shot['timeframe']}: {e}"
                errors.append(msg)
                applog("screenshots", "error",
                       f"推送到渠道 {ch['name']}({ch['type']}) 失败 — {msg}")

        ok = bool(sent) and not errors
        _log_push(task_id, ch, sent, errors, shots)
        results.append({
            "channel_id": ch["id"],
            "channel_name": ch["name"],
            "type": ch["type"],
            "ok": ok,
            "sent": len(sent),
            "failed": len(errors),
            "message_ids": message_ids,
            "errors": errors,
        })
    return results


def capture_and_push(symbol, timeframes=None, channel_ids=None, caption=None,
                     task_id=None, signal_id=None, source="manual") -> dict:
    """截图 + 推送的组合入口。REST 层和手动触发都走这里。

    截图失败时不会尝试推送；渠道解析不到的 id 进 missing_channels，不算致命错误。
    """
    from screenshots import service

    result = service.capture(symbol, timeframes, task_id=task_id,
                             signal_id=signal_id, source=source)
    channels, missing = resolve_channels(channel_ids)
    result["missing_channels"] = missing
    result["pushes"] = push_shots(result["shots"], channels, caption=caption,
                                  task_id=task_id) if result["shots"] else []
    return result


def _render_caption(template, shot) -> str:
    """支持 {symbol} / {timeframe} 占位符。用字面替换而非 str.format，
    避免用户 caption 里的裸花括号触发 KeyError。"""
    tpl = template if template else DEFAULT_CAPTION
    return (tpl.replace("{symbol}", shot["symbol"])
               .replace("{timeframe}", shot["timeframe"]))


def _log_push(task_id, channel, sent, errors, shots):
    """一个渠道一条日志。status 只用 success/failed 两态 —— 渠道历史 UI 依赖它，
    部分成功的明细放在 error_message 里。"""
    status = "success" if sent and not errors else "failed"
    content = f"截图推送 {len(sent)}/{len(shots)} 张"
    error_message = "; ".join(errors)[:1000] if errors else None
    try:
        db = get_db(settings.db_path)
        db.execute(
            "INSERT INTO push_logs (task_id, channel_id, content_text, image_paths, status, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, channel["id"], content, json.dumps(sent), status, error_message),
        )
        db.commit()
        db.close()
    except Exception as e:
        applog("screenshots", "error", f"push_logs 写入失败: {e}")
