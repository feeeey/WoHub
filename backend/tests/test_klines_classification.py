"""
Tests for the hierarchical candle classification (L0 / L1 / L2 / L3).
"""
from klines.classification import classify, CandleClassification
from klines.models import Candle


def c(o, h, l, cl):
    return Candle(open_time=0, close_time=1, open=o, high=h, low=l, close=cl, volume=0, closed=True)


# ---- L0: pure close vs open ----

def test_l0_yang_when_close_above_open():
    assert classify(c(100, 105, 99, 104)).l0 == "阳(1)"


def test_l0_yin_when_close_below_open():
    assert classify(c(100, 101, 95, 96)).l0 == "阴(0)"


def test_l0_yang_when_equal():
    # close == open -> tiebreaker: 阳(1)
    assert classify(c(100, 101, 99, 100)).l0 == "阳(1)"


# ---- L1: body-aware (introduces 十字) ----

def test_l1_yang_xian_when_body_meaningful():
    # body=4, range=6 -> body/range = 0.67 -> 阳线
    res = classify(c(100, 105, 99, 104))
    assert res.l1 == "阳线"


def test_l1_yin_xian_when_body_meaningful():
    res = classify(c(100, 101, 95, 96))
    assert res.l1 == "阴线"


def test_l1_doji_when_body_tiny_even_if_l0_is_yang():
    # body=0.02, range=1.0 -> 0.02 < 0.10 -> 十字; but L0 is 阳
    res = classify(c(100.0, 100.5, 99.5, 100.02))
    assert res.l1 == "十字"
    assert res.l0 == "阳(1)"


# ---- L3: wick prominence ----

def test_l3_long_lower_wick():
    # body 99.5..99.7, range 5.0, lower_wick=4.5 (>=0.4*5=2), upper_wick=0.3 (<0.2*5=1)
    res = classify(c(99.5, 100.0, 95.0, 99.7))
    assert res.l3 == "长下影"


def test_l3_long_upper_wick():
    # body 99.7..99.9, range 5.5, upper_wick=5.1, lower_wick=0.2
    res = classify(c(99.7, 105.0, 99.5, 99.9))
    assert res.l3 == "长上影"


def test_l3_double_long_wick():
    # body 100..100.2, range 4.0, upper_wick=1.8, lower_wick=2.0 — both >= 0.3*4=1.2
    res = classify(c(100.0, 102.0, 98.0, 100.2))
    assert res.l3 == "双长影"


def test_l3_no_significant_wick():
    # body 100..104, range 4.2, tiny wicks
    res = classify(c(100.0, 104.1, 99.9, 104.0))
    assert res.l3 == "无显著影线"


# ---- L2: derived from L1 + L3 ----

def test_l2_yang_xian_with_long_lower_is_bullish():
    # 阳线 with real body (close>open) + long lower wick — open=98, close=99,
    # low=96.5, high=99.2 -> body/range=0.37, lower_wick=0.56, upper_wick=0.07
    res = classify(c(98.0, 99.2, 96.5, 99.0))
    assert res.l1 == "阳线"
    assert res.l3 == "长下影"
    assert res.l2 == "看涨"


def test_l2_yang_xian_with_long_upper_is_bearish():
    # 阳线 with real body + long upper wick — open=98, close=99 (body=1),
    # high=104 (huge upper wick), low=97.8 (tiny lower wick)
    res = classify(c(98.0, 104.0, 97.8, 99.0))
    assert res.l1 == "阳线"
    assert res.l3 == "长上影"
    assert res.l2 == "看跌"


def test_l2_yang_xian_strong_no_wick_is_bullish():
    # marubozu-ish 阳: strongly bullish
    res = classify(c(100.0, 104.1, 99.9, 104.0))
    assert res.l1 == "阳线"
    assert res.l3 == "无显著影线"
    assert res.l2 == "看涨"


def test_l2_yin_xian_with_long_upper_is_bearish():
    # 阴线 with real body + long upper wick — open=99, close=98 (red body),
    # high=104 (huge upper wick)
    res = classify(c(99.0, 104.0, 97.8, 98.0))
    assert res.l1 == "阴线"
    assert res.l3 == "长上影"
    assert res.l2 == "看跌"


def test_l2_yin_xian_with_long_lower_is_bullish():
    # 阴线 with real body + long lower wick — open=99, close=98 (red body),
    # low=94 (huge lower wick), high=99.2 (tiny upper wick)
    res = classify(c(99.0, 99.2, 94.0, 98.0))
    assert res.l1 == "阴线"
    assert res.l3 == "长下影"
    assert res.l2 == "看涨"


def test_l2_yin_xian_strong_no_wick_is_bearish():
    res = classify(c(104.0, 104.1, 99.9, 100.0))
    assert res.l1 == "阴线"
    assert res.l3 == "无显著影线"
    assert res.l2 == "看跌"


def test_l2_double_wick_always_neutral():
    res = classify(c(100.0, 102.0, 98.0, 100.2))
    assert res.l3 == "双长影"
    assert res.l2 == "无方向"


def test_l2_doji_no_wick_is_neutral():
    # almost-perfect doji
    res = classify(c(100.0, 100.05, 99.95, 100.0))
    assert res.l1 == "十字"
    assert res.l2 == "无方向"


# ---- type sanity ----

def test_classify_returns_dataclass_with_all_fields():
    res = classify(c(100, 105, 99, 104))
    assert isinstance(res, CandleClassification)
    assert res.l0 and res.l1 and res.l2 and res.l3
    d = res.to_dict() if hasattr(res, "to_dict") else None
    if d is not None:
        assert set(d.keys()) >= {"l0", "l1", "l2", "l3"}
