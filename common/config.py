"""Settings for every BusTrust process, read from BT_* environment variables or .env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BT_", env_file=ROOT / ".env", extra="ignore")

    database_url: str = f"sqlite:///{ROOT / 'data' / 'bustrust.db'}"

    # Feeds
    feed_mode: Literal["nta", "simulated"] = "simulated"
    nta_base_url: str = "https://api.nationaltransport.ie/gtfsr/v2"
    nta_api_keys: Annotated[list[str], NoDecode] = Field(default_factory=list)
    use_both_keys: bool = False  # only after apisupport@nationaltransport.ie agrees
    poll_interval_s: int = 60
    static_gtfs_url: str = "https://www.transportforireland.ie/transitData/Data/GTFS_Realtime.zip"
    # Dublin v1: Dublin Bus (1) and Go-Ahead Ireland Dublin (3)
    agency_ids: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["1", "3"])

    # Storage
    raw_archive: str = str(ROOT / "data" / "raw")
    static_archive: str = str(ROOT / "data" / "static")
    parquet_dir: str = str(ROOT / "data" / "parquet")
    model_dir: str = str(ROOT / "data" / "models")

    # Ops
    heartbeat_url: str = ""
    sentry_dsn: str = ""
    timezone: str = "Europe/Dublin"

    # Dublin centre, for weather
    latitude: float = 53.3498
    longitude: float = -6.2603

    # Product
    horizon_min: int = 60  # rate every bus due in the next hour
    range_coverage: float = 0.80

    @field_validator("nta_api_keys", "agency_ids", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
