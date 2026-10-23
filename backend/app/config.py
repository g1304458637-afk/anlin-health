import os
import base64
from dataclasses import dataclass, field


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./community_health.db")
    secret_key: str = os.getenv("SECRET_KEY", "")
    demo_auth: bool = os.getenv("ENABLE_DEMO_AUTH", "false").lower() == "true"
    wechat_appid: str = os.getenv("WECHAT_APPID", "")
    wechat_appsecret: str = os.getenv("WECHAT_APPSECRET", "")
    cors_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv("CORS_ORIGINS", "").split(",") if x.strip())
    # External asset origins allowed by the Content-Security-Policy (images/media), e.g. your CDN.
    csp_asset_origins: tuple[str, ...] = tuple(x.strip() for x in os.getenv("ASSET_CSP_ORIGINS", "").split(",") if x.strip())
    # Comma-separated list of hosts that may reach a production deployment.
    allowed_hosts: tuple[str, ...] = tuple(x.strip() for x in os.getenv("ALLOWED_HOSTS", "").split(",") if x.strip())
    # Access-token lifetime; short-lived by design so a stolen bearer token ages out quickly.
    session_ttl_minutes: int = _int_env("SESSION_TTL_MINUTES", 480)
    # AES-GCM key (base64, 32 bytes) for sensitive fields at rest; unset keeps development plaintext.
    field_encryption_key: str = os.getenv("FIELD_ENCRYPTION_KEY", "")
    # Brute-force controls for username/password login.
    login_max_attempts: int = _int_env("LOGIN_MAX_ATTEMPTS", 5)
    login_lockout_minutes: int = _int_env("LOGIN_LOCKOUT_MINUTES", 15)
    login_rate_per_minute: int = _int_env("LOGIN_RATE_PER_MINUTE", 10)
    # WeChat code2session attempts allowed per IP per minute.
    wechat_login_rate_per_minute: int = _int_env("WECHAT_LOGIN_RATE_PER_MINUTE", 10)
    # Server-side only; inherited credentials are never returned to the browser.
    deepseek_api_key: str = field(default=os.getenv("DEEPSEEK_API_KEY", ""), repr=False)
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    report_storage_dir: str = os.getenv("REPORT_STORAGE_DIR", "./private_reports")
    report_worker_enabled: bool = os.getenv("REPORT_WORKER_ENABLED", "true").lower() == "true"
    plan_worker_enabled: bool = os.getenv("PLAN_WORKER_ENABLED", "true").lower() == "true"


settings = Settings()


def validate_settings() -> None:
    if settings.app_env not in {"development", "test", "production"}:
        raise RuntimeError("APP_ENV must be development/test/production")
    if settings.app_env == "production":
        if len(settings.secret_key) < 48 or settings.secret_key in {"change-me", "dev-only-secret"}:
            raise RuntimeError("Production SECRET_KEY must be random and >=48 characters")
        if settings.demo_auth:
            raise RuntimeError("ENABLE_DEMO_AUTH may not run in production")
        if not settings.database_url.startswith(("postgresql://", "postgresql+")):
            raise RuntimeError("Production requires PostgreSQL; SQLite is development-only")
        if not settings.allowed_hosts:
            raise RuntimeError("Production requires ALLOWED_HOSTS to restrict the Host header")
        if not settings.field_encryption_key:
            raise RuntimeError("Production requires FIELD_ENCRYPTION_KEY (base64 of 32 random bytes) for sensitive columns")
        try:
            key = base64.b64decode(settings.field_encryption_key.strip(), validate=True)
        except (ValueError, TypeError):
            raise RuntimeError("Production FIELD_ENCRYPTION_KEY must be valid base64 for 32 bytes") from None
        if len(key) != 32:
            raise RuntimeError("Production FIELD_ENCRYPTION_KEY must decode to 32 bytes")
