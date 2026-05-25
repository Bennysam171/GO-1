"""
TipFusion AI — Game Analysis & Consensus Engine
The brain of TipFusion. Merges tips from all sources, applies
weighted scoring, detects consensus, and generates final predictions.
"""

from datetime import datetime
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from dataclasses import dataclass, field

from database.models import MarketType, RiskLevel
from scrapers.base import RawTip
from core.logger import logger
from core.config import settings


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class MatchKey:
    """Normalized match identifier for grouping tips."""
    home_team: str
    away_team: str
    kickoff: Optional[datetime] = None

    def __hash__(self):
        return hash((self.home_team.lower(), self.away_team.lower()))

    def __eq__(self, other):
        return (self.home_team.lower() == other.home_team.lower() and
                self.away_team.lower() == other.away_team.lower())


@dataclass
class MarketScore:
    """Score for a single market (e.g., Over 2.5)."""
    market: str
    prediction: str
    weighted_score: float = 0.0
    source_votes: int = 0
    total_sources: int = 0
    avg_confidence: float = 0.0
    avg_odds: float = 0.0
    sources: List[str] = field(default_factory=list)

    @property
    def consensus_ratio(self) -> float:
        return self.source_votes / self.total_sources if self.total_sources > 0 else 0.0


@dataclass
class FusionResult:
    """Final merged prediction for a match."""
    home_team: str
    away_team: str
    kickoff: Optional[datetime]
    league: Optional[str]

    best_market: str
    best_prediction: str
    confidence_score: float
    risk_level: str
    source_count: int
    consensus_ratio: float
    market_breakdown: Dict
    source_breakdown: Dict


# ============================================================
# TEAM NAME NORMALIZER
# ============================================================

TEAM_ALIASES = {
    "man utd": "manchester united",
    "man united": "manchester united",
    "man city": "manchester city",
    "tottenham": "tottenham hotspur",
    "spurs": "tottenham hotspur",
    "chelsea fc": "chelsea",
    "arsenal fc": "arsenal",
    "real madrid cf": "real madrid",
    "atletico madrid": "atletico de madrid",
    "fc barcelona": "barcelona",
    "barca": "barcelona",
    "psg": "paris saint-germain",
    "paris sg": "paris saint-germain",
    "inter milan": "internazionale",
    "inter": "internazionale",
    "ac milan": "milan",
    "ajax": "ajax amsterdam",
}


def normalize_team_name(name: str) -> str:
    """Normalize team name for matching across sources."""
    n = name.lower().strip()
    n = n.replace("f.c.", "fc").replace("f.c", "fc")
    n = n.replace("a.f.c.", "afc").replace("a.f.c", "afc")
    return TEAM_ALIASES.get(n, n)


def teams_match(a: str, b: str, threshold: float = 0.75) -> bool:
    """Check if two team names refer to the same team."""
    na = normalize_team_name(a)
    nb = normalize_team_name(b)

    if na == nb:
        return True

    # Check if one is a substring of the other (e.g. "Arsenal" vs "Arsenal FC")
    if na in nb or nb in na:
        return True

    # Simple token overlap
    tokens_a = set(na.split())
    tokens_b = set(nb.split())
    if not tokens_a or not tokens_b:
        return False

    overlap = tokens_a & tokens_b
    score = len(overlap) / max(len(tokens_a), len(tokens_b))
    return score >= threshold


def match_tip_to_group(tip: RawTip, groups: Dict[MatchKey, List[RawTip]]) -> Optional[MatchKey]:
    """Find which existing match group this tip belongs to, if any."""
    for key in groups:
        if teams_match(tip.home_team, key.home_team) and teams_match(tip.away_team, key.away_team):
            return key
    return None


# ============================================================
# SCORING ENGINE
# ============================================================

MARKET_WEIGHTS = {
    # Markets with better edge get higher base weight
    "over_2.5": 1.15,
    "btts_yes": 1.10,
    "1X": 1.08,
    "X2": 1.08,
    "1": 1.0,
    "2": 1.0,
    "X": 0.85,          # Draws are harder to predict
    "under_2.5": 0.95,
    "btts_no": 0.90,
    "over_3.5": 1.05,
    "under_3.5": 0.95,
    "12": 1.05,
    "dnb_1": 1.00,
    "dnb_2": 1.00,
}

# ── V8 Source type base weights (from config.yaml v8.source_weights) ──────
# These are the floor weights per source category.
# Per-source reliability multipliers stack on top of these.
V8_SOURCE_TYPE_WEIGHTS: Dict[str, float] = {
    "api":            0.95,
    "TheOddsAPI":     0.95,
    "SportsGameOdds": 0.95,
    "API-Football":   0.95,
    "FootballData":   0.95,
    "flashscore":     0.75,
    "livescore":      0.70,
    "sofascore":      0.72,
    "Forebet":        0.73,
    "Predictz":       0.68,
    "SoccerVista":    0.65,
    "Windrawwin":     0.65,
    "reddit":         0.60,
    "twitter":        0.50,
    "telegram":       0.55,
    "manual":         0.80,
    "ocr_image":      0.30,
}

# V8 score blend weights (from config.yaml v8.score_weights)
V8_SCORE_WEIGHTS = {
    "confidence":         0.40,
    "source_reliability": 0.30,
    "value_score":        0.30,
}

# V8 sentiment keywords that boost/reduce tip score
V8_POSITIVE_KEYWORDS = {"banker", "safe", "strong", "confident", "lock", "sure", "value", "backing"}
V8_NEGATIVE_KEYWORDS = {"risky", "avoid", "trap", "skip", "unsure", "doubt"}


class AnalysisEngine:
    """
    Weighted consensus analysis engine.
    Aggregates tips from all sources and produces a FusionResult.
    """

    def __init__(self, source_weights: Optional[Dict[str, float]] = None):
        # source_weights: maps source_name → reliability weight from DB history
        self._source_weights = source_weights or {}

    def _get_source_weight(self, source_name: str) -> float:
        """
        Final weight = V8 type base weight × per-source DB reliability multiplier.
        Falls back to 0.70 if source not recognized.
        """
        # Try exact name match in V8 table first
        base = V8_SOURCE_TYPE_WEIGHTS.get(source_name)

        # If no exact match, check if any V8 key is a substring
        if base is None:
            for key, w in V8_SOURCE_TYPE_WEIGHTS.items():
                if key.lower() in source_name.lower():
                    base = w
                    break

        # Fallback by type prefix
        if base is None:
            name_lower = source_name.lower()
            if "telegram" in name_lower:
                base = V8_SOURCE_TYPE_WEIGHTS["telegram"]
            elif "twitter" in name_lower or "reddit" in name_lower:
                base = V8_SOURCE_TYPE_WEIGHTS["twitter"]
            elif "app:" in name_lower or "ocr" in name_lower:
                base = V8_SOURCE_TYPE_WEIGHTS["ocr_image"]
            else:
                base = 0.70  # Unknown web source

        # Multiply by per-source historical reliability from DB (default 1.0)
        db_multiplier = self._source_weights.get(source_name, 1.0)
        return base * db_multiplier

    def _group_tips_by_match(self, tips: List[RawTip]) -> Dict[MatchKey, List[RawTip]]:
        """Group tips by the same match, normalizing team name variations."""
        groups: Dict[MatchKey, List[RawTip]] = {}

        for tip in tips:
            key = match_tip_to_group(tip, groups)
            if key is None:
                key = MatchKey(
                    home_team=normalize_team_name(tip.home_team),
                    away_team=normalize_team_name(tip.away_team),
                    kickoff=tip.kickoff,
                )
                groups[key] = []
            groups[key].append(tip)

        return groups

    def _score_market(
        self,
        market: str,
        tips: List[RawTip],
        total_sources: int
    ) -> MarketScore:
        """
        V8 three-component weighted score per market:
          final = (confidence × 0.40) + (source_reliability × 0.30) + (value_score × 0.30)
        All normalised to 0–100 then blended.
        """
        score = MarketScore(
            market=market,
            prediction="",
            total_sources=total_sources,
        )

        votes = [t for t in tips if t.market == market]
        score.source_votes = len(votes)
        if not votes:
            return score

        # Prediction label from first vote
        score.prediction = votes[0].prediction

        market_w = MARKET_WEIGHTS.get(market, 1.0)
        odds_list = []
        component_confidence = []
        component_reliability = []
        component_value = []

        for tip in votes:
            src_name = tip.source_name or ""
            source_w = self._get_source_weight(src_name)

            # ── Component 1: Confidence (source's own confidence or 60% default) ──
            conf_pct = tip.confidence if tip.confidence else 60.0
            component_confidence.append(conf_pct)

            # ── Component 2: Source reliability (0–100 scale) ───────────────────
            reliability_pct = min(100.0, source_w * 100.0)
            component_reliability.append(reliability_pct)

            # ── Component 3: Value score (edge vs market odds) ──────────────────
            value_pct = 50.0  # Neutral default
            if tip.odds and tip.odds > 1.0:
                implied_prob = 1.0 / tip.odds
                # If source confidence > implied probability → value exists
                conf_prob = conf_pct / 100.0
                edge = (conf_prob - implied_prob) / implied_prob
                value_pct = min(100.0, max(0.0, 50.0 + edge * 100.0))
            component_value.append(value_pct)

            if tip.odds:
                odds_list.append(tip.odds)
            if src_name:
                score.sources.append(src_name)

        # Apply V8 sentiment modifier to any tips whose text hints at quality
        sentiment_modifier = 1.0
        for tip in votes:
            extra_text = str(tip.extra).lower() if tip.extra else ""
            pred_text = (tip.prediction or "").lower()
            combined = extra_text + " " + pred_text
            if any(kw in combined for kw in V8_POSITIVE_KEYWORDS):
                sentiment_modifier = min(1.15, sentiment_modifier + 0.05)
            if any(kw in combined for kw in V8_NEGATIVE_KEYWORDS):
                sentiment_modifier = max(0.80, sentiment_modifier - 0.08)

        # Average each component
        avg_conf = sum(component_confidence) / len(component_confidence)
        avg_rel  = sum(component_reliability) / len(component_reliability)
        avg_val  = sum(component_value) / len(component_value)

        # V8 blend: confidence(40%) + reliability(30%) + value(30%)
        blended = (
            avg_conf * V8_SCORE_WEIGHTS["confidence"] +
            avg_rel  * V8_SCORE_WEIGHTS["source_reliability"] +
            avg_val  * V8_SCORE_WEIGHTS["value_score"]
        )

        # Consensus ratio bonus — more sources agreeing → higher final score
        consensus_ratio = score.source_votes / total_sources
        consensus_bonus = consensus_ratio * 15.0  # Up to +15 points for full consensus

        # Market weight multiplier
        raw_score = (blended + consensus_bonus) * market_w * sentiment_modifier

        score.weighted_score = min(98.0, max(0.0, raw_score))
        score.avg_confidence = avg_conf
        if odds_list:
            score.avg_odds = sum(odds_list) / len(odds_list)

        return score

    def _compute_risk(self, confidence: float, sources: int) -> str:
        """Determine risk level from confidence and source count."""
        if confidence >= settings.high_value_threshold and sources >= 3:
            return RiskLevel.LOW
        elif confidence >= settings.min_confidence_score and sources >= 2:
            return RiskLevel.MEDIUM
        else:
            return RiskLevel.HIGH

    def analyze_match(self, key: MatchKey, tips: List[RawTip]) -> Optional[FusionResult]:
        """
        Analyze all tips for a single match.
        Returns FusionResult if confidence exceeds threshold.
        """
        if not tips:
            return None

        # Count unique sources
        source_names = list(set(t.source_name for t in tips if t.source_name))
        total_sources = len(source_names)

        if total_sources < settings.min_sources_required:
            return None

        # Score each market present in tips
        markets_present = set(t.market for t in tips)
        market_scores: Dict[str, MarketScore] = {}

        for market in markets_present:
            ms = self._score_market(market, tips, total_sources)
            if ms.source_votes > 0:
                market_scores[market] = ms

        if not market_scores:
            return None

        # Pick the best market by weighted score
        best_ms = max(market_scores.values(), key=lambda x: x.weighted_score)

        # Final confidence: blend of consensus ratio and weighted confidence
        final_confidence = (
            best_ms.consensus_ratio * 40 +     # 40% from consensus
            best_ms.avg_confidence * 0.60       # 60% from weighted confidence
        )
        final_confidence = round(min(98, max(0, final_confidence)), 1)

        if final_confidence < settings.min_confidence_score:
            return None

        risk = self._compute_risk(final_confidence, best_ms.source_votes)

        # Build breakdown dicts for DB storage
        market_breakdown = {
            m: {
                "score": ms.weighted_score,
                "votes": ms.source_votes,
                "consensus": ms.consensus_ratio,
                "avg_confidence": ms.avg_confidence,
                "avg_odds": ms.avg_odds,
            }
            for m, ms in market_scores.items()
        }

        source_breakdown = {
            name: len([t for t in tips if t.source_name == name])
            for name in source_names
        }

        # Determine league and kickoff from tips
        leagues = [t.league for t in tips if t.league]
        league = leagues[0] if leagues else None
        kickoffs = [t.kickoff for t in tips if t.kickoff]
        kickoff = kickoffs[0] if kickoffs else key.kickoff

        return FusionResult(
            home_team=key.home_team,
            away_team=key.away_team,
            kickoff=kickoff,
            league=league,
            best_market=best_ms.market,
            best_prediction=best_ms.prediction,
            confidence_score=final_confidence,
            risk_level=risk.value if hasattr(risk, "value") else str(risk),
            source_count=total_sources,
            consensus_ratio=best_ms.consensus_ratio,
            market_breakdown=market_breakdown,
            source_breakdown=source_breakdown,
        )

    def analyze_all(self, tips: List[RawTip]) -> List[FusionResult]:
        """
        Analyze all tips and return high-confidence FusionResults.
        Entry point called by the orchestrator.
        """
        logger.info(f"🧠 [Analysis] Processing {len(tips)} tips...")

        groups = self._group_tips_by_match(tips)
        logger.debug(f"[Analysis] Grouped into {len(groups)} unique matches")

        results = []
        for key, match_tips in groups.items():
            result = self.analyze_match(key, match_tips)
            if result:
                results.append(result)

        # Sort by confidence descending
        results.sort(key=lambda r: r.confidence_score, reverse=True)

        logger.success(
            f"✅ [Analysis] {len(results)}/{len(groups)} matches passed threshold "
            f"(min={settings.min_confidence_score}%)"
        )
        return results
