import os

from app_logger import log as applog
from channels.sender import send_photo
from config import settings
from database import get_db
from screenshots.client import chartshot_client


def capture_and_dispatch(task_id, symbol, timeframes, channel):
    """Capture screenshot(s) via ChartShot, push them to channel, persist to DB.

    Silent on failure: each step logs to app_logger and continues with the next file.
    """
    try:
        result = chartshot_client.screenshot(symbol, timeframes)
    except Exception as e:
        applog("screenshots", "error", f"Screenshot request failed for {symbol}: {e}")
        return

    if not result.get("ok"):
        applog("screenshots", "error", f"Screenshot failed for {symbol}: {result.get('error', 'unknown')}")
        return

    files = result.get("files") or []
    if not files:
        return

    for filename in files:
        tf = _parse_tf_from_filename(filename, timeframes)
        local_path = os.path.join(settings.screenshots_dir, filename)

        if not os.path.isfile(local_path):
            applog("screenshots", "error", f"Screenshot file not accessible at {local_path}")
            continue

        if channel:
            try:
                send_photo(channel["type"], channel["config"], local_path, caption=f"📸 {symbol} {tf}")
            except Exception as e:
                applog("screenshots", "error", f"send_photo failed for {symbol} {tf}: {e}")

        _record_screenshot(task_id, symbol, tf, local_path)


def get_screenshot_for_signal(signal_id):
    """Return absolute file_path of the most recent screenshot for this signal, or None."""
    try:
        db = get_db(settings.db_path)
        row = db.execute(
            "SELECT file_path FROM screenshots WHERE signal_id = ? ORDER BY created_at DESC LIMIT 1",
            (signal_id,),
        ).fetchone()
        db.close()
        return row["file_path"] if row else None
    except Exception as e:
        applog("screenshots", "error", f"Failed to read screenshot for signal {signal_id}: {e}")
        return None


def _parse_tf_from_filename(filename, candidates):
    """Extract timeframe from filename like '{symbol}_{tf}_{timestamp}.png'."""
    name = filename.rsplit(".", 1)[0]
    for part in name.split("_"):
        if part in candidates:
            return part
    return candidates[0] if candidates else "?"


def _record_screenshot(task_id, symbol, timeframe, file_path):
    """Persist screenshot row, linked to the latest matching signal if any."""
    try:
        db = get_db(settings.db_path)
        sig = db.execute(
            "SELECT id FROM signals WHERE task_id = ? AND symbol = ? AND timeframe = ? "
            "ORDER BY triggered_at DESC LIMIT 1",
            (task_id, symbol, timeframe),
        ).fetchone()
        signal_id = sig["id"] if sig else None
        db.execute(
            "INSERT INTO screenshots (signal_id, symbol, timeframe, file_path) "
            "VALUES (?, ?, ?, ?)",
            (signal_id, symbol, timeframe, file_path),
        )
        db.commit()
        db.close()
    except Exception as e:
        applog("screenshots", "error", f"Failed to record screenshot: {e}")
