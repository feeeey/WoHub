"""Session cookies must not be forgeable when SECRET_KEY is left at its default.

A published default signing key means anyone can mint `{"authenticated": true}`
offline and skip the login form. `settings.session_secret` therefore falls back
to a persisted random key, while credential encryption keeps deriving from
SECRET_KEY so stored API secrets survive the change.
"""
import os
import pytest
from itsdangerous import URLSafeTimedSerializer

from config import Settings, DEFAULT_SECRET_KEY, _load_or_create_session_key


def _settings_with(tmp_path, monkeypatch, secret_key):
    monkeypatch.setenv("SECRET_KEY", secret_key)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "data" / "wohub.db"))
    (tmp_path / "data").mkdir(exist_ok=True)
    return Settings()


def test_explicit_secret_key_is_used_verbatim(tmp_path, monkeypatch):
    """Operators who already set a strong key keep their existing sessions."""
    s = _settings_with(tmp_path, monkeypatch, "a-strong-random-key")
    assert s.session_secret == "a-strong-random-key"
    assert not (tmp_path / "data" / "session_key").exists()


def test_default_secret_key_does_not_sign_sessions(tmp_path, monkeypatch):
    s = _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY)
    assert s.session_secret != DEFAULT_SECRET_KEY
    assert len(s.session_secret) >= 32


def test_forged_cookie_from_default_key_is_rejected(tmp_path, monkeypatch):
    """The actual attack: sign a token with the published default and present it."""
    s = _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY)
    forged = URLSafeTimedSerializer(DEFAULT_SECRET_KEY).dumps({"authenticated": True})
    real = URLSafeTimedSerializer(s.session_secret)
    with pytest.raises(Exception):
        real.loads(forged, max_age=3600)


def test_generated_key_persists_across_restarts(tmp_path, monkeypatch):
    first = _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY).session_secret
    second = _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY).session_secret
    assert first == second, "a new key each boot would log everyone out on restart"


def test_key_file_is_owner_only(tmp_path, monkeypatch):
    _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY).session_secret
    mode = os.stat(tmp_path / "data" / "session_key").st_mode
    assert mode & 0o077 == 0, "session key must not be group/world readable"


def test_existing_key_file_is_reused(tmp_path):
    path = tmp_path / "session_key"
    path.write_text("pre-existing-key")
    assert _load_or_create_session_key(str(path)) == "pre-existing-key"


def test_unwritable_location_still_avoids_the_default(tmp_path):
    """Read-only data dir: a per-process key still beats a published constant."""
    key = _load_or_create_session_key(str(tmp_path / "nope" / "\0bad" / "k"))
    assert key and key != DEFAULT_SECRET_KEY


def test_credential_encryption_still_uses_secret_key(tmp_path, monkeypatch):
    """Separating the keys must not silently re-key stored API secrets."""
    s = _settings_with(tmp_path, monkeypatch, DEFAULT_SECRET_KEY)
    assert s.secret_key == DEFAULT_SECRET_KEY
    assert s.session_secret != s.secret_key
    # mainnet gate still keys off SECRET_KEY, since the Fernet key is still weak
    assert "SECRET_KEY" in s.insecure_defaults()
