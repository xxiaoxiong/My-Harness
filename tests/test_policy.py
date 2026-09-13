import json
import unittest

from harness.context import ContextBuilder
from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)
from harness.policy import (
    CompositePolicyEngine,
    InvalidPermissionDecision,
    PermissionDecision,
    PermissionRequest,
    PolicyEngine,
    StaticPolicyEngine,
)
from harness.runtime import ToolAgentLoop
from harness.state import AgentStatus, TrajectoryEventKind
from harness.tools import CalculatorTool, ToolCall, ToolExecutor, ToolRegistry


class SequenceProvider(ModelProvider):
    def __init__(self, *actions: dict[str, object]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("provider was called unexpectedly")
        action = self._actions.pop(0)
        return ModelResponse(
            response_id=f"policy-response-{len(self.requests)}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class InvalidPolicyEngine(PolicyEngine):
    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        return "allow"  # type: ignore[return-value]


def _tool_call(expression: str) -> dict[str, object]:
    return {
        "type": "tool_call",
        "name": "calculator",
        "arguments": {"expression": expression},
    }


def _final(answer: str = "Done.") -> dict[str, object]:
    return {"type": "final_answer", "answer": answer}


def _loop(
    provider: ModelProvider,
    policy: PolicyEngine,
) -> ToolAgentLoop:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return ToolAgentLoop(
        provider,
        model="policy-demo-model",
        max_steps=3,
        tool_executor=ToolExecutor(registry),
        context_builder=ContextBuilder(max_context_chars=4_000),
        policy_engine=policy,
    )


class PolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_composite_policy_uses_strongest_decision(self) -> None:
        request = PermissionRequest(
            task_id="composite-task",
            goal="Test policy composition",
            step=1,
            tool_call=ToolCall("git_commit", {"message": "test"}),
        )
        approval = StaticPolicyEngine(
            {"git_commit": PermissionDecision.REQUIRE_APPROVAL}
        )
        denial = StaticPolicyEngine(
            {"git_commit": PermissionDecision.DENY}
        )

        self.assertEqual(
            await CompositePolicyEngine([approval]).decide(request),
            PermissionDecision.REQUIRE_APPROVAL,
        )
        self.assertEqual(
            await CompositePolicyEngine([approval, denial]).decide(request),
            PermissionDecision.DENY,
        )

    async def test_static_policy_uses_tool_rules_and_default(self) -> None:
        policy = StaticPolicyEngine(
            {
                "git_push": PermissionDecision.REQUIRE_APPROVAL,
                "remove_tree": PermissionDecision.DENY,
            }
        )
        git_request = PermissionRequest(
            task_id="policy-task",
            goal="Publish changes",
            step=1,
            tool_call=ToolCall("git_push", {"branch": "main"}),
        )
        read_request = PermissionRequest(
            task_id="policy-task",
            goal="Read data",
            step=2,
            tool_call=ToolCall("read_file", {"path": "README.md"}),
        )

        self.assertEqual(
            await policy.decide(git_request),
            PermissionDecision.REQUIRE_APPROVAL,
        )
        self.assertEqual(
            await policy.decide(read_request),
            PermissionDecision.ALLOW,
        )
        self.assertEqual(policy.rules["remove_tree"], PermissionDecision.DENY)

    def test_static_policy_rejects_invalid_configuration(self) -> None:
        with self.assertRaisesRegex(TypeError, "PermissionDecision"):
            StaticPolicyEngine({"calculator": "allow"})  # type: ignore[dict-item]
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            StaticPolicyEngine({" ": PermissionDecision.ALLOW})

    async def test_allow_reaches_executor_and_records_separate_decision(self) -> None:
        result = await _loop(
            SequenceProvider(_tool_call("20 + 22"), _final("Allowed.")),
            StaticPolicyEngine({"calculator": PermissionDecision.ALLOW}),
        ).run("Calculate.", task_id="allow-task")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertEqual(result.last_tool_result.result, 42)  # type: ignore[union-attr]
        decision_event = result.state.trajectory[2]
        self.assertEqual(
            decision_event.kind,
            TrajectoryEventKind.PERMISSION_DECISION,
        )
        self.assertEqual(decision_event.details["decision"], "allow")

    async def test_deny_skips_executor_and_returns_observation_to_model(self) -> None:
        provider = SequenceProvider(_tool_call("20 + 22"), _final("Denied."))
        result = await _loop(
            provider,
            StaticPolicyEngine({"calculator": PermissionDecision.DENY}),
        ).run("Calculate.", task_id="deny-task")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertEqual(
            result.last_tool_result.error,  # type: ignore[union-attr]
            "tool call denied by policy",
        )
        self.assertIsNone(result.last_tool_result.result)  # type: ignore[union-attr]
        observation = json.loads(provider.requests[1].messages[3].content)
        self.assertEqual(observation["error"], "tool call denied by policy")
        self.assertEqual(result.state.trajectory[2].details["decision"], "deny")

    async def test_invalid_policy_return_is_rejected_before_execution(self) -> None:
        with self.assertRaisesRegex(
            InvalidPermissionDecision,
            "must return a PermissionDecision",
        ):
            await _loop(
                SequenceProvider(_tool_call("20 + 22")),
                InvalidPolicyEngine(),
            ).run("Calculate.", task_id="invalid-policy-task")


if __name__ == "__main__":
    unittest.main()
