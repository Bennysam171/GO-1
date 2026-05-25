"""
TipFusion AI — Football-Data.org Scraper
Form, H2H, and standings-based predictions.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from scrapers.base import BaseScraper, RawTip
from core.config import settings
from core.logger import logger

API_BASE = "https://api.football-data.org/v4"

COMPETITIONS = {
    "PL": "Premier League",
    "PD": "La Liga",
    "BL1": "Bundesliga",
    "SA": "Serie A",
    "FL1": "Ligue 1",
    "CL": "Champions League",
    "EC": "European Championship",
}


class FootballDataScraper(BaseScraper):
    SOURCE_TYPE = "api"
    SOURCE_NAME = "Football-Data.org"
    SOURCE_IDENTIFIER = "football-data.org"

    def __init__(self):
        super().__init__()
        self._api_key = settings.football_data_key

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "X-Auth-Token": self._api_key or "",
            "Content-Type": "application/json",
        }

    async def _get_matches(self, competition: str) -> List[Dict]:
        today = datetime.utcnow().date()
        date_to = today + timedelta(days=2)
        url = f"{API_BASE}/competitions/{competition}/matches"
        params = {
            "dateFrom": str(today),
            "dateTo": str(date_to),
            "status": "SCHEDULED",
        }
        try:
            data = await self.fetch_json(url, params=params)
            return data.get("matches", [])
        except Exception as e:
            logger.warning(f"[FD.org] {competition} error: {e}")
            return []

    async def _get_standings(self, competition: str) -> List[Dict]:
        url = f"{API_BASE}/competitions/{competition}/standings"
        try:
            data = await self.fetch_json(url)
            tables = data.get("standings", [])
            if tables:
                return tables[0].get("table", [])
        except Exception as e:
            logger.warning(f"[FD.org] standings {competition}: {e}")
        return []

    def _team_rank(self, standings: List[Dict], team_name: str) -> Optional[int]:
        for entry in standings:
            if entry.get("team", {}).get("name", "").lower() == team_name.lower():
                return entry.get("position")
        return None

    def _analyze_match(
        self,
        match: Dict,
        standings: List[Dict],
        league_name: str
    ) -> Optional[RawTip]:
        home = match.get("homeTeam", {}).get("name", "")
        away = match.get("awayTeam", {}).get("name", "")
        utc_date = match.get("utcDate", "")

        if not home or not away:
            return None

        try:
            kickoff = datetime.fromisoformat(utc_date.replace("Z", "+00:00"))
        except Exception:
            kickoff = None

        home_rank = self._team_rank(standings, home)
        away_rank = self._team_rank(standings, away)

        if not home_rank or not away_rank:
            return None

        rank_diff = away_rank - home_rank  # Positive = home stronger

        # Simple standings-based heuristic
        if rank_diff >= 6:
            # Home team much stronger
            market = "1"
            pred_str = f"Home Win ({home})"
            confidence = min(75, 50 + rank_diff * 3)
        elif rank_diff <= -6:
            # Away team much stronger
            market = "2"
            pred_str = f"Away Win ({away})"
            confidence = min(70, 50 + abs(rank_diff) * 2.5)
        elif abs(rank_diff) <= 3:
            # Evenly matched — lean toward draw or BTTS
            market = "btts_yes"
            pred_str = "BTTS Yes"
            confidence = 58.0
        else:
            return None  # Inconclusive

        return RawTip(
            home_team=home,
            away_team=away,
            prediction=pred_str,
            market=market,
            confidence=round(confidence, 1),
            league=league_name,
            kickoff=kickoff,
            tipster="FD.org Standings AI",
            source_name=self.SOURCE_NAME,
            extra={"home_rank": home_rank, "away_rank": away_rank}
        )

    async def scrape(self) -> List[RawTip]:
        if not self._api_key:
            logger.warning("[FD.org] No API key — skipping")
            return []

        tips = []

        for code, name in COMPETITIONS.items():
            matches = await self._get_matches(code)
            standings = await self._get_standings(code)

            for match in matches:
                tip = self._analyze_match(match, standings, name)
                if tip:
                    tips.append(tip)

        return tips
