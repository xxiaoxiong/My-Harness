import unittest
from datetime import UTC

from harness.model import MessageRole, ModelMessage, ModelResponse
from harness.state import AgentState, AgentStatus, TrajectoryEventKind
from harness.tools import ToolCall, ToolResult


class AgentStateTests(unittest.TestCase):
    def test_records_required_state_and_ordered_trajectory(self) -> None:
        initial_message = ModelMessage(MessageRole.USER, "Goal")
        state = AgentState.start(
            task_id=" task-123 ",
            goal=" Calculate 6 * 7 ",
            messages=[initial_message],
        )

        state.begin_model_step()
        state.record_model_call(
            ModelResponse(
                response_id="response-1",
                model="demo-model",
                message=ModelMessage(MessageRole.ASSISTANT, "tool call"),
                finish_reason="stop",
                usage=None,
                latency_ms=1.0,
            ),
            input_message_count=1,
        )
        call = ToolCall("calculator", {"expression": "6 * 7"})
        result = ToolResult("calculator", call.arguments, result=42)
        state.record_tool_call(call)
        state.record_tool_result(result)
        state.finish("The answer is 42.")

        self.assertEqual(state.task_id, "task-123")
        self.assertEqual(state.goal, "Calculate 6 * 7")
        self.assertEqual(state.current_step, 1)
        self.assertEqual(state.status, AgentStatus.FINISHED)
        self.assertEqual(state.messages, [initial_message])
        self.assertEqual(state.tool_calls, [call])
        self.assertEqual(state.tool_results, [result])
        self.assertEqual(state.final_answer, "The answer is 42.")
        self.assertEqual(state.created_at.tzinfo, UTC)
        self.assertGreaterEqual(state.updated_at, state.created_at)
        self.assertEqual(
            [event.sequence for event in state.trajectory],
            [1, 2, 3, 4],
        )
        self.assertEqual(
            [event.kind for event in state.trajectory],
            [
                TrajectoryEventKind.MODEL_CALL,
                TrajectoryEventKind.TOOL_CALL,
                TrajectoryEventKind.TOOL_RESULT,
                TrajectoryEventKind.FINAL_ANSWER,
            ],
        )

    def test_terminal_state_rejects_further_transitions(self) -> None:
        state = AgentState.start(goal="Finish", messages=[])
        state.finish("Done")

        with self.assertRaisesRegex(RuntimeError, "not running"):
            state.begin_model_step()

    def test_start_generates_distinct_task_ids(self) -> None:
        first = AgentState.start(goal="One", messages=[])
        second = AgentState.start(goal="Two", messages=[])

        self.assertTrue(first.task_id)
        self.assertNotEqual(first.task_id, second.task_id)


if __name__ == "__main__":
    unittest.main()
