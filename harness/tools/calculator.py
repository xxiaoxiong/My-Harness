"""A deliberately small and safe arithmetic tool."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable

CALCULATOR_NAME = "calculator"
MAX_EXPRESSION_CHARS = 200
MAX_SYNTAX_NODES = 64

Number = int | float
BinaryOperator = Callable[[Number, Number], Number]
UnaryOperator = Callable[[Number], Number]

_BINARY_OPERATORS: dict[type[ast.operator], BinaryOperator] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], UnaryOperator] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculatorError(ValueError):
    """Raised when an expression is invalid or cannot be calculated."""


def calculate(expression: str) -> Number:
    """Evaluate numbers, parentheses, and the four basic operators."""

    if not isinstance(expression, str):
        raise CalculatorError("expression must be a string")
    normalized_expression = expression.strip()
    if not normalized_expression:
        raise CalculatorError("expression must not be empty")
    if len(normalized_expression) > MAX_EXPRESSION_CHARS:
        raise CalculatorError(
            f"expression must not exceed {MAX_EXPRESSION_CHARS} characters"
        )

    try:
        tree = ast.parse(normalized_expression, mode="eval")
    except (SyntaxError, ValueError) as error:
        raise CalculatorError("expression is not valid arithmetic") from error

    if sum(1 for _ in ast.walk(tree)) > MAX_SYNTAX_NODES:
        raise CalculatorError("expression is too complex")

    try:
        result = _evaluate(tree.body)
    except ZeroDivisionError as error:
        raise CalculatorError("division by zero") from error

    if isinstance(result, float) and not math.isfinite(result):
        raise CalculatorError("result must be finite")
    return result


def _evaluate(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value

    if isinstance(node, ast.BinOp):
        operation = _BINARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise CalculatorError("only +, -, *, and / are supported")
        return operation(_evaluate(node.left), _evaluate(node.right))

    if isinstance(node, ast.UnaryOp):
        operation = _UNARY_OPERATORS.get(type(node.op))
        if operation is None:
            raise CalculatorError("only unary + and - are supported")
        return operation(_evaluate(node.operand))

    raise CalculatorError("expression contains unsupported syntax")
