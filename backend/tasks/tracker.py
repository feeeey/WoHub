"""
Signal outcome tracker.
After a signal is recorded, schedules delayed price lookups to track
what happened after the signal fired (1h, 4h, 24h later).
"""
import json
import time
import threading
from datetime import datetime, timezone
from database import get_db
from config import settings


def record_snapshot(signal_id, symbol, exchange):
    """Record market snapshot at signal time. Called by executor after recording signal."""
    try:
        from sources.exchanges import fetch_all_tickers, fetch_all_funding_rates

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


def schedule_outcome_tracking(signal_id, symbol, exchange):
    """Schedule delayed price lookups at 1h, 4h, 24h after signal."""
    delays = [
        (3600, "1h"),      # 1 hour
        (14400, "4h"),     # 4 hours
        (86400, "24h"),    # 24 hours
    ]

    for delay_seconds, label in delays:
        t = threading.Timer(
            delay_seconds,
            _check_outcome,
            args=[signal_id, symbol, exchange, label],
        )
        t.daemon = True
        t.start()


def _check_outcome(signal_id, symbol, exchange, period):
    """Called by timer to record price at 1h/4h/24h after signal."""
    try:
        from sources.exchanges import fetch_all_tickers

        tickers, _ = fetch_all_tickers()
        current_price = 0.0
        for t in tickers:
            if t["symbol"] == symbol and t["exchange"] == exchange:
                current_price = t["lastPrice"]
                break

        if current_price == 0:
            return

        # Get original snapshot price
        db = get_db(settings.db_path)
        snap = db.execute(
            "SELECT price FROM snapshots WHERE signal_id = ?", (signal_id,)
        ).fetchone()

        if not snap or not snap["price"]:
            db.close()
            return

        original_price = snap["price"]
        change_pct = ((current_price - original_price) / original_price) * 100

        # Check if outcome row exists
        outcome = db.execute(
            "SELECT id FROM outcomes WHERE signal_id = ?", (signal_id,)
        ).fetchone()

        if outcome:
            # Update existing row
            db.execute(
                f"UPDATE outcomes SET price_{period} = ?, change_{period} = ?, tracked_at = datetime('now') WHERE signal_id = ?",
                (current_price, round(change_pct, 4), signal_id),
            )
        else:
            # Create new row
            db.execute(
                f"INSERT INTO outcomes (signal_id, price_{period}, change_{period}) VALUES (?, ?, ?)",
                (signal_id, current_price, round(change_pct, 4)),
            )

        db.commit()
        db.close()
        print(f"[tracker] Outcome {period} for signal {signal_id}: {symbol} {change_pct:+.2f}%")

    except Exception as e:
        print(f"[tracker] Outcome check failed: {e}")
