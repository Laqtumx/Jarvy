from __future__ import annotations

import argparse
import asyncio

from jarvy.core.config import get_settings
from jarvy.core.runtime import build_runtime
from jarvy.scheduler.service import ReminderScheduler


async def _chat() -> None:
    agent, memory, jobs = build_runtime()
    scheduler_task = asyncio.create_task(ReminderScheduler(jobs).run())
    settings = get_settings()
    if settings.gemini_api_key and settings.groq_api_key:
        model_label = "Gemini → Groq fallback"
    elif settings.gemini_api_key:
        model_label = "Gemini"
    elif settings.groq_api_key:
        model_label = "Groq"
    else:
        model_label = "local fallback"
    print(
        f"Jarvy ({model_label}) — type 'exit' to quit. "
        "'Ultron stop' shuts down the current session."
    )
    while True:
        try:
            prompt = (await asyncio.to_thread(input, "You> ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if prompt.lower() in {"exit", "quit"}:
            break
        if prompt.lower() == "ultron stop":
            print("Jarvy> Session stopped.")
            break
        if not prompt:
            continue
        result = await agent.run(prompt)
        if result.pending_approval:
            print(f"Jarvy> {result.response}")
            try:
                token = input("Approval token (blank to cancel)> ").strip()
            except (EOFError, KeyboardInterrupt):
                token = ""
            if token:
                result = await agent.run(prompt, approval=token)
        print(f"Jarvy> {result.response}")
        memory.log_episode(prompt, result.response)
    scheduler_task.cancel()
    try:
        await scheduler_task
    except asyncio.CancelledError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvy", description="Local-first personal assistant")
    parser.add_argument("command", nargs="?", default="chat", choices=("chat", "serve"))
    args = parser.parse_args()
    if args.command == "chat":
        asyncio.run(_chat())
    else:
        import uvicorn

        settings = get_settings()
        uvicorn.run("jarvy.interface.api:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
