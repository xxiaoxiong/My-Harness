"""OpenAI-compatible Chat Completions model provider."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import httpx

from harness.core.logging import get_logger
from harness.model.provider import ModelProvider, ModelProviderError
from harness.model.types import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ModelTransportError(ModelProviderError):
    """Raised when no HTTP response can be obtained from the model service."""


class ModelHTTPError(ModelProviderError):
    """Raised when the model service returns a non-success status code."""

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        request_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.request_id = request_id
        super().__init__(message)


class ModelProtocolError(ModelProviderError):
    """Raised when a successful response does not match the expected schema."""


class OpenAICompatibleProvider(ModelProvider):
    """Call an OpenAI-compatible ``/chat/completions`` endpoint.

    An ``httpx.AsyncClient`` may be injected for tests or application-owned
    connection pooling. When omitted, a short-lived client is used per call.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")

        self._api_key = api_key.strip() if api_key and api_key.strip() else None
        self._base_url = normalized_base_url
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._logger = get_logger("model.openai_compatible")

    async def generate(self, request: ModelRequest) -> ModelResponse:
        endpoint = f"{self._base_url}/chat/completions"
        payload = _build_payload(request)
        headers = {"Content-Type": "application/json"}
        if self._api_key is not None:
            headers["Authorization"] = f"Bearer {self._api_key}"

        self._logger.info(
            "model.request provider=openai-compatible model=%s messages=%d endpoint=%s",
            request.model,
            len(request.messages),
            endpoint,
        )
        started_at = time.perf_counter()

        try:
            response = await self._post(endpoint, headers=headers, payload=payload)
        except httpx.TimeoutException as exc:
            latency_ms = _elapsed_ms(started_at)
            self._logger.error(
                "model.error provider=openai-compatible model=%s kind=timeout latency_ms=%.2f",
                request.model,
                latency_ms,
            )
            raise ModelTransportError(
                f"model request timed out after {self._timeout_seconds:g} seconds"
            ) from exc
        except httpx.RequestError as exc:
            latency_ms = _elapsed_ms(started_at)
            self._logger.error(
                "model.error provider=openai-compatible model=%s kind=transport latency_ms=%.2f",
                request.model,
                latency_ms,
            )
            raise ModelTransportError(
                "model request failed before receiving a response"
            ) from exc

        latency_ms = _elapsed_ms(started_at)
        request_id = response.headers.get("x-request-id")
        if response.is_error:
            message = _read_error_message(response)
            self._logger.error(
                "model.error provider=openai-compatible model=%s kind=http status=%d request_id=%s latency_ms=%.2f",
                request.model,
                response.status_code,
                request_id,
                latency_ms,
            )
            raise ModelHTTPError(
                response.status_code,
                message,
                request_id=request_id,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ModelProtocolError("model response body is not valid JSON") from exc

        result = _parse_response(body, latency_ms=latency_ms)
        usage = result.usage
        self._logger.info(
            "model.response provider=openai-compatible request_id=%s model=%s finish_reason=%s prompt_tokens=%s completion_tokens=%s total_tokens=%s latency_ms=%.2f",
            result.response_id,
            result.model,
            result.finish_reason,
            usage.prompt_tokens if usage else None,
            usage.completion_tokens if usage else None,
            usage.total_tokens if usage else None,
            result.latency_ms,
        )
        return result

    async def _post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            )

        async with httpx.AsyncClient() as client:
            return await client.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=self._timeout_seconds,
            )


def _build_payload(request: ModelRequest) -> dict[str, object]:
    return {
        "model": request.model,
        "messages": [
            {"role": message.role.value, "content": message.content}
            for message in request.messages
        ],
    }


def _parse_response(body: Any, *, latency_ms: float) -> ModelResponse:
    root = _require_mapping(body, "response")
    response_id = _require_string(root.get("id"), "response.id")
    model = _require_string(root.get("model"), "response.model")

    choices = root.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ModelProtocolError("response.choices must be a non-empty list")

    choice = _require_mapping(choices[0], "response.choices[0]")
    message_data = _require_mapping(
        choice.get("message"), "response.choices[0].message"
    )
    role_value = _require_string(
        message_data.get("role"), "response.choices[0].message.role"
    )
    try:
        role = MessageRole(role_value)
    except ValueError as exc:
        raise ModelProtocolError(
            f"response.choices[0].message.role is unsupported: {role_value!r}"
        ) from exc
    content = _require_string(
        message_data.get("content"), "response.choices[0].message.content"
    )

    finish_reason_value = choice.get("finish_reason")
    if finish_reason_value is not None and not isinstance(finish_reason_value, str):
        raise ModelProtocolError(
            "response.choices[0].finish_reason must be a string or null"
        )

    return ModelResponse(
        response_id=response_id,
        model=model,
        message=ModelMessage(role=role, content=content),
        finish_reason=finish_reason_value,
        usage=_parse_usage(root.get("usage")),
        latency_ms=latency_ms,
    )


def _parse_usage(value: Any) -> TokenUsage | None:
    if value is None:
        return None
    usage = _require_mapping(value, "response.usage")
    try:
        return TokenUsage(
            prompt_tokens=_require_integer(
                usage.get("prompt_tokens"), "response.usage.prompt_tokens"
            ),
            completion_tokens=_require_integer(
                usage.get("completion_tokens"), "response.usage.completion_tokens"
            ),
            total_tokens=_require_integer(
                usage.get("total_tokens"), "response.usage.total_tokens"
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ModelProtocolError(str(exc)) from exc


def _read_error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"model service returned HTTP {response.status_code}"

    if isinstance(body, Mapping):
        error = body.get("error")
        if isinstance(error, Mapping) and isinstance(error.get("message"), str):
            return error["message"]
    return f"model service returned HTTP {response.status_code}"


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelProtocolError(f"{path} must be an object")
    return value


def _require_string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise ModelProtocolError(f"{path} must be a string")
    return value


def _require_integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ModelProtocolError(f"{path} must be an integer")
    return value


def _elapsed_ms(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000
