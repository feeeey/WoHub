"""
Candlestick pattern detection.

Detects 14 classical patterns (6 single, 5 double, 3 triple) on a chronological
list of Candles. By default, detection runs only on closed candles to avoid
repaint risk. Pass include_current=True to also detect on the (still-forming)
last candle.

Pattern indices in the returned PatternMatch are NEGATIVE offsets into the
input `candles` list (so [-1] is the most recent candle, [-2,-1] is the last two).
"""
from klines.models import Candle, PatternMatch

# ---------- tunable thresholds ----------

DOJI_BODY_RATIO = 0.10            # body / range <= 0.10
DOJI_MIN_WICK_RATIO = 0.15        # min(upper, lower) / range >= 0.15 (rules out dragonfly etc.)
MARUBOZU_BODY_RATIO = 0.95
HAMMER_BODY_IN_THIRD_RATIO = 0.5  # body_bottom >= low + 0.5*range (upper half)
HAMMER_WICK_TO_BODY = 2.0
HAMMER_OPP_WICK_TO_RANGE = 0.30   # opposite wick <= 0.30 * range
HAMMER_OPP_WICK_TO_BODY = 1.5     # opposite wick <= 1.5 * body
LONG_BODY_TO_RANGE = 0.5          # "long" body: body / range >= 0.5
HARAMI_BODY_RATIO = 0.6           # curr.body / prev.body <= 0.6
TREND_LOOKBACK = 3
TREND_THRESHOLD = 0.005           # 0.5% close-to-close change to count as up/down


# ---------- localization ----------

_ZH = {
    "Doji": "十字星",
    "Hammer": "锤子线",
    "InvertedHammer": "倒锤子",
    "ShootingStar": "流星",
    "HangingMan": "上吊线",
    "Marubozu": "光头光脚",
    "BullishEngulfing": "看涨吞没",
    "BearishEngulfing": "看跌吞没",
    "BullishHarami": "看涨孕线",
    "BearishHarami": "看跌孕线",
    "PiercingLine": "刺透形态",
    "DarkCloudCover": "乌云盖顶",
    "MorningStar": "启明星",
    "EveningStar": "黄昏星",
    "ThreeWhiteSoldiers": "红三兵",
    "ThreeBlackCrows": "黑三兵",
}


# ---------- helpers ----------

def _prior_trend(candles: list[Candle], i: int, lookback: int = TREND_LOOKBACK) -> str:
    """Slope of close prices over the `lookback` candles ending just before i."""
    start = max(0, i - lookback)
    prior = candles[start:i]
    if len(prior) < 2:
        return "flat"
    a = prior[0].close
    b = prior[-1].close
    if a <= 0:
        return "flat"
    slope = (b - a) / a
    if slope > TREND_THRESHOLD:
        return "up"
    if slope < -TREND_THRESHOLD:
        return "down"
    return "flat"


def _neg_idx(i: int, n: int) -> int:
    """Convert positive index to negative (relative to end of the list)."""
    return i - n


def _emit(matches, name, category, direction, idxs, n, candles, on_closed_input):
    """Build a PatternMatch with on_closed derived from the involved candles."""
    on_closed = all(candles[i].closed for i in idxs)
    if not on_closed_input and not on_closed:
        # caller said default-mode but somehow a non-closed got through; defensive
        return
    metrics = _build_metrics(candles, idxs)
    matches.append(PatternMatch(
        name=name,
        name_zh=_ZH.get(name, name),
        category=category,
        direction=direction,
        indices=[_neg_idx(i, n) for i in idxs],
        on_closed=on_closed,
        metrics=metrics,
    ))


def _build_metrics(candles: list[Candle], idxs: list[int]) -> dict:
    if len(idxs) == 1:
        m = candles[idxs[0]].metrics()
        return m
    # multi-candle: include per-candle metrics keyed by relative position
    labels = ["a", "b", "c"][: len(idxs)]
    return {label: candles[i].metrics() for label, i in zip(labels, idxs)}


# ---------- single-candle rules ----------

def _is_doji(cd: Candle) -> bool:
    if cd.range <= 0:
        return False
    if cd.body / cd.range > DOJI_BODY_RATIO:
        return False
    # rule out one-sided wicks (those are hammer / inverted hammer territory)
    min_wick = min(cd.upper_wick, cd.lower_wick)
    return min_wick / cd.range >= DOJI_MIN_WICK_RATIO


def _is_marubozu(cd: Candle) -> bool:
    if cd.range <= 0:
        return False
    return cd.body / cd.range >= MARUBOZU_BODY_RATIO


def _is_hammer_shape(cd: Candle) -> bool:
    """Body in upper portion + long lower wick + small upper wick.
    Differentiation between Hammer and HangingMan is by prior trend."""
    if cd.range <= 0:
        return False
    # body fully in upper half of range
    if cd.body_bottom < cd.low + HAMMER_BODY_IN_THIRD_RATIO * cd.range:
        return False
    # long lower wick: either >= 2x body, or >= 60% of range when body is tiny
    if cd.lower_wick < HAMMER_WICK_TO_BODY * cd.body and cd.lower_wick < 0.6 * cd.range:
        return False
    # small upper wick
    if cd.upper_wick > HAMMER_OPP_WICK_TO_RANGE * cd.range:
        return False
    if cd.body > 0 and cd.upper_wick > HAMMER_OPP_WICK_TO_BODY * cd.body:
        return False
    return True


def _is_inverted_hammer_shape(cd: Candle) -> bool:
    """Body in lower portion + long upper wick + small lower wick."""
    if cd.range <= 0:
        return False
    if cd.body_top > cd.high - HAMMER_BODY_IN_THIRD_RATIO * cd.range:
        return False
    if cd.upper_wick < HAMMER_WICK_TO_BODY * cd.body and cd.upper_wick < 0.6 * cd.range:
        return False
    if cd.lower_wick > HAMMER_OPP_WICK_TO_RANGE * cd.range:
        return False
    if cd.body > 0 and cd.lower_wick > HAMMER_OPP_WICK_TO_BODY * cd.body:
        return False
    return True


# ---------- two-candle rules ----------

def _is_bullish_engulfing(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bearish or not curr.is_bullish:
        return False
    if prev.range <= 0 or curr.range <= 0:
        return False
    # curr body covers prev body
    return curr.open <= prev.close and curr.close >= prev.open and curr.body > prev.body


def _is_bearish_engulfing(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bullish or not curr.is_bearish:
        return False
    if prev.range <= 0 or curr.range <= 0:
        return False
    return curr.open >= prev.close and curr.close <= prev.open and curr.body > prev.body


def _is_bullish_harami(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bearish or not curr.is_bullish:
        return False
    if prev.body <= 0 or curr.body <= 0:
        return False
    if prev.body / max(prev.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    # curr body fully inside prev body
    if not (curr.open >= prev.close and curr.close <= prev.open):
        return False
    return curr.body <= HARAMI_BODY_RATIO * prev.body


def _is_bearish_harami(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bullish or not curr.is_bearish:
        return False
    if prev.body <= 0 or curr.body <= 0:
        return False
    if prev.body / max(prev.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    if not (curr.open <= prev.close and curr.close >= prev.open):
        return False
    return curr.body <= HARAMI_BODY_RATIO * prev.body


def _is_piercing_line(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bearish or not curr.is_bullish:
        return False
    if prev.body / max(prev.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    mid = (prev.open + prev.close) / 2
    return (
        curr.open < prev.close          # opens below prev close (gap-ish down)
        and curr.close > mid            # closes above midpoint of prev body
        and curr.close < prev.open      # but does not fully engulf
    )


def _is_dark_cloud_cover(prev: Candle, curr: Candle) -> bool:
    if not prev.is_bullish or not curr.is_bearish:
        return False
    if prev.body / max(prev.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    mid = (prev.open + prev.close) / 2
    return (
        curr.open > prev.close
        and curr.close < mid
        and curr.close > prev.open
    )


# ---------- three-candle rules ----------

def _is_morning_star(a: Candle, b: Candle, d: Candle) -> bool:
    if not a.is_bearish or not d.is_bullish:
        return False
    if a.body / max(a.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    if d.body / max(d.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    # b is small relative to a
    if b.body > 0.5 * a.body:
        return False
    # b sits at or below a's close
    if max(b.open, b.close) > a.close:
        return False
    a_mid = (a.open + a.close) / 2
    return d.close > a_mid


def _is_evening_star(a: Candle, b: Candle, d: Candle) -> bool:
    if not a.is_bullish or not d.is_bearish:
        return False
    if a.body / max(a.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    if d.body / max(d.range, 1e-12) < LONG_BODY_TO_RANGE:
        return False
    if b.body > 0.5 * a.body:
        return False
    if min(b.open, b.close) < a.close:
        return False
    a_mid = (a.open + a.close) / 2
    return d.close < a_mid


def _is_three_white_soldiers(a: Candle, b: Candle, d: Candle) -> bool:
    if not (a.is_bullish and b.is_bullish and d.is_bullish):
        return False
    # closes strictly increasing
    if not (a.close < b.close < d.close):
        return False
    # each opens within prior body (not gap-ups too far)
    if not (a.open <= b.open <= a.close):
        return False
    if not (b.open <= d.open <= b.close):
        return False
    # bodies must be real (not all dojis)
    for cd in (a, b, d):
        if cd.range <= 0 or cd.body / cd.range < 0.4:
            return False
    return True


def _is_three_black_crows(a: Candle, b: Candle, d: Candle) -> bool:
    if not (a.is_bearish and b.is_bearish and d.is_bearish):
        return False
    if not (a.close > b.close > d.close):
        return False
    if not (a.close <= b.open <= a.open):
        return False
    if not (b.close <= d.open <= b.open):
        return False
    for cd in (a, b, d):
        if cd.range <= 0 or cd.body / cd.range < 0.4:
            return False
    return True


# ---------- public entry point ----------

def detect_patterns(candles: list[Candle], include_current: bool = False) -> list[PatternMatch]:
    """Detect classical candlestick patterns over the input list.

    Args:
      candles: chronological (oldest first).
      include_current: if False (default), only the closed prefix is considered.
                       If True, the in-progress last candle is also eligible.
    """
    if not candles:
        return []

    n = len(candles)
    if include_current:
        eligible = list(range(n))
    else:
        eligible = [i for i in range(n) if candles[i].closed]

    if not eligible:
        return []

    elig_set = set(eligible)
    matches: list[PatternMatch] = []

    def consecutive(*idxs: int) -> bool:
        # all idxs in elig_set AND form a consecutive run
        return all(i in elig_set for i in idxs) and all(
            idxs[k + 1] - idxs[k] == 1 for k in range(len(idxs) - 1)
        )

    for i in eligible:
        cd = candles[i]

        # ----- single -----
        if _is_doji(cd):
            _emit(matches, "Doji", "single", "neutral", [i], n, candles, include_current)
        if _is_marubozu(cd):
            direction = "bullish" if cd.is_bullish else "bearish"
            _emit(matches, "Marubozu", "single", direction, [i], n, candles, include_current)
        if _is_hammer_shape(cd):
            trend = _prior_trend(candles, i)
            if trend == "down":
                _emit(matches, "Hammer", "single", "bullish", [i], n, candles, include_current)
            elif trend == "up":
                _emit(matches, "HangingMan", "single", "bearish", [i], n, candles, include_current)
            else:
                # ambiguous prior trend — pick by candle's own colour as a tie-breaker
                if cd.is_bullish:
                    _emit(matches, "Hammer", "single", "bullish", [i], n, candles, include_current)
                else:
                    _emit(matches, "HangingMan", "single", "bearish", [i], n, candles, include_current)
        if _is_inverted_hammer_shape(cd):
            trend = _prior_trend(candles, i)
            if trend == "down":
                _emit(matches, "InvertedHammer", "single", "bullish", [i], n, candles, include_current)
            elif trend == "up":
                _emit(matches, "ShootingStar", "single", "bearish", [i], n, candles, include_current)
            else:
                if cd.is_bullish:
                    _emit(matches, "InvertedHammer", "single", "bullish", [i], n, candles, include_current)
                else:
                    _emit(matches, "ShootingStar", "single", "bearish", [i], n, candles, include_current)

        # ----- double -----
        if i >= 1 and consecutive(i - 1, i):
            prev, curr = candles[i - 1], candles[i]
            if _is_bullish_engulfing(prev, curr):
                _emit(matches, "BullishEngulfing", "double", "bullish", [i - 1, i], n, candles, include_current)
            if _is_bearish_engulfing(prev, curr):
                _emit(matches, "BearishEngulfing", "double", "bearish", [i - 1, i], n, candles, include_current)
            if _is_bullish_harami(prev, curr):
                _emit(matches, "BullishHarami", "double", "bullish", [i - 1, i], n, candles, include_current)
            if _is_bearish_harami(prev, curr):
                _emit(matches, "BearishHarami", "double", "bearish", [i - 1, i], n, candles, include_current)
            if _is_piercing_line(prev, curr):
                _emit(matches, "PiercingLine", "double", "bullish", [i - 1, i], n, candles, include_current)
            if _is_dark_cloud_cover(prev, curr):
                _emit(matches, "DarkCloudCover", "double", "bearish", [i - 1, i], n, candles, include_current)

        # ----- triple -----
        if i >= 2 and consecutive(i - 2, i - 1, i):
            a, b, d = candles[i - 2], candles[i - 1], candles[i]
            if _is_morning_star(a, b, d):
                _emit(matches, "MorningStar", "triple", "bullish", [i - 2, i - 1, i], n, candles, include_current)
            if _is_evening_star(a, b, d):
                _emit(matches, "EveningStar", "triple", "bearish", [i - 2, i - 1, i], n, candles, include_current)
            if _is_three_white_soldiers(a, b, d):
                _emit(matches, "ThreeWhiteSoldiers", "triple", "bullish", [i - 2, i - 1, i], n, candles, include_current)
            if _is_three_black_crows(a, b, d):
                _emit(matches, "ThreeBlackCrows", "triple", "bearish", [i - 2, i - 1, i], n, candles, include_current)

    return matches
