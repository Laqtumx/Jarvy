from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jarvy.tools.registry import RiskTier, Tool


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    requires_confirmation: bool
    reason: str


class PermissionEngine:
    """Conservative policy: T2 only executes after an exact approval token; T3 always denies."""

    @staticmethod
    def preview(tool: Tool, arguments: dict[str, Any]) -> str:
        return f"{tool.name}({arguments!r})"

    def authorize(
        self, tool: Tool, arguments: dict[str, Any], approval: str | None = None
    ) -> PermissionDecision:
        if tool.risk is RiskTier.T3:
            return PermissionDecision(False, False, "T3 actions are denied by default.")
        if tool.risk is RiskTier.T2:
            expected = f"APPROVE: {self.preview(tool, arguments)}"
            if approval != expected:
                return PermissionDecision(False, True, expected)
        return PermissionDecision(True, False, "Allowed by risk policy.")
