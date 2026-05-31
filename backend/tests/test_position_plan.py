import pytest
from trading.position_plan import (
    SymbolFilters, parse_filters, _round_step,
)


def _fake_info(symbol="BTCUSDT", notional_type="MIN_NOTIONAL"):
    return {"symbols": [{
        "symbol": symbol,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
            {"filterType": notional_type, "notional": "5"},
        ],
    }]}


def test_parse_filters_extracts_all_fields():
    f = parse_filters(_fake_info(), "BTCUSDT")
    assert f == SymbolFilters(tick_size=0.1, step_size=0.001, min_qty=0.001, min_notional=5.0)


def test_parse_filters_symbol_case_insensitive():
    f = parse_filters(_fake_info(), "btcusdt")
    assert f.tick_size == 0.1


def test_parse_filters_unknown_symbol_raises():
    with pytest.raises(ValueError):
        parse_filters(_fake_info(), "ETHUSDT")


def test_round_step_floor_ceil_nearest():
    assert _round_step(94.47, 0.10, "floor") == 94.4
    assert _round_step(94.41, 0.10, "ceil") == 94.5
    assert _round_step(108.44, 0.10, "nearest") == 108.4
    assert _round_step(108.46, 0.10, "nearest") == 108.5


def test_round_step_zero_step_is_passthrough():
    assert _round_step(123.456, 0, "floor") == 123.456
