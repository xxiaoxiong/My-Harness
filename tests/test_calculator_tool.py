import unittest

from harness.tools import (
    CalculatorError,
    ToolCall,
    calculate,
    execute_tool_call,
)


class CalculatorTests(unittest.TestCase):
    def test_calculates_precedence_parentheses_and_unary_values(self) -> None:
        self.assertEqual(calculate("2 + 3 * 4"), 14)
        self.assertEqual(calculate("-(2 + 3) * 4"), -20)
        self.assertEqual(calculate("7 / 2"), 3.5)

    def test_rejects_code_and_unsupported_operators(self) -> None:
        with self.assertRaisesRegex(CalculatorError, "unsupported"):
            calculate("__import__('os').getcwd()")
        with self.assertRaisesRegex(CalculatorError, r"only \+"):
            calculate("2 ** 8")

    def test_reports_division_by_zero_as_a_calculator_error(self) -> None:
        with self.assertRaisesRegex(CalculatorError, "division by zero"):
            calculate("1 / 0")


class HardcodedExecutorTests(unittest.TestCase):
    def test_preserves_name_arguments_and_successful_result(self) -> None:
        call = ToolCall("calculator", {"expression": "10 + 5"})

        result = execute_tool_call(call)

        self.assertEqual(result.name, "calculator")
        self.assertEqual(result.arguments, {"expression": "10 + 5"})
        self.assertEqual(result.result, 15)
        self.assertIsNone(result.error)
        self.assertTrue(result.succeeded)

    def test_converts_invalid_arguments_into_an_observable_error(self) -> None:
        result = execute_tool_call(ToolCall("calculator", {"value": 2}))

        self.assertIsNone(result.result)
        self.assertIn("expression", result.error or "")
        self.assertFalse(result.succeeded)

    def test_unknown_tool_is_an_error_without_a_registry(self) -> None:
        result = execute_tool_call(ToolCall("weather", {"city": "Shanghai"}))

        self.assertEqual(result.name, "weather")
        self.assertEqual(result.arguments, {"city": "Shanghai"})
        self.assertEqual(result.error, "unsupported tool: weather")


if __name__ == "__main__":
    unittest.main()
