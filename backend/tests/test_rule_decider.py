from agent.decider import SignalBatch, RuleDecider

RESULTS = [
    {"label": "底背离", "resolution": "1h", "symbols": ["BINANCE:AUSDT.P", "BINANCE:BUSDT.P"], "count": 2},
    {"label": "超卖",   "resolution": "1h", "symbols": ["BINANCE:AUSDT.P"], "count": 1},
    {"label": "底背离", "resolution": "4h", "symbols": ["BINANCE:CUSDT.P"], "count": 1},
]


def _batch(task_type="watchlist_signal", screeners=2, threshold=2):
    return SignalBatch(
        task_id=1, task_type=task_type,
        config={"resolutions": ["1h", "4h"], "overlap_threshold": threshold,
                "screeners": [{}] * screeners},
        results=RESULTS, bias_map={"底背离": "long", "超卖": "long"})


def test_watchlist_multi_screener_overlap():
    out = RuleDecider().decide(_batch())
    assert out.signals_by_res == {"1h": {"BINANCE:AUSDT.P": ["底背离", "超卖"]}, "4h": {}}


def test_watchlist_single_screener_passes_all():
    out = RuleDecider().decide(_batch(screeners=1))
    assert out.signals_by_res["1h"] == {"BINANCE:AUSDT.P": ["底背离", "超卖"], "BINANCE:BUSDT.P": ["底背离"]}


def test_market_scan_overlaps():
    out = RuleDecider().decide(_batch(task_type="market_scan"))
    assert out.overlaps == {"BINANCE:AUSDT.P": ["底背离", "超卖"]}


def test_decisions_direction_from_unanimous_bias():
    out = RuleDecider().decide(_batch())
    d = out.decisions[0]
    assert (d.symbol, d.timeframe, d.direction) == ("BINANCE:AUSDT.P", "1h", "long")
    assert d.confidence is None


def test_decisions_skip_on_conflicting_bias():
    b = _batch()
    b.bias_map = {"底背离": "long", "超卖": "short"}
    assert RuleDecider().decide(b).decisions[0].direction == "skip"


def test_watchlist_empty_results_yields_empty_per_res():
    b = SignalBatch(task_id=1, task_type="watchlist_signal",
                    config={"resolutions": ["1h", "4h"], "overlap_threshold": 2,
                            "screeners": [{}, {}]},
                    results=[])
    out = RuleDecider().decide(b)
    assert out.signals_by_res == {"1h": {}, "4h": {}}
    assert out.decisions == []
