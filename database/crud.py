"""
TipFusion AI — CRUD Operations
All database read/write operations in one place.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, and_, func
from database.models import (
    Source, Match, Tip, FusionPrediction,
    ScrapeLog, TelegramLog, SourceType,
    PredictionStatus, MarketType
)
from core.logger import logger


# ============================================================
# SOURCES
# ============================================================

async def get_or_create_source(
    db: AsyncSession,
    name: str,
    source_type: SourceType,
    identifier: str,
    **kwargs
) -> tuple[Source, bool]:
    """Get existing source or create new one. Returns (source, created)."""
    result = await db.execute(
        select(Source).where(
            and_(Source.type == source_type, Source.identifier == identifier)
        )
    )
    source = result.scalar_one_or_none()
    if source:
        return source, False

    source = Source(
        name=name,
        type=source_type,
        identifier=identifier,
        **kwargs
    )
    db.add(source)
    await db.flush()
    logger.info(f"📡 New source registered: {name} [{source_type}]")
    return source, True


async def get_active_sources(
    db: AsyncSession,
    source_type: Optional[SourceType] = None
) -> List[Source]:
    query = select(Source).where(Source.is_active == True)
    if source_type:
        query = query.where(Source.type == source_type)
    result = await db.execute(query)
    return result.scalars().all()


async def update_source_stats(
    db: AsyncSession,
    source_id: int,
    won: bool
):
    """Update win/loss counts and recalculate hit rate for a source."""
    result = await db.execute(select(Source).where(Source.id == source_id))
    source = result.scalar_one_or_none()
    if not source:
        return

    if won:
        source.wins += 1
    else:
        source.losses += 1
    source.total_tips += 1
    source.update_hit_rate()
    source.compute_reliability_score()
    source.weight = source.reliability_score / 50.0  # Normalize weight around 1.0
    await db.flush()


# ============================================================
# MATCHES
# ============================================================

async def get_or_create_match(
    db: AsyncSession,
    home_team: str,
    away_team: str,
    kickoff_time: datetime,
    **kwargs
) -> tuple[Match, bool]:
    result = await db.execute(
        select(Match).where(
            and_(
                Match.home_team == home_team,
                Match.away_team == away_team,
                Match.kickoff_time == kickoff_time,
            )
        )
    )
    match = result.scalar_one_or_none()
    if match:
        return match, False

    match = Match(
        home_team=home_team,
        away_team=away_team,
        kickoff_time=kickoff_time,
        **kwargs
    )
    db.add(match)
    await db.flush()
    return match, True


async def get_upcoming_matches(
    db: AsyncSession,
    hours_ahead: int = 24
) -> List[Match]:
    now = datetime.utcnow()
    cutoff = now + timedelta(hours=hours_ahead)
    result = await db.execute(
        select(Match).where(
            and_(
                Match.kickoff_time >= now,
                Match.kickoff_time <= cutoff,
                Match.status == "scheduled",
            )
        ).order_by(Match.kickoff_time)
    )
    return result.scalars().all()


# ============================================================
# TIPS
# ============================================================

async def save_tip(
    db: AsyncSession,
    source_id: int,
    raw_home: str,
    raw_away: str,
    market: MarketType,
    prediction: str,
    match_id: Optional[int] = None,
    **kwargs
) -> tuple[Tip, bool]:
    """Save a tip, avoiding duplicates from the same source for same match+market."""
    result = await db.execute(
        select(Tip).where(
            and_(
                Tip.source_id == source_id,
                Tip.raw_home_team == raw_home,
                Tip.raw_away_team == raw_away,
                Tip.market == market,
            )
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return existing, False

    tip = Tip(
        source_id=source_id,
        match_id=match_id,
        raw_home_team=raw_home,
        raw_away_team=raw_away,
        market=market,
        prediction=prediction,
        **kwargs
    )
    db.add(tip)
    await db.flush()
    return tip, True


async def get_tips_for_match(
    db: AsyncSession,
    match_id: int
) -> List[Tip]:
    result = await db.execute(
        select(Tip).where(Tip.match_id == match_id)
    )
    return result.scalars().all()


async def get_recent_tips(
    db: AsyncSession,
    hours: int = 6
) -> List[Tip]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    result = await db.execute(
        select(Tip).where(Tip.scraped_at >= cutoff)
    )
    return result.scalars().all()


# ============================================================
# FUSION PREDICTIONS
# ============================================================

async def save_fusion_prediction(
    db: AsyncSession,
    match_id: int,
    market: MarketType,
    prediction: str,
    confidence_score: float,
    risk_level: str,
    source_count: int,
    consensus_ratio: float,
    market_breakdown: Dict,
    source_breakdown: Dict,
) -> FusionPrediction:
    fp = FusionPrediction(
        match_id=match_id,
        market=market,
        prediction=prediction,
        confidence_score=confidence_score,
        risk_level=risk_level,
        source_count=source_count,
        consensus_ratio=consensus_ratio,
        market_breakdown=market_breakdown,
        source_breakdown=source_breakdown,
    )
    db.add(fp)
    await db.flush()
    return fp


async def get_high_confidence_predictions(
    db: AsyncSession,
    min_score: float = 65.0,
    unposted_only: bool = True
) -> List[FusionPrediction]:
    query = select(FusionPrediction).where(
        and_(
            FusionPrediction.confidence_score >= min_score,
            FusionPrediction.status == PredictionStatus.PENDING,
        )
    )
    if unposted_only:
        query = query.where(FusionPrediction.posted_to_telegram == False)
    result = await db.execute(query.order_by(FusionPrediction.confidence_score.desc()))
    return result.scalars().all()


async def mark_prediction_posted(
    db: AsyncSession,
    prediction_id: int
):
    await db.execute(
        update(FusionPrediction)
        .where(FusionPrediction.id == prediction_id)
        .values(posted_to_telegram=True, posted_at=datetime.utcnow())
    )


# ============================================================
# SCRAPE LOG
# ============================================================

async def log_scrape(
    db: AsyncSession,
    source_id: Optional[int],
    source_name: str,
    tips_found: int,
    tips_new: int,
    success: bool,
    started_at: datetime,
    error_message: Optional[str] = None,
) -> ScrapeLog:
    finished_at = datetime.utcnow()
    duration = (finished_at - started_at).total_seconds()
    log = ScrapeLog(
        source_id=source_id,
        source_name=source_name,
        started_at=started_at,
        finished_at=finished_at,
        tips_found=tips_found,
        tips_new=tips_new,
        success=success,
        error_message=error_message,
        duration_seconds=duration,
    )
    db.add(log)
    await db.flush()
    return log


# ============================================================
# STATS
# ============================================================

async def get_overall_stats(db: AsyncSession) -> Dict[str, Any]:
    total_sources = await db.scalar(select(func.count(Source.id)))
    active_sources = await db.scalar(
        select(func.count(Source.id)).where(Source.is_active == True)
    )
    total_tips = await db.scalar(select(func.count(Tip.id)))
    total_predictions = await db.scalar(select(func.count(FusionPrediction.id)))
    won = await db.scalar(
        select(func.count(FusionPrediction.id))
        .where(FusionPrediction.status == PredictionStatus.WON)
    )
    lost = await db.scalar(
        select(func.count(FusionPrediction.id))
        .where(FusionPrediction.status == PredictionStatus.LOST)
    )
    settled = (won or 0) + (lost or 0)
    hit_rate = (won / settled * 100) if settled > 0 else 0.0

    return {
        "total_sources": total_sources or 0,
        "active_sources": active_sources or 0,
        "total_tips_scraped": total_tips or 0,
        "total_predictions": total_predictions or 0,
        "won": won or 0,
        "lost": lost or 0,
        "hit_rate": round(hit_rate, 1),
    }
