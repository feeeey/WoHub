import time
from sources.http_client import get_session, cached


def test_get_session_returns_session():
    session = get_session()
    assert hasattr(session, "get")
    assert hasattr(session, "post")


def test_get_session_singleton():
    s1 = get_session()
    s2 = get_session()
    assert s1 is s2


def test_cached_returns_data():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"a": 1}], []

    data, errors = cached("test_key_1", fetcher, ttl=10)
    assert data == [{"a": 1}]
    assert errors == []
    assert call_count == 1


def test_cached_uses_cache_on_second_call():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"b": 2}], []

    cached("test_key_2", fetcher, ttl=10)
    cached("test_key_2", fetcher, ttl=10)
    assert call_count == 1


def test_cached_expires():
    call_count = 0

    def fetcher():
        nonlocal call_count
        call_count += 1
        return [{"c": 3}], []

    cached("test_key_3", fetcher, ttl=0.1)
    time.sleep(0.15)
    cached("test_key_3", fetcher, ttl=0.1)
    assert call_count == 2


def test_fetch_with_fallback_sets_default_timeout(monkeypatch):
    """Session objects have no working .timeout attr — a per-request timeout
    must be injected or a hung connection blocks the worker forever."""
    from sources import http_client
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(kwargs)

            class _R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {}
            return _R()

    monkeypatch.setattr(http_client, "get_session", lambda: _FakeSession())
    http_client.fetch_with_fallback("get", "https://example.com")
    assert captured["timeout"] == 10


def test_fetch_with_fallback_respects_caller_timeout(monkeypatch):
    from sources import http_client
    captured = {}

    class _FakeSession:
        def get(self, url, **kwargs):
            captured.update(kwargs)

            class _R:
                status_code = 200
                def raise_for_status(self): pass
                def json(self): return {}
            return _R()

    monkeypatch.setattr(http_client, "get_session", lambda: _FakeSession())
    http_client.fetch_with_fallback("get", "https://example.com", timeout=3)
    assert captured["timeout"] == 3
