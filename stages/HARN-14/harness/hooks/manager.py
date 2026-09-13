"""Ordered Hook registration and lifecycle dispatch."""

from __future__ import annotations

from collections.abc import Iterable

from harness.hooks.base import Hook, HookContext


class HookExecutionError(RuntimeError):
    """Wrap a callback failure with the lifecycle name and Hook type."""

    def __init__(self, callback: str, hook: Hook, cause: Exception) -> None:
        self.callback = callback
        self.hook = hook
        self.cause = cause
        super().__init__(
            f"{type(hook).__name__}.{callback} failed: {cause}"
        )


class HookManager:
    """Dispatch callbacks sequentially in registration order."""

    def __init__(self, hooks: Iterable[Hook] = ()) -> None:
        self._hooks: list[Hook] = []
        for hook in hooks:
            self.register(hook)

    @property
    def hooks(self) -> tuple[Hook, ...]:
        return tuple(self._hooks)

    def register(self, hook: Hook) -> None:
        if not isinstance(hook, Hook):
            raise TypeError("hook must be a Hook")
        self._hooks.append(hook)

    async def before_model_call(self, context: HookContext) -> None:
        await self._dispatch("before_model_call", context)

    async def after_model_call(self, context: HookContext) -> None:
        await self._dispatch("after_model_call", context)

    async def before_tool_call(self, context: HookContext) -> None:
        await self._dispatch("before_tool_call", context)

    async def after_tool_call(self, context: HookContext) -> None:
        await self._dispatch("after_tool_call", context)

    async def on_error(self, context: HookContext) -> None:
        await self._dispatch("on_error", context, continue_after_failure=True)

    async def before_step(self, context: HookContext) -> None:
        await self._dispatch("before_step", context)

    async def after_step(self, context: HookContext) -> None:
        await self._dispatch("after_step", context)

    async def _dispatch(
        self,
        callback: str,
        context: HookContext,
        *,
        continue_after_failure: bool = False,
    ) -> None:
        if not isinstance(context, HookContext):
            raise TypeError("context must be a HookContext")
        first_error: HookExecutionError | None = None
        for hook in tuple(self._hooks):
            try:
                await getattr(hook, callback)(context)
            except Exception as error:
                hook_error = HookExecutionError(callback, hook, error)
                if not continue_after_failure:
                    raise hook_error from error
                if first_error is None:
                    first_error = hook_error
        if first_error is not None:
            raise first_error from first_error.cause
