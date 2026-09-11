"""Run HARN-01 through a deterministic OpenAI-compatible mock endpoint."""

import asyncio
import json

import httpx

from harness import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    OpenAICompatibleProvider,
    configure_logging,
    load_config,
)


def demo_llm(request: httpx.Request) -> httpx.Response:
    """Stand in for an LLM while preserving the real HTTP wire format."""

    payload = json.loads(request.content)
    user_text = payload["messages"][-1]["content"]
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-harn-01-demo",
            "model": payload["model"],
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": f"The adapter received: {user_text}",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 7,
                "total_tokens": 12,
            },
        },
    )


async def main() -> None:
    configure_logging(load_config())
    transport = httpx.MockTransport(demo_llm)

    async with httpx.AsyncClient(transport=transport) as client:
        provider = OpenAICompatibleProvider(
            base_url="https://demo-llm.local/v1",
            client=client,
        )
        request = ModelRequest(
            model="demo-model",
            messages=(
                ModelMessage(MessageRole.USER, "What is a Model Adapter?"),
            ),
        )
        response = await provider.generate(request)

    print("\nNormalized ModelResponse")
    print(f"  content:       {response.message.content}")
    print(f"  finish_reason: {response.finish_reason}")
    print(f"  token_usage:   {response.usage}")
    print(f"  latency_ms:    {response.latency_ms:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
