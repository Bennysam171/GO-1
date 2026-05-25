"""
TipFusion AI — Database Cleanup
Run this ONCE to mark all old/non-football predictions as posted.
They will never be broadcast again after this.

Usage:
  python cleanup_db.py
"""

import asyncio
import re
from datetime import datetime, timedelta

NON_FOOTBALL = re.compile(
    r"\b(basketball|nba|wnba|nbl|pba|abl|bbl|bsn|lnb|acb|eurocup|euroleague"
    r"|baseball|mlb|npb|kbo|hockey|nhl|khl|nfl|rugby|volleyball"
    r"|handball|cricket|ipl|mma|ufc|boxing|futsal)\b",
    re.IGNORECASE,
)

NON_FOOTBALL_TEAMS = re.compile(
    r"\b(beermen|elasto|painters|gin.?kings|fuelmasters|honey.?badgers"
    r"|blackjacks|shooting.?stars|capitanes|marineros|titanes|licey"
    r"|gigantes|vaqueros|criollos|piratas|nuggets|lakers|celtics"
    r"|warriors|bulls|heat|knicks|nets|raptors|yankees|dodgers"
    r"|astros|cubs|mets|braves)\b",
    re.IGNORECASE,
)


async def cleanup():
    from database.connection import init_db, AsyncSessionLocal
    from database.models import FusionPrediction, Match
    from sqlalchemy import select, update

    await init_db()

    marked = 0
    now    = datetime.utcnow()
    cutoff = now - timedelta(hours=3)

    async with AsyncSessionLocal() as db:
        # Load all unposted predictions
        result = await db.execute(
            select(FusionPrediction).where(
                FusionPrediction.posted_to_telegram == False
            )
        )
        preds = result.scalars().all()
        print(f"Found {len(preds)} unposted predictions to check...")

        for pred in preds:
            should_mark = False

            # Load match
            m_result = await db.execute(
                select(Match).where(Match.id == pred.match_id)
            )
            match = m_result.scalar_one_or_none()

            if not match:
                should_mark = True
            else:
                league = (match.league or "").lower()
                home   = (match.home_team or "").lower()
                away   = (match.away_team or "").lower()

                # Non-football sport
                if NON_FOOTBALL.search(league):
                    should_mark = True
                elif NON_FOOTBALL_TEAMS.search(home) or NON_FOOTBALL_TEAMS.search(away):
                    should_mark = True
                # Old game (kickoff was more than 3 hours ago)
                elif match.kickoff_time and match.kickoff_time < cutoff:
                    should_mark = True

            if should_mark:
                pred.posted_to_telegram = True
                pred.posted_at = datetime.utcnow()
                marked += 1

        await db.commit()

    print(f"\n✅ Marked {marked} old/non-football predictions as posted")
    print(f"   They will never be broadcast again.")
    print(f"\n   Remaining unposted: {len(preds) - marked} football predictions")


if __name__ == "__main__":
    asyncio.run(cleanup())
