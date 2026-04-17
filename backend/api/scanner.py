from fastapi import APIRouter
from pydantic import BaseModel, Field
from sources.pine_screener import run_screener, list_screeners
from app_logger import log as applog

router = APIRouter(prefix="/scanner", tags=["scanner"])


class ScreenerItem(BaseModel):
    folder_type: str
    screener_name: str
    label: str


class ScanRequest(BaseModel):
    screeners: list[ScreenerItem]
    resolutions: list[str] = ["1h"]
    watchlist_id: int
    overlap_threshold: int = Field(default=2, ge=1)


@router.post("/run")
def run_scan(req: ScanRequest):
    screeners = req.screeners
    resolutions = req.resolutions
    overlap_threshold = req.overlap_threshold
    is_single = len(screeners) <= 1

    # Run all screener x timeframe combos sequentially (TradingView API: no concurrency)
    all_results = []
    for res in resolutions:
        for sc in screeners:
            try:
                symbols = run_screener(sc.folder_type, sc.screener_name, res, req.watchlist_id)
                all_results.append({
                    "label": sc.label,
                    "resolution": res,
                    "symbols": symbols,
                    "count": len(symbols),
                })
                applog("scanner", "info", f"Screener {sc.label} ({res}): {len(symbols)} symbols")
            except Exception as e:
                applog("scanner", "error", f"Screener {sc.label} ({res}) error: {e}")

    if not all_results:
        return {
            "results": [],
            "signals_by_res": {},
            "total_signals": 0,
            "total_unique": 0,
        }

    # Build signals per timeframe independently
    # Overlap is computed across screeners within each timeframe
    signals_by_res: dict[str, dict[str, list[str]]] = {}
    for res in resolutions:
        res_results = [r for r in all_results if r["resolution"] == res]
        if is_single:
            # Single screener: all symbols are signals
            per_res: dict[str, list[str]] = {}
            for r in res_results:
                for sym in r["symbols"]:
                    per_res.setdefault(sym, []).append(r["label"])
            signals_by_res[res] = per_res
        else:
            # Multi screener: count how many different screeners hit each symbol
            sym_screeners: dict[str, list[str]] = {}
            for r in res_results:
                for sym in r["symbols"]:
                    sym_screeners.setdefault(sym, []).append(r["label"])
            signals_by_res[res] = {
                sym: labels for sym, labels in sym_screeners.items()
                if len(labels) >= overlap_threshold
            }

    # Compute totals
    total_signals = sum(len(sigs) for sigs in signals_by_res.values())
    all_unique = set()
    for sigs in signals_by_res.values():
        all_unique.update(sigs.keys())

    return {
        "results": [
            {"label": r["label"], "resolution": r["resolution"], "count": r["count"], "symbols": r["symbols"]}
            for r in all_results
        ],
        "signals_by_res": signals_by_res,
        "total_signals": total_signals,
        "total_unique": len(all_unique),
    }
