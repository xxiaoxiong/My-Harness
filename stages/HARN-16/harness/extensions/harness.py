"""Composition root populated by Plugins before creating an Agent Runtime."""

from __future__ import annotations

from harness.context import (
    DEFAULT_TOOL_AGENT_SYSTEM_PROMPT,
    ContextBuilder,
    HistorySummarizer,
)
from harness.extensions.base import (
    DuplicatePluginError,
    DuplicatePromptFragmentError,
    HarnessSealedError,
    Plugin,
    PromptFragment,
)
from harness.hooks import Hook, HookManager
from harness.model import ModelProvider
from harness.policy import CompositePolicyEngine, PolicyEngine
from harness.runtime import CheckpointStore, ToolAgentLoop
from harness.tools import IdempotencyStore, Tool, ToolExecutor, ToolRegistry


class Harness:
    """Collect extension contributions and build a deterministic Agent Loop."""

    def __init__(
        self,
        *,
        base_system_prompt: str = DEFAULT_TOOL_AGENT_SYSTEM_PROMPT,
        idempotency_store: IdempotencyStore | None = None,
    ) -> None:
        if not isinstance(base_system_prompt, str) or not base_system_prompt.strip():
            raise ValueError("base_system_prompt must not be empty")
        if idempotency_store is not None and not isinstance(
            idempotency_store,
            IdempotencyStore,
        ):
            raise TypeError("idempotency_store must be an IdempotencyStore or null")
        self._base_system_prompt = base_system_prompt.strip()
        self._tools = ToolRegistry()
        self._hooks = HookManager()
        self._prompt_fragments: dict[str, PromptFragment] = {}
        self._policies: list[PolicyEngine] = []
        self._plugins: dict[str, Plugin] = {}
        self._idempotency_store = idempotency_store
        self._sealed = False

    @property
    def tools(self) -> tuple[Tool, ...]:
        return self._tools.list_tools()

    @property
    def hooks(self) -> tuple[Hook, ...]:
        return self._hooks.hooks

    @property
    def prompt_fragments(self) -> tuple[PromptFragment, ...]:
        return tuple(self._prompt_fragments.values())

    @property
    def policies(self) -> tuple[PolicyEngine, ...]:
        return tuple(self._policies)

    @property
    def plugins(self) -> tuple[Plugin, ...]:
        return tuple(self._plugins.values())

    @property
    def sealed(self) -> bool:
        return self._sealed

    def register_tool(self, tool: Tool) -> None:
        self._require_open()
        self._tools.register(tool)

    def register_hook(self, hook: Hook) -> None:
        self._require_open()
        self._hooks.register(hook)

    def register_prompt_fragment(self, fragment: PromptFragment) -> None:
        self._require_open()
        if not isinstance(fragment, PromptFragment):
            raise TypeError("fragment must be a PromptFragment")
        if fragment.name in self._prompt_fragments:
            raise DuplicatePromptFragmentError(
                f"prompt fragment is already registered: {fragment.name}"
            )
        self._prompt_fragments[fragment.name] = fragment

    def register_policy(self, policy: PolicyEngine) -> None:
        self._require_open()
        if not isinstance(policy, PolicyEngine):
            raise TypeError("policy must be a PolicyEngine")
        self._policies.append(policy)

    def load_plugin(self, plugin: Plugin) -> None:
        self._require_open()
        if not isinstance(plugin, Plugin):
            raise TypeError("plugin must be a Plugin")
        name = plugin.name
        if not isinstance(name, str) or not name.strip():
            raise ValueError("plugin name must not be empty")
        normalized_name = name.strip()
        if normalized_name in self._plugins:
            raise DuplicatePluginError(
                f"plugin is already loaded: {normalized_name}"
            )
        plugin.setup(self)
        self._plugins[normalized_name] = plugin

    def create_agent_loop(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_steps: int,
        max_context_chars: int,
        checkpoint_store: CheckpointStore | None = None,
        summarizer: HistorySummarizer | None = None,
        recent_history_messages: int = 4,
        summary_max_chars: int = 400,
    ) -> ToolAgentLoop:
        """Seal extension registration and assemble the Runtime dependencies."""

        context_builder = ContextBuilder(
            system_prompt=self._system_prompt(),
            max_context_chars=max_context_chars,
            summarizer=summarizer,
            recent_history_messages=recent_history_messages,
            summary_max_chars=summary_max_chars,
        )
        loop = ToolAgentLoop(
            provider,
            model=model,
            max_steps=max_steps,
            tool_executor=ToolExecutor(
                self._tools,
                idempotency_store=self._idempotency_store,
            ),
            context_builder=context_builder,
            checkpoint_store=checkpoint_store,
            hook_manager=self._hooks,
            policy_engine=CompositePolicyEngine(self._policies),
        )
        self._sealed = True
        return loop

    def _system_prompt(self) -> str:
        sections = [self._base_system_prompt]
        sections.extend(
            f"Plugin Prompt [{fragment.name}]:\n{fragment.content}"
            for fragment in self._prompt_fragments.values()
        )
        return "\n\n".join(sections)

    def _require_open(self) -> None:
        if self._sealed:
            raise HarnessSealedError(
                "Harness is sealed after creating an Agent Loop"
            )
