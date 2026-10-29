from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
