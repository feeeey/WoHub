"""
Hierarchical candle classification.

Four levels of increasing nuance. Each candle gets one tag per level.

    L0 — pure close vs open: 阳(1) / 阴(0)
    L1 — body-aware:          阳线 / 阴线 / 十字
    L2 — direction inferred:  看涨 / 看跌 / 无方向
    L3 — wick prominence:     长上影 / 长下影 / 双长影 / 无显著影线

Parallel to the 14 named patterns — patterns answer "what specific structure
is this?", classification answers "what does this candle look like at a glance?".
"""
from dataclasses import dataclass, asdict
from typing import Any

from klines.models import Candle


# ---- thresholds ----

L1_DOJI_THRESHOLD = 0.10        # body / range below this -> 十字
LONG_WICK_RATIO = 0.40          # >= 40% of range to be "long"
SHORT_WICK_RATIO = 0.20         # opposite wick must be < 20% for single-side "long"
DOUBLE_WICK_RATIO = 0.30        # both wicks >= 30% -> 双长影


# ---- L2 lookup table ----

_L2_MAP = {
    ("阳线", "长上影"):    "看跌",
    ("阳线", "长下影"):    "看涨",
    ("阳线", "双长影"):    "无方向",
    ("阳线", "无显著影线"): "看涨",
    ("阴线", "长上影"):    "看跌",
    ("阴线", "长下影"):    "看涨",
    ("阴线", "双长影"):    "无方向",
    ("阴线", "无显著影线"): "看跌",
    ("十字", "长上影"):    "看跌",
    ("十字", "长下影"):    "看涨",
    ("十字", "双长影"):    "无方向",
    ("十字", "无显著影线"): "无方向",
}


@dataclass
class CandleClassification:
    l0: str
    l1: str
    l2: str
    l3: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify(candle: Candle) -> CandleClassification:
    rng = candle.range
    body = candle.body

    # L0 — binary; tiebreak (close == open) goes to 阳.
    l0 = "阳(1)" if candle.close >= candle.open else "阴(0)"

    # L1 — body must be a meaningful fraction of range to count as a real body.
    if rng <= 0 or body / rng < L1_DOJI_THRESHOLD:
        l1 = "十字"
    elif candle.close > candle.open:
        l1 = "阳线"
    else:
        l1 = "阴线"

    # L3 — wick prominence.
    if rng <= 0:
        l3 = "无显著影线"
    else:
        uw = candle.upper_wick / rng
        lw = candle.lower_wick / rng
        if uw >= LONG_WICK_RATIO and lw < SHORT_WICK_RATIO:
            l3 = "长上影"
        elif lw >= LONG_WICK_RATIO and uw < SHORT_WICK_RATIO:
            l3 = "长下影"
        elif uw >= DOUBLE_WICK_RATIO and lw >= DOUBLE_WICK_RATIO:
            l3 = "双长影"
        else:
            l3 = "无显著影线"

    # L2 — small lookup that bakes in light TA folk-wisdom.
    l2 = _L2_MAP.get((l1, l3), "无方向")

    return CandleClassification(l0=l0, l1=l1, l2=l2, l3=l3)
