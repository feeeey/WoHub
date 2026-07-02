"""
Signal outcome tracker.
After a signal is recorded, persists due-check rows (1h/4h/24h) into
outcome_checks; the outcome poller thread executes them when due and
writes results into outcomes. Restart-safe (no in-memory timers).
"""
from database import get_db
from config import settings
from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates


def record_snapshot(signal_id, symbol, exchange):
    """Record market snapshot at signal time. Called by executor after recording signal."""
    try:
        tickers, _ = fetch_all_tickers()
        funding, _ = fetch_all_funding_rates()

        price = 0.0
        volume = 0.0
        change = 0.0
        rate = 0.0

        for t in tickers:
            if t["symbol"] == symbol and t["exchange"] == exchange:
                price = t["lastPrice"]
                volume = t["volume24h"]
                change = t["priceChangePercent"]
                break

        for f in funding:
            if f["symbol"] == symbol and f["exchange"] == exchange:
                rate = f["fundingRate"]
                break

        db = get_db(settings.db_path)
        db.execute(
            "INSERT INTO snapshots (signal_id, price, volume_24h, change_24h, funding_rate) VALUES (?, ?, ?, ?, ?)",
            (signal_id, price, volume, change, rate),
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"[tracker] Snapshot failed for signal {signal_id}: {e}")


HORIZONS = [("1h", "+1 hour"), ("4h", "+4 hours"), ("24h", "+1 day")]


def schedule_outcome_tracking(signal_id, symbol, exchange):
    """Persist due-checks; the outcome poller thread executes them when due.
    Survives process restarts (unlike the former in-memory Timers)."""
    db = get_db(settings.db_path)
    for horizon, offset in HORIZONS:
        db.execute(
            "INSERT INTO outcome_checks (signal_id, horizon, due_at) VALUES (?, ?, datetime('now', ?))",
            (signal_id, horizon, offset),
        )
    db.commit()
    db.close()


def run_outcome_check(signal_id, symbol, exchange, period) -> str | None:
    """Check price outcome for a signal at the given horizon period.

    Returns None on success, or an error string describing what went wrong.
    Called by the outcome poller thread (Task 3) when a check becomes due.
    """
    try:
        tickers, _ = fetch_all_tickers()
        current_price = 0.0
        for t in tickers:
            if t["symbol"] == symbol and t["exchange"] == exchange:
                current_price = t["lastPrice"]
                break

        if current_price == 0:
            return f"price miss: no ticker for {symbol}@{exchange}"

        db = get_db(settings.db_path)
        try:
            snap = db.execute(
                "SELECT price FROM snapshots WHERE signal_id = ?", (signal_id,)
            ).fetchone()

            if not snap:
                return f"snapshot missing for signal {signal_id}"
            if not snap["price"]:
                return f"snapshot price=0 for signal {signal_id}"

            original_price = snap["price"]
            change_pct = ((current_price - original_price) / original_price) * 100

            # Check if outcome row exists
            outcome = db.execute(
                "SELECT id FROM outcomes WHERE signal_id = ?", (signal_id,)
            ).fetchone()

            if outcome:
                db.execute(
                    f"UPDATE outcomes SET price_{period} = ?, change_{period} = ?, tracked_at = datetime('now') WHERE signal_id = ?",
                    (current_price, round(change_pct, 4), signal_id),
                )
            else:
                db.execute(
                    f"INSERT INTO outcomes (signal_id, price_{period}, change_{period}) VALUES (?, ?, ?)",
                    (signal_id, current_price, round(change_pct, 4)),
                )

            db.commit()
            print(f"[tracker] Outcome {period} for signal {signal_id}: {symbol} {change_pct:+.2f}%")
            return None
        finally:
            db.close()

    except Exception as e:
        return str(e)
