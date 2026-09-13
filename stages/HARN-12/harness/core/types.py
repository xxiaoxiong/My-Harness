"""Small dependency-free types shared by the harness foundation."""

from enum import Enum, unique
from typing import Mapping, TypeAlias


JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
Metadata: TypeAlias = Mapping[str, JsonValue]


@unique
class Environment(str, Enum):
    """Supported process environments."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@unique
class LogLevel(str, Enum):
    """Log levels accepted by :class:`HarnessConfig`."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

