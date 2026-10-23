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


settings = Settings()
