"""Typed runtime configuration (12-factor; everything comes from the environment)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    api_key: str = "change-me-admin-key"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Persistence. SQLite fallback keeps `make dev` zero-dependency.
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    redis_url: str | None = None
    cache_ttl_s: int = 30
    model_dir: str = "./data/models"  # DQN checkpoints / parquet cache (Fly volume in prod)

    # Oracles
    parsebot_base_url: str = "https://api.parse.bot/v1"
    parsebot_api_key: str | None = None
    ppt_base_url: str = "https://www.pokemonpricetracker.com/api/v2"
    ppt_api_key: str | None = None
    oracle_timeout_s: float = 20.0
    oracle_rps: float = 2.0  # polite token-bucket rate limit per oracle

    # Grading / market economics
    grading_fee_usd: float = 25.0
    grading_shipping_usd: float = 8.0
    marketplace_fee_pct: float = 0.1325
    risk_free_rate: float = 0.045
    kelly_fraction_cap: float = 0.25  # never allocate more than 25% of bankroll to one slab
    kelly_multiplier: float = 0.5  # half-Kelly by default

    # Worker cadence
    worker_enabled: bool = True
    price_ingest_interval_s: int = 86_400
    pop_ingest_interval_s: int = 21_600
    analytics_interval_s: int = 3_600
    signal_z_threshold: float = 2.0

    @field_validator("database_url", mode="before")
    @classmethod
    def _asyncpg_driver(cls, v: object) -> object:
        """`fly postgres attach` injects postgres://...; SQLAlchemy needs the asyncpg dialect."""
        if isinstance(v, str):
            for prefix in ("postgres://", "postgresql://"):
                if v.startswith(prefix):
                    return "postgresql+asyncpg://" + v[len(prefix):]
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def demo_mode(self) -> bool:
        """No oracle keys configured => synthetic data only."""
        return not (self.parsebot_api_key or self.ppt_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
