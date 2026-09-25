"""Bounded JSON error storage. Never pass raw exceptions or request bodies here."""
import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("scraper.errors")
logger.setLevel(logging.ERROR)
logger.propagate = False


def close():
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()


def configure():
    close()
    handlers = [logging.StreamHandler()]
    if filename := os.getenv("ERROR_LOG_FILE"):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)


def write(**fields):
    logger.error(json.dumps({**fields, "timestamp": datetime.now(timezone.utc).isoformat(),
                            "severity": "ERROR", "service": os.getenv("LOG_SERVICE", "scraper-api"),
                            "revision": os.getenv("CONTAINER_APP_REVISION", "local")}, ensure_ascii=False))
