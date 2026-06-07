#!/usr/bin/env python
"""Testnet end-to-end verification for the WoHub trading path. (helpers; full
CLI added in the next task)."""
import os
import sys

# Make backend/ importable when run as `python scripts/verify_testnet.py`.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def wrong_side_stop_price(direction: str, entry_price: float, tick_size: float) -> float:
    """A stop price on the WRONG side of entry so Binance rejects the
    STOP_MARKET (error -2021 'would immediately trigger'). long -> above entry,
    short -> below entry. Rounded to tick. Used to demonstrate the
    entry-fills-but-SL-rejects 'naked position' gap."""
    from trading.position_plan import _round_step
    raw = entry_price * (1.05 if direction == "long" else 0.95)
    return _round_step(raw, tick_size, "nearest")


def sub_min_notional_qty(entry_price: float, min_notional: float, step_size: float) -> float:
    """A quantity whose notional is deliberately below min_notional (a filter
    rejection). Falls back to one step if half-notional floors to zero."""
    from trading.position_plan import _round_step
    qty = _round_step((min_notional * 0.5) / entry_price, step_size, "floor")
    if qty <= 0:
        qty = step_size
    return qty
