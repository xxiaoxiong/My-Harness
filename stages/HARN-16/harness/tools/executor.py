"""Tool execution through a registry-owned implementation boundary."""

from __future__ import annotations

from harness.tools.base import ToolError, ToolSchema
from harness.tools.idempotency import (
    IdempotencyRecord,
    IdempotencyStore,
)
from harness.tools.registry import ToolNotFoundError, ToolRegistry
from harness.tools.types import ToolCall, ToolResult


class ToolExecutor:
    """Resolve calls through a registry and normalize expected failures."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        if not isinstance(registry, ToolRegistry):
            raise TypeError("registry must be a ToolRegistry")
        if idempotency_store is not None and not isinstance(
            idempotency_store,
            IdempotencyStore,
        ):
            raise TypeError("idempotency_store must be an IdempotencyStore or null")
        self._registry = registry
        self._idempotency_store = idempotency_store

    @property
    def idempotency_enabled(self) -> bool:
        return self._idempotency_store is not None

    def list_schemas(self) -> tuple[ToolSchema, ...]:
        """Expose definitions without exposing implementations to the Agent."""

        return tuple(tool.schema for tool in self._registry.list_tools())

    async def execute(
        self,
        call: ToolCall,
        *,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Execute one call and preserve its name, arguments, result, or error."""

        idempotency_store = self._idempotency_store
        if idempotency_key is not None:
            if idempotency_store is None:
                raise ValueError(
                    "idempotency_key requires an idempotency_store"
                )
            cached = idempotency_store.get(idempotency_key)
            if cached is not None:
                if cached.call != call:
                    return ToolResult(
                        name=call.name,
                        arguments=call.arguments,
                        error="idempotency key conflicts with a different Tool Call",
                    )
                return cached.result

        try:
            tool = self._registry.get(call.name)
        except ToolNotFoundError as error:
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                error=str(error),
            )

        try:
            result = await tool.execute(call.arguments)
        except ToolError as error:
            return ToolResult(
                name=call.name,
                arguments=call.arguments,
                error=str(error),
            )

        observation = ToolResult(
            name=call.name,
            arguments=call.arguments,
            result=result,
        )
        if idempotency_key is not None and idempotency_store is not None:
            idempotency_store.save(
                IdempotencyRecord(
                    key=idempotency_key,
                    call=call,
                    result=observation,
                )
            )
        return observation
