"""
TipFusion AI — Test Suite
Tests for core components: analysis engine, normalizer, scrapers.
Run with: pytest tests/ -v
"""

import asyncio
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from scrapers.base import RawTip, normalize_prediction
from analyzer.engine import (
    AnalysisEngine, MatchKey, normalize_team_name,
    teams_match, match_tip_to_group
)
from core.config import settings


# ============================================================
# FIXTURES
# ============================================================

@pytest.fixture
def sample_tips():
    """Multi-source tips for Arsenal vs Chelsea."""
    kickoff = datetime(2025, 1, 15, 15, 0)
    return [
        RawTip("Arsenal", "Chelsea", "Over 2.5", "over_2.5",
               confidence=72, odds=1.85, league="Premier League",
               kickoff=kickoff, source_name="Forebet"),
        RawTip("Arsenal", "Chelsea", "Over 2.5", "over_2.5",
               confidence=68, odds=1.88, league="Premier League",
               kickoff=kickoff, source_name="API-Football"),
        RawTip("Arsenal FC", "Chelsea FC", "Over 2.5", "over_2.5",
               confidence=75, odds=1.80, league="EPL",
               kickoff=kickoff, source_name="TheOddsAPI"),
        RawTip("Arsenal", "Chelsea", "Home Win (Arsenal)", "1",
               confidence=60, odds=2.10,
               kickoff=kickoff, source_name="Reddit/r/soccerbetting"),
    ]


@pytest.fixture
def engine():
    return AnalysisEngine(source_weights={
        "Forebet": 1.2,
        "API-Football": 1.5,
        "TheOddsAPI": 1.3,
        "Reddit/r/soccerbetting": 0.8,
    })


# ============================================================
# NORMALIZER TESTS
# ============================================================

class TestNormalizePrediction:
    def test_home_win(self):
        assert normalize_prediction("home win") == "1"
        assert normalize_prediction("Home") == "1"
        assert normalize_prediction("1") == "1"

    def test_draw(self):
        assert normalize_prediction("draw") == "X"
        assert normalize_prediction("x") == "X"
        assert normalize_prediction("tie") == "X"

    def test_away_win(self):
        assert normalize_prediction("away win") == "2"
        assert normalize_prediction("away") == "2"

    def test_over_under(self):
        assert normalize_prediction("over 2.5") == "over_2.5"
        assert normalize_prediction("o2.5") == "over_2.5"
        assert normalize_prediction("under 2.5") == "under_2.5"
        assert normalize_prediction("u2.5") == "under_2.5"

    def test_btts(self):
        assert normalize_prediction("btts yes") == "btts_yes"
        assert normalize_prediction("gg") == "btts_yes"
        assert normalize_prediction("btts no") == "btts_no"
        assert normalize_prediction("ng") == "btts_no"

    def test_unknown(self):
        assert normalize_prediction("random garbage") is None
        assert normalize_prediction("") is None


# ============================================================
# TEAM NAME NORMALIZER TESTS
# ============================================================

class TestTeamNormalizer:
    def test_basic(self):
        assert normalize_team_name("Arsenal") == "arsenal"
        assert normalize_team_name("  Arsenal  ") == "arsenal"

    def test_alias(self):
        assert normalize_team_name("Man Utd") == "manchester united"
        assert normalize_team_name("man city") == "manchester city"
        assert normalize_team_name("Spurs") == "tottenham hotspur"
        assert normalize_team_name("Barca") == "barcelona"
        assert normalize_team_name("PSG") == "paris saint-germain"

    def test_fc_removal(self):
        assert normalize_team_name("Arsenal FC") == "arsenal fc"  # Not aliased specifically
        # Chelsea FC → chelsea after ALIASES check
        assert normalize_team_name("Chelsea FC") == "chelsea"

    def test_teams_match_exact(self):
        assert teams_match("Arsenal", "Arsenal") is True

    def test_teams_match_alias(self):
        assert teams_match("Man Utd", "Manchester United") is True
        assert teams_match("Tottenham", "Tottenham Hotspur") is True

    def test_teams_match_substring(self):
        assert teams_match("Arsenal", "Arsenal FC") is True

    def test_teams_no_match(self):
        assert teams_match("Arsenal", "Chelsea") is False
        assert teams_match("Liverpool", "Everton") is False


# ============================================================
# ANALYSIS ENGINE TESTS
# ============================================================

class TestAnalysisEngine:
    def test_group_tips_same_match(self, engine, sample_tips):
        groups = engine._group_tips_by_match(sample_tips)
        # All 4 tips should group into 1 match
        assert len(groups) == 1

    def test_group_tips_different_matches(self, engine):
        tips = [
            RawTip("Arsenal", "Chelsea", "Over 2.5", "over_2.5",
                   source_name="A", kickoff=datetime(2025, 1, 15, 15, 0)),
            RawTip("Liverpool", "Man City", "BTTS Yes", "btts_yes",
                   source_name="B", kickoff=datetime(2025, 1, 15, 17, 30)),
        ]
        groups = engine._group_tips_by_match(tips)
        assert len(groups) == 2

    def test_score_market(self, engine, sample_tips):
        groups = engine._group_tips_by_match(sample_tips)
        key = list(groups.keys())[0]
        match_tips = groups[key]

        ms = engine._score_market("over_2.5", match_tips, total_sources=3)

        assert ms.source_votes == 3
        assert ms.weighted_score > 0
        assert ms.avg_confidence > 60

    def test_analyze_match_high_confidence(self, engine, sample_tips):
        groups = engine._group_tips_by_match(sample_tips)
        key = list(groups.keys())[0]

        result = engine.analyze_match(key, groups[key])

        assert result is not None
        assert result.best_market == "over_2.5"
        assert result.confidence_score >= settings.min_confidence_score
        assert result.source_count >= 2

    def test_analyze_all(self, engine, sample_tips):
        results = engine.analyze_all(sample_tips)
        assert len(results) >= 1
        # Results sorted by confidence desc
        if len(results) > 1:
            for i in range(len(results) - 1):
                assert results[i].confidence_score >= results[i+1].confidence_score

    def test_insufficient_sources(self, engine):
        """Match with only 1 source should not produce a prediction."""
        tips = [
            RawTip("Arsenal", "Chelsea", "Over 2.5", "over_2.5",
                   confidence=80, source_name="SingleSource",
                   kickoff=datetime(2025, 1, 15, 15, 0)),
        ]
        results = engine.analyze_all(tips)
        assert len(results) == 0  # Needs min 2 sources

    def test_risk_level(self, engine):
        assert engine._compute_risk(80, 4).value == "low"
        assert engine._compute_risk(68, 2).value == "medium"
        assert engine._compute_risk(50, 1).value == "high"


# ============================================================
# RAW TIP TESTS
# ============================================================

class TestRawTip:
    def test_normalization(self):
        tip = RawTip("  arsenal  ", "  chelsea  ", "Over 2.5", "over_2.5")
        assert tip.home_team == "Arsenal"
        assert tip.away_team == "Chelsea"

    def test_defaults(self):
        tip = RawTip("A", "B", "Draw", "X")
        assert tip.confidence is None
        assert tip.odds is None
        assert tip.extra == {}


# ============================================================
# TELEGRAM MESSAGE FORMAT TESTS
# ============================================================

class TestTelegramFormatter:
    def test_format_prediction_card(self):
        from telegram_bot.bot import format_prediction_card
        from database.models import FusionPrediction, Match, MarketType, RiskLevel

        match = MagicMock()
        match.home_team = "Arsenal"
        match.away_team = "Chelsea"
        match.league = "Premier League"
        match.kickoff_time = datetime(2025, 1, 15, 15, 0)

        pred = MagicMock()
        pred.prediction = "Over 2.5"
        pred.market = "over_2.5"
        pred.confidence_score = 78.5
        pred.risk_level = "low"
        pred.source_count = 4
        pred.market_breakdown = {"over_2.5": {"avg_odds": 1.85}}

        card = format_prediction_card(pred, match, 1, 3)

        assert "Arsenal" in card
        assert "Chelsea" in card
        assert "Over 2.5" in card
        assert "78%" in card
        assert "low" in card.lower()

    def test_format_stats_card(self):
        from telegram_bot.bot import format_stats_card

        stats = {
            "active_sources": 12,
            "total_sources": 15,
            "total_tips_scraped": 5000,
            "total_predictions": 200,
            "won": 140,
            "lost": 40,
            "hit_rate": 77.8,
        }

        card = format_stats_card(stats)
        assert "77.8%" in card
        assert "12" in card


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    import subprocess
    subprocess.run(["pytest", __file__, "-v", "--tb=short"])
