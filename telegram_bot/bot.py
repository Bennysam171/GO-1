"""
TipFusion AI — Telegram Bot
- Posts all individual football predictions
- Builds and posts LOW RISK + HIGH RISK accumulator slips
- Commands: /today /top /value /lowrisk /highrisk /slips /stats
"""

import asyncio
from datetime import datetime, date
from typing import List, Optional, Dict, Any, Set, Tuple

from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TimedOut, NetworkError

from core.config import settings
from core.logger import logger
from database.connection import AsyncSessionLocal
from database import crud
from database.models import FusionPrediction, Match
from telegram_bot.slip_builder import SlipBuilder, BettingSlip, format_slip, format_slip_summary

_BOT_TOKEN  = "8529703370:AAGj-ccRad5A0DnPrSe_XZ6VgDqhgf60Khg"
_CHANNEL_ID = "-1003954198931"

DELAY_BETWEEN_POSTS = 0.5

EXCLUDED_LEAGUES = {
    "nba", "basketball", "baseball", "nfl", "nhl", "mlb",
    "wnba", "ncaa", "abl", "nbl", "nba g league", "cricket",
}

_posted_today: Set[int] = set()
_posted_date: Optional[date] = None

# Cache today's slips so /lowrisk and /highrisk work instantly
_cached_low_slip:  Optional[BettingSlip] = None
_cached_high_slip: Optional[BettingSlip] = None

RISK_EMOJI   = {"low": "🟢", "medium": "🟡", "high": "🔴"}
MARKET_EMOJI = {
    "1": "🏠", "X": "🤝", "2": "✈️",
    "over_2.5": "⚽", "under_2.5": "🛑",
    "over_3.5": "🔥", "under_3.5": "🧊",
    "btts_yes": "✅", "btts_no": "❌",
    "1X": "🔰", "X2": "🔰", "12": "🔰",
}


def _reset_daily_tracker():
    global _posted_today, _posted_date
    today = date.today()
    if _posted_date != today:
        _posted_today = set()
        _posted_date  = today


def _is_football(match: Match) -> bool:
    if not match:
        return False
    league = (match.league or "").lower()
    for excl in EXCLUDED_LEAGUES:
        if excl in league:
            return False
    return True


def format_prediction_card(prediction, match, position=1, total=1) -> str:
    kickoff_str = match.kickoff_time.strftime("%a %d %b • %H:%M UTC") \
                  if match.kickoff_time else "TBD"
    league_str  = f"🏆 {match.league}\n" if match.league else ""
    risk_emoji  = RISK_EMOJI.get(prediction.risk_level, "⚪")
    mkt_emoji   = MARKET_EMOJI.get(prediction.market, "📊")
    pct         = int(prediction.confidence_score)
    bar         = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))

    odds_str = ""
    try:
        avg_odds = (prediction.market_breakdown or {}).get(
            prediction.market, {}).get("avg_odds", 0)
        if avg_odds and avg_odds > 1.0:
            odds_str = f"💰 Avg Odds: *{avg_odds:.2f}*\n"
    except Exception:
        pass

    # Tip: tap any `code text` in Telegram to copy it instantly
    home_copy = f"`{match.home_team}`"
    away_copy = f"`{match.away_team}`"

    return (
        f"{'─' * 28}\n"
        f"⚽ *TipFusion AI* `[{position}/{total}]`\n"
        f"{'─' * 28}\n"
        f"{league_str}"
        f"🗓 {kickoff_str}\n\n"
        f"🏟 {home_copy} vs {away_copy}\n\n"
        f"{mkt_emoji} Pick: *{prediction.prediction}*\n"
        f"{odds_str}"
        f"📊 Confidence: `{bar}` *{pct}%*\n"
        f"{risk_emoji} Risk: *{prediction.risk_level.capitalize()}*\n"
        f"{'─' * 28}\n"
        f"💡 _Tap team name to copy_\n"
        f"#TipFusion #Football"
    )


def format_stats_card(stats: Dict[str, Any]) -> str:
    return (
        f"📈 *TipFusion AI — Stats*\n"
        f"{'─' * 28}\n"
        f"📡 Active Sources: *{stats['active_sources']}*\n"
        f"🔍 Tips Scraped: *{stats['total_tips_scraped']:,}*\n"
        f"🎯 Predictions: *{stats['total_predictions']:,}*\n"
        f"✅ Won: *{stats['won']}*  ❌ Lost: *{stats['lost']}*\n"
        f"📊 Hit Rate: *{stats['hit_rate']}%*\n"
        f"{'─' * 28}"
    )


class TipFusionBot:
    def __init__(self):
        self._token      = settings.telegram_bot_token or _BOT_TOKEN
        self._channel_id = settings.telegram_channel_id or _CHANNEL_ID
        self._app: Optional[Application] = None
        self._bot: Optional[Bot] = None
        self._sending    = False
        self._slip_builder = SlipBuilder()

    async def init(self):
        if not self._token:
            logger.warning("[TelegramBot] No token")
            return
        self._app = Application.builder().token(self._token).build()
        self._bot = self._app.bot
        self._app.add_handler(CommandHandler("start",    self.cmd_start))
        self._app.add_handler(CommandHandler("today",    self.cmd_today))
        self._app.add_handler(CommandHandler("top",      self.cmd_top))
        self._app.add_handler(CommandHandler("value",    self.cmd_value))
        self._app.add_handler(CommandHandler("stats",    self.cmd_stats))
        self._app.add_handler(CommandHandler("slips",    self.cmd_slips))
        self._app.add_handler(CommandHandler("lowrisk",  self.cmd_lowrisk))
        self._app.add_handler(CommandHandler("highrisk", self.cmd_highrisk))
        self._app.add_handler(CommandHandler("results",  self.cmd_results))
        self._app.add_handler(CommandHandler("allslips", self.cmd_allslips))
        logger.info(f"✅ [TelegramBot] Ready — channel {self._channel_id}")

    async def send_message(self, text: str, chat_id: str = None,
                           parse_mode: str = ParseMode.MARKDOWN) -> bool:
        target = chat_id or self._channel_id
        if not target or not self._bot:
            return False

        while True:
            try:
                await self._bot.send_message(
                    chat_id=target, text=text,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )
                return True
            except RetryAfter as e:
                wait = e.retry_after + 1
                logger.warning(f"[TelegramBot] Flood — waiting {wait}s")
                await asyncio.sleep(wait)
            except (TimedOut, NetworkError):
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"[TelegramBot] Send error: {e}")
                return False

    async def _load_football_preds(self) -> List[Tuple[FusionPrediction, Match]]:
        """Load all football predictions from DB."""
        from sqlalchemy import select
        results = []
        seen    = set()
        async with AsyncSessionLocal() as db:
            preds = await crud.get_high_confidence_predictions(
                db, min_score=45.0, unposted_only=False)
            for pred in preds:
                if pred.match_id in seen:
                    continue
                result = await db.execute(
                    select(Match).where(Match.id == pred.match_id))
                match = result.scalar_one_or_none()
                if match and _is_football(match):
                    results.append((pred, match))
                    seen.add(pred.match_id)
        return results

    async def broadcast_predictions(self, predictions: List[FusionPrediction]):
        """
        Full broadcast cycle:
        1. Post all individual football predictions
        2. Build and post LOW RISK slip
        3. Build and post HIGH RISK slip
        """
        global _cached_low_slip, _cached_high_slip

        if not predictions or self._sending:
            return

        _reset_daily_tracker()
        self._sending = True

        try:
            from sqlalchemy import select

            # ── Collect new football predictions ─────────────────
            football_preds: List[Tuple[FusionPrediction, Match]] = []
            async with AsyncSessionLocal() as db:
                for pred in sorted(predictions,
                                   key=lambda p: p.confidence_score, reverse=True):
                    if pred.match_id in _posted_today:
                        continue
                    result = await db.execute(
                        select(Match).where(Match.id == pred.match_id))
                    match = result.scalar_one_or_none()
                    if not match or not _is_football(match):
                        continue
                    if pred.confidence_score < 45.0:
                        continue
                    football_preds.append((pred, match))

            if not football_preds:
                logger.info("[TelegramBot] No new football predictions")
                return

            total = len(football_preds)
            logger.info(f"📤 [TelegramBot] Broadcasting {total} predictions + slips...")

            # ── 1. Header ─────────────────────────────────────────
            now_str = datetime.utcnow().strftime("%d %b %Y • %H:%M UTC")
            await self.send_message(
                f"🤖 *TipFusion AI — {total} New Football Tips*\n"
                f"🕐 {now_str}\n"
                f"{'─' * 28}"
            )
            await asyncio.sleep(DELAY_BETWEEN_POSTS)

            # ── 2. Individual predictions ─────────────────────────
            async with AsyncSessionLocal() as db:
                for i, (pred, match) in enumerate(football_preds, 1):
                    text = format_prediction_card(pred, match, i, total)
                    ok   = await self.send_message(text)
                    if ok:
                        _posted_today.add(pred.match_id)
                        await crud.mark_prediction_posted(db, pred.id)
                        from database.models import TelegramLog
                        db.add(TelegramLog(
                            fusion_prediction_id=pred.id,
                            chat_id=self._channel_id,
                            message_text=text,
                            success=True,
                        ))
                        logger.info(
                            f"[TelegramBot] ✅ {i}/{total}: "
                            f"{match.home_team} vs {match.away_team} "
                            f"({pred.confidence_score:.1f}%)"
                        )
                    await asyncio.sleep(DELAY_BETWEEN_POSTS)
                await db.commit()

            # ── 3. Build ALL dynamic slips ────────────────────────
            all_slips = self._slip_builder.build_slips(football_preds)
            n_slips   = len(all_slips)

            # Cache for quick commands
            global _cached_low_slip, _cached_high_slip
            _cached_low_slip  = all_slips.get("low_risk")
            _cached_high_slip = all_slips.get("high_risk")

            if all_slips:
                # ── 4. Post slip header ───────────────────────────
                await asyncio.sleep(1.0)
                await self.send_message(
                    f"{'═' * 30}\n"
                    f"🎰 *{n_slips} ACCUMULATOR SLIPS*\n"
                    f"{'═' * 30}"
                )
                await asyncio.sleep(DELAY_BETWEEN_POSTS)

                # ── 5. Post every valid slip ──────────────────────
                for i, (slip_key, slip) in enumerate(all_slips.items(), 1):
                    try:
                        text = format_slip(slip, stake=1000, index=i, total=n_slips)
                        await self.send_message(text)
                        logger.info(
                            f"[TelegramBot] Slip {i}/{n_slips}: "
                            f"{slip.emoji} {slip.name} "
                            f"({len(slip.games)} games, "
                            f"odds {slip.combined_odds:.2f}x)"
                        )
                        await asyncio.sleep(1.0)
                    except Exception as e:
                        logger.warning(f"[TelegramBot] Slip {slip_key} error: {e}")
            else:
                logger.info("[TelegramBot] No valid slips built")

            logger.success(
                f"✅ [TelegramBot] Broadcast done — "
                f"{total} tips + {n_slips} slips posted"
            )

        finally:
            self._sending = False

    # ── HELPER ───────────────────────────────────────────────

    async def _get_football_preds(self, min_score: float, limit: int = 50):
        from sqlalchemy import select
        results = []
        seen    = set()
        async with AsyncSessionLocal() as db:
            preds = await crud.get_high_confidence_predictions(
                db, min_score=min_score, unposted_only=False)
            for pred in preds:
                if pred.match_id in seen:
                    continue
                result = await db.execute(
                    select(Match).where(Match.id == pred.match_id))
                match = result.scalar_one_or_none()
                if match and _is_football(match):
                    results.append((pred, match))
                    seen.add(pred.match_id)
                if len(results) >= limit:
                    break
        return results

    # ── COMMANDS ─────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 *TipFusion AI* — Football Predictions\n\n"
            "*Individual Tips:*\n"
            "/today    — All today's football picks\n"
            "/top      — Top 5 highest confidence\n"
            "/value    — Best value bets\n\n"
            "*Accumulator Slips:*\n"
            "/slips    — Summary of today's slips\n"
            "/lowrisk  — 🟢 Low risk slip (safe picks)\n"
            "/highrisk — 🔴 High risk slip (value picks)\n\n"
            "*Other:*\n"
            "/stats    — System performance",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_today(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        items = await self._get_football_preds(45.0, 50)
        if not items:
            await update.message.reply_text("No football predictions yet today ⏳")
            return
        await update.message.reply_text(
            f"📋 *{len(items)} football predictions today:*",
            parse_mode=ParseMode.MARKDOWN)
        for i, (pred, match) in enumerate(items, 1):
            await update.message.reply_text(
                format_prediction_card(pred, match, i, len(items)),
                parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.5)

    async def cmd_top(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        items = await self._get_football_preds(70.0, 10)
        if not items:
            await update.message.reply_text("No high-confidence picks right now.")
            return
        for i, (pred, match) in enumerate(items, 1):
            await update.message.reply_text(
                format_prediction_card(pred, match, i, len(items)),
                parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.5)

    async def cmd_value(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        items = await self._get_football_preds(45.0, 50)
        value = [(p, m) for p, m in items
                 if (p.market_breakdown or {}).get(p.market, {}).get("avg_odds", 0) >= 1.80]
        if not value:
            await update.message.reply_text("No value bets right now.")
            return
        for i, (pred, match) in enumerate(value, 1):
            await update.message.reply_text(
                format_prediction_card(pred, match, i, len(value)),
                parse_mode=ParseMode.MARKDOWN)
            await asyncio.sleep(0.5)

    async def cmd_slips(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show summary of both slips."""
        global _cached_low_slip, _cached_high_slip

        # Build fresh if not cached
        if not _cached_low_slip or not _cached_high_slip:
            items = await self._get_football_preds(45.0, 100)
            if not items:
                await update.message.reply_text("No predictions available for slips yet ⏳")
                return
            slips = self._slip_builder.build_slips(items)
            _cached_low_slip  = slips["low_risk"]
            _cached_high_slip = slips["high_risk"]

        await update.message.reply_text(
            format_slip_summary(_cached_low_slip, _cached_high_slip),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_lowrisk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show full LOW RISK accumulator slip."""
        global _cached_low_slip

        if not _cached_low_slip or not _cached_low_slip.games:
            items = await self._get_football_preds(45.0, 100)
            if not items:
                await update.message.reply_text("No predictions available yet ⏳")
                return
            slips = self._slip_builder.build_slips(items)
            _cached_low_slip = slips["low_risk"]

        if not _cached_low_slip.is_valid():
            await update.message.reply_text(
                f"🟢 *LOW RISK SLIP*\n\n"
                f"Only {len(_cached_low_slip.games)} high-confidence games found "
                f"(need at least 5). More games may be added later today.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(
            format_slip(_cached_low_slip, stake=1000),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_highrisk(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Show full HIGH RISK accumulator slip."""
        global _cached_high_slip

        if not _cached_high_slip or not _cached_high_slip.games:
            items = await self._get_football_preds(45.0, 100)
            if not items:
                await update.message.reply_text("No predictions available yet ⏳")
                return
            slips = self._slip_builder.build_slips(items)
            _cached_high_slip = slips["high_risk"]

        if not _cached_high_slip.is_valid():
            await update.message.reply_text(
                f"🔴 *HIGH RISK SLIP*\n\n"
                f"Only {len(_cached_high_slip.games)} value games found "
                f"(need at least 5). More games may be added later today.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        await update.message.reply_text(
            format_slip(_cached_high_slip, stake=1000),
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_allslips(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Post all available slips one by one."""
        items = await self._get_football_preds(45.0, 100)
        if not items:
            await update.message.reply_text("No predictions available yet ⏳")
            return
        all_slips = self._slip_builder.build_slips(items)
        if not all_slips:
            await update.message.reply_text("Not enough games for slips ⏳")
            return

        await update.message.reply_text(
            f"🎰 Sending all *{len(all_slips)} slips*...",
            parse_mode=ParseMode.MARKDOWN
        )
        for i, (key, slip) in enumerate(all_slips.items(), 1):
            try:
                await update.message.reply_text(
                    format_slip(slip, stake=1000, index=i, total=len(all_slips)),
                    parse_mode=ParseMode.MARKDOWN
                )
                await asyncio.sleep(0.8)
            except Exception as e:
                logger.warning(f"[TelegramBot] /allslips error {key}: {e}")

    async def cmd_results(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Check and show latest match results."""
        await update.message.reply_text(
            "🔄 Checking match results online... please wait",
            parse_mode=ParseMode.MARKDOWN
        )
        try:
            from core.result_checker import ResultChecker
            checker = ResultChecker()

            # Run check
            result = await checker.check_and_update()
            if isinstance(result, tuple):
                stats, results_list = result
            else:
                stats, results_list = result, []

            if stats["checked"] == 0:
                await update.message.reply_text(
                    "⏳ No finished matches found yet.\n"
                    "Results are checked automatically every 2 hours.\n"
                    "Games need at least 2 hours after kickoff to be settled."
                )
                return

            # Build results message
            total    = len(results_list)
            won_list = [(p,m,h,a) for p,m,won,h,a in results_list if won is True]
            lost_list= [(p,m,h,a) for p,m,won,h,a in results_list if won is False]
            void_list= [(p,m,h,a) for p,m,won,h,a in results_list if won is None]
            hit_rate = (len(won_list)/total*100) if total > 0 else 0

            msg = (
                f"📊 *TipFusion AI — Results*\n"
                f"{'─'*28}\n"
                f"✅ Won:  *{len(won_list)}*\n"
                f"❌ Lost: *{len(lost_list)}*\n"
                f"🔁 Void: *{len(void_list)}*\n"
                f"📈 Hit Rate: *{hit_rate:.1f}%*\n"
                f"{'─'*28}\n"
            )

            if won_list:
                msg += "✅ *WINNERS:*\n"
                for pred, match, h, a in won_list[:8]:
                    msg += f"  `{match.home_team}` *{h}–{a}* `{match.away_team}` ✓\n"
                if len(won_list) > 8:
                    msg += f"  _...+{len(won_list)-8} more wins_\n"
                msg += "\n"

            if lost_list:
                msg += "❌ *LOSSES:*\n"
                for pred, match, h, a in lost_list[:5]:
                    msg += f"  `{match.home_team}` *{h}–{a}* `{match.away_team}` ✗\n"
                if len(lost_list) > 5:
                    msg += f"  _...+{len(lost_list)-5} more losses_\n"

            msg += f"{'─'*28}\n#TipFusion #Results"

            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

        except Exception as e:
            logger.error(f"[TelegramBot] /results error: {e}")
            await update.message.reply_text(
                "❌ Could not fetch results right now. Try again in a few minutes."
            )

    async def cmd_stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        async with AsyncSessionLocal() as db:
            stats = await crud.get_overall_stats(db)
        await update.message.reply_text(
            format_stats_card(stats), parse_mode=ParseMode.MARKDOWN)

    async def run_polling(self):
        if not self._app:
            return
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self):
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
                await self._app.shutdown()
            except Exception:
                pass