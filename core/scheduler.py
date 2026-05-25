"""
TipFusion AI — Task Scheduler
APScheduler-powered job runner:
- Scrape + analyze + post on schedule
- Discovery crawler on separate interval
- Source reliability recalculation
- Daily performance summary
"""

import asyncio
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from core.config import settings
from core.logger import logger


class TipFusionScheduler:
    """Wraps APScheduler with all TipFusion jobs."""

    def __init__(self, orchestrator, bot, discoverer):
        self._orchestrator = orchestrator
        self._bot = bot
        self._discoverer = discoverer
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def _setup_jobs(self):
        """Register all scheduled jobs."""

        # ── Main pipeline at 4 fixed times from config.yaml ─────────
        # wake_up_times: ["07:00", "12:00", "17:00", "21:00"]
        wake_times = [
            {"hour": 7,  "minute": 0},
            {"hour": 12, "minute": 0},
            {"hour": 17, "minute": 0},
            {"hour": 21, "minute": 0},
        ]
        for wt in wake_times:
            self._scheduler.add_job(
                self._run_pipeline,
                trigger=CronTrigger(hour=wt["hour"], minute=wt["minute"], timezone="UTC"),
                id=f"pipeline_{wt['hour']:02d}{wt['minute']:02d}",
                name=f"Pipeline {wt['hour']:02d}:{wt['minute']:02d} UTC",
                replace_existing=True,
                max_instances=1,
            )

        # ── Also run every 5 minutes in autonomous mode (300s interval) ──
        # config: autonomous_mode: true, autonomous_interval_seconds: 300
        self._scheduler.add_job(
            self._run_pipeline_autonomous,
            trigger=IntervalTrigger(seconds=1800),
            id="pipeline_autonomous",
            name="Autonomous pipeline (every 30 min)",
            replace_existing=True,
            max_instances=1,
        )

        # ── Source discovery every 6 hours ───────────────────────────
        self._scheduler.add_job(
            self._run_discovery,
            trigger=IntervalTrigger(hours=settings.discovery_interval_hours),
            id="discovery",
            name="Auto source discovery",
            replace_existing=True,
            max_instances=1,
        )

        # ── Daily summary at 07:00 UTC (before first pipeline run) ───
        self._scheduler.add_job(
            self._daily_summary,
            trigger=CronTrigger(hour=6, minute=55, timezone="UTC"),
            id="daily_summary",
            name="Daily summary post",
            replace_existing=True,
        )

        # ── Source reliability recalculation every 6 hours ───────────
        self._scheduler.add_job(
            self._recalculate_reliability,
            trigger=IntervalTrigger(hours=6),
            id="reliability",
            name="Source reliability update",
            replace_existing=True,
        )

        # ── Result checker every 2 hours ──────────────────────────────
        self._scheduler.add_job(
            self._check_results,
            trigger=IntervalTrigger(hours=2),
            id="result_checker",
            name="Check match results (won/lost)",
            replace_existing=True,
        )

        # ── API cache refresh (api_cache_minutes: 120) ────────────────
        self._scheduler.add_job(
            self._refresh_api_cache,
            trigger=IntervalTrigger(minutes=120),
            id="api_cache",
            name="API cache refresh (120 min)",
            replace_existing=True,
        )

        logger.info("📅 [Scheduler] All jobs registered")

    async def _run_pipeline(self):
        """Execute the full pipeline at scheduled wake-up times."""
        logger.info("⏰ [Scheduler] Triggering wake-up pipeline...")
        try:
            await self._orchestrator.run_full_pipeline(broadcast=True)
        except Exception as e:
            logger.error(f"[Scheduler] Pipeline job error: {e}")

    async def _run_pipeline_autonomous(self):
        """
        Lightweight autonomous check every 5 minutes.
        Only runs full pipeline if there are new tips to process.
        Respects api_cache_minutes: 120 — won't re-hit APIs until cache expires.
        """
        try:
            # Check if there are any unposted predictions already waiting
            from database.connection import AsyncSessionLocal
            from database import crud
            async with AsyncSessionLocal() as db:
                unposted = await crud.get_high_confidence_predictions(db, unposted_only=True)

            if unposted:
                logger.info(f"⏰ [Scheduler] Autonomous: {len(unposted)} unposted — broadcasting")
                if self._bot:
                    await self._bot.broadcast_predictions(unposted)
            else:
                logger.debug("[Scheduler] Autonomous: nothing to post — idle")
        except Exception as e:
            logger.error(f"[Scheduler] Autonomous job error: {e}")

    async def _refresh_api_cache(self):
        """Scrape APIs and store fresh tips (no broadcast — feeds next analysis)."""
        logger.info("⏰ [Scheduler] Refreshing API cache...")
        try:
            tips = await self._orchestrator.run_scrape_only()
            if tips:
                await self._orchestrator.store_tips(tips)
                logger.info(f"✅ [Scheduler] Cache refreshed: {len(tips)} tips stored")
        except Exception as e:
            logger.error(f"[Scheduler] Cache refresh error: {e}")

    async def _run_discovery(self):
        """Run the auto source discovery crawler."""
        logger.info("⏰ [Scheduler] Triggering source discovery...")
        try:
            await self._discoverer.run()
        except Exception as e:
            logger.error(f"[Scheduler] Discovery job error: {e}")

    async def _daily_summary(self):
        """Post a daily performance summary to Telegram."""
        logger.info("⏰ [Scheduler] Posting daily summary...")
        try:
            from database.connection import AsyncSessionLocal
            from database import crud
            from telegram_bot.bot import format_stats_card

            async with AsyncSessionLocal() as db:
                stats = await crud.get_overall_stats(db)

            text = (
                f"☀️ *Good morning! TipFusion AI Daily Summary*\n\n"
                + format_stats_card(stats)
                + f"\n\n_Updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}_"
            )
            if self._bot:
                await self._bot.send_message(text)

        except Exception as e:
            logger.error(f"[Scheduler] Daily summary error: {e}")

    async def _recalculate_reliability(self):
        """Update source reliability scores based on recent outcomes."""
        logger.info("⏰ [Scheduler] Recalculating source reliability...")
        try:
            from database.connection import AsyncSessionLocal
            from database.models import Source, Tip, FusionPrediction, PredictionStatus
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(select(Source).where(Source.is_active == True))
                sources = result.scalars().all()

                for source in sources:
                    source.update_hit_rate()
                    source.compute_reliability_score()
                    source.weight = source.reliability_score / 50.0

                await db.commit()

            logger.info(f"✅ [Scheduler] Updated reliability for {len(sources)} sources")

        except Exception as e:
            logger.error(f"[Scheduler] Reliability update error: {e}")

    async def _check_results(self):
        """Check match results and post won/lost summary to Telegram."""
        logger.info("⏰ [Scheduler] Checking match results...")
        try:
            from core.result_checker import ResultChecker
            checker = ResultChecker()
            await checker.run(bot=self._bot)
        except Exception as e:
            logger.error(f"[Scheduler] Result check error: {e}")

    def start(self):
        self._setup_jobs()
        self._scheduler.start()
        logger.success("✅ [Scheduler] Started — all jobs active")

    def stop(self):
        self._scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Stopped")

    def get_jobs(self):
        return [
            {
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time),
            }
            for job in self._scheduler.get_jobs()
        ]
