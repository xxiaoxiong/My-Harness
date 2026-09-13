"""Provider-neutral request and response types for model generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique


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
