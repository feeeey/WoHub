import os
import secrets
import threading

DEFAULT_APP_PASSWORD = "admin"
DEFAULT_SECRET_KEY = "change-me-in-production"

SESSION_KEY_FILE = "session_key"


class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", DEFAULT_APP_PASSWORD)
        self.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
        self.db_path = os.environ.get("DB_PATH", "data/wohub.db")
        self.chartshot_url = os.environ.get("CHARTSHOT_URL", "http://chartshot:5000")
        self.screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "data/screenshots")
        self.chat_uploads_dir = os.environ.get("CHAT_UPLOADS_DIR", "data/chat_uploads")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"
        self.cache_ttl = int(os.environ.get("CACHE_TTL", "15"))
        self.min_volume_24h = float(os.environ.get("MIN_VOLUME_24H", "100000"))
        self.proxy_enabled = os.environ.get("PROXY_ENABLED", "false").lower() == "true"
        self.proxy_host = os.environ.get("PROXY_HOST", "host.docker.internal")
        self.proxy_port = os.environ.get("PROXY_PORT", "24000")
        self._session_secret = None
        self._session_lock = threading.Lock()

    @property
    def session_secret(self) -> str:
        """Key used to sign session cookies — deliberately NOT `secret_key`.

        A published default SECRET_KEY lets anyone mint a valid
        `{"authenticated": true}` cookie offline and skip the login form
        entirely. When SECRET_KEY is still the default we therefore sign with a
        random key persisted next to the database instead, so a stock
        `docker-compose up` is not trivially bypassable.

        Credential encryption still derives from `secret_key` (see
        trading/credentials.py) — keeping the two separate means enabling this
        never invalidates already-stored API secrets. It does invalidate
        existing session cookies once, which is the point: they were forgeable.
        """
        if self.secret_key != DEFAULT_SECRET_KEY:
            return self.secret_key
        if self._session_secret is not None:
            return self._session_secret
        with self._session_lock:
            if self._session_secret is None:
                self._session_secret = _load_or_create_session_key(
                    os.path.join(os.path.dirname(self.db_path) or ".",
                                 SESSION_KEY_FILE))
        return self._session_secret

    def insecure_defaults(self) -> list[str]:
        """Names of security-critical settings still at their insecure default.
        Used to gate mainnet trading and to emit a startup warning."""
        bad = []
        if self.secret_key == DEFAULT_SECRET_KEY:
            bad.append("SECRET_KEY")
        if self.app_password == DEFAULT_APP_PASSWORD:
            bad.append("APP_PASSWORD")
        return bad


def _load_or_create_session_key(path: str) -> str:
    """Read the persisted session key, creating it on first run.

    Persisted (not per-process) so sessions survive a restart. Created with
    0600 and an exclusive open, so a concurrent worker either wins the create
    or reads the winner's key — never a half-written file.
    """
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except (OSError, ValueError):
        pass
    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, key.encode())
        finally:
            os.close(fd)
        return key
    except FileExistsError:
        try:
            with open(path) as f:
                return f.read().strip() or key
        except (OSError, ValueError):
            return key
    except (OSError, ValueError):
        # Unwritable location (read-only mount, bad path): a per-process random
        # key still beats a published constant — it only costs a re-login on
        # restart.
        return key


settings = Settings()
