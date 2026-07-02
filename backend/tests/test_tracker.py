import pytest
import tempfile
import os
import sqlite3
from unittest.mock import patch, MagicMock

# Need to set up test DB before importing tracker
_tmpdir = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(_tmpdir, "test.db"))

from database import init_db
from tasks.tracker import record_snapshot, run_outcome_check


MOCK_TICKERS = [
    {"symbol": "BTCUSDT", "lastPrice": 50000.0, "priceChangePercent": 5.0,
     "volume24h": 2000000000.0, "exchange": "Binance"},
]

MOCK_FUNDING = [
    {"symbol": "BTCUSDT", "fundingRate": 0.0001, "exchange": "Binance"},
]


def _mock_tickers():
    return MOCK_TICKERS[:], []


def _mock_funding():
    return MOCK_FUNDING[:], []


@pytest.fixture(autouse=True)
def setup_db():
    db_path = os.environ["DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    init_db(db_path)
    # Insert a test signal
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO signals (id, task_id, symbol, exchange, indicator, timeframe) VALUES (1, 1, 'BTCUSDT', 'Binance', 'test', '1h')"
    )
    conn.commit()
    conn.close()
    yield


@patch("tasks.tracker.fetch_all_tickers", side_effect=_mock_tickers)
@patch("tasks.tracker.fetch_all_funding_rates", side_effect=_mock_funding)
def test_record_snapshot(mock_fund, mock_tick):
    record_snapshot(1, "BTCUSDT", "Binance")

    from config import settings
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    snap = conn.execute("SELECT * FROM snapshots WHERE signal_id = 1").fetchone()
    conn.close()

    assert snap is not None
    assert snap["price"] == 50000.0
    assert snap["volume_24h"] == 2000000000.0
    assert snap["funding_rate"] == 0.0001


@patch("tasks.tracker.fetch_all_tickers", side_effect=_mock_tickers)
@patch("tasks.tracker.fetch_all_funding_rates", side_effect=_mock_funding)
def test_check_outcome(mock_fund, mock_tick):
    # First record snapshot
    record_snapshot(1, "BTCUSDT", "Binance")

    # Mock current price at 52000 (4% up)
    mock_current = [
        {"symbol": "BTCUSDT", "lastPrice": 52000.0, "exchange": "Binance",
         "priceChangePercent": 0, "volume24h": 0},
    ]
    with patch("tasks.tracker.fetch_all_tickers", return_value=(mock_current, [])):
        err = run_outcome_check(1, "BTCUSDT", "Binance", "1h")
    assert err is None

    from config import settings
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    outcome = conn.execute("SELECT * FROM outcomes WHERE signal_id = 1").fetchone()
    conn.close()

    assert outcome is not None
    assert outcome["price_1h"] == 52000.0
    assert outcome["change_1h"] == 4.0  # (52000-50000)/50000 * 100
