"""HARN-15 Demo: a stored Task outlives the Client that submitted it."""

from __future__ import annotations

import asyncio
import json

from harness import (
    AgentTask,
    AgentWorker,
    ContextBuilder,
    InMemoryTaskStore,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    TaskStatus,
    ToolAgentLoop,
    ToolExecutor,
    ToolRegistry,
)


class WorkerProvider(ModelProvider):
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.started.set()
        await self.release.wait()
        action = {
            "type": "final_answer",
            "answer": "The Worker completed a Task after its Client returned.",
        }
        return ModelResponse(
            response_id="harn-15-response",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


def submit_from_client(store: InMemoryTaskStore) -> str:
    """Represent a short Client request that only persists work."""

    task = AgentTask.create(
        "Complete this work independently from my connection.",
        task_id="harn-15-demo",
    )
    store.create(task)
    print(f"Client submitted: task_id={task.task_id}, status={task.status.value}")
    return task.task_id


def _loop(provider: ModelProvider) -> ToolAgentLoop:
    return ToolAgentLoop(
        provider,
        model="task-runtime-demo-model",
        max_steps=2,
        tool_executor=ToolExecutor(ToolRegistry()),
        context_builder=ContextBuilder(max_context_chars=6_000),
    )


async def main() -> None:
    store = InMemoryTaskStore()
    task_id = submit_from_client(store)
    print("Client request returned; the Task remains in TaskStore.")

    provider = WorkerProvider()
    worker = AgentWorker(store, lambda task: _loop(provider))
    background_work = asyncio.create_task(worker.execute(task_id))
    await provider.started.wait()
    print(f"Worker claimed:   status={store.get(task_id).status.value}")
    provider.release.set()
    completed = await background_work

    print(f"Worker persisted: status={completed.status.value}")
    print(f"Later Client GET: answer={store.get(task_id).final_answer}")
    print(f"Task states: {[status.value for status in TaskStatus]}")


if __name__ == "__main__":
    asyncio.run(main())
