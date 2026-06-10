from dataclasses import dataclass, field, asdict
from typing import Any, Literal


# Type aliases — kept as plain str literals so they survive JSON round-trips.
OrderSide = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT"]
Env = Literal["testnet", "mainnet"]
MarginType = Literal["ISOLATED", "CROSSED"]


@dataclass
class OrderRequest:
    """User-facing intent. The service layer translates this into the actual
    fapi /fapi/v1/order payload after applying leverage + margin-type prep."""
    symbol: str                          # "BTCUSDT", uppercase
    side: OrderSide                      # "BUY" or "SELL"
    order_type: OrderType                # "MARKET" or "LIMIT"
    quantity: float                      # contract qty (e.g. 0.001 BTC)
    price: float | None = None           # required when order_type == "LIMIT"
    leverage: int = 1                    # 1..125, applied via /fapi/v1/leverage
    margin_type: MarginType = "ISOLATED"
    reduce_only: bool = False

    def validate(self) -> None:
        if self.order_type == "LIMIT" and (self.price is None or self.price <= 0):
            raise ValueError("LIMIT order requires a positive price")
        if self.quantity <= 0:
            raise ValueError("quantity must be > 0")
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"unknown side: {self.side}")
        # MARKET / LIMIT are user-facing; STOP_MARKET / TAKE_PROFIT_MARKET are
        # used internally for SL/TP protection orders (paired with closePosition).
        if self.order_type not in ("MARKET", "LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"):
            raise ValueError(f"unknown order_type: {self.order_type}")
        if not (1 <= self.leverage <= 125):
            raise ValueError("leverage must be in [1, 125]")


@dataclass
class OrderResult:
    """Outcome of placing one order."""
    ok: bool
    binance_order_id: str | None = None
    status: str | None = None             # NEW / FILLED / PARTIALLY_FILLED / CANCELED / ...
    executed_qty: float = 0.0
    avg_price: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    warning: str | None = None       # non-fatal note, e.g. margin type left unchanged

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Position:
    symbol: str
    position_amt: float                  # signed; > 0 = long, < 0 = short
    entry_price: float
    mark_price: float
    unrealized_pnl: float
    leverage: int
    margin_type: str                     # "isolated" or "cross" per fapi

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Balance:
    asset: str
    wallet_balance: float
    available_balance: float
    unrealized_pnl: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BracketOrderResult:
    """Outcome of an entry-plus-protection submission.

    The entry is the source of truth — if it fails, neither SL nor TP is
    attempted. If the entry succeeds, SL and TP are best-effort: a failure
    on either does NOT undo the entry; the caller sees the failure and
    decides what to do (typically: place the missing protection manually).
    """
    ok: bool
    entry: OrderResult
    stop_loss: OrderResult | None = None
    take_profit: OrderResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entry": self.entry.to_dict(),
            "stop_loss": self.stop_loss.to_dict() if self.stop_loss else None,
            "take_profit": self.take_profit.to_dict() if self.take_profit else None,
        }
