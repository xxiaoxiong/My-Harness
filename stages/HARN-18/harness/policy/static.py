"""Deterministic tool-name rules for learning and tests."""

from __future__ import annotations

from collections.abc import Mapping

from harness.policy.base import (
    PermissionDecision,
    PermissionRequest,
    PolicyEngine,
)


class StaticPolicyEngine(PolicyEngine):
    """Look up decisions by normalized Tool name with a fixed default."""

    def __init__(
        self,
        rules: Mapping[str, PermissionDecision],
        *,
        default: PermissionDecision = PermissionDecision.ALLOW,
    ) -> None:
        if not isinstance(rules, Mapping):
            raise TypeError("policy rules must be an object")
        if not isinstance(default, PermissionDecision):
            raise TypeError("default must be a PermissionDecision")

        normalized: dict[str, PermissionDecision] = {}
        for name, decision in rules.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("policy tool names must not be empty")
            if not isinstance(decision, PermissionDecision):
                raise TypeError("policy rule values must be PermissionDecision values")
            normalized[name.strip()] = decision
        self._rules = normalized
        self._default = default

    @property
    def rules(self) -> Mapping[str, PermissionDecision]:
        return dict(self._rules)

    @property
    def default(self) -> PermissionDecision:
        return self._default

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if not isinstance(request, PermissionRequest):
            raise TypeError("request must be a PermissionRequest")
        return self._rules.get(request.tool_call.name, self._default)
