from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class RiskTier(IntEnum):
    T0 = 0  # read-only
    T1 = 1  # reversible, low-impact change
    T2 = 2  # consequential; exact-action confirmation required
    T3 = 3  # critical; denied unless a future explicit re-auth flow exists


ToolHandler = Callable[..., Any | Awaitable[Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: RiskTier
    handler: ToolHandler
    timeout_seconds: float = 15.0

    async def invoke(self, arguments: dict[str, Any]) -> Any:
        async def run() -> Any:
            result = self.handler(**arguments)
            return await result if inspect.isawaitable(result) else result

        return await asyncio.wait_for(run(), timeout=self.timeout_seconds)

    def gemini_declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def declarations(self) -> list[dict[str, Any]]:
        return [tool.gemini_declaration() for tool in self._tools.values()]
