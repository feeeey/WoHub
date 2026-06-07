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


from trading.credentials import add_credential


def test_add_credential_blocks_mainnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    with pytest.raises(ValueError) as ei:
        add_credential("m", "mainnet", "key0000000", "secret0000")
    assert "主网" in str(ei.value)


def test_add_credential_allows_testnet_under_defaults(monkeypatch):
    _set_defaults(monkeypatch)
    cid = add_credential("t", "testnet", "key0000000", "secret0000")
    assert cid > 0


@pytest.mark.asyncio
async def test_api_add_mainnet_credential_blocked_under_defaults(client, monkeypatch):
    _set_defaults(monkeypatch)
    resp = await client.post("/api/trading/credentials", json={
        "label": "m", "env": "mainnet",
        "api_key": "key0000000", "api_secret": "secret0000",
    })
    assert resp.status_code == 400
    assert "主网" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_api_add_testnet_credential_allowed_under_defaults(client, monkeypatch):
    _set_defaults(monkeypatch)
    resp = await client.post("/api/trading/credentials", json={
        "label": "t", "env": "testnet",
        "api_key": "key0000000", "api_secret": "secret0000",
    })
    assert resp.status_code == 200
    assert "id" in resp.json()


def test_add_credential_allows_testnet_under_defaults_and_resolve_succeeds(monkeypatch):
    """Full integration: a testnet credential created under insecure defaults
    must remain resolvable (create -> store -> fetch -> decrypt -> _resolve)."""
    _set_defaults(monkeypatch)
    cid = add_credential("t", "testnet", "key0000000", "secret0000")
    assert cid > 0
    assert service._resolve(cid) == ("testnet", "key0000000", "secret0000")


@pytest.mark.asyncio
async def test_api_add_mainnet_credential_allowed_when_secure(client, monkeypatch):
    _set_secure(monkeypatch)
    resp = await client.post("/api/trading/credentials", json={
        "label": "m", "env": "mainnet",
        "api_key": "key0000000", "api_secret": "secret0000",
    })
    assert resp.status_code == 200
    assert resp.json()["id"] > 0
