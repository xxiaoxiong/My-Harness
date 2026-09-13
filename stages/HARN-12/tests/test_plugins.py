import json
import tempfile
import unittest
from collections.abc import Mapping

from harness import (
    GIT_COMMIT_NAME,
    GIT_DIFF_NAME,
    GIT_STATUS_NAME,
    DuplicatePluginError,
    GitSkill,
    Harness,
    HarnessSealedError,
    Hook,
    HookContext,
    InMemoryGitBackend,
    JsonCheckpointStore,
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    PermissionDecision,
    Plugin,
    PromptFragment,
    StaticPolicyEngine,
    Tool,
    ToolError,
    ToolSchema,
)
from harness.core import JsonValue
from harness.state import AgentStatus


class IntentProvider(ModelProvider):
    def __init__(self, name: str, arguments: dict[str, JsonValue]) -> None:
        self._name = name
        self._arguments = arguments
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        state_message = next(
            message.content
            for message in request.messages
            if message.content.startswith("Current State:\n")
        )
        step = json.loads(state_message.split("\n", 1)[1])["current_step"]
        if step == 1:
            action: dict[str, object] = {
                "type": "tool_call",
                "name": self._name,
                "arguments": self._arguments,
            }
        else:
            action = {"type": "final_answer", "answer": "Done."}
        return ModelResponse(
            response_id=f"plugin-response-{step}",
            model=request.model,
            message=ModelMessage(
                MessageRole.ASSISTANT,
                json.dumps(action, separators=(",", ":")),
            ),
            finish_reason="stop",
            usage=None,
            latency_ms=0.0,
        )


class RecordingHook(Hook):
    def __init__(self) -> None:
        self.steps: list[int] = []

    async def before_step(self, context: HookContext) -> None:
        self.steps.append(context.step)


class EchoTool(Tool):
    def __init__(self) -> None:
        self.executions = 0

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="echo",
            description="Echo a value.",
            parameters={"type": "object"},
        )

    async def execute(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        self.executions += 1
        return dict(arguments)


class EverythingPlugin(Plugin):
    def __init__(self) -> None:
        self.tool = EchoTool()
        self.hook = RecordingHook()

    @property
    def name(self) -> str:
        return "everything"

    def setup(self, harness: Harness) -> None:
        harness.register_tool(self.tool)
        harness.register_hook(self.hook)
        harness.register_prompt_fragment(
            PromptFragment("echo-guidance", "Use echo only for harmless values.")
        )
        harness.register_policy(
            StaticPolicyEngine({"echo": PermissionDecision.DENY})
        )


class PluginsAndSkillsTests(unittest.IsolatedAsyncioTestCase):
    async def test_plugin_injects_tool_hook_prompt_and_policy(self) -> None:
        harness = Harness()
        plugin = EverythingPlugin()
        harness.load_plugin(plugin)
        provider = IntentProvider("echo", {"value": "hello"})

        result = await harness.create_agent_loop(
            provider,
            model="plugin-demo-model",
            max_steps=3,
            max_context_chars=6_000,
        ).run("Echo hello.", task_id="plugin-task")

        self.assertEqual(result.status, AgentStatus.FINISHED)
        self.assertEqual(plugin.tool.executions, 0)
        self.assertEqual(result.last_tool_result.error, "tool call denied by policy")  # type: ignore[union-attr]
        self.assertEqual(plugin.hook.steps, [1, 2])
        self.assertIn(
            "Plugin Prompt [echo-guidance]:\nUse echo only for harmless values.",
            provider.requests[0].messages[0].content,
        )
        self.assertIn('"name":"echo"', provider.requests[0].messages[2].content)
        self.assertEqual(harness.plugins, (plugin,))
        self.assertEqual(len(harness.tools), 1)
        self.assertEqual(len(harness.hooks), 1)
        self.assertEqual(len(harness.prompt_fragments), 1)
        self.assertEqual(len(harness.policies), 1)

    async def test_git_skill_capabilities_appear_only_after_loading(self) -> None:
        backend = InMemoryGitBackend(
            changed_files=["README.md"],
            unstaged_diff="-old\n+new",
            staged_diff="+staged",
        )
        harness = Harness()
        self.assertEqual(harness.tools, ())

        skill = GitSkill(backend)
        harness.load_plugin(skill)

        tools = {tool.schema.name: tool for tool in harness.tools}
        self.assertEqual(
            set(tools),
            {GIT_STATUS_NAME, GIT_DIFF_NAME, GIT_COMMIT_NAME},
        )
        self.assertEqual(
            await tools[GIT_STATUS_NAME].execute({}),
            {
                "branch": "main",
                "changed_files": ["README.md"],
                "clean": False,
            },
        )
        self.assertEqual(
            await tools[GIT_DIFF_NAME].execute({"staged": True}),
            {"staged": True, "diff": "+staged"},
        )
        self.assertEqual(harness.plugins, (skill,))
        self.assertEqual(harness.prompt_fragments[0].name, "git-skill")
        self.assertEqual(len(harness.policies), 1)

    async def test_git_commit_waits_then_resumes_through_loaded_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backend = InMemoryGitBackend(changed_files=["harness.py"])
            store = JsonCheckpointStore(directory)
            first_harness = Harness()
            first_harness.load_plugin(GitSkill(backend))
            provider = IntentProvider(
                GIT_COMMIT_NAME,
                {"message": "feat: plugin stage"},
            )
            waiting = await first_harness.create_agent_loop(
                provider,
                model="plugin-demo-model",
                max_steps=3,
                max_context_chars=6_000,
                checkpoint_store=store,
            ).run("Commit the stage.", task_id="git-skill-task")

            self.assertEqual(waiting.status, AgentStatus.WAITING_APPROVAL)
            self.assertEqual(backend.commits, ())
            self.assertIn("Plugin Prompt [git-skill]", provider.requests[0].messages[0].content)

            second_harness = Harness()
            second_harness.load_plugin(GitSkill(backend))
            completed = await second_harness.create_agent_loop(
                IntentProvider(
                    GIT_COMMIT_NAME,
                    {"message": "feat: plugin stage"},
                ),
                model="plugin-demo-model",
                max_steps=3,
                max_context_chars=6_000,
                checkpoint_store=store,
            ).resume("git-skill-task", approve=True)

            self.assertEqual(completed.status, AgentStatus.FINISHED)
            self.assertEqual(backend.commits, ("feat: plugin stage",))
            self.assertEqual(
                completed.last_tool_result.result,  # type: ignore[union-attr]
                {
                    "commit_id": "demo-0001",
                    "message": "feat: plugin stage",
                },
            )

    async def test_duplicate_plugins_and_post_build_registration_are_rejected(
        self,
    ) -> None:
        harness = Harness()
        harness.load_plugin(GitSkill(InMemoryGitBackend()))
        with self.assertRaisesRegex(DuplicatePluginError, "already loaded"):
            harness.load_plugin(GitSkill(InMemoryGitBackend()))

        harness.create_agent_loop(
            IntentProvider(GIT_STATUS_NAME, {}),
            model="plugin-demo-model",
            max_steps=2,
            max_context_chars=4_000,
        )
        self.assertTrue(harness.sealed)
        with self.assertRaises(HarnessSealedError):
            harness.register_prompt_fragment(PromptFragment("late", "Too late."))

    async def test_git_tool_arguments_are_validated(self) -> None:
        harness = Harness()
        harness.load_plugin(GitSkill(InMemoryGitBackend()))
        tools = {tool.schema.name: tool for tool in harness.tools}

        with self.assertRaises(ToolError):
            await tools[GIT_STATUS_NAME].execute([])  # type: ignore[arg-type]
        with self.assertRaises(ToolError):
            await tools[GIT_DIFF_NAME].execute({"staged": "yes"})
        with self.assertRaises(ToolError):
            await tools[GIT_COMMIT_NAME].execute({"message": " "})

    def test_failed_runtime_construction_does_not_seal_harness(self) -> None:
        harness = Harness()

        with self.assertRaisesRegex(ValueError, "greater than zero"):
            harness.create_agent_loop(
                IntentProvider(GIT_STATUS_NAME, {}),
                model="plugin-demo-model",
                max_steps=2,
                max_context_chars=0,
            )

        self.assertFalse(harness.sealed)
        harness.register_prompt_fragment(PromptFragment("still-open", "Continue."))


if __name__ == "__main__":
    unittest.main()
