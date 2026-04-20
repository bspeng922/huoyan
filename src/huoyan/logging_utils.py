from __future__ import annotations

import logging
import os


DEFAULT_LOG_LEVEL = "INFO"
LOGGER_NAMESPACE = "huoyan"

_LOGGING_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    global _LOGGING_CONFIGURED

    resolved_level = (level or os.getenv("HUOYAN_LOG_LEVEL") or DEFAULT_LOG_LEVEL).upper()
    numeric_level = getattr(logging, resolved_level, logging.INFO)

    root_logger = logging.getLogger()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not _LOGGING_CONFIGURED:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.handlers.clear()
        root_logger.addHandler(handler)
        _LOGGING_CONFIGURED = True
    else:
        for handler in root_logger.handlers:
            handler.setFormatter(formatter)

    root_logger.setLevel(numeric_level)
    logging.getLogger(LOGGER_NAMESPACE).setLevel(numeric_level)


def get_logger(name: str) -> logging.Logger:
    if name.startswith(LOGGER_NAMESPACE):
        return logging.getLogger(name)
    return logging.getLogger(f"{LOGGER_NAMESPACE}.{name}")
