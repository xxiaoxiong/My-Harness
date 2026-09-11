"""Educational Agent Harness package through the completed HARN-02 stage.

The public API contains foundational services, a model adapter, and the first
bounded agent loop.
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
from harness.runtime import (
    AgentDecision,
    AgentLoop,
    AgentRunResult,
    AgentRunStatus,
    InvalidAgentDecision,
)

__all__ = [
    "AgentDecision",
    "AgentLoop",
    "AgentRunResult",
    "AgentRunStatus",
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
    "InvalidAgentDecision",
    "load_config",
]
