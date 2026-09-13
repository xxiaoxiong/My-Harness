"""HARN-12 Demo: load GitSkill, then use its injected capabilities."""

from __future__ import annotations

import asyncio
import json

from harness import (
    GIT_COMMIT_NAME,
    GitSkill,
    Harness,
    InMemoryGitBackend,
    JsonCheckpointStore,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
)


class GitWorkflowProvider(ModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = _current_step(request)
        actions: dict[int, dict[str, object]] = {
            1: {"type": "tool_call", "name": "git_status", "arguments": {}},
            2: {
                "type": "tool_call",
                "name": "git_diff",
                "arguments": {"staged": False},
            },
            3: {
                "type": "tool_call",
                "name": GIT_COMMIT_NAME,
                "arguments": {"message": "feat: learn plugins"},
            },
        }
        action = actions.get(
            step,
            {
                "type": "final_answer",
                "answer": "GitSkill workflow completed after approval.",
            },
        )
        return ModelResponse(
            response_id=f"harn-12-response-{step}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


def _current_step(request: ModelRequest) -> int:
    state_message = next(
        message.content
        for message in request.messages
        if message.content.startswith("Current State:\n")
    )
    state = json.loads(state_message.split("\n", 1)[1])
    return int(state["current_step"])


def _build_harness(backend: InMemoryGitBackend) -> Harness:
    harness = Harness()
    harness.load_plugin(GitSkill(backend))
    return harness


async def main() -> None:
    backend = InMemoryGitBackend(
        branch="main",
        changed_files=["README.md", "harness/extensions/harness.py"],
        unstaged_diff="- HARN-11\n+ HARN-12",
    )
    empty_harness = Harness()
    print(f"Tools before GitSkill: {[tool.schema.name for tool in empty_harness.tools]}")

    first_harness = _build_harness(backend)
    print(f"Loaded Plugins: {[plugin.name for plugin in first_harness.plugins]}")
    print(f"Tools after GitSkill: {[tool.schema.name for tool in first_harness.tools]}")
    print(
        "Prompt Fragments: "
        f"{[fragment.name for fragment in first_harness.prompt_fragments]}"
    )
    provider = GitWorkflowProvider()
    store = JsonCheckpointStore(".harness-checkpoints")
    waiting = await first_harness.create_agent_loop(
        provider,
        model="skill-demo-model",
        max_steps=5,
        max_context_chars=8_000,
        checkpoint_store=store,
    ).run("Inspect changes and create a learning commit.", task_id="harn-12-demo")

    print(
        f"Before approval: status={waiting.status.value}, "
        f"pending={waiting.pending_tool_call.name}, commits={backend.commits}"  # type: ignore[union-attr]
    )
    print(
        "Prompt injected: "
        f"{'Plugin Prompt [git-skill]' in provider.requests[0].messages[0].content}"
    )

    second_harness = _build_harness(backend)
    completed = await second_harness.create_agent_loop(
        GitWorkflowProvider(),
        model="skill-demo-model",
        max_steps=5,
        max_context_chars=8_000,
        checkpoint_store=store,
    ).resume("harn-12-demo", approve=True)
    print(
        f"After approval:  status={completed.status.value}, "
        f"commits={backend.commits}"
    )
    print(f"Final answer: {completed.final_answer}")


if __name__ == "__main__":
    asyncio.run(main())
