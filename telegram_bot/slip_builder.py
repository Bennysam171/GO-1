"""
TipFusion AI — Dynamic Slip Builder
Generates as many slips as the data allows.

Slip types (applied in order, games can repeat across slips):
  🎯 SURE BANKER     — top 3–5 by confidence (safest)
  🟢 LOW RISK        — confidence ≥ 70%, odds 1.10–2.50, up to 8 games
  🟡 MEDIUM RISK     — confidence 55–69%, odds 1.50–4.00, up to 10 games
  🔴 HIGH RISK       — confidence 45–54%, any odds, up to 15 games
  💎 VALUE BANKER    — odds ≥ 1.80, top 5 by odds × confidence
  ⚡ MEGA ACCA       — all available games combined (max 20)
  🔥 OVER/UNDER ONLY — only Over/Under tips
  ⚽ 1X2 ONLY        — only Home/Draw/Away tips
  ✅ BTTS ONLY       — only BTTS Yes tips
  📦 MIXED SPECIAL   — 1 from each market type

Minimum 3 games required per slip. If fewer, slip is skipped.
"""

from datetime import datetime
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass, field

from core.logger import logger


MARKET_EMOJI = {
    "1": "🏠", "X": "🤝", "2": "✈️",
    "over_2.5": "⚽", "under_2.5": "🛑",
    "over_3.5": "🔥", "under_3.5": "🧊",
    "btts_yes": "✅", "btts_no": "❌",
    "1X": "🔰", "X2": "🔰", "12": "🔰",
}


@dataclass
class SlipGame:
    home_team:  str
    away_team:  str
    league:     str
    kickoff:    Optional[datetime]
    prediction: str
    market:     str
    confidence: float
    odds:       float


@dataclass
class BettingSlip:
    name:        str
    emoji:       str
    description: str
    games:       List[SlipGame] = field(default_factory=list)
    min_games:   int = 3

    @property
    def combined_odds(self) -> float:
        r = 1.0
        for g in self.games:
            if g.odds > 0:
                r *= g.odds
        return round(r, 2)

    @property
    def avg_confidence(self) -> float:
        if not self.games:
            return 0.0
        return round(sum(g.confidence for g in self.games) / len(self.games), 1)

    def projected_payout(self, stake: float = 1000.0) -> float:
        return round(stake * self.combined_odds, 2)

    def is_valid(self) -> bool:
        return len(self.games) >= self.min_games


# ── Odds extractor ────────────────────────────────────────────

def _get_odds(pred, match) -> float:
    odds = 0.0
    try:
        bd   = pred.market_breakdown or {}
        best = bd.get(pred.market, {}).get("best", 0)
        avg  = bd.get(pred.market, {}).get("avg_odds", 0)
        odds = best if best and best > 0 else avg
    except Exception:
        pass
    if not odds or odds < 1.01:
        conf = pred.confidence_score / 100.0
        if conf > 0.05:
            odds = round(1.0 / conf, 2)
    return round(max(odds, 1.01), 2)


def _to_slip_game(pred, match) -> SlipGame:
    return SlipGame(
        home_team=match.home_team,
        away_team=match.away_team,
        league=match.league or "",
        kickoff=match.kickoff_time,
        prediction=pred.prediction,
        market=pred.market,
        confidence=pred.confidence_score,
        odds=_get_odds(pred, match),
    )


# ── Slip builder ──────────────────────────────────────────────

class SlipBuilder:

    def build_slips(self, predictions: List[Tuple]) -> Dict[str, BettingSlip]:
        """
        Build as many slips as the data allows.
        Returns dict of slip_key → BettingSlip (only valid ones).
        """
        all_slips: Dict[str, BettingSlip] = {}

        # Convert to SlipGame list once
        games = [_to_slip_game(p, m) for p, m in predictions]
        if not games:
            return {}

        # Sort helpers
        by_conf     = sorted(games, key=lambda g: g.confidence,          reverse=True)
        by_odds_d   = sorted(games, key=lambda g: g.odds,               reverse=True)
        by_value    = sorted(games, key=lambda g: g.confidence * g.odds, reverse=True)
        by_odds_a   = sorted(games, key=lambda g: g.odds)

        # ── 1. SURE BANKER ──────────────────────────────────
        sure = [g for g in by_conf if g.confidence >= 75.0][:5]
        all_slips["sure_banker"] = BettingSlip(
            "SURE BANKER", "🎯",
            "Top picks by confidence — safest bets",
            sure, min_games=3
        )

        # ── 2. LOW RISK ──────────────────────────────────────
        low = [g for g in by_conf
               if g.confidence >= 70.0 and 1.10 <= g.odds <= 2.50][:8]
        all_slips["low_risk"] = BettingSlip(
            "LOW RISK", "🟢",
            "High confidence, safe odds",
            low, min_games=3
        )

        # ── 3. MEDIUM RISK ───────────────────────────────────
        med = [g for g in by_conf
               if 55.0 <= g.confidence < 70.0 and 1.50 <= g.odds <= 4.00][:10]
        all_slips["medium_risk"] = BettingSlip(
            "MEDIUM RISK", "🟡",
            "Balanced confidence and odds",
            med, min_games=3
        )

        # ── 4. HIGH RISK ─────────────────────────────────────
        high = [g for g in by_conf
                if 45.0 <= g.confidence < 55.0][:15]
        all_slips["high_risk"] = BettingSlip(
            "HIGH RISK", "🔴",
            "Lower confidence, higher reward",
            high, min_games=3
        )

        # ── 5. VALUE BANKER (high odds × confidence) ─────────
        value = [g for g in by_value if g.odds >= 1.80][:5]
        all_slips["value_banker"] = BettingSlip(
            "VALUE BANKER", "💎",
            "Best odds × confidence score",
            value, min_games=3
        )

        # ── 6. MEGA ACCA (all games) ──────────────────────────
        mega = by_conf[:20]
        all_slips["mega_acca"] = BettingSlip(
            "MEGA ACCA", "⚡",
            f"All {len(mega)} predictions combined",
            mega, min_games=5
        )

        # ── 7. OVER/UNDER ONLY ───────────────────────────────
        ou = [g for g in by_conf
              if g.market in ("over_2.5","under_2.5","over_3.5","under_3.5")][:12]
        all_slips["over_under"] = BettingSlip(
            "OVER/UNDER ONLY", "📊",
            "Only Over/Under goals markets",
            ou, min_games=3
        )

        # ── 8. 1X2 ONLY ──────────────────────────────────────
        x12 = [g for g in by_conf if g.market in ("1","X","2")][:10]
        all_slips["1x2_only"] = BettingSlip(
            "1X2 ONLY", "🏆",
            "Home/Draw/Away predictions only",
            x12, min_games=3
        )

        # ── 9. BTTS ONLY ─────────────────────────────────────
        btts = [g for g in by_conf if g.market == "btts_yes"][:10]
        all_slips["btts_only"] = BettingSlip(
            "BTTS ONLY", "✅",
            "Both Teams to Score predictions",
            btts, min_games=3
        )

        # ── 10. DOUBLE CHANCE ────────────────────────────────
        dc = [g for g in by_conf
              if g.market in ("1X","X2","12")][:8]
        all_slips["double_chance"] = BettingSlip(
            "DOUBLE CHANCE", "🔰",
            "Double chance market bets only",
            dc, min_games=3
        )

        # ── 11. SAFE ODDS (all odds 1.10–1.50) ───────────────
        safe_odds = [g for g in by_odds_a if 1.10 <= g.odds <= 1.50][:8]
        all_slips["safe_odds"] = BettingSlip(
            "SAFE ODDS", "🛡️",
            "Very low odds — near certainties",
            safe_odds, min_games=3
        )

        # ── 12. HIGH ODDS ACCA ────────────────────────────────
        big_odds = [g for g in by_odds_d if g.odds >= 2.50][:8]
        all_slips["high_odds"] = BettingSlip(
            "HIGH ODDS ACCA", "🚀",
            "Big odds accumulator — dream payout",
            big_odds, min_games=3
        )

        # Dynamic extra slips based on game count
        total_games = len(games)

        if total_games >= 30:
            # ── 13. TOP 10 ────────────────────────────────────
            top10 = by_conf[:10]
            all_slips["top10"] = BettingSlip(
                "TOP 10", "🌟",
                "Top 10 predictions by confidence",
                top10, min_games=5
            )

        if total_games >= 50:
            # ── 14. TOP 15 ────────────────────────────────────
            top15 = by_conf[:15]
            all_slips["top15"] = BettingSlip(
                "TOP 15", "💫",
                "Top 15 predictions — mega slip",
                top15, min_games=8
            )

        if total_games >= 20:
            # ── Mixed: 1 of each market ───────────────────────
            mixed = []
            used_markets = set()
            for g in by_value:
                if g.market not in used_markets and len(mixed) < 6:
                    mixed.append(g)
                    used_markets.add(g.market)
            all_slips["mixed_special"] = BettingSlip(
                "MIXED SPECIAL", "🎲",
                "One pick from each market type",
                mixed, min_games=3
            )

        # Filter to only valid slips
        valid = {k: v for k, v in all_slips.items() if v.is_valid()}

        # Log summary
        logger.info(f"[SlipBuilder] Built {len(valid)}/{len(all_slips)} valid slips "
                    f"from {total_games} games")
        for k, slip in valid.items():
            logger.debug(f"  {slip.emoji} {slip.name}: {len(slip.games)} games, "
                        f"odds {slip.combined_odds:.2f}")

        return valid


# ── Formatters ────────────────────────────────────────────────

def format_slip(slip: BettingSlip, stake: float = 1000.0, index: int = 1, total: int = 1) -> str:
    cfg      = slip.config if hasattr(slip, 'config') else slip
    payout   = slip.projected_payout(stake)
    now_str  = datetime.utcnow().strftime("%d %b %Y")

    lines = [
        f"{'═' * 30}",
        f"{slip.emoji} *{slip.name}* `[{index}/{total}]`",
        f"{'═' * 30}",
        f"📅 {now_str}",
        f"🎯 *{len(slip.games)} Games*  |  Avg Conf: *{slip.avg_confidence}%*",
        f"💹 Combined Odds: *{slip.combined_odds:.2f}x*",
        f"💰 ₦{stake:,.0f} → *₦{payout:,.0f}* potential",
        f"_{slip.description}_",
        f"{'─' * 30}",
    ]

    for i, game in enumerate(slip.games, 1):
        kickoff_str = game.kickoff.strftime("%d %b %H:%M") if game.kickoff else "TBD"
        league_str  = f"  └ {game.league}" if game.league else ""
        mkt_emoji   = MARKET_EMOJI.get(game.market, "📊")
        odds_str    = f"@ *{game.odds:.2f}*" if game.odds > 1.01 else ""

        lines.append(
            f"*{i}.* `{game.home_team}` vs `{game.away_team}`\n"
            f"{league_str}\n"
            f"   {mkt_emoji} *{game.prediction}* {odds_str}\n"
            f"   📊 {game.confidence:.0f}% conf  🗓 {kickoff_str}"
        )
        if i < len(slip.games):
            lines.append("┄" * 18)

    lines += [
        f"{'─' * 30}",
        f"💡 _Tap team name to copy_",
        f"⚠️ _Bet responsibly. AI-generated tips._",
        f"#TipFusion #{slip.name.replace(' ','').replace('/', '')}",
    ]

    return "\n".join(lines)


def format_slip_summary(slips: Dict[str, BettingSlip]) -> str:
    now_str = datetime.utcnow().strftime("%d %b %Y • %H:%M UTC")
    lines   = [
        f"🎰 *TipFusion AI — {len(slips)} Slips Available*",
        f"🗓 {now_str}",
        f"{'─' * 28}",
    ]
    for slip in slips.values():
        lines.append(
            f"{slip.emoji} *{slip.name}*: "
            f"{len(slip.games)} games | odds *{slip.combined_odds:.2f}x*"
        )
    lines += [
        f"{'─' * 28}",
        f"_Use /slips to see all | /lowrisk /highrisk for quick access_"
    ]
    return "\n".join(lines)
