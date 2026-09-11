"""The HARN-03 executor, intentionally hard-coded to one tool."""

from __future__ import annotations

from harness.tools.calculator import CALCULATOR_NAME, CalculatorError, calculate
from harness.tools.types import ToolCall, ToolResult


def execute_tool_call(call: ToolCall) -> ToolResult:
    """Execute the calculator or return an observable tool error."""

    if call.name != CALCULATOR_NAME:
        return ToolResult(
            name=call.name,
            arguments=call.arguments,
            error=f"unsupported tool: {call.name}",
        )

    if set(call.arguments) != {"expression"}:
        return ToolResult(
            name=call.name,
            arguments=call.arguments,
            error="calculator arguments must contain only 'expression'",
        )

    expression = call.arguments["expression"]
    if not isinstance(expression, str):
        return ToolResult(
            name=call.name,
            arguments=call.arguments,
            error="calculator argument 'expression' must be a string",
        )

    try:
        result = calculate(expression)
    except CalculatorError as error:
        return ToolResult(
            name=call.name,
            arguments=call.arguments,
            error=str(error),
        )

    return ToolResult(
        name=call.name,
        arguments=call.arguments,
        result=result,
    )
