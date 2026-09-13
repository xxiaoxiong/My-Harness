"""A small, application-owned logging boundary."""

from __future__ import annotations

import logging
import sys
import time
from typing import TextIO

from harness.core.config import HarnessConfig

LOGGER_NAME = "harness"
CONSOLE_HANDLER_NAME = "harness.console"
LOG_FORMAT = "%(asctime)sZ %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime


def configure_logging(
    config: HarnessConfig,
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the top-level harness logger.

    Repeated calls replace only the console handler owned by this module, so
    setup is idempotent without deleting handlers installed by an application.
    """

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(config.log_level.value)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if handler.get_name() == CONSOLE_HANDLER_NAME:
            logger.removeHandler(handler)
            handler.close()

    console_handler = logging.StreamHandler(sys.stderr if stream is None else stream)
    console_handler.set_name(CONSOLE_HANDLER_NAME)
    console_handler.setLevel(config.log_level.value)
    console_handler.setFormatter(_UtcFormatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(console_handler)
    return logger


def get_logger(component: str | None = None) -> logging.Logger:
    """Return the harness logger or a namespaced child logger."""

    if component is None:
        return logging.getLogger(LOGGER_NAME)
    normalized = component.strip(".")
    if not normalized:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{normalized}")
