"""
TipFusion AI — Predictz & SoccerVista Scrapers
Web scrapers for popular football prediction sites.
"""

import re
from datetime import datetime
from typing import List, Optional
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawTip
from core.logger import logger


# ============================================================
# PREDICTZ
# ============================================================

class PredictzScraper(BaseScraper):
    SOURCE_TYPE = "web"
    SOURCE_NAME = "Predictz"
    SOURCE_IDENTIFIER = "predictz.com"

    BASE_URL = "https://www.predictz.com/predictions/"

    async def scrape(self) -> List[RawTip]:
        tips = []
        try:
            html = await self.fetch(self.BASE_URL)
            soup = BeautifulSoup(html, "lxml")

            # Find match prediction rows
            rows = soup.select("table.ptable tr") or soup.select(".predrow")

            for row in rows:
                tip = self._parse_row(row)
                if tip:
                    tips.append(tip)

        except Exception as e:
            logger.error(f"[Predictz] error: {e}")

        return tips

    def _parse_row(self, row) -> Optional[RawTip]:
        try:
            cells = row.find_all("td")
            if len(cells) < 5:
                return None

            home = cells[0].get_text(strip=True)
            away = cells[2].get_text(strip=True)

            if not home or not away or home == "Home":
                return None

            pred_text = cells[3].get_text(strip=True).upper()

            market_map = {
                "1": "1", "X": "X", "2": "2",
                "1X": "1X", "X2": "X2", "12": "12",
                "OVER": "over_2.5", "UNDER": "under_2.5",
                "GG": "btts_yes", "NG": "btts_no",
            }

            market = None
            for key in market_map:
                if key in pred_text:
                    market = market_map[key]
                    break

            if not market:
                return None

            pred_labels = {
                "1": f"Home Win ({home})",
                "X": "Draw", "2": f"Away Win ({away})",
                "over_2.5": "Over 2.5", "under_2.5": "Under 2.5",
                "btts_yes": "BTTS Yes", "btts_no": "BTTS No",
                "1X": "Double Chance 1X", "X2": "Double Chance X2", "12": "Double Chance 12",
            }

            pred_str = pred_labels.get(market, pred_text)

            # Confidence from odds column
            confidence = None
            if len(cells) > 4:
                try:
                    odds_text = cells[4].get_text(strip=True)
                    odds = float(re.search(r"[\d.]+", odds_text).group())
                    confidence = round((1 / odds) * 100, 1)
                except Exception:
                    pass

            return RawTip(
                home_team=home,
                away_team=away,
                prediction=pred_str,
                market=market,
                confidence=confidence,
                tipster="Predictz",
                source_name=self.SOURCE_NAME,
            )
        except Exception:
            return None


# ============================================================
# SOCCERVISTA
# ============================================================

class SoccerVistaScraper(BaseScraper):
    SOURCE_TYPE = "web"
    SOURCE_NAME = "SoccerVista"
    SOURCE_IDENTIFIER = "soccervista.com"

    BASE_URL = "https://www.soccervista.com/"

    async def scrape(self) -> List[RawTip]:
        tips = []
        try:
            html = await self.fetch(self.BASE_URL)
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("table.svi-table tr") or soup.select("tr.odd, tr.even")

            for row in rows:
                tip = self._parse_row(row)
                if tip:
                    tips.append(tip)

        except Exception as e:
            logger.error(f"[SoccerVista] error: {e}")

        return tips

    def _parse_row(self, row) -> Optional[RawTip]:
        try:
            cells = row.find_all("td")
            if len(cells) < 4:
                return None

            # Extract teams from match cell
            match_cell = cells[1].get_text(separator=" ").strip()
            parts = re.split(r"\s+-\s+|\s+v\s+|\s+vs\s+", match_cell, maxsplit=1)
            if len(parts) != 2:
                return None

            home, away = parts[0].strip(), parts[1].strip()

            pred_text = cells[3].get_text(strip=True).upper()

            pred_map = {
                "1": ("1", f"Home Win ({home})"),
                "X": ("X", "Draw"),
                "2": ("2", f"Away Win ({away})"),
                "1X": ("1X", "Double Chance 1X"),
                "X2": ("X2", "Double Chance X2"),
            }

            result = pred_map.get(pred_text)
            if not result:
                return None

            market, pred_str = result

            # Try to parse % confidence
            pct_cell = cells[-1].get_text(strip=True)
            confidence = None
            try:
                pct = float(re.search(r"[\d.]+", pct_cell).group())
                if 0 < pct <= 100:
                    confidence = pct
            except Exception:
                pass

            return RawTip(
                home_team=home,
                away_team=away,
                prediction=pred_str,
                market=market,
                confidence=confidence,
                tipster="SoccerVista",
                source_name=self.SOURCE_NAME,
            )
        except Exception:
            return None


# ============================================================
# WINDRAWWIN
# ============================================================

class WindrawwinScraper(BaseScraper):
    SOURCE_TYPE = "web"
    SOURCE_NAME = "Windrawwin"
    SOURCE_IDENTIFIER = "windrawwin.com"

    BASE_URL = "https://windrawwin.com/predictions/today/"

    async def scrape(self) -> List[RawTip]:
        tips = []
        try:
            html = await self.fetch(self.BASE_URL)
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("table.wdw-table tr, .match-row")

            for row in rows:
                tip = self._parse_row(row)
                if tip:
                    tips.append(tip)
        except Exception as e:
            logger.error(f"[Windrawwin] error: {e}")

        return tips

    def _parse_row(self, row) -> Optional[RawTip]:
        try:
            cells = row.find_all("td")
            if len(cells) < 3:
                return None

            teams_text = cells[0].get_text(separator=" vs ").strip()
            if " vs " not in teams_text:
                return None

            home, away = [t.strip() for t in teams_text.split(" vs ", 1)]
            pred_text = cells[-2].get_text(strip=True)

            if not pred_text or len(pred_text) > 10:
                return None

            market_map = {
                "1": "1", "X": "X", "2": "2",
                "1X": "1X", "X2": "X2", "12": "12",
            }
            market = market_map.get(pred_text.upper())
            if not market:
                return None

            return RawTip(
                home_team=home,
                away_team=away,
                prediction=pred_text.upper(),
                market=market,
                tipster="Windrawwin",
                source_name=self.SOURCE_NAME,
            )
        except Exception:
            return None
