"""
Run this once to fix scraper_config.json:
  python fix_config.py
"""
import json
from pathlib import Path

cfg = {
    "_comment": "Set any scraper to false to disable it.",
    "apis": {
        "api_football":   True,
        "odds_api":       True,
        "sportsgameodds": False
    },
    "web": {
        "forebet":     False,
        "predictz":    False,
        "soccervista": False,
        "windrawwin":  False
    },
    "social": {
        "telegram": False,
        "twitter":  False,
        "reddit":   False
    },
    "android": {
        "bluestacks": True
    }
}

Path("scraper_config.json").write_text(json.dumps(cfg, indent=2))
print("scraper_config.json fixed!")
print(json.dumps(cfg, indent=2))
