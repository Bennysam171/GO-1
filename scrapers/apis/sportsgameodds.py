"""
TipFusion AI — SportsGameOdds Scraper
API key: 6acdbdb871b0eacd9752c580da0b7371
Endpoint: https://api.sportsgameodds.com/v2/events
Covers soccer upcoming events — extracts h2h + over/under.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from scrapers.base import BaseScraper, RawTip
from core.logger import logger

API_BASE = "https://api.sportsgameodds.com/v2"
SGO_API_KEY = "6acdbdb871b0eacd9752c580da0b7371"

# Market keys as used by SportsGameOdds
SGO_MARKETS = ["moneyline", "totals", "spread"]

SPORT_CODES = [
    "SOCCER",
]


class SportsGameOddsScraper(BaseScraper):
    SOURCE_TYPE = "api"
    SOURCE_NAME = "SportsGameOdds"
    SOURCE_IDENTIFIER = "sportsgameodds.com"

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "X-Api-Key": SGO_API_KEY,
            "Content-Type": "application/json",
        }

    async def _fetch_events(self, sport: str) -> List[Dict]:
        url = f"{API_BASE}/events"
        params = {
            "sport": sport,
            "status": "upcoming",
            "limit": 50,
        }
        try:
            data = await self.fetch_json(url, params=params)
            # Response: {"data": [...], "meta": {...}}
            return data.get("data", []) if isinstance(data, dict) else []
        except Exception as e:
            logger.warning(f"[SGO] {sport} fetch error: {e}")
            return []

    def _parse_event(self, event: Dict) -> List[RawTip]:
        tips = []

        # Extract team names
        home = event.get("homeTeam", {}).get("name", "") or event.get("home_team", "")
        away = event.get("awayTeam", {}).get("name", "") or event.get("away_team", "")
        league = event.get("league", {}).get("name", "") or event.get("competition", "")
        start_str = event.get("startDate", "") or event.get("start_date", "")

        if not home or not away:
            return []

        # Parse kickoff
        kickoff = None
        try:
            kickoff = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except Exception:
            pass

        # Skip matches more than 2 days away
        if kickoff and kickoff > datetime.utcnow() + timedelta(days=2):
            return []

        # Extract odds from nested structure
        odds_data = event.get("odds", {}) or {}

        # ── 1X2 (moneyline) ────────────────────────────────────────
        ml = odds_data.get("moneyline", {}) or odds_data.get("h2h", {})
        if ml:
            home_odds_raw = ml.get("home", ml.get("homeOdds", 0))
            draw_odds_raw = ml.get("draw", ml.get("drawOdds", 0))
            away_odds_raw = ml.get("away", ml.get("awayOdds", 0))

            try:
                ho = float(home_odds_raw) if home_odds_raw else 0
                do = float(draw_odds_raw) if draw_odds_raw else 0
                ao = float(away_odds_raw) if away_odds_raw else 0

                if ho > 0 and do > 0 and ao > 0:
                    # Implied probabilities (normalized)
                    hi = 1 / ho
                    di = 1 / do
                    ai = 1 / ao
                    total = hi + di + ai
                    hn, dn, an = hi/total, di/total, ai/total

                    best_prob = max(hn, dn, an)
                    if best_prob == hn:
                        market, pred = "1", f"Home Win ({home})"
                        odds_val = ho
                    elif best_prob == an:
                        market, pred = "2", f"Away Win ({away})"
                        odds_val = ao
                    else:
                        market, pred = "X", "Draw"
                        odds_val = do

                    tips.append(RawTip(
                        home_team=home, away_team=away,
                        prediction=pred, market=market,
                        confidence=round(best_prob * 100, 1),
                        odds=odds_val,
                        league=league, kickoff=kickoff,
                        tipster="SportsGameOdds",
                        source_name=self.SOURCE_NAME,
                        extra={"home_prob": hn, "draw_prob": dn, "away_prob": an}
                    ))
            except (TypeError, ZeroDivisionError, ValueError):
                pass

        # ── Totals (Over/Under) ─────────────────────────────────────
        totals = odds_data.get("totals", {}) or odds_data.get("total", {})
        if totals:
            over_raw = totals.get("over", totals.get("overOdds", 0))
            under_raw = totals.get("under", totals.get("underOdds", 0))
            line = totals.get("line", totals.get("total", 2.5))

            try:
                ov = float(over_raw) if over_raw else 0
                un = float(under_raw) if under_raw else 0
                line_val = float(line) if line else 2.5

                if ov > 0 and un > 0:
                    over_prob = 1 / ov
                    under_prob = 1 / un
                    norm = over_prob + under_prob
                    over_norm = over_prob / norm

                    market_key = f"over_{line_val}" if line_val != 2.5 else "over_2.5"
                    under_key = f"under_{line_val}" if line_val != 2.5 else "under_2.5"

                    if over_norm >= 0.54:
                        tips.append(RawTip(
                            home_team=home, away_team=away,
                            prediction=f"Over {line_val}",
                            market="over_2.5" if line_val == 2.5 else "over_3.5",
                            confidence=round(over_norm * 100, 1),
                            odds=ov,
                            league=league, kickoff=kickoff,
                            tipster="SportsGameOdds",
                            source_name=self.SOURCE_NAME,
                        ))
                    elif (1 - over_norm) >= 0.58:
                        tips.append(RawTip(
                            home_team=home, away_team=away,
                            prediction=f"Under {line_val}",
                            market="under_2.5" if line_val == 2.5 else "under_3.5",
                            confidence=round((1 - over_norm) * 100, 1),
                            odds=un,
                            league=league, kickoff=kickoff,
                            tipster="SportsGameOdds",
                            source_name=self.SOURCE_NAME,
                        ))
            except (TypeError, ZeroDivisionError, ValueError):
                pass

        return tips

    async def scrape(self) -> List[RawTip]:
        all_tips = []

        for sport in SPORT_CODES:
            events = await self._fetch_events(sport)
            logger.debug(f"[SGO] {sport}: {len(events)} events fetched")

            for event in events:
                try:
                    tips = self._parse_event(event)
                    all_tips.extend(tips)
                except Exception as e:
                    logger.warning(f"[SGO] Parse error: {e}")

        return all_tips
