import pytest

from config import settings, DEFAULT_APP_PASSWORD, DEFAULT_SECRET_KEY
from trading import service


def _set_defaults(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)


def _set_secure(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "strong-key")
    monkeypatch.setattr(settings, "app_password", "strong-pass")


def test_resolve_blocks_mainnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("mainnet", "k", "s"))
    with pytest.raises(ValueError) as ei:
        service._resolve(1)
    assert "主网" in str(ei.value)


def test_resolve_allows_testnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("testnet", "k", "s"))
    assert service._resolve(1) == ("testnet", "k", "s")


def test_resolve_allows_mainnet_when_secure(monkeypatch):
    _set_secure(monkeypatch)
    monkeypatch.setattr(service, "get_credential", lambda cid: ("mainnet", "k", "s"))
    assert service._resolve(1) == ("mainnet", "k", "s")
