"""
TipFusion AI — Telegram Intelligence Scraper
Uses Telethon to read football prediction channels autonomously.
Extracts tips from raw message text using NLP/regex parsing.
"""

import re
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass

from core.config import settings
from core.logger import logger
from scrapers.base import RawTip

# All Telegram channels from config.yaml
DEFAULT_CHANNELS = [
    # ── Verified active channels (May 2026) ──────────────────
    "betfairexchange",
    "tipsterpro",
    "soccerprediction",
    "footballtipstoday",
    "freebettingtips1",
    "bettingtipsworld",
    "footballbettingtips",
    "sure9japredictions",
    "naijabettingtips",
    "viptips247",
    "sportbettingtips",
    "dailyfootballtips",
    "footballpredictions",
    "surebettingtips",
    "oddsvalue",
    "bettinganalysis",
    "pronosticfoot",
    "tipstersoccer",
    "betsmarter",
    "footballedge",
]


@dataclass
class TelegramSource:
    channel: str
    display_name: str
    reliability_score: float = 50.0


class TelegramScraper:
    SOURCE_TYPE = "telegram"
    SOURCE_NAME = "Telegram"

    # Regex patterns for extracting tips from messages
    MATCH_PATTERN = re.compile(
        r"(?P<home>[\w\s]+?)\s+(?:vs?|v\.?|[-–—])\s+(?P<away>[\w\s]+)",
        re.IGNORECASE
    )
    PICK_PATTERNS = {
        "1": re.compile(r"\b(home\s*win|home|1(?!x|2|\.5|\s*x|\s*2))\b", re.IGNORECASE),
        "X": re.compile(r"\b(draw|x(?!2))\b", re.IGNORECASE),
        "2": re.compile(r"\b(away\s*win|away|2(?!\.5|\s*x|\s*1))\b", re.IGNORECASE),
        "over_2.5": re.compile(r"\b(over\s*2\.5|o2\.5|o\/u\s*over)\b", re.IGNORECASE),
        "under_2.5": re.compile(r"\b(under\s*2\.5|u2\.5)\b", re.IGNORECASE),
        "over_3.5": re.compile(r"\b(over\s*3\.5|o3\.5)\b", re.IGNORECASE),
        "btts_yes": re.compile(r"\b(btts\s*yes|gg|both\s*teams?\s*to?\s*score)\b", re.IGNORECASE),
        "btts_no": re.compile(r"\b(btts\s*no|ng|no\s*btts)\b", re.IGNORECASE),
        "1X": re.compile(r"\b(double\s*chance\s*1x|1x|home\s*or\s*draw)\b", re.IGNORECASE),
        "X2": re.compile(r"\b(double\s*chance\s*x2|x2|draw\s*or\s*away)\b", re.IGNORECASE),
        "12": re.compile(r"\b(double\s*chance\s*12|12|home\s*or\s*away)\b", re.IGNORECASE),
    }
    ODDS_PATTERN = re.compile(r"@\s*([\d.]+)|odds[:\s]+([\d.]+)|([\d.]+)\s*odds", re.IGNORECASE)
    CONFIDENCE_PATTERN = re.compile(r"(\d+)\s*%", re.IGNORECASE)

    def __init__(self, channels: Optional[List[str]] = None):
        self._channels = channels or DEFAULT_CHANNELS
        self._client = None

    async def _init_client(self):
        """Initialize Telethon client."""
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession

            api_id   = settings.telegram_api_id or _TG_API_ID
            api_hash = settings.telegram_api_hash or _TG_API_HASH
            if not api_id or not api_hash:
                logger.warning("[Telegram] No API credentials — skipping Telegram scraping")
                return False

            self._client = TelegramClient(
                settings.telegram_session_name,
                settings.telegram_api_id,
                settings.telegram_api_hash,
            )
            await self._client.start()
            logger.info("✅ [Telegram] Client connected")
            return True
        except Exception as e:
            logger.error(f"[Telegram] Client init failed: {e}")
            return False

    def _parse_message(self, text: str, channel: str) -> List[RawTip]:
        """
        Parse a Telegram message to extract football tips.
        Handles various formats used by tipster channels.
        """
        tips = []
        lines = text.split("\n")
        current_match = None
        current_home = None
        current_away = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Try to detect a match line
            match_m = self.MATCH_PATTERN.search(line)
            if match_m:
                current_home = match_m.group("home").strip()
                current_away = match_m.group("away").strip()

                # Clean team names (remove emoji and special chars)
                current_home = re.sub(r"[^\w\s'-]", "", current_home).strip()
                current_away = re.sub(r"[^\w\s'-]", "", current_away).strip()

            if not current_home or not current_away:
                continue

            # Try to detect a pick in this line
            for market, pattern in self.PICK_PATTERNS.items():
                if pattern.search(line):
                    pred_labels = {
                        "1": f"Home Win ({current_home})",
                        "X": "Draw",
                        "2": f"Away Win ({current_away})",
                        "over_2.5": "Over 2.5",
                        "under_2.5": "Under 2.5",
                        "over_3.5": "Over 3.5",
                        "btts_yes": "BTTS Yes",
                        "btts_no": "BTTS No",
                        "1X": "Double Chance 1X",
                        "X2": "Double Chance X2",
                        "12": "Double Chance 12",
                    }
                    pred_str = pred_labels.get(market, market)

                    # Extract odds if present
                    odds = None
                    odds_m = self.ODDS_PATTERN.search(line)
                    if odds_m:
                        val = odds_m.group(1) or odds_m.group(2) or odds_m.group(3)
                        try:
                            odds = float(val)
                        except Exception:
                            pass

                    # Extract confidence if present
                    confidence = None
                    conf_m = self.CONFIDENCE_PATTERN.search(line)
                    if conf_m:
                        try:
                            confidence = float(conf_m.group(1))
                        except Exception:
                            pass

                    tips.append(RawTip(
                        home_team=current_home,
                        away_team=current_away,
                        prediction=pred_str,
                        market=market,
                        confidence=confidence,
                        odds=odds,
                        tipster=f"Telegram:{channel}",
                        source_name=f"Telegram/{channel}",
                    ))
                    break  # One market per line

        return tips

    async def scrape_channel(self, channel: str, limit: int = 50) -> List[RawTip]:
        """Scrape recent messages from a Telegram channel."""
        if not self._client:
            return []

        tips = []
        cutoff = datetime.utcnow() - timedelta(hours=24)

        try:
            entity = await self._client.get_entity(channel)
            async for message in self._client.iter_messages(entity, limit=limit):
                if not message.date:
                    continue
                msg_time = message.date.replace(tzinfo=None)
                if msg_time < cutoff:
                    break

                if message.text:
                    parsed = self._parse_message(message.text, channel)
                    tips.extend(parsed)

            logger.debug(f"[Telegram] {channel}: {len(tips)} tips found")

        except Exception as e:
            logger.warning(f"[Telegram] channel {channel} error: {e}")

        return tips

    async def scrape(self) -> List[RawTip]:
        """Scrape all configured channels."""
        ok = await self._init_client()
        if not ok:
            return []

        all_tips = []
        for channel in self._channels:
            tips = await self.scrape_channel(channel)
            all_tips.extend(tips)
            await asyncio.sleep(2)  # Rate limiting

        if self._client:
            await self._client.disconnect()

        return all_tips

    async def run(self) -> List[RawTip]:
        """Wrapper with logging."""
        logger.info(f"🔍 [Telegram] Scraping {len(self._channels)} channels...")
        tips = await self.scrape()
        logger.success(f"✅ [Telegram] Total tips: {len(tips)}")
        return tips
