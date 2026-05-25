"""
TipFusion AI — Database Models
SQLAlchemy 2.0 ORM definitions for all entities.
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import (
    Column, Integer, String, Float, Boolean,
    DateTime, Text, ForeignKey, Enum, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


# ============================================================
# ENUMS
# ============================================================

class SourceType(str, enum.Enum):
    API = "api"
    WEB = "web"
    TELEGRAM = "telegram"
    TWITTER = "twitter"
    REDDIT = "reddit"
    ANDROID_APP = "android_app"
    MANUAL = "manual"


class MarketType(str, enum.Enum):
    HOME_WIN = "1"
    DRAW = "X"
    AWAY_WIN = "2"
    OVER_25 = "over_2.5"
    UNDER_25 = "under_2.5"
    OVER_35 = "over_3.5"
    UNDER_35 = "under_3.5"
    BTTS_YES = "btts_yes"
    BTTS_NO = "btts_no"
    DOUBLE_CHANCE_1X = "1X"
    DOUBLE_CHANCE_X2 = "X2"
    DOUBLE_CHANCE_12 = "12"
    DRAW_NO_BET_1 = "dnb_1"
    DRAW_NO_BET_2 = "dnb_2"


class PredictionStatus(str, enum.Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    CANCELLED = "cancelled"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ============================================================
# SOURCES
# ============================================================

class Source(Base):
    """A data source (API, website, Telegram channel, etc.)"""
    __tablename__ = "sources"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    type = Column(Enum(SourceType), nullable=False)
    identifier = Column(String(500), nullable=False)  # URL, channel ID, etc.
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    auto_discovered = Column(Boolean, default=False)

    # Reliability tracking
    total_tips = Column(Integer, default=0)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    voids = Column(Integer, default=0)
    hit_rate = Column(Float, default=0.0)
    reliability_score = Column(Float, default=50.0)  # 0–100
    weight = Column(Float, default=1.0)              # Multiplier in scoring

    # Metadata
    last_scraped_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extra_data = Column(JSON, default=dict)

    tips = relationship("Tip", back_populates="source")
    scrape_logs = relationship("ScrapeLog", back_populates="source")

    __table_args__ = (
        UniqueConstraint("type", "identifier", name="uq_source_type_identifier"),
        Index("ix_source_type", "type"),
        Index("ix_source_active", "is_active"),
    )

    def __repr__(self):
        return f"<Source {self.name} [{self.type}] score={self.reliability_score:.1f}>"

    def update_hit_rate(self):
        settled = self.wins + self.losses
        self.hit_rate = (self.wins / settled * 100) if settled > 0 else 0.0

    def compute_reliability_score(self) -> float:
        """Weighted reliability — considers volume and hit rate."""
        settled = self.wins + self.losses
        if settled < 10:
            return 50.0  # Neutral until we have enough data
        volume_bonus = min(10, settled / 50)  # Up to 10 bonus for high volume
        score = self.hit_rate + volume_bonus
        self.reliability_score = min(100, max(0, score))
        return self.reliability_score


# ============================================================
# MATCHES
# ============================================================

class Match(Base):
    """A football match being tracked."""
    __tablename__ = "matches"

    id = Column(Integer, primary_key=True)
    external_id = Column(String(200), nullable=True)    # API match ID
    home_team = Column(String(200), nullable=False)
    away_team = Column(String(200), nullable=False)
    league = Column(String(200), nullable=True)
    country = Column(String(100), nullable=True)
    kickoff_time = Column(DateTime, nullable=False)
    status = Column(String(50), default="scheduled")    # scheduled/live/finished

    # Odds snapshot
    home_odds = Column(Float, nullable=True)
    draw_odds = Column(Float, nullable=True)
    away_odds = Column(Float, nullable=True)
    over25_odds = Column(Float, nullable=True)
    under25_odds = Column(Float, nullable=True)
    btts_yes_odds = Column(Float, nullable=True)
    btts_no_odds = Column(Float, nullable=True)

    # Result
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)
    result = Column(String(10), nullable=True)   # "1", "X", "2"

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extra_data = Column(JSON, default=dict)

    tips = relationship("Tip", back_populates="match")
    fusion_predictions = relationship("FusionPrediction", back_populates="match")

    __table_args__ = (
        UniqueConstraint("home_team", "away_team", "kickoff_time", name="uq_match"),
        Index("ix_match_kickoff", "kickoff_time"),
        Index("ix_match_status", "status"),
    )

    def __repr__(self):
        return f"<Match {self.home_team} vs {self.away_team} @ {self.kickoff_time}>"


# ============================================================
# TIPS
# ============================================================

class Tip(Base):
    """A raw tip from a single source."""
    __tablename__ = "tips"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=True)

    # Raw strings (before normalization)
    raw_home_team = Column(String(200), nullable=False)
    raw_away_team = Column(String(200), nullable=False)
    raw_league = Column(String(200), nullable=True)
    raw_kickoff = Column(String(100), nullable=True)

    # Prediction
    market = Column(Enum(MarketType), nullable=False)
    prediction = Column(String(50), nullable=False)    # e.g. "Over 2.5"
    confidence = Column(Float, nullable=True)          # Source's own confidence %
    odds = Column(Float, nullable=True)
    tipster_name = Column(String(200), nullable=True)

    # Outcome tracking
    status = Column(Enum(PredictionStatus), default=PredictionStatus.PENDING)
    is_correct = Column(Boolean, nullable=True)

    scraped_at = Column(DateTime, default=datetime.utcnow)
    extra_data = Column(JSON, default=dict)

    source = relationship("Source", back_populates="tips")
    match = relationship("Match", back_populates="tips")

    __table_args__ = (
        Index("ix_tip_source", "source_id"),
        Index("ix_tip_match", "match_id"),
        Index("ix_tip_status", "status"),
        Index("ix_tip_scraped", "scraped_at"),
    )

    def __repr__(self):
        return f"<Tip {self.raw_home_team} vs {self.raw_away_team} — {self.prediction}>"


# ============================================================
# FUSION PREDICTIONS (AI-merged output)
# ============================================================

class FusionPrediction(Base):
    """
    A consolidated prediction generated by TipFusion's analysis engine.
    Merges tips from multiple sources with weighted scoring.
    """
    __tablename__ = "fusion_predictions"

    id = Column(Integer, primary_key=True)
    match_id = Column(Integer, ForeignKey("matches.id"), nullable=False)

    # Best market and prediction chosen
    market = Column(Enum(MarketType), nullable=False)
    prediction = Column(String(50), nullable=False)

    # Scoring
    confidence_score = Column(Float, nullable=False)   # 0–100
    risk_level = Column(Enum(RiskLevel), nullable=False)
    source_count = Column(Integer, default=0)
    consensus_ratio = Column(Float, default=0.0)       # % sources agreeing

    # Breakdown (JSON for flexibility)
    market_breakdown = Column(JSON, default=dict)      # Per-market scores
    source_breakdown = Column(JSON, default=dict)      # Per-source votes

    # Delivery
    posted_to_telegram = Column(Boolean, default=False)
    posted_at = Column(DateTime, nullable=True)

    # Outcome
    status = Column(Enum(PredictionStatus), default=PredictionStatus.PENDING)

    created_at = Column(DateTime, default=datetime.utcnow)

    match = relationship("Match", back_populates="fusion_predictions")

    __table_args__ = (
        Index("ix_fusion_confidence", "confidence_score"),
        Index("ix_fusion_posted", "posted_to_telegram"),
        Index("ix_fusion_status", "status"),
    )

    def __repr__(self):
        return (
            f"<FusionPrediction match={self.match_id} "
            f"{self.prediction} conf={self.confidence_score:.1f}>"
        )


# ============================================================
# SCRAPE LOG
# ============================================================

class ScrapeLog(Base):
    """Audit log for every scraping run."""
    __tablename__ = "scrape_logs"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=True)
    source_name = Column(String(200), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    tips_found = Column(Integer, default=0)
    tips_new = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    source = relationship("Source", back_populates="scrape_logs")

    def __repr__(self):
        return f"<ScrapeLog {self.source_name} tips={self.tips_found} ok={self.success}>"


# ============================================================
# TELEGRAM LOG
# ============================================================

class TelegramLog(Base):
    """Record of every message posted to Telegram."""
    __tablename__ = "telegram_logs"

    id = Column(Integer, primary_key=True)
    fusion_prediction_id = Column(Integer, ForeignKey("fusion_predictions.id"), nullable=True)
    chat_id = Column(String(100), nullable=False)
    message_id = Column(Integer, nullable=True)
    message_text = Column(Text, nullable=False)
    posted_at = Column(DateTime, default=datetime.utcnow)
    success = Column(Boolean, default=True)
    error = Column(Text, nullable=True)
