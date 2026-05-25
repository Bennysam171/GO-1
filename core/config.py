"""
TipFusion AI — Core Configuration
Loads and validates all environment settings using Pydantic Settings.
"""

from functools import lru_cache
from typing import List, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_env: str = "development"
    app_name: str = "TipFusion AI"
    log_level: str = "INFO"
    debug: bool = False

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./tipfusion.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- Football APIs ---
    api_football_key: Optional[str] = None
    odds_api_key: Optional[str] = None
    football_data_key: Optional[str] = None

    # --- Telegram Bot ---
    telegram_bot_token: Optional[str] = None
    telegram_channel_id: Optional[str] = None
    telegram_admin_chat_id: Optional[str] = None

    # --- Telegram Scraping ---
    telegram_api_id: Optional[int] = None
    telegram_api_hash: Optional[str] = None
    telegram_session_name: str = "tipfusion"

    # --- Twitter ---
    twitter_bearer_token: Optional[str] = None
    twitter_api_key: Optional[str] = None
    twitter_api_secret: Optional[str] = None
    twitter_access_token: Optional[str] = None
    twitter_access_secret: Optional[str] = None

    # --- Reddit ---
    reddit_client_id: Optional[str] = None
    reddit_client_secret: Optional[str] = None
    reddit_user_agent: str = "TipFusionBot/1.0"

    # --- Android / BlueStacks ---
    bluestacks_path: str = r"C:\Program Files\BlueStacks_nxt\HD-Player.exe"
    adb_host: str = "127.0.0.1"
    adb_port: int = 5555
    appium_host: str = "http://localhost:4723"

    # --- Scoring ---
    min_confidence_score: int = 65
    min_sources_required: int = 2
    high_value_threshold: int = 75

    # --- Scheduling ---
    scrape_interval_minutes: int = 30
    discovery_interval_hours: int = 6
    post_schedule: str = "0 8,12,16,20 * * *"

    # --- Anti-ban ---
    min_request_delay: float = 2.0
    max_request_delay: float = 6.0
    rotate_user_agents: bool = True
    use_proxies: bool = False
    proxy_list: str = ""

    # --- Notifications ---
    alert_on_error: bool = True
    error_threshold: int = 10

    @property
    def proxy_urls(self) -> List[str]:
        if not self.proxy_list:
            return []
        return [p.strip() for p in self.proxy_list.split(",") if p.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
