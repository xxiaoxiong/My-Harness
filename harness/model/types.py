"""Provider-neutral request and response types for model generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from math import isfinite


@unique
class MessageRole(str, Enum):
    """Text message roles supported at the HARN-01 boundary."""

    DEVELOPER = "developer"
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """A provider-neutral text message."""

    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        if not isinstance(self.role, MessageRole):
            raise TypeError("role must be a MessageRole")
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    """Input accepted by every model provider."""

    model: str
    messages: tuple[ModelMessage, ...]

    def __post_init__(self) -> None:
        normalized_model = self.model.strip()
        if not normalized_model:
            raise ValueError("model must not be empty")

        messages = tuple(self.messages)
        if not messages:
            raise ValueError("messages must not be empty")
        if not all(isinstance(message, ModelMessage) for message in messages):
            raise TypeError("messages must contain only ModelMessage values")

        object.__setattr__(self, "model", normalized_model)
        object.__setattr__(self, "messages", messages)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Normalized token counters returned by a model provider."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self) -> None:
        values = (self.prompt_tokens, self.completion_tokens, self.total_tokens)
        if any(
            isinstance(value, bool) or not isinstance(value, int) for value in values
        ):
            raise TypeError("token counts must be integers")
        if any(value < 0 for value in values):
            raise ValueError("token counts must not be negative")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    """Normalized result returned by every model provider."""

    response_id: str
    model: str
    message: ModelMessage
    finish_reason: str | None
    usage: TokenUsage | None
    latency_ms: float
    ttft_ms: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.response_id, str) or not self.response_id.strip():
            raise ValueError("response_id must not be empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("response model must not be empty")
        if not isinstance(self.message, ModelMessage):
            raise TypeError("response message must be a ModelMessage")
        if self.finish_reason is not None and not isinstance(
            self.finish_reason,
            str,
        ):
            raise TypeError("finish_reason must be a string or null")
        if self.usage is not None and not isinstance(self.usage, TokenUsage):
            raise TypeError("usage must be TokenUsage or null")
        _nonnegative_duration(self.latency_ms, "latency_ms")
        if self.ttft_ms is not None:
            _nonnegative_duration(self.ttft_ms, "ttft_ms")

        object.__setattr__(self, "response_id", self.response_id.strip())
        object.__setattr__(self, "model", self.model.strip())
        object.__setattr__(self, "latency_ms", float(self.latency_ms))
        if self.ttft_ms is not None:
            object.__setattr__(self, "ttft_ms", float(self.ttft_ms))


def _nonnegative_duration(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if value < 0 or not isfinite(value):
        raise ValueError(f"{name} must be nonnegative and finite")
