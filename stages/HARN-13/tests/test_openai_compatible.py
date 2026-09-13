import io
import json
import logging
import unittest

import httpx

from harness.core.config import HarnessConfig
from harness.core.logging import configure_logging
from harness.model import (
    MessageRole,
    ModelHTTPError,
    ModelMessage,
    ModelProtocolError,
    ModelRequest,
    ModelTransportError,
    OpenAICompatibleProvider,
    TokenUsage,
)


class OpenAICompatibleProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.log_stream = io.StringIO()
        configure_logging(HarnessConfig(), stream=self.log_stream)

    def tearDown(self) -> None:
        logger = logging.getLogger("harness")
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    async def test_generate_normalizes_response_and_observes_call(self) -> None:
        captured_request: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured_request.update(json.loads(request.content))
            self.assertEqual(request.headers["authorization"], "Bearer secret")
            self.assertEqual(
                str(request.url),
                "https://models.example.test/v1/chat/completions",
            )
            return httpx.Response(
                200,
                json={
                    "id": "chatcmpl-demo",
                    "model": "demo-model-v2",
                    "choices": [
                        {
                            "message": {"role": "assistant", "content": "Hello!"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 4,
                        "completion_tokens": 2,
                        "total_tokens": 6,
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                api_key="secret",
                base_url="https://models.example.test/v1/",
                client=client,
            )
            response = await provider.generate(_request())

        self.assertEqual(
            captured_request,
            {
                "model": "demo-model",
                "messages": [{"role": "user", "content": "Say hello"}],
            },
        )
        self.assertEqual(response.response_id, "chatcmpl-demo")
        self.assertEqual(response.model, "demo-model-v2")
        self.assertEqual(
            response.message,
            ModelMessage(MessageRole.ASSISTANT, "Hello!"),
        )
        self.assertEqual(response.finish_reason, "stop")
        self.assertEqual(response.usage, TokenUsage(4, 2, 6))
        self.assertGreaterEqual(response.latency_ms, 0)

        logs = self.log_stream.getvalue()
        self.assertIn("model.request", logs)
        self.assertIn("messages=1", logs)
        self.assertIn("model.response", logs)
        self.assertIn("finish_reason=stop", logs)
        self.assertIn("total_tokens=6", logs)
        self.assertIn("latency_ms=", logs)
        self.assertNotIn("secret", logs)
        self.assertNotIn("Say hello", logs)

    async def test_generate_allows_compatible_service_without_api_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(200, json=_response_body(usage=None))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(
                base_url="http://localhost:8000/v1",
                client=client,
            )
            response = await provider.generate(_request())

        self.assertIsNone(response.usage)

    async def test_generate_exposes_http_error_without_leaking_key(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                headers={"x-request-id": "req-rate-limit"},
                json={"error": {"message": "rate limited"}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(api_key="secret", client=client)
            with self.assertRaises(ModelHTTPError) as raised:
                await provider.generate(_request())

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.request_id, "req-rate-limit")
        self.assertEqual(str(raised.exception), "rate limited")
        self.assertNotIn("secret", self.log_stream.getvalue())

    async def test_generate_maps_transport_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client)
            with self.assertRaisesRegex(ModelTransportError, "before receiving"):
                await provider.generate(_request())

        self.assertIn("kind=transport", self.log_stream.getvalue())

    async def test_generate_rejects_malformed_success_response(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"id": "broken", "choices": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenAICompatibleProvider(client=client)
            with self.assertRaisesRegex(ModelProtocolError, "response.model"):
                await provider.generate(_request())


def _request() -> ModelRequest:
    return ModelRequest(
        model="demo-model",
        messages=(ModelMessage(MessageRole.USER, "Say hello"),),
    )


def _response_body(*, usage: dict[str, int] | None) -> dict[str, object]:
    return {
        "id": "chatcmpl-demo",
        "model": "demo-model",
        "choices": [
            {
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


if __name__ == "__main__":
    unittest.main()
