# TipFusion AI Ultimate 🚀
### Autonomous Football Intelligence & Prediction Platform

---

## What Is TipFusion AI?

TipFusion AI is a fully autonomous football intelligence system that:

- **Scrapes** tips from APIs, prediction websites, Telegram channels, Reddit, Twitter, and Android apps running inside BlueStacks
- **Analyzes** all collected predictions with a weighted consensus engine
- **Scores** every match from 0–100 based on source agreement, reliability, and confidence
- **Automatically posts** only high-confidence predictions to your Telegram channel
- **Continuously discovers** new sources from across the web and adds them automatically

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     TipFusion AI System                      │
├──────────────┬──────────────┬──────────────┬────────────────┤
│  V1: APIs    │  V2: Social  │  V3: Android │  V7: Discovery │
│  • API-Football│ • Telegram │  • BlueStacks│  • Web crawler │
│  • TheOddsAPI│  • Reddit   │  • ADB + OCR │  • Auto-find   │
│  • FD.org    │  • Twitter  │  • Appium    │  • New channels│
├──────────────┴──────────────┴──────────────┴────────────────┤
│                   V4: Analysis Engine                        │
│   Normalize → Group → Score → Consensus → Confidence 0-100  │
├────────────────────────────────────────────────────────────┤
│              V5: Source Reliability AI                       │
│   Track wins/losses per source → Weight adjustments         │
├────────────────────────────────────────────────────────────┤
│                V6: Telegram Delivery Bot                     │
│   Format cards → Post → /today /top /value /stats           │
├────────────────────────────────────────────────────────────┤
│                V8: Database (PostgreSQL/SQLite)              │
│   matches | tips | sources | fusion_predictions | logs       │
└────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
tipfusion/
│
├── core/
│   ├── config.py          # Pydantic settings (from .env)
│   ├── logger.py          # Loguru logging setup
│   ├── orchestrator.py    # Main pipeline controller
│   └── scheduler.py       # APScheduler job runner
│
├── database/
│   ├── models.py          # SQLAlchemy ORM models
│   ├── connection.py      # Async engine + sessions
│   └── crud.py            # All DB read/write operations
│
├── scrapers/
│   ├── base.py            # BaseScraper + RawTip dataclass
│   ├── apis/
│   │   ├── api_football.py    # API-Football.com
│   │   ├── odds_api.py        # TheOddsAPI
│   │   └── football_data.py   # Football-Data.org
│   ├── web/
│   │   ├── forebet.py         # Forebet.com
│   │   └── prediction_sites.py # Predictz, SoccerVista, Windrawwin
│   └── social/
│       ├── telegram_scraper.py # Telethon channel scraper
│       └── social_scrapers.py  # Reddit + Twitter/X
│
├── android/
│   └── bluestacks.py      # BlueStacks + ADB + OCR engine
│
├── analyzer/
│   └── engine.py          # Weighted consensus analysis
│
├── telegram_bot/
│   └── bot.py             # Bot delivery + commands
│
├── discovery/
│   └── crawler.py         # Auto source discovery
│
├── api/
│   └── app.py             # FastAPI REST API
│
├── tests/
│   └── test_core.py       # Full test suite (pytest)
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── deploy/
│   └── setup_vps.sh       # Ubuntu VPS one-click deploy
│
├── main.py                # CLI entry point
├── requirements.txt
└── .env.example
```

---

## Quick Start (Local)

### 1. Clone and set up environment

```bash
git clone https://github.com/youruser/tipfusion.git
cd tipfusion
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure your .env

```bash
cp .env.example .env
nano .env
```

**Minimum required** to get started:
```env
# At least one of these API keys:
API_FOOTBALL_KEY=your_key
ODDS_API_KEY=your_key

# Telegram bot token (get from @BotFather):
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHANNEL_ID=-100your_channel_id
```

### 3. Initialize database

```bash
python main.py initdb
```

### 4. Run your first scrape

```bash
python main.py scrape --no-broadcast   # Test without posting
python main.py scrape                  # Scrape + post to Telegram
```

### 5. Start the full server

```bash
python main.py serve
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

---

## Docker Deployment

```bash
# Copy and edit your env
cp .env.example .env
nano .env

# Build and start
cd docker
docker compose up -d

# Check logs
docker compose logs -f tipfusion
```

Services started:
- `tipfusion` — Main app on port 8000
- `postgres` — PostgreSQL database
- `redis` — Redis cache
- `adminer` — DB admin UI on port 8080

---

## VPS Deployment (Ubuntu 22.04)

```bash
# Upload your files to /opt/tipfusion
scp -r . user@your-vps:/opt/tipfusion

# Run setup script
ssh user@your-vps
cd /opt/tipfusion
sudo bash deploy/setup_vps.sh

# Edit your .env with real API keys
nano /opt/tipfusion/.env

# Restart
supervisorctl restart tipfusion
```

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `python main.py serve` | Start API + scheduler + bot |
| `python main.py scrape` | One-shot: scrape + analyze + post |
| `python main.py scrape --dry-run` | Scrape + analyze but don't post |
| `python main.py discover` | Find new sources |
| `python main.py post` | Post pending predictions |
| `python main.py stats` | Print stats table |
| `python main.py initdb` | Create database tables |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | System info |
| GET | `/health` | Health check |
| GET | `/predictions` | All predictions (filterable) |
| GET | `/predictions/today` | Today's picks |
| GET | `/predictions/top` | Top 5 by confidence |
| GET | `/sources` | All sources |
| GET | `/sources/{id}/stats` | Source reliability stats |
| POST | `/sources/{id}/toggle` | Enable/disable source |
| GET | `/stats` | Overall system stats |
| GET | `/stats/scrape-logs` | Recent scrape history |
| POST | `/control/run-pipeline` | Trigger manual run |
| POST | `/control/broadcast` | Re-post unposted picks |
| GET | `/control/jobs` | Scheduler job status |

---

## Telegram Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message + help |
| `/today` | All of today's picks |
| `/top` | Top 5 highest confidence |
| `/value` | Value bets (high odds + high confidence) |
| `/stats` | Win rate + performance stats |

---

## BlueStacks / Android Setup

1. Install BlueStacks on your Windows machine
2. Install target prediction apps from Play Store:
   - TipsPro AI
   - AI Betting Tips
   - Betting Tips AI Prediction
3. Enable ADB in BlueStacks settings (port 5555)
4. Set in `.env`:
   ```env
   BLUESTACKS_PATH=C:\Program Files\BlueStacks_nxt\HD-Player.exe
   ADB_HOST=127.0.0.1
   ADB_PORT=5555
   ```
5. The system will auto-launch BlueStacks, open each app, scroll through predictions, OCR the screen, and extract tips

---

## Adding New Sources

### Add a new API scraper:
```python
# scrapers/apis/my_source.py
from scrapers.base import BaseScraper, RawTip

class MySourceScraper(BaseScraper):
    SOURCE_TYPE = "api"
    SOURCE_NAME = "MySource"
    SOURCE_IDENTIFIER = "mysource.com"

    async def scrape(self) -> list[RawTip]:
        data = await self.fetch_json("https://mysource.com/api/tips")
        return [RawTip(...) for item in data]
```

Then register it in `core/orchestrator.py` → `build_scraper_list()`.

### Add a Telegram channel:
```python
# In scrapers/social/telegram_scraper.py
DEFAULT_CHANNELS.append("your_channel_name")
```

Or via auto-discovery — the system will find channels automatically every 6 hours.

---

## Tuning the Analysis Engine

In `.env`:
```env
MIN_CONFIDENCE_SCORE=65    # Only post predictions above this %
MIN_SOURCES_REQUIRED=2     # Min sources that must agree
HIGH_VALUE_THRESHOLD=75    # "High confidence" threshold
```

Source weights are automatically adjusted based on historical win rates. A source with an 80% hit rate gets a 1.6x weight multiplier versus a source with a 50% hit rate (1.0x).

---

## Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## Environment Variables Reference

See `.env.example` for the full annotated list. Key categories:

- **APIs**: `API_FOOTBALL_KEY`, `ODDS_API_KEY`, `FOOTBALL_DATA_KEY`
- **Telegram**: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
- **Social**: `TWITTER_BEARER_TOKEN`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`
- **Android**: `BLUESTACKS_PATH`, `ADB_PORT`
- **Thresholds**: `MIN_CONFIDENCE_SCORE`, `MIN_SOURCES_REQUIRED`
- **Scheduling**: `SCRAPE_INTERVAL_MINUTES`, `DISCOVERY_INTERVAL_HOURS`
- **Anti-ban**: `MIN_REQUEST_DELAY`, `MAX_REQUEST_DELAY`, `ROTATE_USER_AGENTS`

---

## API Keys You Need

| Service | Free Tier | Get At |
|---------|-----------|--------|
| API-Football | 100 req/day | https://rapidapi.com/api-sports/api/api-football |
| TheOddsAPI | 500 req/month | https://the-odds-api.com |
| Football-Data.org | Free plan | https://football-data.org |
| Telegram Bot | Free | @BotFather on Telegram |
| Telegram API | Free | https://my.telegram.org |
| Reddit API | Free | https://reddit.com/prefs/apps |
| Twitter/X API | Free tier | https://developer.x.com |
