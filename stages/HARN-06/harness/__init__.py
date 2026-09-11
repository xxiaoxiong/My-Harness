"""Educational Agent Harness package through the completed HARN-06 stage.

The public API now builds bounded model context outside the Agent Loop.
"""

from harness.core import (
    Environment,
    HarnessConfig,
    LogLevel,
    configure_logging,
    get_logger,
    load_config,
)
from harness.context import (
    DEFAULT_TOOL_AGENT_SYSTEM_PROMPT,
    ContextBuilder,
    ContextBuildResult,
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
    "ContextBuildResult",
    "ContextBuilder",
    "DEFAULT_TOOL_AGENT_SYSTEM_PROMPT",
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
