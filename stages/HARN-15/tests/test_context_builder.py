import json
import unittest

from harness.context import (
    SUMMARY_TRUNCATION_MARKER,
    ContextBuilder,
    HistorySummarizer,
    SimpleHistorySummarizer,
)
from harness.model import MessageRole, ModelMessage
from harness.state import AgentState
from harness.tools import CalculatorTool, ToolResult


class RecordingSummarizer(HistorySummarizer):
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[ModelMessage, ...], int]] = []

    def summarize(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_chars: int,
    ) -> str:
        self.calls.append((messages, max_chars))
        return "recorded summary"[:max_chars]


class ContextBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = CalculatorTool().schema

    def test_builds_all_context_components_without_mutating_state(self) -> None:
        history = [
            ModelMessage(MessageRole.ASSISTANT, "previous action"),
            ModelMessage(MessageRole.USER, "previous observation"),
        ]
        state = AgentState.start(
            task_id="context-task",
            goal="Calculate safely",
            messages=history,
        )
        state.begin_model_step()
        builder = ContextBuilder(
            system_prompt="Follow the tool protocol.",
            max_context_chars=10_000,
        )

        result = builder.build(state, tool_schemas=(self.schema,))

        self.assertFalse(result.truncated)
        self.assertEqual(result.dropped_messages, 0)
        self.assertEqual(result.total_chars, sum(len(m.content) for m in result.messages))
        self.assertLessEqual(result.total_chars, result.max_context_chars)
        self.assertTrue(result.messages[0].content.startswith("System Prompt:\n"))
        self.assertEqual(result.messages[1].content, "Goal:\nCalculate safely")
        self.assertEqual(result.messages[2:4], tuple(history))
        self.assertTrue(result.messages[4].content.startswith("Tool Definitions:\n"))
        self.assertIn('"name":"calculator"', result.messages[4].content)
        self.assertTrue(result.messages[5].content.startswith("Current State:\n"))
        current_state = json.loads(result.messages[5].content.split("\n", 1)[1])
        self.assertEqual(current_state["task_id"], "context-task")
        self.assertEqual(current_state["current_step"], 1)
        self.assertEqual(state.messages, history)

    def test_truncates_old_history_but_keeps_state_unchanged(self) -> None:
        old_message = ModelMessage(MessageRole.ASSISTANT, "OLD-" * 100)
        recent_message = ModelMessage(MessageRole.USER, "RECENT-" * 100)
        state = AgentState.start(
            task_id="long-task",
            goal="Bound this context",
            messages=[old_message, recent_message],
        )
        baseline = ContextBuilder(max_context_chars=10_000).build(
            AgentState.start(
                task_id="long-task",
                goal="Bound this context",
                messages=[],
            ),
            tool_schemas=(self.schema,),
        )
        builder = ContextBuilder(max_context_chars=baseline.total_chars + 80)

        result = builder.build(state, tool_schemas=(self.schema,))

        self.assertTrue(result.truncated)
        self.assertEqual(result.dropped_messages, 1)
        self.assertEqual(result.total_chars, result.max_context_chars)
        self.assertEqual(len(result.messages), 5)
        self.assertIn("RECENT-", result.messages[2].content)
        self.assertNotIn("OLD-", "".join(message.content for message in result.messages))
        self.assertEqual(state.messages, [old_message, recent_message])

    def test_fixed_components_are_also_fitted_to_a_tiny_budget(self) -> None:
        state = AgentState.start(goal="A goal", messages=[])
        builder = ContextBuilder(max_context_chars=37)

        result = builder.build(state, tool_schemas=(self.schema,))

        self.assertTrue(result.truncated)
        self.assertEqual(result.total_chars, 37)
        self.assertEqual(len(result.messages), 4)

    def test_formats_a_tool_result_as_history(self) -> None:
        message = ContextBuilder.tool_result_message(
            ToolResult(
                name="calculator",
                arguments={"expression": "2 + 2"},
                result=4,
            )
        )

        self.assertEqual(message.role, MessageRole.USER)
        self.assertEqual(
            json.loads(message.content),
            {
                "arguments": {"expression": "2 + 2"},
                "error": None,
                "name": "calculator",
                "result": 4,
                "type": "tool_result",
            },
        )

    def test_requires_a_positive_integer_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_context_chars"):
            ContextBuilder(max_context_chars=0)
        with self.assertRaisesRegex(TypeError, "max_context_chars"):
            ContextBuilder(max_context_chars=True)


class ContextCompactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = CalculatorTool().schema

    def test_summarizes_old_history_and_keeps_recent_messages(self) -> None:
        history = [
            ModelMessage(
                MessageRole.ASSISTANT if index % 2 == 0 else MessageRole.USER,
                f"message-{index}-" * 12,
            )
            for index in range(6)
        ]
        state = AgentState.start(
            task_id="compact-task",
            goal="Compact a long session",
            messages=history,
        )
        fixed_size = ContextBuilder(max_context_chars=10_000).build(
            AgentState.start(
                task_id=state.task_id,
                goal=state.goal,
                messages=[],
            ),
            tool_schemas=(self.schema,),
        ).total_chars
        summarizer = RecordingSummarizer()
        builder = ContextBuilder(
            max_context_chars=fixed_size + 250,
            summarizer=summarizer,
            recent_history_messages=2,
            summary_max_chars=100,
        )

        result = builder.build(state, tool_schemas=(self.schema,))

        self.assertTrue(result.truncated)
        self.assertEqual(result.summary, "recorded summary")
        self.assertGreater(result.summarized_messages, 0)
        self.assertEqual(result.dropped_messages, result.summarized_messages)
        self.assertEqual(len(summarizer.calls), 1)
        summarized_input, summary_budget = summarizer.calls[0]
        self.assertEqual(len(summarized_input), result.summarized_messages)
        self.assertLessEqual(len(result.summary), summary_budget)
        self.assertEqual(result.messages[2].role, MessageRole.DEVELOPER)
        self.assertTrue(result.messages[2].content.startswith("History Summary:\n"))
        self.assertLessEqual(result.total_chars, result.max_context_chars)
        self.assertIn("message-5-", "".join(m.content for m in result.messages))
        self.assertEqual(state.messages, history)

    def test_does_not_summarize_when_full_history_fits(self) -> None:
        summarizer = RecordingSummarizer()
        state = AgentState.start(
            goal="Short session",
            messages=[ModelMessage(MessageRole.ASSISTANT, "short")],
        )
        builder = ContextBuilder(
            max_context_chars=10_000,
            summarizer=summarizer,
        )

        result = builder.build(state, tool_schemas=(self.schema,))

        self.assertFalse(result.truncated)
        self.assertIsNone(result.summary)
        self.assertEqual(result.summarized_messages, 0)
        self.assertEqual(summarizer.calls, [])

    def test_recent_tool_result_survives_compaction(self) -> None:
        builder = ContextBuilder(
            max_context_chars=1_200,
            summarizer=SimpleHistorySummarizer(),
            recent_history_messages=2,
            summary_max_chars=120,
        )
        tool_result_message = builder.tool_result_message(
            ToolResult(
                name="calculator",
                arguments={"expression": "6 * 7"},
                result=42,
            )
        )
        state = AgentState.start(
            goal="Retain the observation",
            messages=[
                *(ModelMessage(MessageRole.ASSISTANT, "old " * 150) for _ in range(5)),
                ModelMessage(MessageRole.ASSISTANT, "latest tool call"),
                tool_result_message,
            ],
        )

        result = builder.build(state, tool_schemas=(self.schema,))

        context_text = "\n".join(message.content for message in result.messages)
        self.assertTrue(result.truncated)
        self.assertIn('"type":"tool_result"', context_text)
        self.assertEqual(len(state.messages), 7)

    def test_simple_summarizer_is_bounded_and_explicitly_lossy(self) -> None:
        summary = SimpleHistorySummarizer().summarize(
            (
                ModelMessage(MessageRole.USER, "important detail " * 20),
                ModelMessage(MessageRole.ASSISTANT, "another detail " * 20),
            ),
            max_chars=60,
        )

        self.assertEqual(len(summary), 60)
        self.assertTrue(summary.endswith(SUMMARY_TRUNCATION_MARKER))


if __name__ == "__main__":
    unittest.main()
