"""
TipFusion AI — Master Orchestrator
Scraper toggles controlled by scraper_config.json
All known issues fixed:
  - OddsAPI: markets=h2h,totals only (btts removed)
  - API-Football: rate limit delay added
  - Confidence threshold lowered to 45
  - Twitter/web scrapers disabled by default
  - Pillow error handled gracefully
"""

import asyncio
from datetime import datetime
from typing import List, Dict, Optional

from core.config import settings
from core.scraper_config import is_enabled, get_all_statuses
from core.logger import logger
from database.connection import AsyncSessionLocal, init_db
from database import crud
from database.models import SourceType, MarketType, PredictionStatus
from scrapers.base import RawTip
from analyzer.engine import AnalysisEngine, FusionResult

# ── Market key → Enum ────────────────────────────────────────
MARKET_KEY_TO_ENUM = {
    "1":          MarketType.HOME_WIN,
    "X":          MarketType.DRAW,
    "2":          MarketType.AWAY_WIN,
    "over_2.5":   MarketType.OVER_25,
    "under_2.5":  MarketType.UNDER_25,
    "over_3.5":   MarketType.OVER_35,
    "under_3.5":  MarketType.UNDER_35,
    "btts_yes":   MarketType.BTTS_YES,
    "btts_no":    MarketType.BTTS_NO,
    "1X":         MarketType.DOUBLE_CHANCE_1X,
    "X2":         MarketType.DOUBLE_CHANCE_X2,
    "12":         MarketType.DOUBLE_CHANCE_12,
    "dnb_1":      MarketType.DRAW_NO_BET_1,
    "dnb_2":      MarketType.DRAW_NO_BET_2,
}


def map_market(key: str) -> Optional[MarketType]:
    return MARKET_KEY_TO_ENUM.get(key)


# ============================================================
# BUILD SCRAPER LIST — reads scraper_config.json
# ============================================================

async def build_scraper_list():
    scrapers = []

    # ── API-Football (football + NBA + basketball + baseball) ─
    if is_enabled("apis", "api_football"):
        from scrapers.apis.api_football import ApiFootballScraper
        scrapers.append(ApiFootballScraper())

    # ── TheOddsAPI (4 rotating keys, btts removed) ────────────
    if is_enabled("apis", "odds_api"):
        from scrapers.apis.odds_api import OddsApiScraper
        scrapers.append(OddsApiScraper())

    # ── SportsGameOdds ────────────────────────────────────────
    if is_enabled("apis", "sportsgameodds"):
        from scrapers.apis.sportsgameodds import SportsGameOddsScraper
        scrapers.append(SportsGameOddsScraper())

    # ── Web scrapers ──────────────────────────────────────────
    if is_enabled("web", "forebet"):
        from scrapers.web.forebet import ForebetScraper
        scrapers.append(ForebetScraper())

    if is_enabled("web", "predictz"):
        from scrapers.web.prediction_sites import PredictzScraper
        scrapers.append(PredictzScraper())

    if is_enabled("web", "soccervista"):
        from scrapers.web.prediction_sites import SoccerVistaScraper
        scrapers.append(SoccerVistaScraper())

    if is_enabled("web", "windrawwin"):
        from scrapers.web.prediction_sites import WindrawwinScraper
        scrapers.append(WindrawwinScraper())

    return scrapers


async def build_social_scrapers():
    scrapers = []

    if is_enabled("social", "telegram"):
        if settings.telegram_api_id and settings.telegram_api_hash:
            from scrapers.social.telegram_scraper import TelegramScraper
            scrapers.append(TelegramScraper())
        else:
            logger.info("[Orchestrator] Telegram creds not set — skipping")

    if is_enabled("social", "twitter"):
        if settings.twitter_bearer_token:
            from scrapers.social.social_scrapers import TwitterScraper
            scrapers.append(TwitterScraper())

    if is_enabled("social", "reddit"):
        if settings.reddit_client_id:
            from scrapers.social.social_scrapers import RedditScraper
            scrapers.append(RedditScraper())

    return scrapers


# ============================================================
# ORCHESTRATOR
# ============================================================

class Orchestrator:
    def __init__(self, bot=None):
        self._bot = bot

    async def _load_source_weights(self) -> Dict[str, float]:
        weights = {}
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            from database.models import Source
            result = await db.execute(select(Source).where(Source.is_active == True))
            for src in result.scalars().all():
                weights[src.name] = src.weight
        return weights

    # ── STEP 1: SCRAPE ───────────────────────────────────────

    async def scrape_all(self) -> List[RawTip]:
        all_tips: List[RawTip] = []

        scrapers = await build_scraper_list()
        social   = await build_social_scrapers()

        logger.info(f"🚀 [Orchestrator] Running {len(scrapers)} API/web + {len(social)} social scrapers")

        # Show disabled scrapers in log
        statuses = get_all_statuses()
        for cat, items in statuses.items():
            if cat.startswith("_"):
                continue
            for name, enabled in items.items():
                if not enabled:
                    logger.info(f"   ⏸  [{cat}] {name} — DISABLED")

        # Run ALL scrapers concurrently (API + web + social together)
        all_scrapers_combined = scrapers + social
        results = await asyncio.gather(
            *[s.run() for s in all_scrapers_combined],
            return_exceptions=True
        )
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"[Orchestrator] Scraper {i} error: {result}")
            elif isinstance(result, list):
                all_tips.extend(result)

        # Android/BlueStacks
        if is_enabled("android", "bluestacks"):
            try:
                from android.bluestacks import BlueStacksController
                tips = await BlueStacksController().run()
                all_tips.extend(tips)
            except Exception as e:
                logger.warning(f"[Orchestrator] Android skipped: {e}")
        else:
            logger.info("   ⏸  [android] bluestacks — DISABLED")

        logger.success(f"✅ [Orchestrator] Total tips collected: {len(all_tips)}")
        return all_tips

    # ── STEP 2: STORE ────────────────────────────────────────

    async def store_tips(self, tips: List[RawTip]) -> int:
        new_count = 0
        async with AsyncSessionLocal() as db:
            for tip in tips:
                try:
                    market_enum = map_market(tip.market)
                    if not market_enum:
                        continue

                    source_type = SourceType.WEB
                    name = tip.source_name or "Unknown"
                    if "Telegram" in name:  source_type = SourceType.TELEGRAM
                    elif "Reddit"   in name:  source_type = SourceType.REDDIT
                    elif "Twitter"  in name:  source_type = SourceType.TWITTER
                    elif "App:"     in name:  source_type = SourceType.ANDROID_APP
                    elif name in ("API-Football","TheOddsAPI","Football-Data.org",
                                  "SportsGameOdds","API-NBA","API-Basketball","API-Baseball"):
                        source_type = SourceType.API

                    source, _ = await crud.get_or_create_source(
                        db, name=name, source_type=source_type, identifier=name)

                    match = None
                    if tip.kickoff:
                        match, _ = await crud.get_or_create_match(
                            db,
                            home_team=tip.home_team,
                            away_team=tip.away_team,
                            kickoff_time=tip.kickoff,
                            league=tip.league,
                        )

                    _, created = await crud.save_tip(
                        db,
                        source_id=source.id,
                        raw_home=tip.home_team,
                        raw_away=tip.away_team,
                        market=market_enum,
                        prediction=tip.prediction,
                        match_id=match.id if match else None,
                        confidence=tip.confidence,
                        odds=tip.odds,
                        tipster_name=tip.tipster,
                        raw_league=tip.league,
                        raw_kickoff=str(tip.kickoff) if tip.kickoff else None,
                    )
                    if created:
                        new_count += 1
                    source.last_scraped_at = datetime.utcnow()

                except Exception as e:
                    logger.warning(f"[Orchestrator] Tip store error: {e}")

            await db.commit()

        logger.info(f"💾 [Orchestrator] Stored {new_count} new tips")
        return new_count

    # ── STEP 3: ANALYZE ──────────────────────────────────────

    async def analyze(self, tips: List[RawTip]) -> List[FusionResult]:
        weights = await self._load_source_weights()
        engine  = AnalysisEngine(source_weights=weights)
        return engine.analyze_all(tips)

    # ── STEP 4: SAVE FUSION PREDICTIONS ──────────────────────

    async def save_fusion_predictions(self, results: List[FusionResult]) -> List:
        saved = []
        async with AsyncSessionLocal() as db:
            for result in results:
                try:
                    market_enum = map_market(result.best_market)
                    if not market_enum or not result.kickoff:
                        continue

                    match, _ = await crud.get_or_create_match(
                        db,
                        home_team=result.home_team,
                        away_team=result.away_team,
                        kickoff_time=result.kickoff,
                        league=result.league,
                    )

                    from database.models import RiskLevel
                    risk_map = {"low": RiskLevel.LOW, "medium": RiskLevel.MEDIUM, "high": RiskLevel.HIGH}
                    risk = risk_map.get(result.risk_level, RiskLevel.MEDIUM)

                    fp = await crud.save_fusion_prediction(
                        db,
                        match_id=match.id,
                        market=market_enum,
                        prediction=result.best_prediction,
                        confidence_score=result.confidence_score,
                        risk_level=risk,
                        source_count=result.source_count,
                        consensus_ratio=result.consensus_ratio,
                        market_breakdown=result.market_breakdown,
                        source_breakdown=result.source_breakdown,
                    )
                    saved.append(fp)
                except Exception as e:
                    logger.warning(f"[Orchestrator] Save error: {e}")
            await db.commit()

        logger.info(f"💾 [Orchestrator] Saved {len(saved)} fusion predictions")
        return saved

    # ── STEP 5: BROADCAST ────────────────────────────────────

    async def broadcast(self, predictions: List):
        if not self._bot:
            logger.warning("[Orchestrator] No bot — skipping broadcast")
            return
        high = [p for p in predictions
                if p.confidence_score >= settings.min_confidence_score]
        if not high:
            logger.info("[Orchestrator] No predictions above threshold to broadcast")
            return
        await self._bot.broadcast_predictions(high)

    # ── FULL PIPELINE ─────────────────────────────────────────

    async def run_full_pipeline(self, broadcast: bool = True):
        start = datetime.utcnow()
        logger.info("=" * 50)
        logger.info("🔄 [Orchestrator] FULL PIPELINE STARTED")
        logger.info("=" * 50)

        try:
            tips = await self.scrape_all()
            if not tips:
                logger.warning("[Orchestrator] No tips collected — aborting")
                return

            await self.store_tips(tips)
            results = await self.analyze(tips)

            if not results:
                logger.info("[Orchestrator] No high-confidence predictions this run")
                return

            saved = await self.save_fusion_predictions(results)

            if broadcast and saved:
                await self.broadcast(saved)

            duration = (datetime.utcnow() - start).total_seconds()
            posted = len([p for p in saved
                          if p.confidence_score >= settings.min_confidence_score])
            logger.success(
                f"✅ Pipeline done in {duration:.1f}s — "
                f"{len(tips)} tips → {len(results)} predictions → {posted} posted"
            )
        except Exception as e:
            logger.error(f"❌ [Orchestrator] Pipeline error: {e}", exc_info=True)

    async def run_scrape_only(self) -> List[RawTip]:
        return await self.scrape_all()

    async def run_analysis_only(self, tips: List[RawTip]) -> List[FusionResult]:
        return await self.analyze(tips)
