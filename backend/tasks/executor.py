import json
import traceback
from datetime import datetime, timezone
from database import get_db
from config import settings
from sources.pine_screener import run_screener, ScreenerUnavailable
from agent.decider import SignalBatch, RuleDecider, bias_map_for
from channels.sender import send_text
from screenshots import capture_and_dispatch
from app_logger import log as applog
from tasks.tracker import record_snapshot, schedule_outcome_tracking

# Store last execution result for the test endpoint
_last_results = {}

# 每次任务执行最多截几张图。全市场扫描可能命中上百个标的，逐个截图既跑不完
# （ChartShot 单 worker 串行，每张约 10s）也会把队列堵死，拖垮后续任务。
DEFAULT_MAX_SCREENSHOTS = 3
# 上限兜底：即使任务里配了更大的值也不放行
SCREENSHOT_HARD_CAP = 20
# 连续失败这么多次就放弃本轮剩余截图 —— ChartShot 卡住时继续投递只会加深积压
SCREENSHOT_FAILURE_STREAK = 2


def _run_screeners(task_id, screeners, resolutions, watchlist_id):
    """跑完所有 screener×timeframe 组合。返回 (成功结果, 失败说明)。

    失败与「跑成了但 0 命中」必须分开：把失败当空结果会让限流的一轮看起来
    像行情平静，用户据此以为没机会。失败明细进消息和 app 日志。
    """
    results, failures = [], []
    for res in resolutions:
        for sc in screeners:
            label = sc.get("label", sc["screener_name"])
            try:
                symbols = run_screener(sc["folder_type"], sc["screener_name"],
                                       res, watchlist_id)
            except ScreenerUnavailable as e:
                failures.append(f"{label}({res})")
                applog("executor", "error", f"任务 {task_id}: 筛选器未取到结果 — {e}")
                continue
            except Exception as e:
                failures.append(f"{label}({res})")
                applog("executor", "error", f"任务 {task_id}: 筛选器异常 {label}({res}): {e}")
                continue
            results.append({"label": label, "resolution": res,
                            "symbols": symbols, "count": len(symbols)})
            applog("executor", "info", f"Screener {label} ({res}): {len(symbols)} symbols")
    return results, failures


def _failure_note(failures):
    """失败明细的一行中文说明，附到推送消息尾部。"""
    if not failures:
        return ""
    return (f"\n\n⚠️ {len(failures)} 个筛选器本轮未取到结果："
            f"{' · '.join(failures[:6])}"
            f"{' 等' if len(failures) > 6 else ''}（结果不完整，勿据此判断行情平静）")


def _shot_limit(config):
    raw = config.get("max_screenshots", DEFAULT_MAX_SCREENSHOTS)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = DEFAULT_MAX_SCREENSHOTS
    return max(0, min(n, SCREENSHOT_HARD_CAP))


def _capture_batch(task_id, symbols, timeframes, channel, limit, total=None):
    """按上限逐个截图，连续失败即熔断。返回成功张数。

    symbols 已是清洗过的标的列表。total 是过滤前的候选数，仅用于日志说明被截断多少。
    """
    picked = list(symbols)[:limit]
    total = len(symbols) if total is None else total
    if total > len(picked):
        applog("screenshots", "info",
               f"任务 {task_id}: {total} 个候选标的，按上限只截前 {len(picked)} 个")

    ok_count, streak = 0, 0
    for sym in picked:
        result = capture_and_dispatch(task_id, sym, timeframes, channel)
        if result.get("shots"):
            ok_count += 1
            streak = 0
            continue
        streak += 1
        if streak >= SCREENSHOT_FAILURE_STREAK:
            applog("screenshots", "warn",
                   f"任务 {task_id}: 连续 {streak} 次截图失败，跳过本轮剩余 "
                   f"{len(picked) - picked.index(sym) - 1} 个标的")
            break
    return ok_count


def get_last_result(task_id):
    return _last_results.get(task_id)


def execute_task(task_id, resolution=None):
    """Execute a task. If resolution is given, only run that timeframe
    (used by per-resolution scheduled jobs). If None, run all configured
    resolutions (used by manual test endpoint)."""
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
                # id 必须带上：_log_push 用它写 push_logs.channel_id，
                # 缺了会让渠道历史页查不到任何任务推送记录
                "id": ch_row["id"],
                "name": ch_row["name"],
                "type": ch_row["type"],
                "config": json.loads(ch_row["config_json"]),
            }
    db.close()

    # Scope to single resolution if provided (scheduled invocation)
    if resolution is not None:
        config = {**config, "resolutions": [resolution]}

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
    is_single = len(screeners) <= 1

    # Run all screener×timeframe combos sequentially (TradingView API: no concurrency)
    all_results, failures = _run_screeners(task_id, screeners, resolutions, watchlist_id)

    if not all_results:
        msg = ("全部筛选器本轮均未取到结果（限流或接口异常），并非行情平静"
               if failures else "无筛选结果")
        _last_results[task_id] = {"results": [], "signals": {}, "message": msg,
                                  "failures": failures}
        if failures:
            applog("executor", "error", f"任务 {task_id}: {msg}：{' · '.join(failures)}")
            _log_push(task_id, channel, msg, status="failed",
                      error=" · ".join(failures))
        return

    batch = SignalBatch(task_id=task_id, task_type="watchlist_signal", config=config,
                        results=all_results, bias_map=bias_map_for(screeners))
    rule_out = RuleDecider().decide(batch)
    signals_by_res = rule_out.signals_by_res

    # Merge all timeframes into flat signal dict for recording/screenshots
    all_signals = {}  # {sym: [label(res), ...]}
    for res, sigs in signals_by_res.items():
        for sym, labels in sigs.items():
            tags = [f"{l}({res})" for l in labels] if not is_single else [f"{labels[0]}({res})"]
            all_signals.setdefault(sym, []).extend(tags)

    # Store results for test endpoint
    _last_results[task_id] = {
        "results": [{"label": r["label"], "resolution": r["resolution"], "count": r["count"]} for r in all_results],
        "signals": {sym: labels for sym, labels in list(all_signals.items())[:20]},
        "total_signals": len(all_signals),
        "message": "",
        "failures": failures,
    }

    if not all_signals:
        _last_results[task_id]["message"] = "无信号命中" + _failure_note(failures)
        return

    # Build message grouped by timeframe
    ts = datetime.now(timezone.utc).strftime('%m-%d %H:%M')
    lines = [f"🔔 信号触发 [{ts} UTC]"]
    for res in resolutions:
        sigs = signals_by_res.get(res, {})
        if not sigs:
            continue
        lines.append(f"\n📊 {res}:")
        for sym, labels in sorted(sigs.items(), key=lambda x: -len(x[1]))[:30]:
            clean_sym = sym.replace("BINANCE:", "").replace(".P", "")
            lines.append(f"  {clean_sym} → {' · '.join(labels)}")
        lines.append(f"  共 {len(sigs)} 个标的")
    lines.append(f"\n合计 {len(all_signals)} 个标的")
    message = "\n".join(lines) + _failure_note(failures)
    _last_results[task_id]["message"] = message

    if "text_summary" in actions and channel:
        _push_and_log(task_id, channel, message)

    entries = [(sym, label, res)
               for res, sigs in signals_by_res.items()
               for sym, labels in sigs.items() for label in labels]
    _record_signals(task_id, entries)

    # 不再要求 channel 存在：没配推送渠道时仍截图存档，供 UI / agent 事后调阅
    if "chart_shot" in actions:
        cleaned = [s.replace("BINANCE:", "").replace(".P", "") for s in all_signals]
        _capture_batch(task_id, cleaned, resolutions, channel, _shot_limit(config))

    if "agent_digest" in actions and all_signals:
        _maybe_digest(task_id, message, channel)


def _exec_market_scan(task_id, config, actions, channel):
    from sources.exchanges import fetch_all_tickers

    screeners = config.get("screeners", [])
    resolutions = config.get("resolutions", ["1h"])
    overlap_threshold = config.get("overlap_threshold", 2)  # 仅用于消息文案；过滤阈值由 RuleDecider 从 config 读取
    watchlist_id = config.get("watchlist_id", 0)

    all_results, failures = _run_screeners(task_id, screeners, resolutions, watchlist_id)

    batch = SignalBatch(task_id=task_id, task_type="market_scan", config=config,
                        results=all_results, bias_map=bias_map_for(screeners))
    rule_out = RuleDecider().decide(batch)
    overlaps = rule_out.overlaps

    if not overlaps and not all_results:
        if failures:
            msg = "全市场扫描：全部筛选器本轮均未取到结果（限流或接口异常），并非行情平静"
            applog("executor", "error", f"任务 {task_id}: {msg}：{' · '.join(failures)}")
            _log_push(task_id, channel, msg, status="failed",
                      error=" · ".join(failures))
        return

    lines = [f"📊 全市场扫描 [{datetime.now(timezone.utc).strftime('%m-%d %H:%M')} UTC]"]
    for r in all_results:
        lines.append(f"  {r['label']} ({r['resolution']}): {r['count']} 命中")
    if overlaps:
        lines.append(f"\n🎯 {len(overlaps)} 个标的 ≥{overlap_threshold} 信号叠加:")
        for sym, labels in sorted(overlaps.items(), key=lambda x: -len(x[1])):
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            lines.append(f"  {clean} ({len(labels)}): {' · '.join(labels)}")
    message = "\n".join(lines) + _failure_note(failures)

    if "text_summary" in actions and channel:
        _push_and_log(task_id, channel, message)

    entries = [(sym, r["label"], r["resolution"])
               for r in all_results for sym in r["symbols"] if sym in overlaps]
    _record_signals(task_id, entries)

    if "chart_shot" in actions and overlaps:
        shot_threshold = config.get("screenshot_threshold", 3)
        # 这里原本没有数量上限：全市场扫描命中上百个标的时会逐个截图，
        # 把 ChartShot 队列彻底堵死。现在按 max_screenshots 截断。
        cleaned = [sym.replace("BINANCE:", "").replace(".P", "")
                   for sym, labels in overlaps.items() if len(labels) >= shot_threshold]
        _capture_batch(task_id, cleaned, resolutions[:1], channel, _shot_limit(config))

    if "agent_digest" in actions and overlaps:
        _maybe_digest(task_id, message, channel)


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

    results, failures = _run_screeners(task_id, screeners, resolutions, watchlist_id)
    signal_hits = {}
    for r in results:
        for sym in r["symbols"]:
            signal_hits.setdefault(sym, []).append(r["label"])

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
    message = "\n".join(lines) + _failure_note(failures)

    if "text_summary" in actions and channel:
        _push_and_log(task_id, channel, message)

    # 不再要求 channel 存在：没配推送渠道时仍截图存档，供 UI / agent 事后调阅
    if "chart_shot" in actions:
        _capture_batch(task_id, [m["symbol"] for m in matches],
                       resolutions[:1], channel, _shot_limit(config))

    if "agent_digest" in actions and matches:
        _maybe_digest(task_id, message, channel)


def _maybe_digest(task_id, message, channel):
    """AI 简评入队。所有护栏（启用检查/冷却/防堆积）在 digest 模块内，
    这里只保证：简评的任何失败都不影响任务本体流程。"""
    try:
        from agent.digest import enqueue_digest
        out = enqueue_digest(task_id, message, channel)
        if "skipped" in out:
            applog("agent", "info", f"任务 {task_id} 简评跳过：{out['skipped']}")
    except Exception as e:
        applog("agent", "error", f"任务 {task_id} 简评入队异常: {e}")


def _exec_scheduled_shot(task_id, config, actions, channel):
    symbols = config.get("symbols", [])
    timeframes = config.get("timeframes", ["1h"])

    # 标的是用户显式配的，默认放行全部；但仍受 SCREENSHOT_HARD_CAP 和熔断保护
    limit = _shot_limit({"max_screenshots": config.get("max_screenshots", len(symbols))})
    _capture_batch(task_id, symbols, timeframes, channel, limit)


def _send_push(channel, text):
    """返回 (ok, error)。调用方必须把结果传给 _log_push —— 早期这里吞掉异常后
    _log_push 仍按默认的 success 落库，于是推送审计在最关键的故障上
    （信号根本没送达用户）恒显示正常。用 _push_and_log 就不会再写错。"""
    try:
        send_text(channel["type"], channel["config"], text)
        return True, None
    except Exception as e:
        applog("executor", "error",
               f"推送失败（渠道 {channel.get('name') or channel.get('id')}）: {e}")
        return False, str(e)


def _push_and_log(task_id, channel, text):
    """发送 + 按真实结果落 push_logs。两件事绑在一起，避免再次走偏。"""
    ok, err = _send_push(channel, text)
    _log_push(task_id, channel, text,
              status="success" if ok else "failed", error=err)
    return ok


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


def _record_signals(task_id, entries):
    """entries: list of (raw_symbol, label, resolution) — exact rows to insert.
    Returns {(clean_symbol, resolution): [signal_id, ...]} for decision linkage."""
    id_map = {}
    try:
        db = get_db(settings.db_path)
        pending = []
        for sym, label, res in entries:
            clean = sym.replace("BINANCE:", "").replace(".P", "")
            exchange = "Binance"
            if "OKX:" in sym:
                exchange = "OKX"
            elif "BYBIT:" in sym:
                exchange = "Bybit"
            cursor = db.execute(
                "INSERT INTO signals (task_id, symbol, exchange, indicator, timeframe) VALUES (?, ?, ?, ?, ?)",
                (task_id, clean, exchange, label, res),
            )
            pending.append((cursor.lastrowid, clean, exchange))
            id_map.setdefault((clean, res), []).append(cursor.lastrowid)
        db.commit()
        db.close()

        for signal_id, clean, exchange in pending:
            record_snapshot(signal_id, clean, exchange)
            schedule_outcome_tracking(signal_id, clean, exchange)
    except Exception as e:
        print(f"[executor] Failed to record signals: {e}")
        return {}
    return id_map
