"""
TipFusion AI — FastAPI Application
REST API for monitoring, managing, and querying the system.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import List, Optional, Any, Dict

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from core.config import settings
from core.logger import logger
from core.orchestrator import Orchestrator
from core.scheduler import TipFusionScheduler
from database.connection import init_db, get_session, AsyncSessionLocal
from database import crud
from database.models import (
    Source, FusionPrediction, Match, ScrapeLog,
    PredictionStatus, SourceType
)
from telegram_bot.bot import TipFusionBot
from discovery.crawler import SourceDiscoverer

# ============================================================
# GLOBAL STATE
# ============================================================

_bot: Optional[TipFusionBot] = None
_orchestrator: Optional[Orchestrator] = None
_scheduler: Optional[TipFusionScheduler] = None
_discoverer: Optional[SourceDiscoverer] = None


# ============================================================
# LIFESPAN
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    global _bot, _orchestrator, _scheduler, _discoverer

    logger.info("🚀 TipFusion AI starting up...")

    # Init DB
    await init_db()

    # Init Telegram bot
    _bot = TipFusionBot()
    await _bot.init()

    # Init orchestrator
    _orchestrator = Orchestrator(bot=_bot)

    # Init discovery
    _discoverer = SourceDiscoverer()

    # Init scheduler
    _scheduler = TipFusionScheduler(_orchestrator, _bot, _discoverer)
    _scheduler.start()

    # Run initial pipeline in background
    asyncio.create_task(_orchestrator.run_full_pipeline())

    logger.success("✅ TipFusion AI is live!")
    yield

    # Shutdown
    logger.info("🔻 TipFusion AI shutting down...")
    if _scheduler:
        _scheduler.stop()
    if _bot:
        await _bot.stop()
    logger.info("👋 Shutdown complete")


# ============================================================
# APP
# ============================================================

app = FastAPI(
    title="TipFusion AI Ultimate",
    description="Autonomous football intelligence & prediction platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# PYDANTIC SCHEMAS
# ============================================================

class PredictionOut(BaseModel):
    id: int
    match_id: int
    home_team: str
    away_team: str
    league: Optional[str]
    kickoff: Optional[datetime]
    prediction: str
    market: str
    confidence_score: float
    risk_level: str
    source_count: int
    consensus_ratio: float
    posted_to_telegram: bool
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class SourceOut(BaseModel):
    id: int
    name: str
    type: str
    identifier: str
    is_active: bool
    reliability_score: float
    hit_rate: float
    total_tips: int
    wins: int
    losses: int
    last_scraped_at: Optional[datetime]

    class Config:
        from_attributes = True


class StatsOut(BaseModel):
    total_sources: int
    active_sources: int
    total_tips_scraped: int
    total_predictions: int
    won: int
    lost: int
    hit_rate: float


class JobOut(BaseModel):
    id: str
    name: str
    next_run: str


# ============================================================
# ROUTES — HEALTH
# ============================================================

@app.get("/", tags=["Health"])
async def root():
    return {
        "service": "TipFusion AI Ultimate",
        "status": "running",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/health", tags=["Health"])
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ============================================================
# ROUTES — PREDICTIONS
# ============================================================

@app.get("/predictions", response_model=List[Dict[str, Any]], tags=["Predictions"])
async def get_predictions(
    min_confidence: float = Query(default=65.0, ge=0, le=100),
    limit: int = Query(default=20, ge=1, le=100),
    unposted_only: bool = False,
    db: AsyncSession = Depends(get_session),
):
    """Get high-confidence fusion predictions with match details."""
    preds = await crud.get_high_confidence_predictions(
        db, min_score=min_confidence, unposted_only=unposted_only
    )
    preds = preds[:limit]

    results = []
    for pred in preds:
        result = await db.execute(select(Match).where(Match.id == pred.match_id))
        match = result.scalar_one_or_none()

        results.append({
            "id": pred.id,
            "home_team": match.home_team if match else "?",
            "away_team": match.away_team if match else "?",
            "league": match.league if match else None,
            "kickoff": match.kickoff_time.isoformat() if match and match.kickoff_time else None,
            "prediction": pred.prediction,
            "market": pred.market,
            "confidence_score": pred.confidence_score,
            "risk_level": pred.risk_level,
            "source_count": pred.source_count,
            "consensus_ratio": round(pred.consensus_ratio, 3),
            "posted_to_telegram": pred.posted_to_telegram,
            "status": pred.status,
            "created_at": pred.created_at.isoformat(),
            "market_breakdown": pred.market_breakdown,
        })

    return results


@app.get("/predictions/today", tags=["Predictions"])
async def get_todays_predictions(db: AsyncSession = Depends(get_session)):
    """Get all predictions for today."""
    return await get_predictions(min_confidence=settings.min_confidence_score, db=db)


@app.get("/predictions/top", tags=["Predictions"])
async def get_top_predictions(
    limit: int = 5,
    db: AsyncSession = Depends(get_session),
):
    """Get top N predictions by confidence."""
    return await get_predictions(min_confidence=75.0, limit=limit, db=db)


# ============================================================
# ROUTES — SOURCES
# ============================================================

@app.get("/sources", response_model=List[SourceOut], tags=["Sources"])
async def get_sources(
    active_only: bool = True,
    source_type: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
):
    """List all configured sources."""
    stype = None
    if source_type:
        try:
            stype = SourceType(source_type)
        except ValueError:
            raise HTTPException(400, f"Invalid source_type: {source_type}")

    sources = await crud.get_active_sources(db, source_type=stype)
    if active_only:
        sources = [s for s in sources if s.is_active]
    return sources


@app.post("/sources/{source_id}/toggle", tags=["Sources"])
async def toggle_source(source_id: int, db: AsyncSession = Depends(get_session)):
    """Enable or disable a source."""
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Source not found")

    source.is_active = not source.is_active
    await db.commit()
    return {"id": source_id, "is_active": source.is_active}


@app.get("/sources/{source_id}/stats", tags=["Sources"])
async def source_stats(source_id: int, db: AsyncSession = Depends(get_session)):
    """Detailed stats for one source."""
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        raise HTTPException(404, "Source not found")

    return {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "reliability_score": source.reliability_score,
        "weight": source.weight,
        "hit_rate": source.hit_rate,
        "total_tips": source.total_tips,
        "wins": source.wins,
        "losses": source.losses,
        "last_scraped_at": source.last_scraped_at,
        "created_at": source.created_at,
    }


# ============================================================
# ROUTES — STATS
# ============================================================

@app.get("/stats", response_model=StatsOut, tags=["Stats"])
async def get_stats(db: AsyncSession = Depends(get_session)):
    """Global system performance statistics."""
    return await crud.get_overall_stats(db)


@app.get("/stats/scrape-logs", tags=["Stats"])
async def get_scrape_logs(
    limit: int = 50,
    db: AsyncSession = Depends(get_session),
):
    """Recent scrape logs."""
    result = await db.execute(
        select(ScrapeLog).order_by(desc(ScrapeLog.started_at)).limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": l.id,
            "source_name": l.source_name,
            "started_at": l.started_at.isoformat(),
            "tips_found": l.tips_found,
            "tips_new": l.tips_new,
            "success": l.success,
            "duration_seconds": l.duration_seconds,
            "error": l.error_message,
        }
        for l in logs
    ]


# ============================================================
# ROUTES — CONTROL
# ============================================================

@app.post("/control/run-pipeline", tags=["Control"])
async def trigger_pipeline(background_tasks: BackgroundTasks):
    """Manually trigger the full scrape → analyze → broadcast pipeline."""
    if not _orchestrator:
        raise HTTPException(503, "Orchestrator not initialized")
    background_tasks.add_task(_orchestrator.run_full_pipeline)
    return {"status": "pipeline started", "timestamp": datetime.utcnow().isoformat()}


@app.post("/control/run-discovery", tags=["Control"])
async def trigger_discovery(background_tasks: BackgroundTasks):
    """Manually trigger source discovery."""
    if not _discoverer:
        raise HTTPException(503, "Discoverer not initialized")
    background_tasks.add_task(_discoverer.run)
    return {"status": "discovery started", "timestamp": datetime.utcnow().isoformat()}


@app.post("/control/broadcast", tags=["Control"])
async def trigger_broadcast(background_tasks: BackgroundTasks):
    """Re-broadcast any unposted high-confidence predictions."""
    if not _bot:
        raise HTTPException(503, "Bot not initialized")

    async def _do_broadcast():
        async with AsyncSessionLocal() as db:
            preds = await crud.get_high_confidence_predictions(db, unposted_only=True)
        await _bot.broadcast_predictions(preds)

    background_tasks.add_task(_do_broadcast)
    return {"status": "broadcast triggered"}


@app.get("/control/jobs", response_model=List[JobOut], tags=["Control"])
async def get_jobs():
    """List all scheduled jobs and their next run times."""
    if not _scheduler:
        raise HTTPException(503, "Scheduler not initialized")
    return _scheduler.get_jobs()
