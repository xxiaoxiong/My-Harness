"""A minimal model-driven agent loop with an explicit step limit."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from harness.model import (
    MessageRole,
    ModelMessage,
    ModelProvider,
    ModelRequest,
)


@unique
class AgentDecision(str, Enum):
    """The only decisions understood by the HARN-02 loop."""

    CONTINUE = "CONTINUE"
    FINISH = "FINISH"


@unique
class AgentRunStatus(str, Enum):
    """Why a minimal agent run stopped."""

    FINISHED = "finished"
    MAX_STEPS_REACHED = "max_steps_reached"


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """The small, provider-neutral result returned by the loop."""

    goal: str
    status: AgentRunStatus
    steps_executed: int
    last_decision: AgentDecision


class InvalidAgentDecision(ValueError):
    """Raised when the model returns anything except a supported decision."""

    def __init__(self, output: str) -> None:
        self.output = output
        super().__init__(
            f"model must return exactly CONTINUE or FINISH; received {output!r}"
        )


class AgentLoop:
    """Feed model decisions back into the next call until the run stops."""

    _INSTRUCTION = (
        "You are controlled by a minimal agent loop. Evaluate the goal and "
        "the conversation progress. Reply with exactly CONTINUE if another "
        "step is needed, or FINISH if the goal is complete. Do not output "
        "anything else."
    )

    def __init__(
        self,
        provider: ModelProvider,
        *,
        model: str,
        max_steps: int,
    ) -> None:
        if not isinstance(model, str):
            raise TypeError("model must be a string")
        normalized_model = model.strip()
        if not normalized_model:
            raise ValueError("model must not be empty")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise TypeError("max_steps must be an integer")
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")

        self._provider = provider
        self._model = normalized_model
        self._max_steps = max_steps

    @property
    def max_steps(self) -> int:
        """Return the hard upper bound on model calls for one run."""

        return self._max_steps

    async def run(self, goal: str) -> AgentRunResult:
        """Run the decision loop for one goal."""

        if not isinstance(goal, str):
            raise TypeError("goal must be a string")
        normalized_goal = goal.strip()
        if not normalized_goal:
            raise ValueError("goal must not be empty")

        messages = [
            ModelMessage(MessageRole.DEVELOPER, self._INSTRUCTION),
            ModelMessage(
                MessageRole.USER,
                self._step_prompt(normalized_goal, step=1),
            ),
        ]

        for step in range(1, self._max_steps + 1):
            response = await self._provider.generate(
                ModelRequest(model=self._model, messages=tuple(messages))
            )
            decision = self._parse_decision(response.message.content)

            if decision is AgentDecision.FINISH:
                return AgentRunResult(
                    goal=normalized_goal,
                    status=AgentRunStatus.FINISHED,
                    steps_executed=step,
                    last_decision=decision,
                )

            if step < self._max_steps:
                messages.extend(
                    (
                        ModelMessage(MessageRole.ASSISTANT, decision.value),
                        ModelMessage(
                            MessageRole.USER,
                            self._step_prompt(normalized_goal, step=step + 1),
                        ),
                    )
                )

        return AgentRunResult(
            goal=normalized_goal,
            status=AgentRunStatus.MAX_STEPS_REACHED,
            steps_executed=self._max_steps,
            last_decision=AgentDecision.CONTINUE,
        )

    def _step_prompt(self, goal: str, *, step: int) -> str:
        return (
            f"Goal:\n{goal}\n\n"
            f"Step: {step} of at most {self._max_steps}.\n"
            "Return exactly CONTINUE or FINISH."
        )

    @staticmethod
    def _parse_decision(output: str) -> AgentDecision:
        normalized_output = output.strip()
        try:
            return AgentDecision(normalized_output)
        except ValueError as error:
            raise InvalidAgentDecision(output) from error
