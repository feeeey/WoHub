import os

DEFAULT_APP_PASSWORD = "admin"
DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", DEFAULT_APP_PASSWORD)
        self.secret_key = os.environ.get("SECRET_KEY", DEFAULT_SECRET_KEY)
        self.db_path = os.environ.get("DB_PATH", "data/wohub.db")
        self.chartshot_url = os.environ.get("CHARTSHOT_URL", "http://chartshot:5000")
        self.screenshots_dir = os.environ.get("SCREENSHOTS_DIR", "data/screenshots")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"
        self.cache_ttl = int(os.environ.get("CACHE_TTL", "15"))
        self.min_volume_24h = float(os.environ.get("MIN_VOLUME_24H", "100000"))
        self.proxy_enabled = os.environ.get("PROXY_ENABLED", "false").lower() == "true"
        self.proxy_host = os.environ.get("PROXY_HOST", "host.docker.internal")
        self.proxy_port = os.environ.get("PROXY_PORT", "24000")

    def insecure_defaults(self) -> list[str]:
        """Names of security-critical settings still at their insecure default.
        Used to gate mainnet trading and to emit a startup warning."""
        bad = []
        if self.secret_key == DEFAULT_SECRET_KEY:
            bad.append("SECRET_KEY")
        if self.app_password == DEFAULT_APP_PASSWORD:
            bad.append("APP_PASSWORD")
        return bad


settings = Settings()
