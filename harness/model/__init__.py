"""Provider-neutral model contracts and concrete adapters."""

from harness.model.openai_compatible import (
    ModelHTTPError,
    ModelProtocolError,
    ModelTransportError,
    OpenAICompatibleProvider,
)
from harness.model.provider import ModelProvider, ModelProviderError
from harness.model.types import (
    MessageRole,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TokenUsage,
)

__all__ = [
    "MessageRole",
    "ModelHTTPError",
    "ModelMessage",
    "ModelProtocolError",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "ModelTransportError",
    "OpenAICompatibleProvider",
    "TokenUsage",
]
