import pytest
from unittest.mock import patch, MagicMock
from sources.pine_screener import (
    list_screeners,
    run_screener,
    build_cross_analysis,
)


def test_list_screeners():
    screeners = list_screeners()
    assert len(screeners) > 0
    for s in screeners:
        assert "folder_type" in s
        assert "screener_name" in s
        assert "label" in s
        assert s["folder_type"] in ("oscillator", "trend")


def test_list_screeners_has_directional_divergence():
    # The catch-all 顶底背离 (divergence) is hidden from the listed options;
    # the two directional variants take its place.
    screeners = list_screeners()
    names = [s["screener_name"] for s in screeners]
    assert "divergence_top" in names
    assert "divergence_bottom" in names
    assert "divergence" not in names


MOCK_RESPONSE_TEXT = '{"snapshot":{"symbols":[{"s":"BINANCE:BTCUSDT.P"},{"s":"BINANCE:ETHUSDT.P"}]}}\n'


def test_run_screener_parses_response():
    mock_resp = MagicMock()
    mock_resp.text = MOCK_RESPONSE_TEXT
    mock_resp.raise_for_status = MagicMock()

    with patch("sources.pine_screener._get_session") as mock_sess:
        mock_sess.return_value.post.return_value = mock_resp
        symbols = run_screener("oscillator", "divergence", "1h", 12345)
        assert "BINANCE:BTCUSDT.P" in symbols
        assert "BINANCE:ETHUSDT.P" in symbols


def test_run_screener_invalid_folder():
    with pytest.raises(ValueError, match="folder_type"):
        run_screener("invalid", "divergence", "1h", 12345)


def test_run_screener_invalid_resolution():
    with pytest.raises(ValueError, match="resolution"):
        run_screener("oscillator", "divergence", "99h", 12345)


def test_cross_analysis_screener_overlap():
    results = [
        {"label": "A", "resolution": "1h", "symbols": ["BTC", "ETH", "SOL"]},
        {"label": "B", "resolution": "1h", "symbols": ["BTC", "SOL", "DOGE"]},
        {"label": "C", "resolution": "1h", "symbols": ["BTC"]},
    ]
    analysis = build_cross_analysis(results)
    assert "BTC" in analysis["screener_overlap"]
    assert len(analysis["screener_overlap"]["BTC"]) == 3
    assert "SOL" in analysis["screener_overlap"]


def test_cross_analysis_full_overlap():
    results = [
        {"label": "A", "resolution": "1h", "symbols": ["BTC", "ETH"]},
        {"label": "B", "resolution": "1h", "symbols": ["BTC", "SOL"]},
    ]
    analysis = build_cross_analysis(results)
    assert "BTC" in analysis["full_overlap"]
    assert "ETH" not in analysis["full_overlap"]
