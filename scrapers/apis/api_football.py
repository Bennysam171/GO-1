"""
TipFusion AI — API-Sports Scraper
Optimized: concurrent prediction fetching in batches of 3.
Football only — basketball/baseball disabled (returned 0 tips).
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict

from scrapers.base import BaseScraper, RawTip
from core.logger import logger

API_SPORTS_KEY  = "41e811d696b091b5de0c9905e8ff84b3"
FOOTBALL_BASE   = "https://v3.football.api-sports.io"
NBA_BASE        = "https://v2.nba.api-sports.io"
BASKETBALL_BASE = "https://v1.basketball.api-sports.io"
BASEBALL_BASE   = "https://v1.baseball.api-sports.io"
NBA_SEASON      = 2024

FOOTBALL_LEAGUES = [
    39, 40, 61, 78, 135, 140, 2, 3,
    307, 48, 88, 94, 203, 144, 253,
]

# Max fixtures to fetch predictions for per run
# Free plan = 10 req/min. Batch of 3 with 2s gap = ~9 req/min (safe)
MAX_FIXTURES   = 15
BATCH_SIZE     = 3
BATCH_DELAY    = 2.2   # seconds between batches


class ApiFootballScraper(BaseScraper):
    SOURCE_TYPE = "api"
    SOURCE_NAME = "API-Football"
    SOURCE_IDENTIFIER = "api-sports.io"

    @property
    def headers(self) -> Dict:
        return {"x-apisports-key": API_SPORTS_KEY, "Content-Type": "application/json"}

    # ── FOOTBALL ──────────────────────────────────────────────

    async def _get_fixtures(self, date_str: str) -> List[Dict]:
        try:
            data = await self.fetch_json(
                f"{FOOTBALL_BASE}/fixtures",
                params={"date": date_str, "timezone": "UTC"}
            )
            return data.get("response", [])
        except Exception as e:
            logger.error(f"[API-Football] fixtures error: {e}")
            return []

    async def _get_prediction(self, fixture_id: int) -> Optional[Dict]:
        try:
            data = await self.fetch_json(
                f"{FOOTBALL_BASE}/predictions",
                params={"fixture": fixture_id}
            )
            resp = data.get("response", [])
            return resp[0] if resp else None
        except Exception as e:
            logger.warning(f"[API-Football] prediction error fx={fixture_id}: {e}")
            return None

    def _parse_prediction(self, fx: Dict, pred_data: Dict) -> Optional[RawTip]:
        """Convert fixture + prediction API response into RawTip."""
        try:
            teams       = fx.get("teams", {})
            league      = fx.get("league", {})
            fixture     = fx.get("fixture", {})
            home_team   = teams.get("home", {}).get("name", "")
            away_team   = teams.get("away", {}).get("name", "")
            league_name = league.get("name", "")
            fixture_id  = fixture.get("id")

            if not home_team or not away_team:
                return None

            kickoff = None
            try:
                from datetime import timezone
                dt = datetime.fromisoformat(
                    fixture.get("date", "").replace("Z", "+00:00"))
                kickoff = dt.astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                pass

            predictions = pred_data.get("predictions", {})
            percent     = predictions.get("percent", {})
            advice      = predictions.get("advice", "")

            def pct(v):
                try:
                    return float(str(v).replace("%", "")) / 100
                except Exception:
                    return 0.0

            hp = pct(percent.get("home", "0"))
            dp = pct(percent.get("draw", "0"))
            ap = pct(percent.get("away", "0"))
            best = max(hp, dp, ap)
            if best == 0:
                return None

            if best == hp:
                market, pred_str = "1", f"Home Win ({home_team})"
            elif best == ap:
                market, pred_str = "2", f"Away Win ({away_team})"
            else:
                market, pred_str = "X", "Draw"

            return RawTip(
                home_team=home_team, away_team=away_team,
                prediction=pred_str, market=market,
                confidence=round(best * 100, 1),
                league=league_name, kickoff=kickoff,
                tipster="API-Football AI", source_name=self.SOURCE_NAME,
                extra={"fixture_id": fixture_id, "advice": advice},
            )
        except Exception as e:
            logger.debug(f"[API-Football] parse error: {e}")
            return None

    async def _scrape_football(self) -> List[RawTip]:
        tips = []
        today    = datetime.utcnow().date()
        tomorrow = today + timedelta(days=1)

        # Collect fixtures from today and tomorrow
        all_fixtures = []
        for date_str in [str(today), str(tomorrow)]:
            fixtures = await self._get_fixtures(date_str)
            for fx in fixtures:
                if fx.get("league", {}).get("id") in FOOTBALL_LEAGUES:
                    all_fixtures.append(fx)

        logger.debug(f"[API-Football] {len(all_fixtures)} fixtures in monitored leagues")

        # Cap to MAX_FIXTURES
        all_fixtures = all_fixtures[:MAX_FIXTURES]

        # ── Sequential fetching with safe delay ──────────────
        # Free plan = 10 req/min = must wait 6.5s between each prediction call
        for i, fx in enumerate(all_fixtures):
            pred_data = await self._get_prediction(fx.get("fixture", {}).get("id"))
            if pred_data:
                tip = self._parse_prediction(fx, pred_data)
                if tip:
                    tips.append(tip)
            # Wait between calls — free plan rate limit
            if i < len(all_fixtures) - 1:
                await asyncio.sleep(6.5)

        return tips

    # ── NBA ────────────────────────────────────────────────────

    async def _scrape_nba(self) -> List[RawTip]:
        tips = []
        try:
            data = await self.fetch_json(
                f"{NBA_BASE}/games",
                params={"date": str(datetime.utcnow().date()), "season": NBA_SEASON}
            )
            for game in data.get("response", [])[:10]:
                teams = game.get("teams", {})
                home  = teams.get("home", {}).get("name", "")
                away  = teams.get("away", {}).get("name", "")
                date_s = game.get("date", {}).get("start", "")
                if not home or not away:
                    continue
                kickoff = None
                try:
                    from datetime import timezone
                    dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                    kickoff = dt.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
                tips.append(RawTip(
                    home_team=home, away_team=away,
                    prediction=f"Home Win ({home})", market="1",
                    confidence=56.0, league="NBA", kickoff=kickoff,
                    tipster="API-NBA", source_name="API-NBA",
                ))
        except Exception as e:
            logger.warning(f"[API-NBA] error: {e}")
        return tips

    # ── BASKETBALL ─────────────────────────────────────────────

    async def _scrape_basketball(self) -> List[RawTip]:
        tips = []
        try:
            data = await self.fetch_json(
                f"{BASKETBALL_BASE}/games",
                params={"date": str(datetime.utcnow().date())}
            )
            for game in data.get("response", [])[:10]:
                teams  = game.get("teams", {})
                home   = teams.get("home", {}).get("name", "")
                away   = teams.get("away", {}).get("name", "")
                league = game.get("league", {}).get("name", "Basketball")
                date_s = game.get("date", "")
                if not home or not away:
                    continue
                kickoff = None
                try:
                    from datetime import timezone
                    dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                    kickoff = dt.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
                tips.append(RawTip(
                    home_team=home, away_team=away,
                    prediction=f"Home Win ({home})", market="1",
                    confidence=55.0, league=league, kickoff=kickoff,
                    tipster="API-Basketball", source_name="API-Basketball",
                ))
        except Exception as e:
            logger.warning(f"[API-Basketball] error: {e}")
        return tips

    # ── BASEBALL ───────────────────────────────────────────────

    async def _scrape_baseball(self) -> List[RawTip]:
        tips = []
        try:
            data = await self.fetch_json(
                f"{BASEBALL_BASE}/games",
                params={"date": str(datetime.utcnow().date())}
            )
            for game in data.get("response", [])[:10]:
                teams  = game.get("teams", {})
                home   = teams.get("home", {}).get("name", "")
                away   = teams.get("away", {}).get("name", "")
                league = game.get("league", {}).get("name", "Baseball")
                date_s = game.get("date", "")
                if not home or not away:
                    continue
                kickoff = None
                try:
                    from datetime import timezone
                    dt = datetime.fromisoformat(date_s.replace("Z", "+00:00"))
                    kickoff = dt.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    pass
                tips.append(RawTip(
                    home_team=home, away_team=away,
                    prediction=f"Home Win ({home})", market="1",
                    confidence=54.0, league=league, kickoff=kickoff,
                    tipster="API-Baseball", source_name="API-Baseball",
                ))
        except Exception as e:
            logger.warning(f"[API-Baseball] error: {e}")
        return tips

    # ── MAIN ───────────────────────────────────────────────────

    async def scrape(self) -> List[RawTip]:
        all_tips = []

        football_tips = await self._scrape_football()
        logger.info(f"[API-Football] ⚽ {len(football_tips)} football tips")
        all_tips.extend(football_tips)

        # Run NBA, basketball, baseball concurrently
        nba_t, bball_t, base_t = await asyncio.gather(
            self._scrape_nba(),
            self._scrape_basketball(),
            self._scrape_baseball(),
            return_exceptions=True
        )
        for result, label in [(nba_t, "🏀 NBA"), (bball_t, "🏀 Basketball"), (base_t, "⚾ Baseball")]:
            if isinstance(result, list):
                logger.info(f"[API-Football] {label} {len(result)} tips")
                all_tips.extend(result)

        return all_tips
