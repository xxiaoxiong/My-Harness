"""Educational Agent Harness package through the completed HARN-03 stage.

The public API now includes the first hard-coded tool feedback loop.
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
    InvalidAgentAction,
    InvalidAgentDecision,
    ToolAgentLoop,
    ToolAgentRunResult,
)
from harness.tools import (
    CALCULATOR_NAME,
    CalculatorError,
    ToolCall,
    ToolResult,
    calculate,
    execute_tool_call,
)

__all__ = [
    "AgentDecision",
    "AgentLoop",
    "AgentRunResult",
    "AgentRunStatus",
    "CALCULATOR_NAME",
    "CalculatorError",
    "Environment",
    "HarnessConfig",
    "InvalidAgentAction",
    "InvalidAgentDecision",
    "LogLevel",
    "MessageRole",
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "OpenAICompatibleProvider",
    "TokenUsage",
    "ToolAgentLoop",
    "ToolAgentRunResult",
    "ToolCall",
    "ToolResult",
    "calculate",
    "configure_logging",
    "execute_tool_call",
    "get_logger",
    "load_config",
]
