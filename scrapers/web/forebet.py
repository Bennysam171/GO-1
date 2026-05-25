"""
TipFusion AI — Forebet Web Scraper
Scrapes mathematical football predictions from forebet.com
"""

import re
from datetime import datetime
from typing import List, Optional
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawTip
from core.logger import logger

FOREBET_URL = "https://www.forebet.com/en/football-predictions"


class ForebetScraper(BaseScraper):
    SOURCE_TYPE = "web"
    SOURCE_NAME = "Forebet"
    SOURCE_IDENTIFIER = "forebet.com"

    async def scrape(self) -> List[RawTip]:
        tips = []
        try:
            html = await self.fetch(FOREBET_URL)
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("div.rcnt")

            for row in rows:
                tip = self._parse_row(row)
                if tip:
                    tips.append(tip)

        except Exception as e:
            logger.error(f"[Forebet] scrape error: {e}")

        return tips

    def _parse_row(self, row) -> Optional[RawTip]:
        try:
            # Teams
            home_el = row.select_one(".homeTeam span") or row.select_one(".ht")
            away_el = row.select_one(".awayTeam span") or row.select_one(".at")
            if not home_el or not away_el:
                return None

            home = home_el.get_text(strip=True)
            away = away_el.get_text(strip=True)

            if not home or not away:
                return None

            # League
            league_el = row.select_one(".lgnm") or row.select_one(".league")
            league = league_el.get_text(strip=True) if league_el else None

            # Prediction (1, X, 2)
            pred_el = row.select_one(".forepr") or row.select_one(".prh")
            if not pred_el:
                return None

            pred_text = pred_el.get_text(strip=True)

            # Map to market
            market_map = {"1": "1", "x": "X", "2": "2",
                          "1x": "1X", "x2": "X2", "12": "12"}
            market_key = market_map.get(pred_text.lower())
            if not market_key:
                return None

            pred_labels = {"1": f"Home Win ({home})", "X": "Draw",
                           "2": f"Away Win ({away})", "1X": "1X",
                           "X2": "X2", "12": "12"}
            pred_str = pred_labels.get(market_key, pred_text)

            # Probability
            prob_el = row.select_one(".fpr") or row.select_one(".prob")
            confidence = None
            if prob_el:
                prob_text = prob_el.get_text(strip=True).replace("%", "")
                try:
                    confidence = float(prob_text)
                except ValueError:
                    pass

            # Score prediction (goals hint for over/under)
            score_el = row.select_one(".score_ex") or row.select_one(".ex")
            over_tip = None
            if score_el:
                score_text = score_el.get_text(strip=True)
                match = re.match(r"(\d+)[:\-](\d+)", score_text)
                if match:
                    total = int(match.group(1)) + int(match.group(2))
                    if total >= 3:
                        over_tip = RawTip(
                            home_team=home, away_team=away,
                            prediction="Over 2.5",
                            market="over_2.5",
                            confidence=65.0,
                            league=league,
                            tipster="Forebet Algorithm",
                            source_name=self.SOURCE_NAME,
                        )

            # Kickoff time
            time_el = row.select_one(".kstime") or row.select_one(".mtime")
            kickoff = None
            if time_el:
                try:
                    time_str = time_el.get_text(strip=True)
                    kickoff = datetime.strptime(time_str, "%d/%m/%Y %H:%M")
                except Exception:
                    pass

            return RawTip(
                home_team=home,
                away_team=away,
                prediction=pred_str,
                market=market_key,
                confidence=confidence,
                league=league,
                kickoff=kickoff,
                tipster="Forebet Algorithm",
                source_name=self.SOURCE_NAME,
            )

        except Exception as e:
            logger.debug(f"[Forebet] row parse error: {e}")
            return None
