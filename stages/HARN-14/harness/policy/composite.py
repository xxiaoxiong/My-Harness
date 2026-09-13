"""Composition of independently registered PolicyEngine instances."""

from __future__ import annotations

from collections.abc import Iterable

from harness.policy.base import (
    InvalidPermissionDecision,
    PermissionDecision,
    PermissionRequest,
    PolicyEngine,
)


class CompositePolicyEngine(PolicyEngine):
    """Combine policies with DENY > REQUIRE_APPROVAL > ALLOW precedence."""

    def __init__(self, policies: Iterable[PolicyEngine] = ()) -> None:
        self._policies = tuple(policies)
        if not all(isinstance(policy, PolicyEngine) for policy in self._policies):
            raise TypeError("policies must contain only PolicyEngine values")

    @property
    def policies(self) -> tuple[PolicyEngine, ...]:
        return self._policies

    async def decide(self, request: PermissionRequest) -> PermissionDecision:
        if not isinstance(request, PermissionRequest):
            raise TypeError("request must be a PermissionRequest")

        combined = PermissionDecision.ALLOW
        for policy in self._policies:
            decision = await policy.decide(request)
            if not isinstance(decision, PermissionDecision):
                raise InvalidPermissionDecision(
                    f"{type(policy).__name__}.decide must return "
                    "a PermissionDecision"
                )
            if decision is PermissionDecision.DENY:
                return decision
            if decision is PermissionDecision.REQUIRE_APPROVAL:
                combined = decision
        return combined
