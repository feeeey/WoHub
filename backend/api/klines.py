from fastapi import APIRouter, HTTPException, Query

from klines.service import get_candles_with_patterns
from klines.fetcher import KlineRequestError

router = APIRouter(prefix="/klines")


@router.get("/binance")
def binance_klines(
    symbol: str = Query(..., description="Binance perpetual symbol, e.g. BTCUSDT"),
    interval: str = Query(..., description="Kline interval (1m,5m,15m,1h,4h,1d,...)"),
    limit: int = Query(100, ge=1, le=1500),
    include_current: bool = Query(False, description="Run pattern detection on the in-progress candle too"),
):
    try:
        return get_candles_with_patterns(
            symbol=symbol,
            interval=interval,
            limit=limit,
            include_current_in_detection=include_current,
        )
    except KlineRequestError as e:
        raise HTTPException(status_code=400, detail=str(e))
