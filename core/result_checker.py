"""
TipFusion AI — Result Checker
Checks match results online and marks predictions as WON/LOST.

Sources:
  1. API-Football (primary — uses same key already in system)
  2. TheOddsAPI scores endpoint (fallback)

Runs automatically every 2 hours via scheduler.
Also available as: python main.py results
"""

import asyncio
import re
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Tuple

import aiohttp

from core.config import settings
from core.logger import logger
from database.connection import AsyncSessionLocal
from database.models import (
    FusionPrediction, Match, Tip,
    PredictionStatus, MarketType
)
from database import crud
from sqlalchemy import select, and_, update

API_SPORTS_KEY = "41e811d696b091b5de0c9905e8ff84b3"
FOOTBALL_BASE  = "https://v3.football.api-sports.io"


# ── Market result evaluators ─────────────────────────────────

def evaluate_result(
    market: str,
    home_score: int,
    away_score: int,
    home_ht: Optional[int] = None,
    away_ht: Optional[int] = None,
) -> Optional[bool]:
    """
    Given final scores, determine if a prediction was correct.
    Returns True (won), False (lost), None (can't determine).
    """
    total = home_score + away_score

    if market == "1":          # Home Win
        return home_score > away_score
    if market == "X":          # Draw
        return home_score == away_score
    if market == "2":          # Away Win
        return away_score > home_score
    if market == "over_2.5":   # Over 2.5
        return total > 2
    if market == "under_2.5":  # Under 2.5
        return total < 3
    if market == "over_3.5":
        return total > 3
    if market == "under_3.5":
        return total < 4
    if market == "btts_yes":   # Both teams scored
        return home_score > 0 and away_score > 0
    if market == "btts_no":
        return home_score == 0 or away_score == 0
    if market == "1X":         # Home or Draw
        return home_score >= away_score
    if market == "X2":         # Draw or Away
        return away_score >= home_score
    if market == "12":         # Home or Away (no draw)
        return home_score != away_score
    if market == "dnb_1":      # Draw No Bet Home
        if home_score == away_score:
            return None        # Void (push)
        return home_score > away_score
    if market == "dnb_2":      # Draw No Bet Away
        if home_score == away_score:
            return None
        return away_score > home_score

    return None


# ── API-Football result fetcher ───────────────────────────────

class ResultFetcher:
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def _headers(self) -> Dict:
        return {"x-apisports-key": API_SPORTS_KEY}

    async def _get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=20),
            )
        return self._session

    async def _fetch(self, url: str, params: Dict) -> Dict:
        session = await self._get_session()
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except Exception as e:
            logger.warning(f"[ResultChecker] Fetch error: {e}")
            return {}

    async def get_fixture_result(self, fixture_id: int) -> Optional[Dict]:
        """Get result for a specific fixture ID."""
        data = await self._fetch(
            f"{FOOTBALL_BASE}/fixtures",
            {"id": fixture_id}
        )
        resp = data.get("response", [])
        return resp[0] if resp else None

    async def search_fixture_by_teams(
        self,
        home: str,
        away: str,
        match_date: datetime,
    ) -> Optional[Dict]:
        """Search for a fixture by team names and date."""
        date_str = match_date.strftime("%Y-%m-%d")
        data = await self._fetch(
            f"{FOOTBALL_BASE}/fixtures",
            {"date": date_str, "timezone": "UTC"}
        )

        fixtures = data.get("response", [])
        if not fixtures:
            return None

        # Fuzzy match team names
        home_lower = home.lower().strip()
        away_lower = away.lower().strip()

        def name_match(a: str, b: str) -> bool:
            a, b = a.lower().strip(), b.lower().strip()
            if a == b: return True
            # Check if 4+ char prefix matches
            if len(a) >= 4 and len(b) >= 4:
                if a[:4] in b or b[:4] in a:
                    return True
            # Token overlap
            tokens_a = set(a.split())
            tokens_b = set(b.split())
            if tokens_a & tokens_b:
                return True
            return False

        for fx in fixtures:
            teams = fx.get("teams", {})
            fx_home = teams.get("home", {}).get("name", "")
            fx_away = teams.get("away", {}).get("name", "")

            if name_match(home_lower, fx_home) and name_match(away_lower, fx_away):
                return fx

        return None

    def extract_score(self, fixture: Dict) -> Tuple[Optional[int], Optional[int]]:
        """Extract home/away full-time scores from fixture data."""
        try:
            goals = fixture.get("goals", {})
            home  = goals.get("home")
            away  = goals.get("away")
            if home is not None and away is not None:
                return int(home), int(away)

            # Fallback: score section
            score = fixture.get("score", {})
            ft    = score.get("fulltime", {})
            h     = ft.get("home")
            a     = ft.get("away")
            if h is not None and a is not None:
                return int(h), int(a)
        except Exception:
            pass
        return None, None

    def is_finished(self, fixture: Dict) -> bool:
        """Check if the match is finished."""
        status = fixture.get("fixture", {}).get("status", {})
        short  = status.get("short", "")
        return short in ("FT", "AET", "PEN", "WO", "AWD")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# ── Main result checker ───────────────────────────────────────

class ResultChecker:
    def __init__(self):
        self._fetcher = ResultFetcher()

    async def check_and_update(self) -> Dict:
        """
        Main entry: find all settled predictions that need result checking,
        fetch scores, mark won/lost, post summary to Telegram.
        """
        stats = {"checked": 0, "won": 0, "lost": 0, "void": 0, "skipped": 0}
        now   = datetime.utcnow()

        async with AsyncSessionLocal() as db:
            # Find predictions that:
            # 1. Were posted to Telegram (we only care about posted ones)
            # 2. Are still PENDING
            # 3. Kickoff was at least 2 hours ago (game likely finished)
            result = await db.execute(
                select(FusionPrediction, Match)
                .join(Match, FusionPrediction.match_id == Match.id)
                .where(
                    and_(
                        FusionPrediction.status == PredictionStatus.PENDING,
                        FusionPrediction.posted_to_telegram == True,
                        Match.kickoff_time <= now - timedelta(hours=2),
                        Match.kickoff_time >= now - timedelta(days=3),  # Not too old
                    )
                )
                .order_by(Match.kickoff_time.asc())
            )
            rows = result.all()

        if not rows:
            logger.info("[ResultChecker] No pending predictions to check")
            return stats

        logger.info(f"[ResultChecker] Checking {len(rows)} predictions...")

        results_to_post = []  # (prediction, match, won, home_score, away_score)

        for pred, match in rows:
            try:
                # Try to get result from API
                fx = await self._fetcher.search_fixture_by_teams(
                    match.home_team,
                    match.away_team,
                    match.kickoff_time,
                )

                if not fx:
                    logger.debug(f"[ResultChecker] No fixture found: {match.home_team} vs {match.away_team}")
                    stats["skipped"] += 1
                    continue

                if not self._fetcher.is_finished(fx):
                    logger.debug(f"[ResultChecker] Not finished yet: {match.home_team} vs {match.away_team}")
                    stats["skipped"] += 1
                    continue

                home_score, away_score = self._fetcher.extract_score(fx)
                if home_score is None or away_score is None:
                    stats["skipped"] += 1
                    continue

                # Evaluate result
                won = evaluate_result(
                    pred.market, home_score, away_score
                )

                # Update DB
                async with AsyncSessionLocal() as db:
                    if won is True:
                        new_status = PredictionStatus.WON
                        stats["won"] += 1
                    elif won is False:
                        new_status = PredictionStatus.LOST
                        stats["lost"] += 1
                    else:
                        new_status = PredictionStatus.VOID
                        stats["void"] += 1

                    await db.execute(
                        update(FusionPrediction)
                        .where(FusionPrediction.id == pred.id)
                        .values(status=new_status)
                    )
                    # Also update match scores
                    await db.execute(
                        update(Match)
                        .where(Match.id == match.id)
                        .values(
                            home_score=home_score,
                            away_score=away_score,
                            status="finished",
                        )
                    )

                    # Update source reliability
                    if won is not None:
                        await crud.update_source_stats(
                            db,
                            source_id=1,  # Will be improved when we track per-source
                            won=(won is True)
                        )

                    await db.commit()

                stats["checked"] += 1
                results_to_post.append((pred, match, won, home_score, away_score))

                logger.info(
                    f"[ResultChecker] {match.home_team} {home_score}-{away_score} {match.away_team}"
                    f" | {pred.market} → {'✅ WON' if won else '❌ LOST' if won is False else '🔁 VOID'}"
                )

                await asyncio.sleep(1.0)  # Rate limiting

            except Exception as e:
                logger.warning(f"[ResultChecker] Error checking {match.home_team} vs {match.away_team}: {e}")
                stats["skipped"] += 1

        await self._fetcher.close()

        logger.success(
            f"✅ [ResultChecker] Done — "
            f"checked={stats['checked']} won={stats['won']} "
            f"lost={stats['lost']} void={stats['void']}"
        )

        return stats, results_to_post

    async def run(self, bot=None) -> Dict:
        """Run result check and optionally post summary to Telegram."""
        result = await self.check_and_update()

        if isinstance(result, tuple):
            stats, results_to_post = result
        else:
            stats, results_to_post = result, []

        # Post results to Telegram if bot available
        if bot and results_to_post:
            await self._post_results(bot, results_to_post, stats)

        return stats

    async def _post_results(self, bot, results_to_post: list, stats: Dict):
        """Post result summary to Telegram channel."""
        if not results_to_post:
            return

        total    = len(results_to_post)
        won_list = [(p, m, h, a) for p, m, won, h, a in results_to_post if won is True]
        lost_list= [(p, m, h, a) for p, m, won, h, a in results_to_post if won is False]
        void_list= [(p, m, h, a) for p, m, won, h, a in results_to_post if won is None]

        hit_rate = (len(won_list) / total * 100) if total > 0 else 0

        msg = (
            f"📊 *TipFusion AI — Results Update*\n"
            f"{'─' * 28}\n"
            f"✅ Won:  *{len(won_list)}*\n"
            f"❌ Lost: *{len(lost_list)}*\n"
            f"🔁 Void: *{len(void_list)}*\n"
            f"📈 Hit Rate: *{hit_rate:.1f}%*\n"
            f"{'─' * 28}\n"
        )

        # Won predictions
        if won_list:
            msg += "✅ *WINNERS:*\n"
            for pred, match, h, a in won_list[:10]:
                home_copy = f"`{match.home_team}`"
                away_copy = f"`{match.away_team}`"
                msg += (
                    f"  {home_copy} *{h}–{a}* {away_copy}\n"
                    f"  ✓ {pred.prediction}\n"
                )
            if len(won_list) > 10:
                msg += f"  _...and {len(won_list)-10} more_\n"
            msg += "\n"

        # Lost predictions
        if lost_list:
            msg += "❌ *LOSSES:*\n"
            for pred, match, h, a in lost_list[:5]:
                home_copy = f"`{match.home_team}`"
                away_copy = f"`{match.away_team}`"
                msg += (
                    f"  {home_copy} *{h}–{a}* {away_copy}\n"
                    f"  ✗ {pred.prediction}\n"
                )
            if len(lost_list) > 5:
                msg += f"  _...and {len(lost_list)-5} more_\n"

        msg += f"{'─' * 28}\n#TipFusion #Results"

        await bot.send_message(msg)
        logger.info(f"[ResultChecker] Results posted to Telegram")
