"""
TipFusion AI — BlueStacks & ADB Android Automation
Controls BlueStacks emulator, launches apps, navigates UI,
extracts predictions via ADB + OCR.
"""

import asyncio
import subprocess
import time
import random
import os
from pathlib import Path
from typing import List, Optional, Tuple, Dict
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np
from PIL import Image
import pytesseract

from core.config import settings
from core.logger import logger
from scrapers.base import RawTip

# ============================================================
# TARGET APPS (installed on BlueStacks)
# ============================================================

# ── CONFIRMED via: adb -s 127.0.0.1:5555 shell pm list packages -3 ──
PREDICTION_APPS = [
    {
        "package": "com.tipspro",
        "name": "TipsPro AI",
    },
    {
        "package": "com.blockdelve.aibettingtips",
        "name": "AI Betting Tips",
    },
    {
        "package": "com.azazasport.ai1",
        "name": "Betting Tips AI Prediction",
    },
]


# ============================================================
# ADB CONTROLLER
# ============================================================

class ADBController:
    """Low-level ADB wrapper for device control."""

    # ── From config.yaml adb section ─────────────────────────────────────────
    DEFAULT_HOST = "127.0.0.1"
    DEFAULT_PORT = 5555
    SCREEN_WIDTH  = 1080
    SCREEN_HEIGHT = 1920
    APP_READY_TIMEOUT = 40      # seconds to wait for SportyBet home screen
    BALANCE_WAIT    = 30        # seconds to retry reading NGN balance
    SETTLE_SECONDS  = 4         # extra settle after app ready

    # Human-behaviour delays (ms → converted to seconds where needed)
    DELAY_AFTER_SEARCH    = 2.0    # 2000ms
    DELAY_AFTER_SELECT    = 1.5    # 1500ms
    DELAY_AFTER_TAP_MKT  = 2.0    # 2000ms
    DELAY_AFTER_PLACE_BET = 3.0    # 3000ms
    DELAY_BETWEEN_GAMES   = 3.5    # 3500ms
    DELAY_BETWEEN_SLIPS   = 60.0   # 60000ms
    HUMAN_VARIANCE        = 0.8    # 800ms variance

    def __init__(self, host: str = None, port: int = None):
        self.host = host or self.DEFAULT_HOST
        self.port = port or self.DEFAULT_PORT
        self.device_id = f"{self.host}:{self.port}"

    def _run(self, *args, timeout: int = 30) -> Tuple[str, str, int]:
        """Run ADB command, return (stdout, stderr, returncode)."""
        cmd = ["adb", "-s", self.device_id] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace"
            )
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except subprocess.TimeoutExpired:
            logger.warning(f"[ADB] Command timed out: {' '.join(args)}")
            return "", "timeout", -1
        except FileNotFoundError:
            logger.error("[ADB] adb not found in PATH")
            return "", "not found", -1

    def connect(self) -> bool:
        """Connect to BlueStacks device."""
        out, err, code = self._run("connect", f"{self.host}:{self.port}")
        connected = "connected" in out.lower() or "already connected" in out.lower()
        if connected:
            logger.info(f"✅ [ADB] Connected to {self.device_id}")
        else:
            logger.error(f"❌ [ADB] Connection failed: {err}")
        return connected

    def is_connected(self) -> bool:
        """Check if device is reachable."""
        out, _, _ = self._run("get-state")
        return out == "device"

    def launch_app(self, package: str, activity: str = "") -> bool:
        """Launch an Android app."""
        if activity:
            cmd = ["shell", "am", "start", "-n", f"{package}/{activity}"]
        else:
            cmd = ["shell", "monkey", "-p", package, "-c",
                   "android.intent.category.LAUNCHER", "1"]
        _, err, code = self._run(*cmd)
        success = code == 0
        if success:
            logger.debug(f"[ADB] Launched {package}")
        else:
            logger.warning(f"[ADB] Launch failed {package}: {err}")
        return success

    def close_app(self, package: str):
        """Force stop an app."""
        self._run("shell", "am", "force-stop", package)

    def tap(self, x: int, y: int):
        """Simulate a tap at coordinates."""
        self._run("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 500):
        """Simulate a swipe gesture."""
        self._run("shell", "input", "swipe",
                  str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def scroll_down(self, steps: int = 3):
        """Scroll down on screen."""
        for _ in range(steps):
            # Swipe up = scroll down
            self.swipe(540, 900, 540, 300, 400)
            time.sleep(0.3 + random.uniform(0.1, 0.3))

    def screenshot(self, output_path: str = "/tmp/tipfusion_screen.png") -> Optional[str]:
        """Take screenshot and pull to local path."""
        remote_path = "/sdcard/tipfusion_screen.png"
        _, _, code = self._run("shell", "screencap", "-p", remote_path)
        if code != 0:
            return None
        _, _, code = self._run("pull", remote_path, output_path)
        return output_path if code == 0 else None

    def get_screen_text(self) -> str:
        """Dump UI XML and extract text from it."""
        self._run("shell", "uiautomator", "dump", "/sdcard/ui_dump.xml")
        out, _, _ = self._run("shell", "cat", "/sdcard/ui_dump.xml")
        # Extract text attributes
        import re
        texts = re.findall(r'text="([^"]*)"', out)
        return "\n".join(t for t in texts if t.strip())

    def wait_for_app(self, package: str, timeout: int = 15) -> bool:
        """Wait until an app is in the foreground."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            out, _, _ = self._run("shell", "dumpsys", "activity", "activities")
            if package in out:
                return True
            time.sleep(1)
        return False

    def human_delay(self, min_s: float = 0.5, max_s: float = 1.5):
        """Random human-like delay between actions."""
        time.sleep(random.uniform(min_s, max_s))


# ============================================================
# OCR ENGINE
# ============================================================

class OCREngine:
    """Extract text from screenshots using Tesseract + OpenCV."""

    def __init__(self):
        # Configure Tesseract
        self._config = "--oem 3 --psm 6 -l eng"

    def preprocess_image(self, image_path: str) -> np.ndarray:
        """Enhance image for better OCR accuracy."""
        img = cv2.imread(image_path)
        if img is None:
            return None

        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Increase contrast
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)

        # Denoise
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)

        # Threshold
        _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        return binary

    def extract_text(self, image_path: str) -> str:
        """Extract all text from a screenshot."""
        try:
            processed = self.preprocess_image(image_path)
            if processed is None:
                return ""

            pil_img = Image.fromarray(processed)
            text = pytesseract.image_to_string(pil_img, config=self._config)
            return text.strip()
        except Exception as e:
            logger.warning(f"[OCR] Extract failed: {e}")
            return ""

    def extract_regions(self, image_path: str, regions: List[Tuple]) -> List[str]:
        """Extract text from specific regions of screen (x, y, w, h)."""
        img = cv2.imread(image_path)
        if img is None:
            return []

        texts = []
        for (x, y, w, h) in regions:
            roi = img[y:y+h, x:x+w]
            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            text = pytesseract.image_to_string(Image.fromarray(binary), config=self._config)
            texts.append(text.strip())
        return texts


# ============================================================
# APP-SPECIFIC SCRAPERS
# ============================================================

class AppScraper:
    """Scrapes a specific Android prediction app via ADB + OCR."""

    def __init__(self, adb: ADBController, ocr: OCREngine, app_config: Dict):
        self.adb = adb
        self.ocr = ocr
        self.app = app_config
        self.screenshot_dir = Path("logs/screenshots")
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def _take_screenshot(self, name: str = "screen") -> Optional[str]:
        path = str(self.screenshot_dir / f"{name}_{int(time.time())}.png")
        return self.adb.screenshot(path)

    def _extract_tips_from_text(self, text: str) -> List[RawTip]:
        """
        Parse OCR/UIAutomator text from prediction apps.
        Handles many different app formats.
        """
        import re
        tips = []
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        full_text = " ".join(lines)

        # Multiple match patterns to handle different app layouts
        match_patterns = [
            r"([A-Z][\w\s'\.]{2,25})\s+(?:vs?\.?|v\.|[-–—])\s+([A-Z][\w\s'\.]{2,25})",
            r"([A-Z][\w\s]{2,20})\s+\d\.\d+\s+([A-Z][\w\s]{2,20})",  # Team 1.80 Team
        ]

        picks = {
            "1":          r"\b(Home Win|Home|1(?![x2X\.5]))\b",
            "X":          r"\b(Draw|Tie|X(?![12]))\b",
            "2":          r"\b(Away Win|Away|2(?![x\.5]))\b",
            "over_2.5":   r"\b(Over 2\.5|O2\.5|Over2\.5|OVER 2\.5)\b",
            "under_2.5":  r"\b(Under 2\.5|U2\.5|UNDER 2\.5)\b",
            "over_3.5":   r"\b(Over 3\.5|O3\.5)\b",
            "btts_yes":   r"\b(BTTS Yes|Both Teams|GG|Both Score)\b",
            "btts_no":    r"\b(BTTS No|NG|No Goal)\b",
            "1X":         r"\b(1X|Home or Draw|Double Chance 1X)\b",
            "X2":         r"\b(X2|Draw or Away|Double Chance X2)\b",
        }
        label_map = {
            "1": "Home Win", "X": "Draw", "2": "Away Win",
            "over_2.5": "Over 2.5", "under_2.5": "Under 2.5",
            "over_3.5": "Over 3.5", "btts_yes": "BTTS Yes",
            "btts_no": "BTTS No", "1X": "1X", "X2": "X2",
        }

        found_matches = []
        for pattern in match_patterns:
            for m in re.finditer(pattern, full_text, re.IGNORECASE):
                home = re.sub(r"[^\w\s\'\-]", "", m.group(1)).strip()
                away = re.sub(r"[^\w\s\'\-]", "", m.group(2)).strip()
                if len(home) >= 3 and len(away) >= 3:
                    # Get surrounding context
                    start = max(0, m.start() - 30)
                    end   = min(len(full_text), m.end() + 80)
                    context = full_text[start:end]
                    found_matches.append((home, away, context))

        for home, away, context in found_matches:
            market, pred_str = None, None
            for mk, pattern in picks.items():
                if re.search(pattern, context, re.IGNORECASE):
                    market   = mk
                    pred_str = f"{label_map[mk]} ({home})" if mk == "1" else                                f"{label_map[mk]} ({away})" if mk == "2" else label_map[mk]
                    break

            if not market:
                continue

            # Extract odds — look for decimal format
            odds = None
            for odds_pattern in [r"@\s*([\d]+\.[\d]{2})", r"([1-9]\.[\d]{2})"]:
                odds_m = re.search(odds_pattern, context)
                if odds_m:
                    try:
                        v = float(odds_m.group(1))
                        if 1.01 <= v <= 20.0:
                            odds = v
                            break
                    except Exception:
                        pass

            # Extract confidence %
            conf = None
            conf_m = re.search(r"(\d{2,3})\s*%", context)
            if conf_m:
                try:
                    conf = float(conf_m.group(1))
                except Exception:
                    pass

            tips.append(RawTip(
                home_team=home, away_team=away,
                prediction=pred_str, market=market,
                odds=odds, confidence=conf,
                tipster=self.app["name"],
                source_name=f"App:{self.app['name']}",
            ))

        # Deduplicate
        seen = set()
        unique = []
        for t in tips:
            key = (t.home_team.lower(), t.away_team.lower(), t.market)
            if key not in seen:
                seen.add(key)
                unique.append(t)

        return unique

    async def scrape_app(self) -> List[RawTip]:
        """Full scrape cycle for one app."""
        package = self.app["package"]
        name = self.app["name"]
        tips = []

        logger.info(f"📱 [Android] Scraping {name}...")

        # Launch app
        launched = self.adb.launch_app(package, self.app.get("launch_activity", ""))
        if not launched:
            logger.warning(f"[Android] Could not launch {name}")
            return []

        # Wait for app to load
        await asyncio.sleep(2)
        self.adb.human_delay(0.5, 1.0)

        # Scrape multiple scroll positions
        for scroll_step in range(5):
            # Try UI dump first (faster, more accurate)
            screen_text = self.adb.get_screen_text()

            if not screen_text.strip():
                # Fallback to OCR
                screenshot_path = self._take_screenshot(f"{name}_{scroll_step}")
                if screenshot_path:
                    screen_text = self.ocr.extract_text(screenshot_path)

            if screen_text:
                new_tips = self._extract_tips_from_text(screen_text)
                tips.extend(new_tips)
                logger.debug(f"[Android] {name} scroll {scroll_step}: {len(new_tips)} tips")

            if scroll_step < 4:
                self.adb.scroll_down(steps=2)
                self.adb.human_delay(0.4, 0.8)

        # Close app
        self.adb.close_app(package)
        await asyncio.sleep(1)

        logger.success(f"✅ [Android] {name}: {len(tips)} tips scraped")
        return tips


# ============================================================
# BLUESTACKS CONTROLLER
# ============================================================

class BlueStacksController:
    """
    Master controller for BlueStacks automation.
    Behaviour tuned to config.yaml execution.behavior settings (V4.5).
    """

    SOURCE_TYPE = "android_app"
    SOURCE_NAME = "BlueStacks Apps"

    # ── From config.yaml adb section ─────────────────────────────────
    BLUESTACKS_EXE = "C:/Program Files/BlueStacks_nxt/HD-Player.exe"

    # ── From config.yaml execution.behavior.timing ────────────────────
    BASE_DELAY_MS    = 900
    JITTER_MS        = 600
    LONG_PAUSE_CHANCE = 0.15
    LONG_PAUSE_RANGE = (2.5, 6.0)   # seconds

    # ── From config.yaml execution.behavior.gestures ──────────────────
    TAP_JITTER_PX    = 8
    SWIPE_MIN_MS     = 300
    SWIPE_MAX_MS     = 900

    # ── From config.yaml execution.behavior.session ───────────────────
    MAX_ACTIONS_PER_MIN = 20
    COOLDOWN_AFTER_N    = 12
    COOLDOWN_RANGE      = (20, 60)  # seconds

    # ── From config.yaml execution.behavior.retry ─────────────────────
    MAX_RETRIES  = 2
    BACKOFF_RANGE = (2, 6)  # seconds

    def __init__(self):
        self.adb = ADBController()
        self.ocr = OCREngine()
        self._action_count = 0

    def _jittered_tap(self, x: int, y: int):
        """Tap with pixel jitter (config: tap_jitter_px = 8)."""
        jx = x + random.randint(-self.TAP_JITTER_PX, self.TAP_JITTER_PX)
        jy = y + random.randint(-self.TAP_JITTER_PX, self.TAP_JITTER_PX)
        self.adb.tap(jx, jy)
        self._action_count += 1
        self._maybe_cooldown()

    def _maybe_cooldown(self):
        """Insert a session cooldown every N actions."""
        if self._action_count > 0 and self._action_count % self.COOLDOWN_AFTER_N == 0:
            cooldown = random.uniform(*self.COOLDOWN_RANGE)
            logger.debug(f"[BlueStacks] Session cooldown {cooldown:.1f}s after {self._action_count} actions")
            time.sleep(cooldown)

    def _human_timing(self):
        """Base delay + jitter + occasional long pause."""
        delay = (self.BASE_DELAY_MS + random.randint(0, self.JITTER_MS)) / 1000.0
        time.sleep(delay)
        if random.random() < self.LONG_PAUSE_CHANCE:
            pause = random.uniform(*self.LONG_PAUSE_RANGE)
            logger.debug(f"[BlueStacks] Long pause {pause:.1f}s")
            time.sleep(pause)

    def _is_bluestacks_running(self) -> bool:
        try:
            result = subprocess.run(
                ["tasklist" if os.name == "nt" else "pgrep", "-f", "HD-Player"],
                capture_output=True, text=True
            )
            return "HD-Player" in result.stdout or result.returncode == 0
        except Exception:
            return False

    def _launch_bluestacks(self) -> bool:
        """Launch BlueStacks using hardcoded path from config."""
        if self._is_bluestacks_running():
            logger.info("[BlueStacks] Already running")
            return True

        exe = self.BLUESTACKS_EXE
        if not Path(exe).exists():
            # Try settings fallback
            exe = settings.bluestacks_path
            if not Path(exe).exists():
                logger.error(f"[BlueStacks] Executable not found at {exe}")
                return False

        logger.info(f"[BlueStacks] Launching: {exe}")
        subprocess.Popen([exe], shell=False)

        # Wait for Android boot — poll every 3s up to app_ready_timeout (40s)
        deadline = time.time() + ADBController.APP_READY_TIMEOUT + 20
        while time.time() < deadline:
            if self.adb.connect() and self.adb.is_connected():
                logger.success("✅ [BlueStacks] Android booted and ADB connected")
                # Extra settle time (config: stabilize.settle_seconds = 4)
                time.sleep(2)
                return True
            time.sleep(3)

        logger.error("❌ [BlueStacks] Timed out waiting for boot")
        return False

    async def scrape_all_apps(self) -> List[RawTip]:
        """Launch BlueStacks, scrape all apps with retry logic (max_retries=2)."""
        if not self._launch_bluestacks():
            return []

        if not self.adb.connect():
            logger.error("[BlueStacks] ADB connection failed")
            return []

        await asyncio.sleep(3)

        all_tips = []
        self._action_count = 0  # Reset session counter

        for app_config in PREDICTION_APPS:
            # Retry logic: max_retries=2, backoff 2–6s (from config)
            for attempt in range(self.MAX_RETRIES + 1):
                try:
                    scraper = AppScraper(self.adb, self.ocr, app_config)
                    app_tips = await scraper.scrape_app()
                    all_tips.extend(app_tips)
                    break  # Success — no retry needed
                except Exception as e:
                    if attempt < self.MAX_RETRIES:
                        backoff = random.uniform(*self.BACKOFF_RANGE)
                        logger.warning(
                            f"[BlueStacks] {app_config['name']} attempt {attempt+1} failed: {e}. "
                            f"Retrying in {backoff:.1f}s..."
                        )
                        await asyncio.sleep(backoff)
                    else:
                        logger.error(f"[BlueStacks] {app_config['name']} failed after {self.MAX_RETRIES+1} attempts")

            # Inter-app human timing
            self._human_timing()
            await asyncio.sleep(random.uniform(1, 2))

        logger.success(f"✅ [BlueStacks] Total from all apps: {len(all_tips)} tips")
        return all_tips

    async def run(self) -> List[RawTip]:
        """Entry point with logging."""
        logger.info("🤖 [BlueStacks] Starting Android scraping session...")
        tips = await self.scrape_all_apps()
        return tips
