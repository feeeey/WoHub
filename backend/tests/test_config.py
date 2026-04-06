from config import Settings


def test_default_settings():
    settings = Settings()
    assert settings.app_password == "admin"
    assert settings.secret_key == "change-me-in-production"
    assert settings.db_path == "data/wohub.db"
    assert settings.chartshot_url == "http://chartshot:5000"
    assert settings.host == "0.0.0.0"
    assert settings.port == 8080
    assert settings.debug is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "secret123")
    monkeypatch.setenv("DEBUG", "true")
    settings = Settings()
    assert settings.app_password == "secret123"
    assert settings.debug is True
