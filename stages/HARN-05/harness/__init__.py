"""Educational Agent Harness package through the completed HARN-05 stage.

The public API now tracks each tool-agent run as state plus trajectory.
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
from harness.state import (
    AgentState,
    AgentStatus,
    TrajectoryEvent,
    TrajectoryEventKind,
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
    CalculatorTool,
    DuplicateToolError,
    Tool,
    ToolCall,
    ToolError,
    ToolExecutor,
    ToolNotFoundError,
    ToolRegistry,
    ToolResult,
    ToolSchema,
    calculate,
)

__all__ = [
    "AgentDecision",
    "AgentLoop",
    "AgentRunResult",
    "AgentRunStatus",
    "AgentState",
    "AgentStatus",
    "CALCULATOR_NAME",
    "CalculatorError",
    "CalculatorTool",
    "DuplicateToolError",
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
    "Tool",
    "ToolAgentLoop",
    "ToolAgentRunResult",
    "ToolCall",
    "ToolError",
    "ToolExecutor",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSchema",
    "TrajectoryEvent",
    "TrajectoryEventKind",
    "calculate",
    "configure_logging",
    "get_logger",
    "load_config",
]
