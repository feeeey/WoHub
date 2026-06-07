from config import settings, DEFAULT_APP_PASSWORD, DEFAULT_SECRET_KEY


def test_insecure_defaults_both(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)
    assert settings.insecure_defaults() == ["SECRET_KEY", "APP_PASSWORD"]


def test_insecure_defaults_none(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-strong-random-key")
    monkeypatch.setattr(settings, "app_password", "a-strong-password")
    assert settings.insecure_defaults() == []


def test_insecure_defaults_only_secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    monkeypatch.setattr(settings, "app_password", "a-strong-password")
    assert settings.insecure_defaults() == ["SECRET_KEY"]


def test_insecure_defaults_only_password(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-strong-random-key")
    monkeypatch.setattr(settings, "app_password", DEFAULT_APP_PASSWORD)
    assert settings.insecure_defaults() == ["APP_PASSWORD"]
