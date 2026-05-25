"""
TipFusion AI — Main Entry Point
"""

import asyncio
import sys
import uvicorn
import typer
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from core.config import settings
from core.logger import logger

app = typer.Typer(name="tipfusion", help="TipFusion AI Ultimate")
console = Console()


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Host to bind"),
    port: int = typer.Option(8000, help="Port to bind"),
    workers: int = typer.Option(1, help="Number of workers"),
):
    """Start the TipFusion AI server (API + Scheduler + Bot)."""
    rprint("[bold green]Starting TipFusion AI Ultimate...[/bold green]")
    rprint(f"  API:   http://{host}:{port}")
    rprint(f"  Docs:  http://{host}:{port}/docs")
    uvicorn.run(
        "api.app:app",
        host=host,
        port=port,
        workers=workers,
        log_level=settings.log_level.lower(),
    )


@app.command()
def scrape(
    dry_run: bool = typer.Option(False, "--dry-run/--no-dry-run", help="Analyze but don't post"),
):
    """Run one scraping + analysis cycle immediately."""
    async def _run():
        from database.connection import init_db
        from core.orchestrator import Orchestrator
        from telegram_bot.bot import TipFusionBot

        await init_db()
        broadcast = not dry_run
        bot = None
        if broadcast:
            bot = TipFusionBot()
            await bot.init()

        orch = Orchestrator(bot=bot)
        await orch.run_full_pipeline(broadcast=broadcast)

        if bot:
            await bot.stop()

    mode = "[yellow]DRY RUN[/yellow]" if dry_run else "[green]LIVE[/green]"
    rprint(f"[bold cyan]Running scrape — mode: {mode}[/bold cyan]")
    asyncio.run(_run())


@app.command()
def discover():
    """Run source discovery crawler."""
    async def _run():
        from discovery.crawler import SourceDiscoverer
        d = SourceDiscoverer()
        await d.run()

    rprint("[bold cyan]Running source discovery...[/bold cyan]")
    asyncio.run(_run())


@app.command()
def post():
    """Post all pending predictions to Telegram."""
    async def _run():
        from database.connection import init_db, AsyncSessionLocal
        from database import crud
        from telegram_bot.bot import TipFusionBot

        await init_db()
        bot = TipFusionBot()
        await bot.init()

        async with AsyncSessionLocal() as db:
            preds = await crud.get_high_confidence_predictions(db, unposted_only=True)

        if not preds:
            rprint("[yellow]No unposted predictions found.[/yellow]")
        else:
            rprint(f"[green]Posting {len(preds)} predictions...[/green]")
            await bot.broadcast_predictions(preds)

        await bot.stop()

    rprint("[bold cyan]Posting pending predictions...[/bold cyan]")
    asyncio.run(_run())


@app.command()
def stats():
    """Display system statistics."""
    async def _run():
        from database.connection import init_db, AsyncSessionLocal
        from database import crud

        await init_db()
        async with AsyncSessionLocal() as db:
            s = await crud.get_overall_stats(db)
            sources = await crud.get_active_sources(db)

        t = Table(title="TipFusion AI — Stats", border_style="cyan")
        t.add_column("Metric", style="bold")
        t.add_column("Value", style="green")
        t.add_row("Active Sources", str(s["active_sources"]))
        t.add_row("Total Sources", str(s["total_sources"]))
        t.add_row("Tips Scraped", f"{s['total_tips_scraped']:,}")
        t.add_row("Total Predictions", str(s["total_predictions"]))
        t.add_row("Won", str(s["won"]))
        t.add_row("Lost", str(s["lost"]))
        t.add_row("Hit Rate", f"{s['hit_rate']}%")
        console.print(t)

    asyncio.run(_run())


@app.command()
def initdb():
    """Initialize the database (create tables)."""
    async def _run():
        from database.connection import init_db
        await init_db()
        rprint("[green]Database initialized[/green]")

    asyncio.run(_run())


if __name__ == "__main__":
    app()


@app.command()
def toggle(
    category: str = typer.Argument(help="Category: apis / web / social / android"),
    name: str     = typer.Argument(help="Scraper name e.g. telegram, twitter, forebet"),
    action: str   = typer.Argument(help="on or off"),
):
    """
    Turn a scraper ON or OFF.

    Examples:
      python main.py toggle social telegram off
      python main.py toggle social twitter on
      python main.py toggle web forebet on
      python main.py toggle apis odds_api off
      python main.py toggle android bluestacks on
    """
    from core.scraper_config import set_enabled, get_all_statuses

    if action.lower() not in ("on", "off"):
        rprint(f"[red]Action must be 'on' or 'off', got: {action}[/red]")
        raise typer.Exit(1)

    enabled = action.lower() == "on"
    set_enabled(category, name, enabled)

    symbol = "✅" if enabled else "⏸"
    color  = "green" if enabled else "yellow"
    rprint(f"[{color}]{symbol} [{category}] {name} → {'ENABLED' if enabled else 'DISABLED'}[/{color}]")
    rprint(f"[dim]Takes effect on next scrape cycle[/dim]")


@app.command()
def sources():
    """Show current status of all scrapers (on/off)."""
    from core.scraper_config import get_all_statuses
    from rich.table import Table

    cfg = get_all_statuses()
    t = Table(title="Scraper Status", border_style="cyan")
    t.add_column("Category", style="bold cyan")
    t.add_column("Scraper",  style="bold")
    t.add_column("Status",   style="bold")

    for cat, scrapers in cfg.items():
        if cat.startswith("_"):
            continue
        for name, enabled in scrapers.items():
            status = "[green]✅  ON[/green]" if enabled else "[yellow]⏸  OFF[/yellow]"
            t.add_row(cat, name, status)

    console.print(t)
