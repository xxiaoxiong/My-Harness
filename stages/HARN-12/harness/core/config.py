"""Environment-driven configuration for the harness process."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from harness.core.types import Environment, LogLevel

ENVIRONMENT_VARIABLE = "HARNESS_ENV"
LOG_LEVEL_VARIABLE = "HARNESS_LOG_LEVEL"


class ConfigurationError(ValueError):
    """Raised when a harness environment variable has an invalid value."""


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Immutable process-level settings used by the current harness stage."""

    environment: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = LogLevel.INFO


def load_config(environ: Mapping[str, str] | None = None) -> HarnessConfig:
    """Build settings from an environment mapping.

    Supplying a mapping makes configuration deterministic in tests. When no
    mapping is provided, the current process environment is read.
    """

    source = os.environ if environ is None else environ
    environment = _parse_environment(
        source.get(ENVIRONMENT_VARIABLE, Environment.DEVELOPMENT.value)
    )
    log_level = _parse_log_level(
        source.get(LOG_LEVEL_VARIABLE, LogLevel.INFO.value)
    )
    return HarnessConfig(environment=environment, log_level=log_level)


def _parse_environment(raw_value: str) -> Environment:
    normalized = raw_value.strip().lower()
    try:
        return Environment(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in Environment)
        raise ConfigurationError(
            f"{ENVIRONMENT_VARIABLE} must be one of: {allowed}; got {raw_value!r}"
        ) from exc


def _parse_log_level(raw_value: str) -> LogLevel:
    normalized = raw_value.strip().upper()
    try:
        return LogLevel(normalized)
    except ValueError as exc:
        allowed = ", ".join(item.value for item in LogLevel)
        raise ConfigurationError(
            f"{LOG_LEVEL_VARIABLE} must be one of: {allowed}; got {raw_value!r}"
        ) from exc

