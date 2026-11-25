import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from typing import Dict

from src.config import LOGS_DIR

_FILE_HANDLERS: Dict[str, logging.Handler] = {}
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3


def _get_shared_file_handler(log_path: Path, level: int) -> logging.Handler:
    """Return a shared RotatingFileHandler instance per log file path."""
    key = str(log_path.resolve())
    handler = _FILE_HANDLERS.get(key)
    if handler is None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT)
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        ))
        _FILE_HANDLERS[key] = handler
    return handler


def _stdout_redirects_to(log_path: Path) -> bool:
    """Detect if stdout is already redirected to log_path (e.g. LaunchAgent StandardOutPath)."""
    if not log_path.exists():
        return False
    try:
        return os.path.samefile(sys.stdout.fileno(), log_path)
    except Exception:
        return False


def setup_logger(name: str, log_file: str = None, level=logging.INFO):
    """
    Set up a logger with console and optional rotating file output.
    Avoids duplicate console writes when stdout already redirects to the log file.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    log_path = Path(LOGS_DIR) / log_file if log_file else None
    if log_path is not None:
        logger.addHandler(_get_shared_file_handler(log_path, level))

    if log_path is None or not _stdout_redirects_to(log_path):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console_handler)

    return logger


def get_logger(name: str):
    """Get an existing logger or create one backed by the shared rotating file handler."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = Path(LOGS_DIR) / 'videoserver.log'
    logger.addHandler(_get_shared_file_handler(log_path, logging.INFO))
    return logger
