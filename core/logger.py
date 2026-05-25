"""
TipFusion AI — Logging System
Uses Loguru for structured, colorized, async-safe logging.
"""

import sys
from pathlib import Path
from loguru import logger
from core.config import settings

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def setup_logger():
    """Configure Loguru with console + rotating file handlers."""
    logger.remove()  # Remove default handler

    # Console — colorized
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{line}</cyan> — "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # File — full debug log, rotating daily
    logger.add(
        LOG_DIR / "tipfusion_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",         # New file each midnight
        retention="30 days",
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} — {message}",
    )

    # Errors only — separate file
    logger.add(
        LOG_DIR / "errors.log",
        level="ERROR",
        rotation="10 MB",
        retention="90 days",
        compression="zip",
    )

    return logger


# Initialize on import
setup_logger()

__all__ = ["logger"]
