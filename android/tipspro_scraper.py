"""
TipFusion AI — TipsPro AI Scraper
Built from proven SportyBot v4.0 logic.

KEY INSIGHT: TipsPro shows team names as SEPARATE XML nodes on the same row:
    node1: "Newcastle Utd"   bounds=[45,520,280,560]
    node2: "VS"              bounds=[290,520,330,560]
    node3: "Barcelona"       bounds=[340,520,540,560]
We GROUP nodes by vertical centre (within 20px) into combined lines,
so the parser sees: "Newcastle Utd VS Barcelona"

FLOW:
  1. Go Home
  2. Tap Football Predictions banner (LEFT, x=25%)
  3. Tap Predictions tab (x=52%, 3rd of 5 tabs)
  4. For each AI tab [TipsPro AI, GPT, Gemini]:
       - Tap tab → Today → scroll+scrape → Tomorrow → scroll+scrape
  5. Go Home
  6. Tap Basketball Predictions banner (RIGHT, x=75%)
  7. Tap Predictions tab (x=77%, 3rd of 3 tabs)
  8. Today → scroll+scrape → Tomorrow → scroll+scrape
"""

import asyncio
import time
import re
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from xml.etree import ElementTree as ET

from core.logger import logger
from scrapers.base import RawTip

PACKAGE  = "com.tipspro"
APP_NAME = "TipsPro AI"

# ── Timing (from SportyBot v4.0 CONFIG) ──────────────────────
WAIT_SHORT  = 2
WAIT_MEDIUM = 3
WAIT_LONG   = 5
WAIT_TAB    = 5    # after switching AI sub-tab
WAIT_PAGE   = 8    # after banner/Predictions tap

# ── BlueStacks system-level ad overlay (shown before app loads) ──────────────
# From screenshot: "Today Top #10 APKs" overlay with blue dismiss circles.
# Two dismissal zones identified from screenshot at 720×1600 resolution:
#   · Top circle  (blue arrow icon)           ≈ x=210, y=95
#   · Bottom bar  ("Checking subscription…")  ≈ x=180, y=845
# Stored as screen-width/height fractions so they scale automatically.
_BS_AD_TOP_X_PCT    = 0.29   # 210 / 720
_BS_AD_TOP_Y_PCT    = 0.059  #  95 / 1600
_BS_AD_BOTTOM_X_PCT = 0.25   # 180 / 720
_BS_AD_BOTTOM_Y_PCT = 0.528  # 845 / 1600

# Text that uniquely identifies the BlueStacks "Top APKs" ad overlay
_BS_AD_SIGNALS = [
    "today top", "top #10 apks", "top 10 apks",
    "analysis of the day",
    "checking your subscription",
    "vip betting tips", "enki's betting tips",
    "futpicks", "tipsway",          # app names listed in the ad rows
]

# ── Pick keywords (from SportyBot v4.0 PICK_KEYWORDS) ────────
PICK_KEYWORDS = {
    "btts_yes":   ["btts yes", "btts", "both teams to score", "gg yes", "gg"],
    "over_2.5":   ["full time over 2.5", "over 2.5", "o2.5", "over2.5", "+2.5"],
    "under_2.5":  ["full time under 2.5", "under 2.5", "u2.5", "-2.5"],
    "1X":         ["double chance 1x", "1x", "home or draw"],
    "X2":         ["double chance x2", "x2", "draw or away"],
    "X":          ["draw", "tie", "1x2: x"],
    "1":          ["home win", "home wins", "home", "win 1", "1x2: 1", "w1"],
    "2":          ["away win", "away wins", "away", "win 2", "1x2: 2", "w2"],
}

PRED_LABELS = {
    "btts_yes":  "BTTS Yes",
    "btts_no":   "BTTS No",
    "over_2.5":  "Over 2.5",
    "under_2.5": "Under 2.5",
    "over_3.5":  "Over 3.5",
    "under_3.5": "Under 3.5",
    "1X":        "Double Chance 1X",
    "X2":        "Double Chance X2",
    "X":         "Draw",
    "1":         "Home Win",
    "2":         "Away Win",
}

# Non-football sports to skip
NON_FOOTBALL = [
    "nba", "nfl", "nhl", "ncaa basketball", "mlb", "wnba",
    "handball", "basketball", "baseball", "volleyball", "hockey",
    "afl", "nrl", "ufc", "mma", "boxing", "formula", "nascar",
    "golf", "esports", "bundesliga handball", "handbollsligan",
    "italya serie a çeyrek",  # basketball quarter - Turkish
    "abd nba", "abd nfl",     # Turkish NBA/NFL labels
    "polonya energa basket",  # basketball
    "doğu konferansı", "batı konferansı",  # NBA conference
]

JUNK_LEAGUE_WORDS = [
    "successful", "unsuccessful", "yesterday", "today", "tomorrow",
    "ft ", "ht ", "1x2", "over", "under", "btts",
    "predictions", "analysis", "gemini", "tipspro", "gpt",
    "alternative"
]


# ── Helpers ───────────────────────────────────────────────────

def _is_junk_match(text: str) -> bool:
    t = text.strip()
    if len(t) < 7 or len(t) > 70:        return True
    if t.endswith("..."):                  return True
    if re.match(r'^\d', t):               return True
    if re.search(r'\bHMR\b', t, re.IGNORECASE): return True
    parts = re.split(r'\s+[Vv][Ss]?\s+', t)
    if len(parts) == 2:
        a, b = parts[0].strip(), parts[1].strip()
        if re.match(r'^\d+\.?\d*$', a) or re.match(r'^\d+\.?\d*$', b): return True
        if len(a) < 3 or len(b) < 3:     return True
        if a[0].isdigit() or b[0].isdigit(): return True
    bad = [
        "search", "login", "register", "deposit", "withdraw",
        "settings", "menu", "home", "back", "close", "cancel",
        "football predictions", "basketball predictions",
        "successful", "completed", "upcoming", "influencer",
        "ai predictions", "see all", "mobile app",
        "create match", "coupon", "analysis", "enjoy",
        "ht x", "ht 1", "ht 2", "alternative successful",
        "unsuccessful", "gemini predictions", "gpt predictions",
        "tipspro ai predictions",
    ]
    return any(b in t.lower() for b in bad)


def _parse_confidence(text: str) -> float:
    # "%93.80" or "80%" or "80"
    m = re.search(r'%(\d{2,3}(?:\.\d+)?)', text)
    if m: return float(m.group(1))
    m2 = re.search(r'(\d{2,3}(?:\.\d+)?)\s*%', text)
    if m2: return float(m2.group(1))
    return 50.0


def _parse_odds(text: str) -> Optional[float]:
    normalized = text.replace(",", ".")
    m = re.search(r'\b(\d{1,2}\.\d{1,2})\b', normalized)
    if m:
        v = float(m.group(1))
        if 1.10 <= v <= 15.0:
            return v
    return None


def _detect_pick(lines: List[str]) -> str:
    """Detect betting pick from lines around a match card."""
    combined = " ".join(l.strip() for l in lines).lower()

    # Handicap / HMR
    if re.search(r'handicap|hmr\b', combined):
        if "home wins" in combined or "home win" in combined: return "1"
        if "away wins" in combined or "away win" in combined: return "2"

    # BTTS
    if "btts yes" in combined or "both teams to score yes" in combined: return "btts_yes"
    if "btts no" in combined: return "btts_no"
    if "btts" in combined or "gg yes" in combined: return "btts_yes"

    # Over/Under
    if re.search(r'full time over|ft over|over 2\.5', combined): return "over_2.5"
    if re.search(r'full time under|ft under|under 2\.5', combined): return "under_2.5"
    if re.search(r'over 3\.5', combined): return "over_3.5"
    if re.search(r'under 3\.5', combined): return "under_3.5"
    if re.search(r'\bover\b', combined): return "over_2.5"
    if re.search(r'\bunder\b', combined): return "under_2.5"

    # Double Chance
    if "1x" in combined: return "1X"
    if "x2" in combined: return "X2"

    # 1X2
    if "home wins" in combined or "home win" in combined: return "1"
    if "away wins" in combined or "away win" in combined: return "2"
    if re.search(r'\bdraw\b|\btie\b', combined): return "X"
    if re.search(r'\bhome\b', combined): return "1"
    if re.search(r'\baway\b', combined): return "2"

    return "1"


def _is_football_context(window_texts: List[str]) -> bool:
    """Return False if the match is clearly a non-football sport."""
    combined = " ".join(window_texts).lower()
    for kw in NON_FOOTBALL:
        if kw in combined:
            return False
    return True


def _get_full_texts_xml(d) -> List[str]:
    """
    Parse XML hierarchy and return text lines sorted top-to-bottom.
    CRITICAL: Groups nodes within 20px vertical distance into combined lines
    so "Newcastle Utd" + "VS" + "Barcelona" → "Newcastle Utd VS Barcelona"
    """
    try:
        xml  = d.dump_hierarchy(compressed=False)
        root = ET.fromstring(xml)

        nodes = []
        for node in root.iter():
            for attr in ("text", "content-desc"):
                val = node.get(attr, "").strip()
                if val and len(val) >= 1:
                    nums = re.findall(r'\d+', node.get("bounds", ""))
                    if len(nums) == 4:
                        left   = int(nums[0])
                        top    = int(nums[1])
                        right  = int(nums[2])
                        bottom = int(nums[3])
                        cy     = (top + bottom) // 2
                        nodes.append((cy, left, top, bottom, val))
                    break

        if not nodes:
            return []

        nodes.sort(key=lambda x: (x[0], x[1]))

        # Group nodes within 20px vertical centre into rows
        rows   = []
        bucket = [nodes[0]]
        for node in nodes[1:]:
            if abs(node[0] - bucket[-1][0]) <= 20:
                bucket.append(node)
            else:
                rows.append(bucket)
                bucket = [node]
        rows.append(bucket)

        lines = []
        seen  = set()
        for row in rows:
            row.sort(key=lambda x: x[1])
            combined = " ".join(n[4] for n in row).strip()
            if combined and combined not in seen:
                seen.add(combined)
                lines.append(combined)
            for n in row:
                tok = n[4].strip()
                if tok and tok not in seen:
                    seen.add(tok)
                    lines.append(tok)

        return lines

    except Exception as e:
        logger.warning(f"[TipsPro] get_full_texts_xml error: {e}")
        return []


def _parse_tipspro(raw_texts: List[str], tipster: str) -> List[dict]:
    """
    Parse TipsPro AI prediction cards.
    Card format:
        Champions League              %95.00
        10 Mar Tue | 09:00
        Newcastle Utd  VS  Barcelona
        Full Time Over 2.5            1,23
    """
    games = []
    seen  = set()

    for i, text in enumerate(raw_texts):
        if not re.search(r'\b[Vv][Ss]\b', text):
            continue
        if _is_junk_match(text):
            continue

        match_name = re.sub(r'\b[Vv][Ss]\b', 'vs', text.strip())
        # Strip score garbage: "Team A vs Team B 62 - 54"
        match_name = re.sub(r'\s+\d+\s*[-–]\s*\d+.*$', '', match_name).strip()
        match_name = re.sub(r'\s*%[\d.]+\s*$', '', match_name).strip()
        match_name = re.sub(r'\s{2,}', ' ', match_name).strip()

        # Wide window for context
        window = raw_texts[max(0, i-4): min(len(raw_texts), i+10)]
        before = raw_texts[max(0, i-4): i]

        # Skip finished/junk games
        window_text = " ".join(window).lower()
        if any(sig in window_text for sig in ["successful", "unsuccessful",
                                               "alternative successful", "yesterday"]):
            continue

        # Skip non-football sports
        if not _is_football_context(window):
            continue

        # Confidence
        confidence = 50.0
        for line in window:
            c = _parse_confidence(line)
            if c != 50.0:
                confidence = c
                break

        # Odds
        odds = None
        for line in window:
            if re.search(r'\b[Vv][Ss]\b', line):
                continue
            o = _parse_odds(line)
            if o:
                odds = o
                break

        # Pick
        pick_key = _detect_pick(window)

        # League and date
        league     = ""
        match_date = ""
        match_time = ""

        for line in reversed(before):
            line = line.strip()
            if not line or len(line) < 3:
                continue
            # Date+time line: "10 Mar Tue | 09:00"
            m = re.match(r'(\d+\s+\w{3}).*?(\d{1,2}:\d{2})', line)
            if m and not match_date:
                match_date = m.group(1).strip()
                match_time = m.group(2).strip()
                continue
            lo = line.lower()
            if any(jk in lo for jk in JUNK_LEAGUE_WORDS):
                continue
            if re.match(r'^[\d\s.,;%|/-]+$', line):
                continue
            if re.match(r'^%\d', line):
                continue
            if not re.search(r'\b[Vv][Ss]\b', line) and len(line) > 3:
                league = line
                break

        # Parse kickoff datetime
        kickoff = None
        try:
            if match_date and match_time:
                year = datetime.utcnow().year
                kickoff = datetime.strptime(
                    f"{match_date} {year} {match_time}", "%d %b %Y %H:%M"
                )
            elif match_date:
                year = datetime.utcnow().year
                kickoff = datetime.strptime(
                    f"{match_date} {year}", "%d %b %Y"
                )
        except Exception:
            pass

        key = match_name.lower()
        if key not in seen:
            seen.add(key)
            label = PRED_LABELS.get(pick_key, pick_key)
            # Add team context to home/away wins
            parts = match_name.split(" vs ", 1)
            if pick_key == "1" and len(parts) == 2:
                label = f"Home Win ({parts[0].strip()})"
            elif pick_key == "2" and len(parts) == 2:
                label = f"Away Win ({parts[1].strip()})"

            games.append({
                "match":      match_name,
                "pick_key":   pick_key,
                "pick_label": label,
                "odds":       odds or 0.0,
                "confidence": confidence,
                "league":     league,
                "kickoff":    kickoff,
                "tipster":    tipster,
            })

    return games


class TipsProScraper:
    """
    Navigates TipsPro AI app using proven SportyBot v4.0 logic.
    Scrapes Football (3 AI tabs) + Basketball today/tomorrow.
    """

    def __init__(self, adb):
        self.adb = adb
        self._w  = 720    # screen width (BlueStacks default)
        self._h  = 1600   # screen height

    def _get_screen_size(self):
        """Try to detect actual screen size via ADB."""
        try:
            out, _, _ = self.adb._run("shell", "wm", "size")
            m = re.search(r'(\d+)x(\d+)', out)
            if m:
                self._w = int(m.group(1))
                self._h = int(m.group(2))
        except Exception:
            pass

    def _click(self, xpct: float, ypct: float, label: str = ""):
        x = int(self._w * xpct)
        y = int(self._h * ypct)
        self.adb.tap(x, y)
        if label:
            logger.debug(f"[TipsPro] Tap {label} ({x},{y})")

    def _tap_text(self, text: str, timeout: int = 3) -> bool:
        """Find a text element via UI dump and tap it."""
        try:
            out, _, _ = self.adb._run("shell", "uiautomator", "dump", "/sdcard/ui.xml")
            out2, _, _ = self.adb._run("shell", "cat", "/sdcard/ui.xml")
            root = ET.fromstring(out2)
            for node in root.iter():
                if node.get("text","").strip() == text or text in node.get("text",""):
                    nums = re.findall(r'\d+', node.get("bounds",""))
                    if len(nums) == 4:
                        cx = (int(nums[0]) + int(nums[2])) // 2
                        cy = (int(nums[1]) + int(nums[3])) // 2
                        self.adb.tap(cx, cy)
                        logger.debug(f"[TipsPro] Tapped text '{text}' at ({cx},{cy})")
                        return True
        except Exception:
            pass
        return False

    def _wait(self, seconds: float):
        time.sleep(seconds)

    def _scroll_down(self):
        """Swipe up = scroll content down."""
        x  = int(self._w * 0.5)
        y1 = int(self._h * 0.80)
        y2 = int(self._h * 0.20)
        self.adb.swipe(x, y1, x, y2, 600)
        self._wait(1.2)

    def _scroll_date_strip_left(self):
        """Swipe date strip LEFT to reveal Tomorrow tab."""
        x1 = int(self._w * 0.7)
        x2 = int(self._w * 0.3)
        y  = int(self._h * 0.22)
        self.adb.swipe(x1, y, x2, y, 400)
        self._wait(0.8)

    def _get_texts(self) -> List[str]:
        """Get all text from screen using the proven XML grouping method."""
        try:
            self.adb._run("shell", "uiautomator", "dump", "/sdcard/ui_dump.xml")
            out, _, _ = self.adb._run("shell", "cat", "/sdcard/ui_dump.xml")
            if not out.strip():
                # Fallback: use get_screen_text
                return self.adb.get_screen_text().split("\n")

            root  = ET.fromstring(out)
            nodes = []
            for node in root.iter():
                for attr in ("text", "content-desc"):
                    val = node.get(attr, "").strip()
                    if val and len(val) >= 1:
                        nums = re.findall(r'\d+', node.get("bounds", ""))
                        if len(nums) == 4:
                            cy = (int(nums[1]) + int(nums[3])) // 2
                            nodes.append((cy, int(nums[0]), val))
                        break

            if not nodes:
                return []

            nodes.sort(key=lambda x: (x[0], x[1]))

            # Group within 20px vertical centre
            rows   = []
            bucket = [nodes[0]]
            for node in nodes[1:]:
                if abs(node[0] - bucket[-1][0]) <= 20:
                    bucket.append(node)
                else:
                    rows.append(bucket)
                    bucket = [node]
            rows.append(bucket)

            lines = []
            seen  = set()
            for row in rows:
                row.sort(key=lambda x: x[1])
                combined = " ".join(n[2] for n in row).strip()
                if combined and combined not in seen:
                    seen.add(combined)
                    lines.append(combined)
                for n in row:
                    tok = n[2].strip()
                    if tok and tok not in seen:
                        seen.add(tok)
                        lines.append(tok)
            return lines

        except Exception as e:
            logger.warning(f"[TipsPro] _get_texts error: {e}")
            return self.adb.get_screen_text().split("\n")

    def _tap_date(self, target: str = "today") -> bool:
        """Tap Today or Tomorrow in the date strip. Mirrors tap_date_strip()."""
        now = datetime.utcnow()
        tmr = now + timedelta(days=1)
        today_str = f"{now.day} {now.strftime('%b')}"
        tmr_str   = f"{tmr.day} {tmr.strftime('%b')}"
        tmr_abbr  = tmr.strftime("%a")

        candidates = (
            ["Today", "TODAY", today_str]
            if target == "today"
            else ["Tomorrow", "TOMORROW", tmr_str, tmr_abbr, str(tmr.day)]
        )

        for label in candidates:
            if self._tap_text(label):
                self._wait(WAIT_SHORT)
                logger.info(f"[TipsPro] Date strip: '{label}' ({target})")
                return True

        if target == "tomorrow":
            self._scroll_date_strip_left()
            for label in candidates:
                if self._tap_text(label):
                    self._wait(WAIT_SHORT)
                    logger.info(f"[TipsPro] Date strip: '{label}' (tomorrow after swipe)")
                    return True

        logger.warning(f"[TipsPro] Date strip '{target}' not found")
        return False

    def _scroll_and_collect(self, tipster: str, max_scrolls: int = 20) -> List[dict]:
        """Scroll through predictions list, parse every page. Stops on 3 empty pages."""
        pool         = []
        seen         = set()
        empty_streak = 0

        for page in range(max_scrolls):
            texts = self._get_texts()
            games = _parse_tipspro(texts, tipster)

            added = 0
            for g in games:
                k = g["match"].lower()
                if k not in seen:
                    seen.add(k)
                    pool.append(g)
                    added += 1

            logger.debug(f"[TipsPro] {tipster} page {page+1}: +{added} (total {len(pool)})")

            if added == 0:
                empty_streak += 1
                if empty_streak >= 3:
                    break
            else:
                empty_streak = 0

            self._scroll_down()

        return pool

    def _scrape_today_tomorrow(self, tipster: str) -> List[dict]:
        """Tap Today → scrape, Tap Tomorrow → scrape."""
        out = []
        self._tap_date("today")
        self._wait(WAIT_MEDIUM)
        today_games = self._scroll_and_collect(f"{tipster}-Today")
        logger.info(f"[TipsPro] {tipster} Today: {len(today_games)} games")
        out.extend(today_games)

        if self._tap_date("tomorrow"):
            self._wait(WAIT_MEDIUM)
            tmr_games = self._scroll_and_collect(f"{tipster}-Tomorrow")
            logger.info(f"[TipsPro] {tipster} Tomorrow: {len(tmr_games)} games")
            out.extend(tmr_games)

        return out

    # Ad close-button keywords (case-insensitive) — extend as needed
    _AD_DISMISS_TEXTS = [
        "close", "skip", "skip ad", "skip ads", "×", "✕", "x",
        "no thanks", "not now", "dismiss", "continue", "got it",
    ]
    # Words that indicate an ad/promo overlay is on screen
    _AD_OVERLAY_SIGNALS = [
        "install", "get it on", "google play", "app store",
        "download now", "free download", "open", "ad", "sponsored",
        "upgrade", "subscribe now", "try premium", "try pro",
        "watch ad", "rewarded",
    ]

    def _dismiss_ad(self) -> bool:
        """
        Dismiss an ad overlay by tapping outside it (the dark area around the ad).
        Ad cards are typically centred and take up ~60% of the screen width/height,
        so tapping a corner of the screen reliably hits the dimmed backdrop.
        Falls back to top-right close button if the first tap didn't work.
        Never scrolls, never presses Back.
        """
        logger.info("[TipsPro] Ad detected — tapping outside ad to dismiss...")

        # Tap top-left corner (outside the ad card)
        self._click(0.05, 0.05, "Outside ad (top-left)")
        self._wait(1.5)

        if self._detect_screen_state() != "ad":
            logger.info("[TipsPro] Ad dismissed via outside tap")
            return True

        # Tap bottom-left corner
        self._click(0.05, 0.95, "Outside ad (bottom-left)")
        self._wait(1.5)

        if self._detect_screen_state() != "ad":
            logger.info("[TipsPro] Ad dismissed via bottom-left tap")
            return True

        # Last resort: top-right close button (×)
        logger.info("[TipsPro] Outside taps ineffective — trying close button (x=92%, y=8%)")
        self._click(0.92, 0.08, "Ad close button coord")
        self._wait(1.5)

        return True

    def _detect_screen_state(self) -> str:
        """
        Detect what screen TipsPro is currently showing.
        Returns: "home", "football", "basketball", "predictions",
                 "ad", "splash", "blank", "unknown"
        """
        texts = self._get_texts()
        combined = " ".join(texts).lower()

        if not combined.strip():
            return "blank"
        if "football predictions" in combined and "basketball predictions" in combined:
            return "home"
        if "tipspro ai" in combined and "gpt" in combined and "gemini" in combined:
            return "predictions"
        if "football predictions" in combined or ("predictions" in combined and "enjoy" in combined):
            return "football"
        if "basketball predictions" in combined:
            return "basketball"
        if "loading" in combined or len(combined) < 20:
            return "splash"

        # Ad detection — must come BEFORE "unknown" fallthrough
        ad_hits = sum(1 for sig in self._AD_OVERLAY_SIGNALS if sig in combined)
        if ad_hits >= 2:
            return "ad"

        return "unknown"

    def _recover_to_home(self, max_attempts: int = 5):
        """
        Recovery: press back and/or tap home until we reach home screen.
        Handles cases where app opens but gets stuck on a loading screen,
        ad, or wrong section.
        """
        for attempt in range(max_attempts):
            state = self._detect_screen_state()
            logger.debug(f"[TipsPro] Screen state: {state} (attempt {attempt+1})")

            if state == "home":
                return True

            if state == "blank" or state == "splash":
                # App may still be loading — wait longer
                logger.info(f"[TipsPro] Screen blank/loading — waiting...")
                self._wait(3.0)
                continue

            # ── Ad overlay: dismiss it, never press Back for ads ──
            if state == "ad":
                self._dismiss_ad()
                self._wait(2.0)
                continue

            if state in ("football", "basketball", "predictions", "unknown"):
                # Press back to try to get to home
                self.adb._run("shell", "input", "keyevent", "4")  # BACK key
                self._wait(1.5)
                continue

        # Last resort: tap home button coordinates
        logger.warning("[TipsPro] Recovery: force-tapping Home tab")
        if not self._tap_text("Home"):
            self._click(0.09, 0.965, "Home (recovery)")
        self._wait(WAIT_PAGE)
        return self._detect_screen_state() == "home"

    def _go_home(self):
        """Tap Home tab (far left of bottom nav, x=9%) with state verification."""
        logger.info("[TipsPro] → Going Home...")

        # ── Dismiss BlueStacks system ad if it has reappeared ────────────────
        if self._is_bluestacks_ad():
            self._dismiss_bluestacks_ad()
            self._wait(WAIT_SHORT)

        # ── Dismiss any in-app ad that appeared on launch ──
        state = self._detect_screen_state()
        if state == "ad":
            self._dismiss_ad()
            self._wait(2.0)

        # Try text tap first
        if not self._tap_text("Home"):
            self._click(0.09, 0.965, "Home nav")
        self._wait(WAIT_SHORT)

        # Verify we actually got to home screen
        state = self._detect_screen_state()
        if state != "home":
            logger.warning(f"[TipsPro] Not on home (state={state}) — recovering...")
            self._recover_to_home()
        else:
            self._wait(WAIT_SHORT)  # Extra settle on home

    def _tap_banner(self, sport: str):
        """
        Tap Football or Basketball banner with retry.
        If banner tap doesn't change the screen, retries up to 3 times.
        """
        for attempt in range(3):
            if sport == "football":
                logger.info(f"[TipsPro] → Football banner (attempt {attempt+1})")
                tapped = (self._tap_text("Football Predictions") or
                          self._tap_text("Football"))
                if not tapped:
                    self._click(0.25, 0.28, "Football banner coord")
            else:
                logger.info(f"[TipsPro] → Basketball banner (attempt {attempt+1})")
                tapped = (self._tap_text("Basketball Predictions") or
                          self._tap_text("Basketball"))
                if not tapped:
                    self._click(0.75, 0.28, "Basketball banner coord")

            self._wait(WAIT_PAGE)

            # Verify the screen changed from home
            state = self._detect_screen_state()
            if state != "home":
                logger.info(f"[TipsPro] Banner tap successful (state={state})")
                return

            # Still on home — banner didn't respond, retry
            logger.warning(f"[TipsPro] Banner tap failed (still home) — retry {attempt+1}")
            self._wait(2.0)

        logger.warning("[TipsPro] Banner tap gave up after 3 attempts")

    def _tap_predictions_tab(self, context: str):
        """
        Tap Predictions tab with verification.
        Football context (5 tabs) = x=52%
        Basketball context (3 tabs) = x=77%
        """
        logger.info(f"[TipsPro] → Tapping Predictions tab ({context})...")

        for attempt in range(3):
            tapped = False

            # Try XML text tap — find "Predictions" in bottom nav
            try:
                out, _, _ = self.adb._run("shell", "uiautomator", "dump", "/sdcard/ui.xml")
                out2, _, _ = self.adb._run("shell", "cat", "/sdcard/ui.xml")
                root = ET.fromstring(out2)
                for node in root.iter():
                    if node.get("text","").strip() == "Predictions":
                        nums = re.findall(r'\d+', node.get("bounds",""))
                        if len(nums) == 4:
                            cx = (int(nums[0]) + int(nums[2])) // 2
                            cy = (int(nums[1]) + int(nums[3])) // 2
                            if cy > int(self._h * 0.85):
                                self.adb.tap(cx, cy)
                                tapped = True
                                break
            except Exception:
                pass

            if not tapped:
                xpct = 0.52 if context == "football" else 0.77
                self._click(xpct, 0.965, f"Predictions tab coord ({context})")

            self._wait(WAIT_PAGE)

            # Verify we reached predictions screen
            state = self._detect_screen_state()
            if state == "predictions":
                logger.info(f"[TipsPro] Predictions tab confirmed")
                return

            # Not there yet — check if AI sub-tabs visible
            texts   = self._get_texts()
            combined = " ".join(texts).lower()
            if "tipspro ai" in combined or "gpt" in combined:
                logger.info(f"[TipsPro] Predictions page reached (sub-tabs visible)")
                return

            logger.warning(f"[TipsPro] Predictions tab attempt {attempt+1} failed (state={state})")
            self._wait(2.0)

        logger.warning("[TipsPro] Could not reach predictions tab")

    def _tap_ai_subtab(self, name: str) -> bool:
        """Tap TipsPro AI / GPT / Gemini sub-tab."""
        logger.info(f"[TipsPro] → AI sub-tab: '{name}'")
        if self._tap_text(name):
            self._wait(WAIT_TAB)
            return True
        logger.warning(f"[TipsPro] Sub-tab '{name}' not found")
        return False

    # ── BlueStacks system-ad handling ────────────────────────────────────────

    def _is_bluestacks_ad(self) -> bool:
        """
        Return True when the BlueStacks "Today Top #10 APKs" system overlay
        is covering the screen.  We check the screen text rather than using
        fixed coordinates so this works even if the ad content changes slightly.
        """
        texts    = self._get_texts()
        combined = " ".join(texts).lower()
        hits     = sum(1 for sig in _BS_AD_SIGNALS if sig in combined)
        return hits >= 2   # need at least 2 signals to avoid false positives

    def _dismiss_bluestacks_ad(self, max_attempts: int = 4) -> bool:
        """
        Dismiss the BlueStacks system-level ad overlay that appears on app
        launch (the "Today Top #10 APKs / Analysis of the Day" sheet).

        Strategy (mirrors the screenshot's two blue-circled dismiss zones):
          1. Tap the top dismiss circle  (x≈29%, y≈6% — blue arrow area)
          2. Short wait, re-check
          3. If still visible, tap the bottom bar (x≈25%, y≈53%)
          4. Repeat up to max_attempts times

        Never presses the Android Back key — that might close the app.
        """
        if not self._is_bluestacks_ad():
            return True   # nothing to dismiss

        logger.info("[TipsPro] BlueStacks system ad detected — dismissing...")

        for attempt in range(max_attempts):
            # --- Tap 1: top dismiss circle ---
            self._click(_BS_AD_TOP_X_PCT, _BS_AD_TOP_Y_PCT,
                        f"BS-ad top circle (attempt {attempt+1})")
            self._wait(1.5)

            if not self._is_bluestacks_ad():
                logger.info("[TipsPro] BlueStacks ad dismissed via top tap")
                return True

            # --- Tap 2: bottom subscription/loading bar ---
            self._click(_BS_AD_BOTTOM_X_PCT, _BS_AD_BOTTOM_Y_PCT,
                        f"BS-ad bottom bar (attempt {attempt+1})")
            self._wait(1.5)

            if not self._is_bluestacks_ad():
                logger.info("[TipsPro] BlueStacks ad dismissed via bottom tap")
                return True

            # --- Tap 3: try the very top-right corner (×-button fallback) ---
            self._click(0.95, 0.04, f"BS-ad top-right corner (attempt {attempt+1})")
            self._wait(2.0)

            if not self._is_bluestacks_ad():
                logger.info("[TipsPro] BlueStacks ad dismissed via top-right corner")
                return True

            logger.warning(f"[TipsPro] BlueStacks ad still visible after attempt {attempt+1}")

        logger.warning("[TipsPro] Could not fully dismiss BlueStacks ad — proceeding anyway")
        return False

    def _launch_app(self, wait_seconds: float = WAIT_PAGE) -> None:
        """
        Launch TipsPro via ADB monkey, wait for startup, then clear any
        BlueStacks system ad overlay before returning control to the scraper.
        """
        logger.info(f"[TipsPro] Launching {PACKAGE}...")
        self.adb._run("shell", "monkey", "-p", PACKAGE, "-c",
                      "android.intent.category.LAUNCHER", "1")
        self._wait(wait_seconds)

        # ── Dismiss BlueStacks system ad (fires once per session at most) ──
        if self._is_bluestacks_ad():
            self._dismiss_bluestacks_ad()
            # Give the app a moment to fully render after the overlay is gone
            self._wait(WAIT_SHORT)

    # ─────────────────────────────────────────────────────────────────────────

    async def scrape_football(self) -> List[RawTip]:
        """
        Full football scrape:
        Home → Football banner → Predictions tab →
        TipsPro AI/GPT/Gemini → Today+Tomorrow each
        """
        all_games: List[dict] = []

        self._get_screen_size()

        # ── Launch app and clear any BlueStacks system ad overlay ────────────
        self._launch_app()

        # ── FOOTBALL ─────────────────────────────────────────
        self._go_home()
        self._tap_banner("football")
        self._tap_predictions_tab("football")

        for ai_tab in ["TipsPro AI", "GPT", "Gemini"]:
            logger.info(f"[TipsPro] ── Football / {ai_tab} ──")
            if self._tap_ai_subtab(ai_tab):
                games = self._scrape_today_tomorrow(f"Football-{ai_tab.replace(' ','')}")
                all_games.extend(games)
            await asyncio.sleep(0.5)

        # ── BASKETBALL (for completeness — bot.py filters it out) ──
        self._go_home()
        self._tap_banner("basketball")
        self._tap_predictions_tab("basketball")
        bball = self._scrape_today_tomorrow("Basketball")
        all_games.extend(bball)

        self._go_home()

        # Deduplicate by match key
        seen   = set()
        unique = []
        for g in all_games:
            k = g["match"].lower()
            if k not in seen:
                seen.add(k)
                unique.append(g)

        logger.success(f"✅ [TipsPro] {len(unique)} total predictions scraped")
        return self._to_raw_tips(unique)

    def _to_raw_tips(self, games: List[dict]) -> List[RawTip]:
        tips = []
        for g in games:
            parts = g["match"].split(" vs ", 1)
            home  = parts[0].strip() if parts else g["match"]
            away  = parts[1].strip() if len(parts) > 1 else "Unknown"

            tips.append(RawTip(
                home_team=home,
                away_team=away,
                prediction=g["pick_label"],
                market=g["pick_key"],
                odds=g["odds"] if g["odds"] > 0 else None,
                confidence=g["confidence"],
                league=g["league"],
                kickoff=g["kickoff"],
                tipster=f"TipsPro/{g['tipster']}",
                source_name=f"App:{APP_NAME}",
            ))
        return tips