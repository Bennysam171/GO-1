"""
TipFusion AI — Scraper Toggle Manager
Reads scraper_config.json to enable/disable scrapers at runtime.
Edit scraper_config.json to turn scrapers on or off — no code changes needed.
"""

import json
from pathlib import Path
from core.logger import logger

CONFIG_FILE = Path("scraper_config.json")


def load_config() -> dict:
    """Load scraper on/off config from JSON file."""
    if not CONFIG_FILE.exists():
        logger.warning("[ScraperConfig] scraper_config.json not found — all scrapers enabled")
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"[ScraperConfig] Failed to load config: {e} — all scrapers enabled")
        return {}


def is_enabled(category: str, name: str) -> bool:
    """Check if a specific scraper is enabled."""
    cfg = load_config()
    enabled = cfg.get(category, {}).get(name, True)  # Default: enabled
    if not enabled:
        logger.info(f"[ScraperConfig] ⏸ {name} ({category}) is DISABLED in scraper_config.json")
    return enabled


def set_enabled(category: str, name: str, value: bool):
    """Enable or disable a scraper and save to config file."""
    cfg = load_config()
    if category not in cfg:
        cfg[category] = {}
    cfg[category][name] = value

    # Preserve comment key
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

    status = "ENABLED" if value else "DISABLED"
    logger.info(f"[ScraperConfig] {name} ({category}) → {status}")


def get_all_statuses() -> dict:
    """Return full status of all scrapers."""
    return load_config()
