import unittest
from collections.abc import Mapping

from harness.core.types import JsonValue
from harness.tools import (
    CalculatorTool,
    DuplicateToolError,
    Tool,
    ToolCall,
    ToolExecutor,
    ToolNotFoundError,
    ToolRegistry,
    ToolSchema,
)


class EchoTool(Tool):
    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="echo",
            description="Return supplied text.",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        return arguments["text"]


class ToolRegistryTests(unittest.TestCase):
    def test_register_get_and_list_tools_preserve_identity_and_order(self) -> None:
        registry = ToolRegistry()
        calculator = CalculatorTool()
        echo = EchoTool()

        registry.register(calculator)
        registry.register(echo)

        self.assertIs(registry.get("calculator"), calculator)
        self.assertIs(registry.get(" echo "), echo)
        self.assertEqual(registry.list_tools(), (calculator, echo))

    def test_duplicate_names_are_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())

        with self.assertRaisesRegex(DuplicateToolError, "already registered"):
            registry.register(EchoTool())

    def test_missing_name_has_a_distinct_lookup_error(self) -> None:
        registry = ToolRegistry()

        with self.assertRaisesRegex(ToolNotFoundError, "tool not found"):
            registry.get("missing")


class ToolExecutorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        self.registry.register(CalculatorTool())
        self.registry.register(EchoTool())
        self.executor = ToolExecutor(self.registry)

    async def test_executes_different_implementations_through_one_boundary(self) -> None:
        calculator_result = await self.executor.execute(
            ToolCall("calculator", {"expression": "6 * 7"})
        )
        echo_result = await self.executor.execute(
            ToolCall("echo", {"text": "hello"})
        )

        self.assertEqual(calculator_result.result, 42)
        self.assertTrue(calculator_result.succeeded)
        self.assertEqual(echo_result.result, "hello")

    async def test_expected_tool_error_becomes_a_tool_result(self) -> None:
        result = await self.executor.execute(
            ToolCall("calculator", {"expression": "1 / 0"})
        )

        self.assertIsNone(result.result)
        self.assertEqual(result.error, "division by zero")
        self.assertFalse(result.succeeded)

    async def test_unknown_tool_becomes_a_tool_result(self) -> None:
        result = await self.executor.execute(
            ToolCall("weather", {"city": "Shanghai"})
        )

        self.assertEqual(result.name, "weather")
        self.assertEqual(result.arguments, {"city": "Shanghai"})
        self.assertEqual(result.error, "tool not found: weather")

    async def test_lists_schemas_without_exposing_executor_dispatch_logic(self) -> None:
        schemas = self.executor.list_schemas()

        self.assertEqual([schema.name for schema in schemas], ["calculator", "echo"])
