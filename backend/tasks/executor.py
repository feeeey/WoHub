import json
import traceback
from datetime import datetime, timezone
from database import get_db
from config import settings
from sources.pine_screener import run_screener, build_cross_analysis
from channels.sender import send_text, send_photo
from sources.chart_shot_client import chartshot_client
from app_logger import log as applog

# Store last execution result for the test endpoint
_last_results = {}


def get_last_result(task_id):
    return _last_results.get(task_id)


def execute_task(task_id):
    db = get_db(settings.db_path)
    row = db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        db.close()
        return

    task_type = row["type"]
    config = json.loads(row["config_json"])
    actions = json.loads(row["actions_json"])
    channel_id = row["channel_id"]

    channel = None
    if channel_id:
        ch_row = db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,)).fetchone()
        if ch_row:
            channel = {
                "type": ch_row["type"],
                "config": json.loads(ch_row["config_json"]),
            }
    db.close()

    try:
        if task_type == "watchlist_signal":
            _exec_watchlist_signal(task_id, config, actions, channel)
        elif task_type == "market_scan":
            _exec_market_scan(task_id, config, actions, channel)
        elif task_type == "anomaly_watch":
            _exec_anomaly_watch(task_id, config, actions, channel)
        elif task_type == "scheduled_shot":
            _exec_scheduled_shot(task_id, config, actions, channel)
        else:
            print(f"[executor] Unknown task type: {task_type}")
    except Exception as e:
        print(f"[executor] Task {task_id} failed: {e}")
        traceback.print_exc()
        _log_push(task_id, channel, f"Task execution failed: {e}", status="failed", error=str(e))


def _exec_watchlist_signal(task_id, config, actions, channel):
    watchlist_id = config.get("watchlist_id", 0)
    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])

    all_results = []
    for res in resolutions:
        for sc in screeners:
            try:
                symbols = run_screener(sc["folder_type"], sc["screener_name"], res, watchlist_id)
                label = sc.get("label", sc["screener_name"])
                all_results.append({"label": label, "resolution": res, "symbols": symbols, "count": len(symbols)})
                applog("executor", "info", f"Screener {label} ({res}): {len(symbols)} symbols")
            except Exception as e:
                applog("executor", "error", f"Screener error: {e}")

    if not all_results:
        _last_results[task_id] = {"results": [], "signals": {}, "message": "无筛选结果"}
        return

    analysis = build_cross_analysis(all_results)
    overlaps = analysis.get("screener_overlap", {})

    # For single-screener tasks, use all found symbols as signals (no overlap requirement)
    if len(screeners) <= 1:
        signals = {}
        for r in all_results:
            for sym in r["symbols"]:
                signals.setdefault(sym, []).append(r["label"])
    else:
        signals = overlaps

    # Store results for test endpoint
    _last_results[task_id] = {
        "results": [{"label": r["label"], "resolution": r["resolution"], "count": r["count"]} for r in all_results],
        "signals": {sym: labels for sym, labels in list(signals.items())[:20]},
        "total_signals": len(signals),
        "message": "",
    }

    if not signals:
        _last_results[task_id]["message"] = "无信号命中"
        return

    # Build message
    lines = [f"🔔 信号触发 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    for sym, labels in sorted(signals.items(), key=lambda x: -len(x[1]))[:50]:
        clean_sym = sym.replace("BINANCE:", "").replace(".P", "")
        lines.append(f"  {clean_sym} → {' · '.join(labels)}")
    lines.append(f"\n共 {len(signals)} 个标的")
    message = "\n".join(lines)
    _last_results[task_id]["message"] = message

    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel:
        for sym in list(signals.keys())[:3]:
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            _take_and_send_screenshot(task_id, clean, resolutions, channel)

    _record_signals(task_id, signals, resolutions, all_results)

    if "ai_analysis" in actions:
        import threading
        t = threading.Thread(target=_run_ai_and_edit, args=(task_id, signals, channel, message), daemon=True)
        t.start()


def _exec_market_scan(task_id, config, actions, channel):
    from sources.exchanges import fetch_all_tickers

    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])
    overlap_threshold = config.get("overlap_threshold", 2)
    watchlist_id = config.get("watchlist_id", 0)

    all_results = []
    for res in resolutions:
        for sc in screeners:
            try:
                symbols = run_screener(sc["folder_type"], sc["screener_name"], res, watchlist_id)
                label = sc.get("label", sc["screener_name"])
                all_results.append({"label": label, "resolution": res, "symbols": symbols, "count": len(symbols)})
            except Exception as e:
                print(f"[executor] Screener error: {e}")

    analysis = build_cross_analysis(all_results)
    overlaps = {sym: labels for sym, labels in analysis.get("screener_overlap", {}).items() if len(labels) >= overlap_threshold}

    if not overlaps and not all_results:
        return

    lines = [f"📊 全市场扫描 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    for r in all_results:
        lines.append(f"  {r['label']} ({r['resolution']}): {r['count']} 命中")
    if overlaps:
        lines.append(f"\n🎯 {len(overlaps)} 个标的 ≥{overlap_threshold} 信号叠加:")
        for sym, labels in sorted(overlaps.items(), key=lambda x: -len(x[1])):
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            lines.append(f"  {clean} ({len(labels)}): {' · '.join(labels)}")
    message = "\n".join(lines)

    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel and overlaps:
        shot_threshold = config.get("screenshot_threshold", 3)
        for sym in list(overlaps.keys()):
            if len(overlaps[sym]) >= shot_threshold:
                clean = sym.replace("BINANCE:", "").replace(".P", "")
                _take_and_send_screenshot(task_id, clean, resolutions[:1], channel)

    _record_signals(task_id, overlaps, resolutions, all_results)

    if "ai_analysis" in actions:
        import threading
        t = threading.Thread(target=_run_ai_and_edit, args=(task_id, overlaps, channel, message), daemon=True)
        t.start()


def _exec_anomaly_watch(task_id, config, actions, channel):
    from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

    monitor_type = config.get("monitor_type", "price_change")
    threshold = config.get("threshold", 10.0)
    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])
    watchlist_id = config.get("watchlist_id", 0)

    anomalies = []
    if monitor_type == "price_change":
        tickers, _ = fetch_all_tickers()
        anomalies = [t for t in tickers if abs(t["priceChangePercent"]) >= threshold and t["volume24h"] >= settings.min_volume_24h]
    elif monitor_type == "funding_rate":
        rates, _ = fetch_all_funding_rates()
        anomalies = [r for r in rates if abs(r["fundingRate"]) >= threshold / 10000]

    if not anomalies:
        return

    signal_hits = {}
    for sc in screeners:
        for res in resolutions:
            try:
                symbols = run_screener(sc["folder_type"], sc["screener_name"], res, watchlist_id)
                label = sc.get("label", sc["screener_name"])
                for sym in symbols:
                    signal_hits.setdefault(sym, []).append(label)
            except Exception:
                pass

    matches = []
    for a in anomalies:
        sym = a["symbol"]
        full_sym = f"BINANCE:{sym}.P"
        if full_sym in signal_hits:
            matches.append({**a, "signals": signal_hits[full_sym]})

    if not matches and "text_summary" not in actions:
        return

    lines = [f"⚠️ 异常行情监控 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    lines.append(f"发现 {len(anomalies)} 个异常标的")
    if matches:
        lines.append(f"其中 {len(matches)} 个有信号配合:")
        for m in matches[:10]:
            lines.append(f"  {m['symbol']} ({m.get('priceChangePercent', 0):+.2f}%) → {' · '.join(m['signals'])}")
    message = "\n".join(lines)

    if "text_summary" in actions and channel:
        _send_push(channel, message)
        _log_push(task_id, channel, message)

    if "chart_shot" in actions and channel:
        for m in matches[:3]:
            _take_and_send_screenshot(task_id, m["symbol"], resolutions[:1], channel)


def _exec_scheduled_shot(task_id, config, actions, channel):
    symbols = config.get("symbols", [])
    timeframes = config.get("timeframes", ["1h"])

    for sym in symbols:
        _take_and_send_screenshot(task_id, sym, timeframes, channel)


def _run_ai_and_edit(task_id, overlaps, channel, push_message):
    try:
        from actions.ai_analysis import run_ai_analysis

        db = get_db(settings.db_path)
        recent_signals = db.execute(
            "SELECT id, symbol FROM signals WHERE task_id = ? ORDER BY triggered_at DESC LIMIT 3",
            (task_id,),
        ).fetchall()
        db.close()

        ai_texts = []
        for sig in recent_signals:
            text = run_ai_analysis(sig["id"])
            if text:
                ai_texts.append(f"[{sig['symbol']}] {text}")

        if ai_texts and channel:
            ai_section = "\n\n🤖 AI 分析：\n" + "\n\n".join(ai_texts)
            try:
                from channels.telegram import TelegramChannel
                ch = TelegramChannel(
                    bot_token=channel["config"].get("bot_token", ""),
                    chat_id=channel["config"].get("chat_id", ""),
                )
                ch.send_text(ai_section)
            except Exception as e:
                print(f"[executor] AI push failed: {e}")
    except Exception as e:
        print(f"[executor] AI analysis failed: {e}")


def _send_push(channel, text):
    try:
        send_text(channel["type"], channel["config"], text)
    except Exception as e:
        print(f"[executor] Push failed: {e}")


def _take_and_send_screenshot(task_id, symbol, timeframes, channel):
    try:
        result = chartshot_client.screenshot(symbol, timeframes)
        if result.get("ok") and result.get("files"):
            for filename in result["files"]:
                photo_url = chartshot_client.screenshot_url(filename)
                _send_push(channel, f"📸 {symbol} 截图: {photo_url}")
    except Exception as e:
        print(f"[executor] Screenshot failed for {symbol}: {e}")


def _log_push(task_id, channel, content, status="success", error=None):
    try:
        db = get_db(settings.db_path)
        db.execute(
            "INSERT INTO push_logs (task_id, channel_id, content_text, status, error_message) VALUES (?, ?, ?, ?, ?)",
            (task_id, channel.get("id") if channel else None, content[:1000], status, error),
        )
        db.commit()
        db.close()
    except Exception:
        pass


def _record_signals(task_id, overlaps, resolutions, all_results):
    """Record signals to the signals table and schedule outcome tracking."""
    try:
        from tasks.tracker import record_snapshot, schedule_outcome_tracking

        db = get_db(settings.db_path)
        for sym, labels in overlaps.items():
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            exchange = "Binance"
            if "OKX:" in sym:
                exchange = "OKX"
            elif "BYBIT:" in sym:
                exchange = "Bybit"

            for label in labels:
                for res in resolutions:
                    cursor = db.execute(
                        "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (?, ?, ?, ?, ?)",
                        (task_id, clean, exchange, label, res),
                    )
                    signal_id = cursor.lastrowid

                    # Record snapshot and schedule outcome tracking
                    record_snapshot(signal_id, clean, exchange)
                    schedule_outcome_tracking(signal_id, clean, exchange)

        db.commit()
        db.close()
    except Exception as e:
        print(f"[executor] Failed to record signals: {e}")
