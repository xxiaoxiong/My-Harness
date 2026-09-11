"""The provider contract used by the harness instead of a vendor SDK."""

from abc import ABC, abstractmethod

from harness.model.types import ModelRequest, ModelResponse


class ModelProviderError(RuntimeError):
    """Base exception for failures at the model-provider boundary."""


class ModelProvider(ABC):
    """Uniform asynchronous interface implemented by model adapters."""

    @abstractmethod
    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Generate one normalized model response."""

