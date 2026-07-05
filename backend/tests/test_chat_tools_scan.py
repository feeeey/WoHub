import pytest
from unittest.mock import patch
from agent import tools as T


def test_scan_iterates_combos_with_progress_and_cross():
    calls, progress = [], []

    def fake_run(folder, name, res, wl):
        calls.append((folder, name, res, wl))
        return ["BINANCE:BTCUSDT.P"] if name == "oversold_zone" else []

    with patch("sources.pine_screener.run_screener", side_effect=fake_run):
        out = T.run_screener_scan(
            ["oscillator/oversold_zone", "oscillator/overbought_zone"],
            ["1h", "4h"], watchlist_id=7,
            progress_cb=lambda d, t, n: progress.append((d, t, n)))
    assert len(calls) == 4 and calls[0] == ("oscillator", "oversold_zone", "1h", 7)
    assert [p[:2] for p in progress] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert len(out["results"]) == 4
    # 同一筛选器的不同周期命中同符号，在 resolution_overlap 中记录
    assert len(out["cross"]["resolution_overlap"]) > 0    # oversold_zone at 1h+4h both hit BTC
    assert out["cross"]["full_overlap"] == ["BINANCE:BTCUSDT.P"]  # Only oversold found it
    assert out["errors"] == []


def test_scan_per_combo_error_isolated():
    def fake_run(folder, name, res, wl):
        raise RuntimeError("cookie expired")
    with patch("sources.pine_screener.run_screener", side_effect=fake_run):
        out = T.run_screener_scan(["oscillator/oversold_zone"], ["1h"], 0)
    assert out["results"] == [] and len(out["errors"]) == 1
    assert "cookie" in out["errors"][0]["error"]


def test_scan_rejects_bad_key_and_too_many_combos():
    out = T.run_screener_scan(["not/exists"], ["1h"], 0)
    assert "error" in out
    out = T.run_screener_scan(["oscillator/oversold_zone"] * 5, ["5m", "15m", "1h"], 0)
    assert "error" in out                                   # 15 combos > 12


def test_account_overview_readonly_wrap():
    with patch("trading.service.get_account",
               return_value={"env": "testnet", "total_wallet_balance": 100.0,
                             "available_balance": 90.0, "total_unrealized_pnl": 1.0,
                             "balances": [], "positions": [{"symbol": "BTCUSDT"}]}), \
         patch("trading.service.list_open_orders", return_value=[{"orderId": 1}]):
        out = T.account_overview(credential_id=3)
    assert out["env"] == "testnet" and out["open_orders"] == [{"orderId": 1}]


def test_account_overview_error_returned():
    with patch("trading.service.get_account", side_effect=RuntimeError("auth")):
        out = T.account_overview(credential_id=3)
    assert "error" in out


def test_progress_cb_exception_propagates():
    """取消机制依赖回调异常向外传播——绝不能被工具吞掉。"""
    class Stop(Exception):
        pass

    def cb(done, total, note):
        raise Stop()

    with patch("sources.pine_screener.run_screener", return_value=[]):
        with pytest.raises(Stop):
            T.run_screener_scan(["oscillator/oversold_zone"], ["1h"], 0, progress_cb=cb)


def test_scan_mixed_batch_isolation_and_truncation():
    def fake_run(folder, name, res, wl):
        if res == "1h":
            raise RuntimeError("boom")
        return [f"S{i}" for i in range(150)]

    with patch("sources.pine_screener.run_screener", side_effect=fake_run):
        out = T.run_screener_scan(["oscillator/oversold_zone"], ["1h", "4h"], 0)
    assert len(out["errors"]) == 1 and len(out["results"]) == 1
    assert len(out["results"][0]["symbols"]) == 100
