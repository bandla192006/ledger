import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql://ledgerline_app:ledgerline_app@localhost:5432/ledgerline",
        )
    )
    jwt_secret: str = field(default_factory=lambda: os.getenv("JWT_SECRET", "dev-only-change-me-in-production"))
    jwt_ttl_minutes: int = field(default_factory=lambda: _env_int("JWT_TTL_MINUTES", 60))
    pool_min_size: int = field(default_factory=lambda: _env_int("DB_POOL_MIN", 2))
    pool_max_size: int = field(default_factory=lambda: _env_int("DB_POOL_MAX", 20))
    # How long a request waits for an account row lock before giving up with 503.
    lock_timeout_ms: int = field(default_factory=lambda: _env_int("DB_LOCK_TIMEOUT_MS", 5000))
    statement_timeout_ms: int = field(default_factory=lambda: _env_int("DB_STATEMENT_TIMEOUT_MS", 15000))
    password_hash_iterations: int = field(default_factory=lambda: _env_int("PASSWORD_HASH_ITERATIONS", 310_000))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
            if o.strip()
        )
    )


def get_settings() -> Settings:
    return Settings()
