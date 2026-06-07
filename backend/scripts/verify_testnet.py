#!/usr/bin/env python
"""Testnet end-to-end verification for the WoHub trading path.

Drives the SAME service layer the automation will reuse:
  add_credential -> build_position_plan -> place_order_bracket
  -> list_open_orders -> close_position

Binance USDT-M *testnet only*. Uses a throwaway temp DB (never touches
data/wohub.db). Places REAL testnet orders and cleans up (cancel + close) in a
finally block. The API secret is never printed.

Usage (PowerShell):
  $env:BINANCE_TESTNET_KEY="..."; $env:BINANCE_TESTNET_SECRET="..."
  python scripts/verify_testnet.py --symbol BTCUSDT --interval 15m

Usage (bash):
  BINANCE_TESTNET_KEY=... BINANCE_TESTNET_SECRET=... \
    python scripts/verify_testnet.py --symbol BTCUSDT
"""
import argparse
import getpass
import os
import shutil
import sys
import tempfile

# Make backend/ importable when run as `python scripts/verify_testnet.py`.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)


def wrong_side_stop_price(direction: str, entry_price: float, tick_size: float) -> float:
    """A stop price on the WRONG side of entry so Binance rejects the
    STOP_MARKET (error -2021 'would immediately trigger'). long -> above entry,
    short -> below entry. Rounded to tick."""
    from trading.position_plan import _round_step
    raw = entry_price * (1.05 if direction == "long" else 0.95)
    return _round_step(raw, tick_size, "nearest")


def sub_min_notional_qty(entry_price: float, min_notional: float, step_size: float) -> float:
    """A quantity whose notional is deliberately below min_notional. Falls back
    to one step if half-notional floors to zero."""
    from trading.position_plan import _round_step
    qty = _round_step((min_notional * 0.5) / entry_price, step_size, "floor")
    if qty <= 0:
        qty = step_size
    return qty


class Report:
    def __init__(self):
        self.rows = []

    def record(self, step, status, detail=""):
        self.rows.append((step, status, detail))
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARN": "[WARN]", "SKIP": "[SKIP]"}.get(status, "[?]")
        print(f"{mark} {step}" + (f" -- {detail}" if detail else ""))

    def ok(self):
        return all(s != "FAIL" for _, s, _ in self.rows)

    def summary(self):
        print("\n" + "=" * 60 + "\nSUMMARY")
        for step, status, detail in self.rows:
            print(f"  {status:4}  {step}" + (f" -- {detail}" if detail else ""))
        print("=" * 60)


def _confirm(symbol, assume_yes):
    if assume_yes:
        return True
    ans = input(f"This will place REAL *testnet* orders on {symbol}. Continue? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


def _cleanup(service, cred_id, symbol, quiet=False):
    """Best-effort: cancel all open orders for symbol, then close any position."""
    try:
        for o in service.list_open_orders(cred_id, symbol=symbol):
            oid = o.get("orderId")
            if oid is not None:
                try:
                    service.cancel_open_order(cred_id, symbol, oid)
                except Exception:
                    pass
    except Exception:
        pass
    try:
        res = service.close_position(cred_id, symbol)
        if not quiet:
            print(f"  cleanup: close_position {symbol} ok={res.ok}"
                  + ("" if res.ok else f" ({res.error})"))
    except Exception as e:
        if not quiet:
            print(f"  cleanup: close_position {symbol} error: {e}")


def main(argv=None):
    p = argparse.ArgumentParser(description="WoHub testnet E2E verification")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--interval", default="15m")
    p.add_argument("--risk-pct", type=float, default=1.0)
    p.add_argument("--rr", type=float, default=1.5)
    p.add_argument("--leverage", type=int, default=5)
    p.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p.add_argument("--keep", action="store_true", help="skip cleanup/teardown")
    args = p.parse_args(argv)
    symbol = args.symbol.upper()

    key = os.environ.get("BINANCE_TESTNET_KEY") or input("Binance TESTNET API key: ").strip()
    secret = os.environ.get("BINANCE_TESTNET_SECRET") or getpass.getpass("Binance TESTNET API secret: ").strip()
    if not key or not secret:
        print("Missing API key/secret.", file=sys.stderr)
        return 2
    if not _confirm(symbol, args.yes):
        print("Aborted.")
        return 1

    # Isolate: throwaway DB so we never touch the real one. Must be set before
    # importing config/service.
    tmpdir = tempfile.mkdtemp(prefix="wohub-verify-")
    os.environ["DB_PATH"] = os.path.join(tmpdir, "verify.db")

    rep = Report()
    service = None
    cred_id = None
    try:
        from database import init_db
        init_db(os.environ["DB_PATH"])
        from trading import service as service
        from trading import binance_client as bn
        from trading import position_plan as pp
        from trading.credentials import add_credential
        from trading.models import OrderRequest

        # Step 1: credential + reachability
        cred_id = add_credential("verify-testnet", "testnet", key, secret)
        info = service.test_credential(cred_id)
        if info["env"] != "testnet":
            rep.record("env is testnet", "FAIL", info["env"])
            return 1
        rep.record("credential reachable", "PASS",
                   f"env={info['env']} key=...{info['api_key_tail']}")

        _cleanup(service, cred_id, symbol, quiet=True)  # clear prior-run leftovers

        # Step 2: account snapshot
        acct = service.get_account(cred_id)
        equity = acct["total_wallet_balance"] + acct["total_unrealized_pnl"]
        rep.record("account snapshot", "PASS" if equity > 0 else "WARN",
                   f"equity={equity:.2f} avail={acct['available_balance']:.2f}")

        # Step 3: position plan (read-only)
        plan = service.build_position_plan(
            credential_id=cred_id, symbol=symbol, interval=args.interval,
            direction="long", order_type="MARKET", risk_pct=args.risk_pct,
            rr=args.rr, leverage=args.leverage,
        )
        if not plan["feasible"]:
            rep.record("position plan feasible", "FAIL", "; ".join(plan["warnings"]))
            return 1
        rep.record("position plan feasible", "PASS",
                   f"entry={plan['entry_price']} sl={plan['stop_price']} "
                   f"tp={plan['take_profit_price']} qty={plan['quantity']}")

        entry_price = plan["entry_price"]
        qty = plan["quantity"]
        filters = pp.parse_filters(bn.exchange_info("testnet", key), symbol)

        # Step 4: happy-path bracket (entry + SL + TP), then flatten
        br = service.place_order_bracket(
            cred_id,
            OrderRequest(symbol=symbol, side="BUY", order_type="MARKET",
                         quantity=qty, leverage=args.leverage),
            stop_loss_price=plan["stop_price"],
            take_profit_price=plan["take_profit_price"],
        )
        sl_ok = br.stop_loss is not None and br.stop_loss.ok
        tp_ok = br.take_profit is not None and br.take_profit.ok
        if br.entry.ok and sl_ok and tp_ok:
            opens = service.list_open_orders(cred_id, symbol=symbol)
            prot = [o for o in opens if o.get("type") in ("STOP_MARKET", "TAKE_PROFIT_MARKET")]
            rep.record("bracket entry+SL+TP", "PASS", f"{len(prot)} protective orders live")
        else:
            err = br.entry.error or (br.stop_loss and br.stop_loss.error) or (br.take_profit and br.take_profit.error)
            rep.record("bracket entry+SL+TP", "FAIL",
                       f"entry={br.entry.ok} sl={sl_ok} tp={tp_ok} err={err}")
        _cleanup(service, cred_id, symbol, quiet=True)

        # Step 5: deliberate filter rejection (sub-min-notional)
        bad_qty = sub_min_notional_qty(entry_price, filters.min_notional, filters.step_size)
        rej = service.place_order(cred_id, OrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=bad_qty, leverage=args.leverage))
        if not rej.ok and rej.error:
            rep.record("filter rejection surfaced", "PASS", rej.error[:120])
        else:
            rep.record("filter rejection surfaced", "FAIL", f"expected rejection, got ok={rej.ok}")
            _cleanup(service, cred_id, symbol, quiet=True)

        # Step 6: naked-position reproduction (entry fills, SL rejected)
        bad_stop = wrong_side_stop_price("long", entry_price, filters.tick_size)
        nb = service.place_order_bracket(cred_id, OrderRequest(
            symbol=symbol, side="BUY", order_type="MARKET",
            quantity=qty, leverage=args.leverage), stop_loss_price=bad_stop)
        if nb.entry.ok and (nb.stop_loss is None or not nb.stop_loss.ok):
            acct2 = service.get_account(cred_id)
            has_pos = any(po["symbol"] == symbol and po["position_amt"] != 0
                          for po in acct2["positions"])
            opens2 = service.list_open_orders(cred_id, symbol=symbol)
            stop_present = any(o.get("type") == "STOP_MARKET" for o in opens2)
            sl_err = nb.stop_loss.error[:80] if nb.stop_loss else "?"
            rep.record("naked-position gap reproduced", "WARN",
                       f"entry filled, SL rejected ({sl_err}); position_open={has_pos} "
                       f"stop_present={stop_present} -> Option B (bracket recovery) needed")
        else:
            rep.record("naked-position gap reproduced", "SKIP",
                       f"entry.ok={nb.entry.ok} (scenario not set up)")
        _cleanup(service, cred_id, symbol, quiet=True)

        return 0 if rep.ok() else 1
    except Exception as e:  # noqa: BLE001
        rep.record("unexpected error", "FAIL", str(e)[:200])
        return 1
    finally:
        if service is not None and cred_id is not None and not args.keep:
            _cleanup(service, cred_id, symbol, quiet=False)
        rep.summary()
        if args.keep:
            print(f"--keep set: temp DB left at {os.environ.get('DB_PATH')}; "
                  f"manually flatten {symbol} on testnet if needed.")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
