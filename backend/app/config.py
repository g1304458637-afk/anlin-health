import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./community_health.db")
    secret_key: str = os.getenv("SECRET_KEY", "")
    demo_auth: bool = os.getenv("ENABLE_DEMO_AUTH", "false").lower() == "true"
    wechat_appid: str = os.getenv("WECHAT_APPID", "")
    wechat_appsecret: str = os.getenv("WECHAT_APPSECRET", "")
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip())


settings = Settings()


def validate_settings() -> None:
    if settings.app_env not in {"development", "test", "production"}:
        raise RuntimeError("APP_ENV must be development/test/production")
    if settings.app_env == "production":
        if len(settings.secret_key) < 48 or settings.secret_key in {"change-me", "dev-only-secret"}:
            raise RuntimeError("Production SECRET_KEY must be random and >=48 characters")
        if settings.demo_auth:
            raise RuntimeError("ENABLE_DEMO_AUTH may not run in production")
        if settings.database_url.startswith("sqlite:"):
            raise RuntimeError("Production requires PostgreSQL; SQLite is development-only")
