from scripts.verify_testnet import wrong_side_stop_price, sub_min_notional_qty


def test_wrong_side_stop_long_is_above_entry():
    # long SL must be BELOW entry to be valid; a wrong-side stop is ABOVE.
    assert wrong_side_stop_price("long", 100.0, 0.1) > 100.0


def test_wrong_side_stop_short_is_below_entry():
    assert wrong_side_stop_price("short", 100.0, 0.1) < 100.0


def test_sub_min_notional_qty_below_minimum():
    # entry=100, min_notional=20, step=0.1 -> qty*100 must be < 20 and > 0
    qty = sub_min_notional_qty(100.0, 20.0, 0.1)
    assert qty > 0
    assert qty * 100.0 < 20.0


def test_sub_min_notional_qty_falls_back_to_one_step():
    # half-notional floors to zero -> fall back to a single step (still > 0)
    qty = sub_min_notional_qty(1_000_000.0, 5.0, 0.001)
    assert qty == 0.001
