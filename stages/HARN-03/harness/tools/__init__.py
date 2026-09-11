"""The first calculator tool and its hard-coded execution path."""

from harness.tools.calculator import (
    CALCULATOR_NAME,
    CalculatorError,
    calculate,
)
from harness.tools.hardcoded import execute_tool_call
from harness.tools.types import ToolCall, ToolResult

__all__ = [
    "CALCULATOR_NAME",
    "CalculatorError",
    "ToolCall",
    "ToolResult",
    "calculate",
    "execute_tool_call",
]
