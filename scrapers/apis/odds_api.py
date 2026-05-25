"""
TipFusion AI — TheOddsAPI Scraper
4 rotating keys. Markets: h2h + totals only (btts removed — caused 422).
All datetimes normalized to naive UTC to prevent comparison errors.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Any
from collections import defaultdict

from scrapers.base import BaseScraper, RawTip
from core.logger import logger

API_BASE = "https://api.the-odds-api.com/v4"

ODDS_API_KEYS = [
    "781931e972ee586cefbcc97ba7b3d638",
    "7bc2984d027ba2b05a5b879a0ee43096",
    "365310a3332dc84c3e8af11510fbb8ba",
    "70c19d16a316079a597c27003bb70baa",
]

SPORTS = [
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_germany_bundesliga",
    "soccer_italy_serie_a",
    "soccer_france_ligue_one",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_africa_cup_of_nations",
]

# btts removed — not supported by all leagues and causes 422
MARKETS = ["h2h", "totals"]


def _to_naive_utc(dt: datetime) -> datetime:
    """Convert any datetime to naive UTC. Prevents offset comparison errors."""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class OddsApiScraper(BaseScraper):
    SOURCE_TYPE = "api"
    SOURCE_NAME = "TheOddsAPI"
    SOURCE_IDENTIFIER = "the-odds-api.com"

    def __init__(self):
        super().__init__()
        self._key_idx = 0

    @property
    def _current_key(self) -> str:
        return ODDS_API_KEYS[self._key_idx % len(ODDS_API_KEYS)]

    def _rotate_key(self):
        self._key_idx += 1
        logger.info(f"[OddsAPI] Rotated to key #{self._key_idx % len(ODDS_API_KEYS)}")

    @property
    def headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json"}

    async def _fetch_odds(self, sport: str) -> List[Dict]:
        url = f"{API_BASE}/sports/{sport}/odds/"
        for attempt in range(len(ODDS_API_KEYS)):
            params = {
                "apiKey": self._current_key,
                "regions": "uk,eu",
                "markets": ",".join(MARKETS),
                "oddsFormat": "decimal",
                "dateFormat": "iso",
            }
            try:
                data = await self.fetch_json(url, params=params)
                return data or []
            except Exception as e:
                err = str(e)
                if "401" in err or "429" in err or "quota" in err.lower():
                    logger.warning(f"[OddsAPI] Key exhausted for {sport} — rotating")
                    self._rotate_key()
                else:
                    logger.warning(f"[OddsAPI] {sport} error: {e}")
                    return []
        logger.error("[OddsAPI] All keys exhausted")
        return []

    def _implied_prob(self, odds: float) -> float:
        return 1.0 / odds if odds > 0 else 0.0

    def _best_odds(self, bookmakers: List[Dict], market_key: str) -> Dict[str, Any]:
        """Aggregate odds across bookmakers, return best + avg per outcome."""
        outcome_odds: Dict[str, List[float]] = defaultdict(list)

        for bk in bookmakers:
            for market in bk.get("markets", []):
                if market.get("key") != market_key:
                    continue
                for outcome in market.get("outcomes", []):
                    name  = outcome.get("name", "")
                    price = outcome.get("price", 0)
                    if name and price:
                        outcome_odds[name].append(float(price))

        result = {}
        for name, prices in outcome_odds.items():
            result[name] = {
                "best": max(prices),
                "avg":  sum(prices) / len(prices),
                "count": len(prices),
            }
        return result

    def _process_event(self, event: Dict) -> List[RawTip]:
        tips  = []
        home  = event.get("home_team", "")
        away  = event.get("away_team", "")
        sport = event.get("sport_title", "")
        commence_str = event.get("commence_time", "")
        bookmakers   = event.get("bookmakers", [])

        # ── Parse kickoff → always naive UTC ─────────────────
        kickoff = None
        try:
            dt = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            kickoff = _to_naive_utc(dt)
        except Exception:
            pass

        # Skip matches more than 2 days away
        if kickoff and kickoff > datetime.utcnow() + timedelta(days=2):
            return []

        # ── 1X2 ──────────────────────────────────────────────
        h2h = self._best_odds(bookmakers, "h2h")
        if h2h:
            home_d  = h2h.get(home, {})
            away_d  = h2h.get(away, {})
            draw_d  = h2h.get("Draw", {})

            ho = home_d.get("avg", 0)
            ao = away_d.get("avg", 0)
            do = draw_d.get("avg", 0)

            if ho > 0 and ao > 0 and do > 0:
                hi, ai, di = self._implied_prob(ho), self._implied_prob(ao), self._implied_prob(do)
                total = hi + ai + di
                hn, an, dn = hi/total, ai/total, di/total

                best_prob = max(hn, an, dn)
                if best_prob == hn:
                    market, pred_str = "1", f"Home Win ({home})"
                    best_odds_val    = home_d.get("best", ho)
                elif best_prob == an:
                    market, pred_str = "2", f"Away Win ({away})"
                    best_odds_val    = away_d.get("best", ao)
                else:
                    market, pred_str = "X", "Draw"
                    best_odds_val    = draw_d.get("best", do)

                tips.append(RawTip(
                    home_team=home, away_team=away,
                    prediction=pred_str, market=market,
                    confidence=round(best_prob * 100, 1),
                    odds=best_odds_val,
                    league=sport, kickoff=kickoff,
                    tipster="OddsAPI Consensus",
                    source_name=self.SOURCE_NAME,
                    extra={"home_prob": hn, "draw_prob": dn, "away_prob": an},
                ))

        # ── Over/Under ────────────────────────────────────────
        totals = self._best_odds(bookmakers, "totals")
        over_d  = totals.get("Over",  {})
        under_d = totals.get("Under", {})

        if over_d and under_d:
            ov = over_d.get("avg",  0)
            un = under_d.get("avg", 0)

            if ov > 0 and un > 0:
                op = self._implied_prob(ov)
                up = self._implied_prob(un)
                norm = op + up
                over_pct  = op / norm
                under_pct = up / norm

                if over_pct >= 0.54:
                    tips.append(RawTip(
                        home_team=home, away_team=away,
                        prediction="Over 2.5", market="over_2.5",
                        confidence=round(over_pct * 100, 1),
                        odds=over_d.get("best", ov),
                        league=sport, kickoff=kickoff,
                        tipster="OddsAPI Consensus",
                        source_name=self.SOURCE_NAME,
                    ))
                elif under_pct >= 0.58:
                    tips.append(RawTip(
                        home_team=home, away_team=away,
                        prediction="Under 2.5", market="under_2.5",
                        confidence=round(under_pct * 100, 1),
                        odds=under_d.get("best", un),
                        league=sport, kickoff=kickoff,
                        tipster="OddsAPI Consensus",
                        source_name=self.SOURCE_NAME,
                    ))

        return tips

    async def scrape(self) -> List[RawTip]:
        all_tips = []

        for sport in SPORTS:
            events = await self._fetch_odds(sport)
            logger.debug(f"[OddsAPI] {sport}: {len(events)} events")

            for event in events:
                try:
                    tips = self._process_event(event)
                    all_tips.extend(tips)
                except Exception as e:
                    logger.warning(f"[OddsAPI] Event parse error: {e}")

        return all_tips
