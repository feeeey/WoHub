import os


class Settings:
    def __init__(self):
        self.app_password = os.environ.get("APP_PASSWORD", "admin")
        self.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
        self.db_path = os.environ.get("DB_PATH", "data/wohub.db")
        self.chartshot_url = os.environ.get("CHARTSHOT_URL", "http://chartshot:5000")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.debug = os.environ.get("DEBUG", "false").lower() == "true"


settings = Settings()
