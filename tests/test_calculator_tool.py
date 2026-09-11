import unittest

from harness.tools import (
    CalculatorError,
    CalculatorTool,
    calculate,
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


class CalculatorToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_schema_describes_the_implementation_arguments(self) -> None:
        tool = CalculatorTool()

        self.assertEqual(tool.schema.name, "calculator")
        self.assertEqual(tool.schema.parameters["required"], ["expression"])

    async def test_execute_validates_arguments_then_calculates(self) -> None:
        tool = CalculatorTool()

        self.assertEqual(await tool.execute({"expression": "10 + 5"}), 15)
        with self.assertRaisesRegex(CalculatorError, "only 'expression'"):
            await tool.execute({"value": 2})
        with self.assertRaisesRegex(CalculatorError, "must be a string"):
            await tool.execute({"expression": 2})


if __name__ == "__main__":
    unittest.main()
