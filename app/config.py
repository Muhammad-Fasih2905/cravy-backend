from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # APP_ENV=production on the live host (Render etc.), nothing (= development) on your own PC.
    # production: the app REFUSES to start on a throw-away SQLite file and never loads demo data.
    # development: the app REFUSES to start if DATABASE_URL points at a remote (live) database.
    app_env: str = "development"
    # "disk" = uploads go to UPLOAD_DIR (local PC). "db" = uploads are stored inside Postgres, so they survive
    # redeploys on hosts whose disk is wiped (Render free). Set STORAGE_BACKEND=db on the live host.
    storage_backend: str = "disk"
    allow_remote_db_in_dev: bool = False  # only for a deliberate one-off maintenance run from your PC
    database_url: str = "sqlite:///./cravy.db"
    cors_origins: str = "http://localhost:3000"
    secret_key: str = "dev-secret-change-me"  # MUST be set in production
    admin_email: str | None = None
    admin_password: str | None = None
    upload_dir: str = "./uploads"
    smtp_host: str | None = None  # set to enable email notifications (STARTTLS, usually port 587)
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_pass: str | None = None
    email_from: str | None = None
    admin_notify_email: str | None = None
    # Easypaisa account customers pay to (override on Railway with EASYPAISA_NUMBER if it ever changes)
    easypaisa_number: str = "03143404938"
    easypaisa_account_name: str | None = None
    # Shop location used to measure delivery distance. Can also be set from the admin settings (SHOP_LATITUDE / SHOP_LONGITUDE).
    # Default = Akber Paradise, Shop #01, Block 10-A Gulshan-e-Iqbal (Rashid Minhas Rd service lane).
    shop_latitude: float | None = 24.911012
    shop_longitude: float | None = 67.105612

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"


settings = Settings()
