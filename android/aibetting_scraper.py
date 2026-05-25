"""
TipFusion AI — AI Betting Tips App Scraper (com.blockdelve.aibettingtips)

App layout (from screenshots):
  Title: "AI Tips" — "AI-powered soccer predictions"
  Tabs:  Today [73]  |  Tomorrow [37]
  Cards: Country / League | Date | Time
         Home Team  VS  Away Team
         Prediction (Over 2.5 Goals) | Odds (1.44) | Confidence (79.0%)
  Bottom nav: Tips | Stats | Acca | Settings

Navigation:
  1. App opens directly on Tips screen — no home screen needed
  2. Scrape Today tab (scroll all the way down)
  3. Tap Tomorrow tab → scroll and scrape
  Done.

Screen: 720x1600 (BlueStacks default)
"""

import asyncio
import time
import re
from datetime import datetime
from typing import List, Optional

from core.logger import logger
from scrapers.base import RawTip

PACKAGE  = "com.blockdelve.aibettingtips"
APP_NAME = "AI Betting Tips"

# ── Coordinates ───────────────────────────────────────────────
# Screen: 720x1600 — tabs sit at ~y=201 in the app viewport.
# BlueStacks adds a top chrome (~36px), so effective tap y ≈ 237.
# Today tab centre  ≈ x=185,  Tomorrow tab centre ≈ x=355
TAP_TODAY    = (185, 237)
TAP_TOMORROW = (355, 237)   # was 500 — corrected to actual tab centre

# Scroll: swipe up = scroll content down
SCROLL_FROM  = (360, 1300)
SCROLL_TO    = (360, 400)
SCROLL_MS    = 500

# Prediction market mappings
MARKET_MAP = {
    "over 2.5 goals":  ("over_2.5",  "Over 2.5"),
    "under 2.5 goals": ("under_2.5", "Under 2.5"),
    "over 3.5 goals":  ("over_3.5",  "Over 3.5"),
    "under 3.5 goals": ("under_3.5", "Under 3.5"),
    "over 1.5 goals":  ("over_2.5",  "Over 1.5"),   # map to over_2.5 key
    "btts yes":        ("btts_yes",  "BTTS Yes"),
    "btts no":         ("btts_no",   "BTTS No"),
    "both teams":      ("btts_yes",  "BTTS Yes"),
    "home win":        ("1",         "Home Win"),
    "away win":        ("2",         "Away Win"),
    "draw":            ("X",         "Draw"),
    "1x":              ("1X",        "Double Chance 1X"),
    "x2":              ("X2",        "Double Chance X2"),
    "12":              ("12",        "Double Chance 12"),
}


def _normalize_market(raw: str):
    """Return (market_key, prediction_label) from raw prediction text."""
    r = raw.lower().strip()
    for key, (mk, label) in MARKET_MAP.items():
        if key in r:
            return mk, label
    return "1", raw.strip()   # Default: Home Win


class AIBettingScraper:
    """
    Scrapes AI Betting Tips app.
    Simple 2-step navigation: Today scroll → Tomorrow scroll.
    """

    def __init__(self, adb):
        self.adb = adb

    def _tap(self, xy: tuple, label: str = ""):
        self.adb.tap(xy[0], xy[1])
        if label:
            logger.debug(f"[AIBetting] Tap: {label}")
        time.sleep(0.7)

    def _scroll(self):
        self.adb.swipe(
            SCROLL_FROM[0], SCROLL_FROM[1],
            SCROLL_TO[0],   SCROLL_TO[1],
            SCROLL_MS
        )
        time.sleep(1.0)

    def _parse_screen(self, text: str, date_hint: str = "") -> List[dict]:
        """
        Parse UI dump text from AI Betting Tips.

        Format from screenshots:
          Country
          League
          May 16 | 3:45          (or just embedded in text)
          Home Team
          VS
          Away Team
          Over 2.5 Goals
          1.44
          79.0%

        The UIAutomator dump puts each text node on its own line.
        """
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        records = []
        i = 0

        while i < len(lines):
            line = lines[i]

            # Detect "VS" separator — the team line structure
            if line.upper() == "VS" and i >= 1 and i + 1 < len(lines):
                home = lines[i - 1].strip()
                away = lines[i + 1].strip()

                # Validate teams are real names (not UI labels)
                bad_words = {"vs", "today", "tomorrow", "tips", "stats",
                             "acca", "settings", "ai", "over", "under",
                             "btts", "goals", "%", "may", "jun"}
                if (len(home) < 3 or len(away) < 3 or
                        home.lower() in bad_words or away.lower() in bad_words):
                    i += 1
                    continue

                # Scan nearby lines for league, date, prediction, odds, confidence
                league    = ""
                kickoff   = None
                pred_text = ""
                odds_val  = None
                conf_val  = None

                # Look back for league and date
                for back in range(max(0, i - 6), i):
                    bl = lines[back].strip()
                    # Date: "May 16" or "May 16 | 3:45"
                    date_m = re.search(r"(May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|Jan|Feb|Mar|Apr)\s+\d{1,2}", bl, re.IGNORECASE)
                    if date_m:
                        time_m = re.search(r"(\d{1,2}:\d{2})", bl)
                        try:
                            month_day = date_m.group(0)
                            year      = datetime.utcnow().year
                            time_str  = time_m.group(1) if time_m else "00:00"
                            kickoff   = datetime.strptime(
                                f"{month_day} {year} {time_str}", "%b %d %Y %H:%M"
                            )
                        except Exception:
                            pass
                        continue
                    # Time-only line: "3:45"
                    if re.match(r"^\d{1,2}:\d{2}$", bl):
                        if kickoff is None and date_hint:
                            try:
                                kickoff = datetime.strptime(
                                    f"{date_hint} {bl}", "%Y-%m-%d %H:%M"
                                )
                            except Exception:
                                pass
                        continue
                    # Looks like a league name (not a team, not a date)
                    if len(bl) > 4 and not bl.upper() == bl and "%" not in bl:
                        league = bl

                # Look forward for prediction, odds, confidence
                for fwd in range(i + 2, min(i + 8, len(lines))):
                    fl = lines[fwd].strip()

                    # Confidence: "79.0%" or "79%"
                    conf_m = re.search(r"(\d{2,3}(?:\.\d)?)\s*%", fl)
                    if conf_m:
                        try:
                            conf_val = float(conf_m.group(1))
                        except Exception:
                            pass
                        continue

                    # Odds: "1.44" or "1,44"
                    odds_m = re.match(r"^(\d{1,2}[.,]\d{2})$", fl)
                    if odds_m:
                        try:
                            odds_val = float(odds_m.group(1).replace(",", "."))
                        except Exception:
                            pass
                        continue

                    # Prediction keywords
                    fl_lower = fl.lower()
                    for key in MARKET_MAP:
                        if key in fl_lower:
                            pred_text = fl
                            break
                    if not pred_text and re.search(r"\b(over|under|btts|goals|win|draw)\b", fl_lower):
                        pred_text = fl

                if not pred_text:
                    pred_text = "Over 2.5 Goals"  # Most common in app

                market_key, pred_label = _normalize_market(pred_text)

                # Add home/away context to label
                if market_key == "1":
                    pred_label = f"Home Win ({home})"
                elif market_key == "2":
                    pred_label = f"Away Win ({away})"

                records.append({
                    "home":     home,
                    "away":     away,
                    "league":   league,
                    "kickoff":  kickoff,
                    "market":   market_key,
                    "pred":     pred_label,
                    "odds":     odds_val,
                    "conf":     conf_val,
                })
                i += 2  # Skip past away team
                continue

            i += 1

        return records

    async def _scrape_tab(self, label: str, date_hint: str = "") -> List[RawTip]:
        """Scroll through one tab and collect all predictions."""
        tips = []
        seen = set()
        no_new_count = 0

        for scroll_step in range(20):  # Max 20 scrolls = ~100 predictions
            text    = self.adb.get_screen_text()
            records = self._parse_screen(text, date_hint)

            added = 0
            for r in records:
                key = (r["home"].lower(), r["away"].lower())
                if key in seen:
                    continue
                seen.add(key)
                tips.append(RawTip(
                    home_team=r["home"],
                    away_team=r["away"],
                    prediction=r["pred"],
                    market=r["market"],
                    odds=r["odds"],
                    confidence=r["conf"],
                    league=r["league"],
                    kickoff=r["kickoff"],
                    tipster="AI Betting Tips AI",
                    source_name=f"App:{APP_NAME}",
                ))
                added += 1

            logger.debug(f"[AIBetting] {label} scroll {scroll_step}: +{added} tips (total {len(tips)})")

            if added == 0:
                no_new_count += 1
                if no_new_count >= 2:
                    break  # 2 consecutive empty scrolls = end of list
            else:
                no_new_count = 0

            self._scroll()
            await asyncio.sleep(0.3)

        return tips

    async def scrape(self) -> List[RawTip]:
        """Main entry: scrape Today + Tomorrow."""
        all_tips = []

        today    = datetime.utcnow().strftime("%Y-%m-%d")
        from datetime import timedelta
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")

        # The app opens on Tips/Today by default — start scraping immediately
        logger.info(f"[AIBetting] Scraping Today...")
        self._tap(TAP_TODAY, "Today tab")
        time.sleep(1.5)
        today_tips = await self._scrape_tab("Today", today)
        all_tips.extend(today_tips)
        logger.info(f"[AIBetting] Today: {len(today_tips)} tips")

        # Tomorrow tab — tap and verify the tab actually switched
        logger.info(f"[AIBetting] Switching to Tomorrow tab...")
        for attempt in range(3):
            self._tap(TAP_TOMORROW, f"Tomorrow tab (attempt {attempt + 1})")
            time.sleep(2.0)  # give the tab transition time to animate
            # Confirm switch: "Tomorrow" header text should appear in dump
            screen_check = self.adb.get_screen_text()
            if "tomorrow" in screen_check.lower():
                logger.info(f"[AIBetting] Tomorrow tab confirmed active")
                break
            logger.warning(f"[AIBetting] Tomorrow tab not detected yet, retrying...")
        else:
            logger.error("[AIBetting] Could not switch to Tomorrow tab after 3 attempts")

        logger.info(f"[AIBetting] Scraping Tomorrow...")
        tomorrow_tips = await self._scrape_tab("Tomorrow", tomorrow)
        all_tips.extend(tomorrow_tips)
        logger.info(f"[AIBetting] Tomorrow: {len(tomorrow_tips)} tips")

        logger.success(f"✅ [AIBetting] Total: {len(all_tips)} tips scraped")
        return all_tips