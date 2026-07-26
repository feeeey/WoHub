"""下单请求绝不能被 transport 层盲重发。

`fetch_with_fallback` 在 ProxyError/ConnectionError 时会用直连 session 把同一个
URL 原样再发一次。对 GET 无害，对下单是灾难：

- ConnectionError 的语义是「请求可能已经送达」（代理转发后被 RST 就是这一类）；
- 币安的 `newClientOrderId` 唯一性**只覆盖挂着的订单**，毫秒级成交的 MARKET 单
  再发一次不会被拒，会二次成交；
- 更糟的是回退吞掉了原异常并返回第二笔订单的成功响应，于是 service 层的
  `_query_order_state` 消歧永远不会运行，第一笔成交完全不可见。
"""
import pytest
import requests
from unittest.mock import patch

from config import settings
from sources import http_client
from sources.http_client import fetch_with_fallback


@pytest.fixture(autouse=True)
def _proxy_on(monkeypatch):
    monkeypatch.setattr(settings, "proxy_enabled", True)
    http_client.reset_session()
    yield
    http_client.reset_session()


class _Resp:
    status_code = 200
    def raise_for_status(self): pass


def test_get_still_falls_back_to_direct():
    """行情 GET 的代理回退是这个机制存在的理由，不能误伤。"""
    with patch.object(http_client, "get_session") as proxy, \
         patch.object(http_client, "_get_direct_session") as direct:
        proxy.return_value.get.side_effect = requests.ConnectionError("proxy died")
        direct.return_value.get.return_value = _Resp()
        assert fetch_with_fallback("get", "http://x/api", allow_retry=True)
        assert direct.return_value.get.call_count == 1


def test_post_is_not_resent_when_retry_disallowed():
    with patch.object(http_client, "get_session") as proxy, \
         patch.object(http_client, "_get_direct_session") as direct:
        proxy.return_value.post.side_effect = requests.ConnectionError("RST after forward")
        with pytest.raises(requests.ConnectionError):
            fetch_with_fallback("post", "http://x/order", allow_retry=False)
        direct.assert_not_called()


def test_binance_order_post_never_resends(monkeypatch):
    """端到端：走真实的 binance_client._request 下单路径。"""
    from trading import binance_client as bn

    calls = {"proxy": 0, "direct": 0}

    class ProxySession:
        headers = {}
        def post(self, *a, **kw):
            calls["proxy"] += 1
            raise requests.ConnectionError("connection reset by peer")

    class DirectSession:
        headers = {}
        def post(self, *a, **kw):
            calls["direct"] += 1
            return _Resp()

    monkeypatch.setattr(http_client, "get_session", lambda: ProxySession())
    monkeypatch.setattr(http_client, "_get_direct_session", lambda: DirectSession())

    with pytest.raises(requests.ConnectionError):
        bn.place_order("testnet", "K", "S", symbol="BTCUSDT", side="BUY",
                       order_type="MARKET", quantity=0.01,
                       new_client_order_id="wohub-test")

    assert calls["proxy"] == 1
    assert calls["direct"] == 0, "下单被重发了一次 —— 可能造成二次成交"


def test_binance_public_get_still_falls_back(monkeypatch):
    """公开行情接口仍然享受代理回退。"""
    from trading import binance_client as bn

    calls = {"direct": 0}

    class ProxySession:
        headers = {}
        def get(self, *a, **kw):
            raise requests.ConnectionError("proxy died")

    class DirectSession:
        headers = {}
        def get(self, *a, **kw):
            calls["direct"] += 1
            class R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {"serverTime": 1}
            return R()

    monkeypatch.setattr(http_client, "get_session", lambda: ProxySession())
    monkeypatch.setattr(http_client, "_get_direct_session", lambda: DirectSession())

    assert bn.server_time("testnet", "K") == 1
    assert calls["direct"] == 1


def test_ambiguous_failure_reaches_the_service_disambiguation(monkeypatch):
    """异常上抛后，service 层必须用查单来判定，而不是当作确定性失败。"""
    from trading import service, credentials as creds

    monkeypatch.setattr("trading.binance_client.set_margin_type", lambda *a, **kw: None)
    monkeypatch.setattr("trading.binance_client.set_leverage", lambda *a, **kw: {})
    monkeypatch.setattr("trading.binance_client.place_order",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            requests.ConnectionError("reset")))
    monkeypatch.setattr("trading.binance_client.get_order",
                        lambda env, k, s, sym, orig_client_order_id=None: {
                            "orderId": 42, "status": "FILLED",
                            "executedQty": "0.01", "avgPrice": "70000"})

    from trading.models import OrderRequest
    cid = creds.add_credential("t", "testnet", "K", "S")
    res = service.place_order(cid, OrderRequest(
        symbol="BTCUSDT", side="BUY", order_type="MARKET", quantity=0.01))

    assert res.ok and res.binance_order_id == "42", \
        "网络异常后应查单确认，而不是把已成交的单报成失败"
