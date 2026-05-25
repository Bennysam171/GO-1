"""
TipFusion AI — Base Scraper
Abstract base class for all scrapers with shared utilities:
- anti-ban delays
- user-agent rotation
- retry logic
- session management
"""

import asyncio
import random
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field

import aiohttp
from fake_useragent import UserAgent
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from core.config import settings
from core.logger import logger


# ============================================================
# RAW TIP DATA CLASS
# ============================================================

@dataclass
class RawTip:
    """Normalized raw tip before DB insertion."""
    home_team: str
    away_team: str
    prediction: str              # Human-readable: "Over 2.5", "Home Win", etc.
    market: str                  # MarketType key
    confidence: Optional[float] = None
    odds: Optional[float] = None
    league: Optional[str] = None
    kickoff: Optional[datetime] = None
    tipster: Optional[str] = None
    source_name: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Normalize team names
        self.home_team = self.home_team.strip().title()
        self.away_team = self.away_team.strip().title()


# ============================================================
# PREDICTION NORMALIZER
# ============================================================

PREDICTION_MAP = {
    # 1X2
    "home": "1", "home win": "1", "1": "1", "win": "1",
    "draw": "X", "x": "X", "tie": "X",
    "away": "2", "away win": "2", "2": "2",
    # Over/Under
    "over 2.5": "over_2.5", "o2.5": "over_2.5", "over2.5": "over_2.5",
    "under 2.5": "under_2.5", "u2.5": "under_2.5", "under2.5": "under_2.5",
    "over 3.5": "over_3.5", "o3.5": "over_3.5",
    "under 3.5": "under_3.5", "u3.5": "under_3.5",
    # BTTS
    "btts yes": "btts_yes", "gg": "btts_yes", "yes": "btts_yes",
    "btts no": "btts_no", "ng": "btts_no", "no": "btts_no",
    # Double Chance
    "1x": "1X", "home or draw": "1X",
    "x2": "X2", "draw or away": "X2",
    "12": "12", "home or away": "12",
    # DNB
    "dnb home": "dnb_1", "draw no bet home": "dnb_1",
    "dnb away": "dnb_2", "draw no bet away": "dnb_2",
}


def normalize_prediction(raw: str) -> Optional[str]:
    """Convert any raw prediction string to a MarketType key."""
    key = raw.lower().strip()
    return PREDICTION_MAP.get(key)


# ============================================================
# BASE SCRAPER
# ============================================================

class BaseScraper(ABC):
    """
    Abstract base class for all scrapers.
    Provides HTTP session, anti-ban, retry, and logging.
    """

    SOURCE_TYPE: str = "web"
    SOURCE_NAME: str = "Unknown"
    SOURCE_IDENTIFIER: str = ""

    def __init__(self):
        self._ua = UserAgent()
        self._session: Optional[aiohttp.ClientSession] = None
        self._proxy_pool = list(settings.proxy_urls)
        self._proxy_idx = 0

    @property
    def headers(self) -> Dict[str, str]:
        """Fresh headers with rotated user-agent."""
        return {
            "User-Agent": self._ua.random if settings.rotate_user_agents else self._ua.chrome,
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "DNT": "1",
        }

    @property
    def _next_proxy(self) -> Optional[str]:
        if not self._proxy_pool:
            return None
        proxy = self._proxy_pool[self._proxy_idx % len(self._proxy_pool)]
        self._proxy_idx += 1
        return proxy

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30, connect=10)
            self._session = aiohttp.ClientSession(
                headers=self.headers,
                timeout=timeout,
                connector=aiohttp.TCPConnector(ssl=False),
            )
        return self._session

    async def _human_delay(self):
        """Random delay to simulate human browsing."""
        delay = random.uniform(0.3, 0.8)  # Fast mode: reduced from config defaults
        await asyncio.sleep(delay)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def fetch(self, url: str, **kwargs) -> str:
        """Fetch URL with retry + anti-ban delay."""
        await self._human_delay()
        session = await self._get_session()
        proxy = self._next_proxy

        async with session.get(url, proxy=proxy, headers=self.headers, **kwargs) as resp:
            resp.raise_for_status()
            return await resp.text()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        reraise=True,
    )
    async def fetch_json(self, url: str, params: Optional[Dict] = None, **kwargs) -> Any:
        """Fetch JSON endpoint with retry."""
        await self._human_delay()
        session = await self._get_session()
        proxy = self._next_proxy

        async with session.get(
            url, params=params, proxy=proxy, headers=self.headers, **kwargs
        ) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    @abstractmethod
    async def scrape(self) -> List[RawTip]:
        """
        Main scraping method — implement in each subclass.
        Must return a list of RawTip objects.
        """
        ...

    async def run(self) -> List[RawTip]:
        """
        Run the scraper with full logging and error handling.
        Returns scraped tips.
        """
        started = datetime.utcnow()
        logger.info(f"🔍 [{self.SOURCE_NAME}] Starting scrape...")
        try:
            tips = await self.scrape()
            duration = (datetime.utcnow() - started).total_seconds()
            logger.success(
                f"✅ [{self.SOURCE_NAME}] Found {len(tips)} tips in {duration:.1f}s"
            )
            return tips
        except Exception as e:
            logger.error(f"❌ [{self.SOURCE_NAME}] Scrape failed: {e}")
            return []
        finally:
            await self.close()

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
