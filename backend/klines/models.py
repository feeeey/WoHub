from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Candle:
    open_time: int
    close_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    closed: bool

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)

    @property
    def upper_wick(self) -> float:
        return self.high - self.body_top

    @property
    def lower_wick(self) -> float:
        return self.body_bottom - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    def metrics(self) -> dict[str, float]:
        return {
            "body_top": self.body_top,
            "body_bottom": self.body_bottom,
            "upper_wick": self.upper_wick,
            "lower_wick": self.lower_wick,
            "body": self.body,
            "range": self.range,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatternMatch:
    name: str
    name_zh: str
    category: str            # "single" | "double" | "triple"
    direction: str           # "bullish" | "bearish" | "neutral"
    indices: list[int]       # negative indices into the candle list
    on_closed: bool
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
