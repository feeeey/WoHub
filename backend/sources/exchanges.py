from concurrent.futures import ThreadPoolExecutor
from sources.http_client import cached
from sources import binance, okx, bybit, bitget

_EXCHANGES = [
    ("Binance", binance),
    ("OKX", okx),
    ("Bybit", bybit),
    ("Bitget", bitget),
]


def _fetch_parallel(method_name: str):
    def fetcher():
        data = []
        errors = []

        def call(name, mod):
            try:
                fn = getattr(mod, method_name)
                return fn(), None
            except Exception as e:
                return None, {"exchange": name, "error": str(e)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(call, name, mod): name
                for name, mod in _EXCHANGES
            }
            for future in futures:
                result, error = future.result()
                if error:
                    errors.append(error)
                elif result:
                    data.extend(result)
        return data, errors

    return fetcher


def fetch_all_tickers():
    return cached("all_tickers", _fetch_parallel("get_tickers"))


def fetch_all_funding_rates():
    return cached("all_funding_rates", _fetch_parallel("get_funding_rates"))
