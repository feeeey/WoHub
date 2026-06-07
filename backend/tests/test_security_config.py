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


from main import _insecure_default_warning


def test_warning_none_when_secure():
    assert _insecure_default_warning([]) is None


def test_warning_lists_names():
    msg = _insecure_default_warning(["SECRET_KEY", "APP_PASSWORD"])
    assert msg is not None
    assert "SECRET_KEY" in msg and "APP_PASSWORD" in msg
    assert "主网" in msg
