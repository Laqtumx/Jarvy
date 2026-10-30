from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from typing import Any

from jarvy.brain.agent import Agent
from jarvy.brain.router import LocalRouter
from jarvy.safety.audit import AuditLog
from jarvy.tools.system.basic import build_system_tools

ROOT = Path(__file__).resolve().parent


async def run_suite() -> dict[str, Any]:
    scenarios = json.loads((ROOT / "scenarios.json").read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for case in scenarios:
        with tempfile.TemporaryDirectory(prefix="jarvy-eval-") as directory:
            root = Path(directory)
            agent = Agent(
                LocalRouter(),
                build_system_tools([root]),
                AuditLog(root / "audit.jsonl"),
            )
            started = time.perf_counter()
            outcome = await agent.run(case["prompt"])
            latency_ms = (time.perf_counter() - started) * 1000
        actual_tools = [step["tool"] for step in outcome.steps]
        passed = (
            actual_tools == case["expected_tools"]
            and case["response_contains"].lower() in outcome.response.lower()
        )
        results.append(
            {
                "id": case["id"],
                "passed": passed,
                "steps": len(outcome.steps),
                "latency_ms": round(latency_ms, 3),
                "cost_usd": 0.0,
            }
        )
    passed_count = sum(result["passed"] for result in results)
    return {
        "scenarios": len(results),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0,
        "average_steps": round(sum(r["steps"] for r in results) / len(results), 3)
        if results
        else 0,
        "average_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 3)
        if results
        else 0,
        "estimated_cost_usd": 0.0,
        "failures": [result for result in results if not result["passed"]],
    }


if __name__ == "__main__":
    report = asyncio.run(run_suite())
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["pass_rate"] == 1 else 1)
