"""Logging utilities for the Costco Coupon Agent."""

import logging
import sys
from pathlib import Path


class SafeFormatter(logging.Formatter):
    """Formatter that handles format string errors gracefully."""
    def format(self, record):
        try:
            return super().format(record)
        except (ValueError, KeyError):
            # Fallback to simple formatting if there are issues
            msg = record.getMessage()
            return f"{record.levelname} - {record.name}: {msg}"


def setup_logger(name: str, log_file: Path, level: int = logging.INFO) -> logging.Logger:
    """Set up a logger that writes to both console and file."""
    logger = logging.getLogger(name)
    if logger.handlers:  # Already configured
        return logger

    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        SafeFormatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)",
                      datefmt="%H:%M:%S")
    )
    logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(
        SafeFormatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)")
    )
    logger.addHandler(file_handler)

    return logger


def get_logger(name: str, log_file: Path) -> logging.Logger:
    """Get or create a named logger."""
    logger = logging.getLogger(f"costco_agent.{name}")
    if not logger.handlers:
        setup_logger("costco_agent", log_file)
    return logger
