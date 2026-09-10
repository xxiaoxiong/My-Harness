"""Educational Agent Harness package through the completed HARN-01 stage.

The public API currently contains foundational services and a model adapter;
agent behavior is introduced in later stages.
"""

from harness.core import (
    Environment,
    HarnessConfig,
    LogLevel,
    configure_logging,
    get_logger,
    load_config,
)
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OpenAICompatibleProvider,
    TokenUsage,
)

__all__ = [
    "Environment",
    "HarnessConfig",
    "LogLevel",
    "MessageRole",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "OpenAICompatibleProvider",
    "TokenUsage",
    "configure_logging",
    "get_logger",
    "load_config",
]
