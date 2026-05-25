"""
TipFusion AI — Reddit & Twitter Scrapers
Reddit: Football betting subreddits
Twitter/X: Prediction accounts
"""

import re
import asyncio
from datetime import datetime, timedelta
from typing import List, Optional

from scrapers.base import RawTip
from core.config import settings
from core.logger import logger

# All Twitter/X handles from config.yaml
FOOTBALL_TIP_ACCOUNTS = [
    "PinchBet",
    "AndyRobsonTips",
    "WSTipster",
    "MarkOHaire",
    "BettingExpert",
    "FootyAccums",
    "Oddschecker",
    "SmartBettingClub",
    "TheRealTipster",
    "BettingTips4You",
    "CorrectScorePro",
    "VIPBettingTips",
    "FootballValue",
    "AccaTracker",
    "InfogolApp",
    "OddsJam",
    "RebelBetting",
    "TrademateSports",
    "FootballTipsHub",
    "AccaBet",
    "BetAdvisor",
    "TipsterHQ",
]

# All Reddit subreddits from config.yaml
FOOTBALL_SUBREDDITS = [
    "sportsbook",
    "soccerbetting",
    "sportsbetting",
    "betting",
    "gambling",
    "algobetting",
    "quantbetting",
    "sportsanalytics",
    "dailyfantasy",
    "NBA_Betting",
    "footballbetting",
    "valuebetting",
    "arbitrage",
    "soccer",
    "football",
    "tennis",
    "esportsbetting",
    "MMA",
    "rugbyunion",
    "cricketbetting",
    "horseracing",
]

# Regex patterns shared across parsers
MATCH_RE = re.compile(
    r"(?P<home>[\w\s']+?)\s+(?:vs?\.?|v\.?|[-–—])\s+(?P<away>[\w\s']+)",
    re.IGNORECASE
)
MARKET_RES = {
    "1": re.compile(r"\bhome\s*win\b|\b1(?![x2])\b", re.IGNORECASE),
    "X": re.compile(r"\bdraw\b|\btie\b", re.IGNORECASE),
    "2": re.compile(r"\baway\s*win\b|\b2(?![.x])\b", re.IGNORECASE),
    "over_2.5": re.compile(r"\bover\s*2\.5\b|\bo2\.5\b", re.IGNORECASE),
    "under_2.5": re.compile(r"\bunder\s*2\.5\b|\bu2\.5\b", re.IGNORECASE),
    "btts_yes": re.compile(r"\bbtts\s*yes\b|\bgg\b", re.IGNORECASE),
    "btts_no": re.compile(r"\bbtts\s*no\b|\bng\b", re.IGNORECASE),
}
PRED_LABELS = {
    "1": "Home Win", "X": "Draw", "2": "Away Win",
    "over_2.5": "Over 2.5", "under_2.5": "Under 2.5",
    "btts_yes": "BTTS Yes", "btts_no": "BTTS No",
}


def extract_tip_from_text(
    text: str, source_name: str, tipster: Optional[str] = None
) -> List[RawTip]:
    """Generic text tip extractor — shared by Reddit and Twitter parsers."""
    tips = []
    paragraphs = re.split(r"\n{2,}", text)

    for para in paragraphs:
        m = MATCH_RE.search(para)
        if not m:
            continue

        home = re.sub(r"[^\w\s'-]", "", m.group("home")).strip()
        away = re.sub(r"[^\w\s'-]", "", m.group("away")).strip()

        if not home or not away or len(home) < 3 or len(away) < 3:
            continue

        for market, pattern in MARKET_RES.items():
            if pattern.search(para):
                label = PRED_LABELS.get(market, market)
                if market == "1":
                    label = f"Home Win ({home})"
                elif market == "2":
                    label = f"Away Win ({away})"

                # Extract confidence %
                conf = None
                conf_m = re.search(r"(\d+)\s*%", para)
                if conf_m:
                    try:
                        conf = float(conf_m.group(1))
                    except Exception:
                        pass

                tips.append(RawTip(
                    home_team=home,
                    away_team=away,
                    prediction=label,
                    market=market,
                    confidence=conf,
                    tipster=tipster or source_name,
                    source_name=source_name,
                ))
                break

    return tips


# ============================================================
# REDDIT SCRAPER
# ============================================================

class RedditScraper:
    SOURCE_TYPE = "reddit"
    SOURCE_NAME = "Reddit"

    def __init__(self):
        self._subreddits = FOOTBALL_SUBREDDITS
        # Keywords from config.yaml reddit.filters.keywords
        self._keywords = {
            "over", "under", "btts", "odds", "prediction", "pick",
            "win", "accumulator", "acca", "value bet", "banker",
        }
        self._min_upvotes = 5
        self._max_posts_per_sub = 15

    def _init_reddit(self):
        try:
            import praw
            if not settings.reddit_client_id:
                return None
            return praw.Reddit(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
            )
        except Exception as e:
            logger.warning(f"[Reddit] Init failed: {e}")
            return None

    async def scrape(self) -> List[RawTip]:
        reddit = self._init_reddit()
        if not reddit:
            logger.warning("[Reddit] No credentials — skipping")
            return []

        all_tips = []
        cutoff = datetime.utcnow() - timedelta(hours=24)

        def _scrape_sync():
            tips = []
            for sub_name in self._subreddits:
                try:
                    subreddit = reddit.subreddit(sub_name)
                    for post in subreddit.new(limit=self._max_posts_per_sub):
                        post_time = datetime.utcfromtimestamp(post.created_utc)
                        if post_time < cutoff:
                            continue

                        # Apply upvote filter
                        if hasattr(post, "score") and post.score < self._min_upvotes:
                            continue

                        # Apply keyword filter — post must contain at least one keyword
                        combined_text = (post.title + " " + post.selftext).lower()
                        if not any(kw in combined_text for kw in self._keywords):
                            continue

                        text = f"{post.title}\n{post.selftext}"
                        extracted = extract_tip_from_text(
                            text,
                            source_name=f"Reddit/r/{sub_name}",
                            tipster=f"u/{post.author}" if post.author else None,
                        )
                        tips.extend(extracted)

                        # Also scan top comments
                        post.comments.replace_more(limit=0)
                        for comment in post.comments[:10]:
                            if hasattr(comment, "body"):
                                comment_tips = extract_tip_from_text(
                                    comment.body,
                                    source_name=f"Reddit/r/{sub_name}",
                                    tipster=f"u/{comment.author}" if comment.author else None,
                                )
                                tips.extend(comment_tips)

                except Exception as e:
                    logger.warning(f"[Reddit] {sub_name} error: {e}")
            return tips

        loop = asyncio.get_event_loop()
        all_tips = await loop.run_in_executor(None, _scrape_sync)
        return all_tips

    async def run(self) -> List[RawTip]:
        logger.info(f"🔍 [Reddit] Scanning {len(self._subreddits)} subreddits...")
        tips = await self.scrape()
        logger.success(f"✅ [Reddit] Found {len(tips)} tips")
        return tips


# ============================================================
# TWITTER/X SCRAPER
# ============================================================

class TwitterScraper:
    SOURCE_TYPE = "twitter"
    SOURCE_NAME = "Twitter/X"

    def __init__(self):
        self._accounts = FOOTBALL_TIP_ACCOUNTS

    def _init_client(self):
        try:
            import tweepy
            if not settings.twitter_bearer_token:
                return None
            return tweepy.Client(bearer_token=settings.twitter_bearer_token)
        except Exception as e:
            logger.warning(f"[Twitter] Init failed: {e}")
            return None

    async def scrape(self) -> List[RawTip]:
        client = self._init_client()
        if not client:
            logger.warning("[Twitter] No credentials — skipping")
            return []

        all_tips = []

        def _scrape_sync():
            tips = []
            for account in self._accounts:
                try:
                    # Get user ID
                    user = client.get_user(username=account)
                    if not user.data:
                        continue

                    user_id = user.data.id
                    tweets = client.get_users_tweets(
                        user_id,
                        max_results=20,
                        tweet_fields=["created_at", "text"],
                        exclude=["retweets", "replies"],
                    )

                    if not tweets.data:
                        continue

                    cutoff = datetime.utcnow() - timedelta(hours=24)
                    for tweet in tweets.data:
                        created = tweet.created_at
                        if created and created.replace(tzinfo=None) < cutoff:
                            continue

                        extracted = extract_tip_from_text(
                            tweet.text,
                            source_name=f"Twitter/@{account}",
                            tipster=f"@{account}",
                        )
                        tips.extend(extracted)

                except Exception as e:
                    logger.warning(f"[Twitter] @{account} error: {e}")
            return tips

        loop = asyncio.get_event_loop()
        all_tips = await loop.run_in_executor(None, _scrape_sync)
        return all_tips

    async def run(self) -> List[RawTip]:
        logger.info(f"🔍 [Twitter] Scanning {len(self._accounts)} accounts...")
        tips = await self.scrape()
        logger.success(f"✅ [Twitter] Found {len(tips)} tips")
        return tips
