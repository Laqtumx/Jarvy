from __future__ import annotations

import argparse
import asyncio

from jarvy.core.config import get_settings
from jarvy.core.runtime import build_runtime
from jarvy.scheduler.service import ReminderScheduler
from jarvy.tools.integrations.google_auth import connect_google
from jarvy.tools.web.monitor import PageMonitorService


async def _chat() -> None:
    agent, memory, jobs, monitors = build_runtime()
    scheduler_task = asyncio.create_task(ReminderScheduler(jobs).run())
    monitor_task = asyncio.create_task(PageMonitorService(monitors).run())
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
    for task in (scheduler_task, monitor_task):
        task.cancel()
    await asyncio.gather(scheduler_task, monitor_task, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(prog="jarvy", description="Local-first personal assistant")
    parser.add_argument(
        "command",
        nargs="?",
        default="chat",
        choices=("chat", "serve", "connect-google", "disconnect-google"),
    )
    parser.add_argument("client_secrets_file", nargs="?")
    args = parser.parse_args()
    if args.command == "chat":
        asyncio.run(_chat())
    elif args.command == "connect-google":
        if not args.client_secrets_file:
            parser.error("connect-google requires the path to your OAuth desktop client JSON")
        connect_google(args.client_secrets_file)
        print("Google connected. Tokens are stored in the OS keyring.")
    elif args.command == "disconnect-google":
        from jarvy.tools.integrations.google_auth import GoogleCredentialStore

        removed = GoogleCredentialStore().delete()
        print(
            "Google credentials removed from the OS keyring."
            if removed
            else "No Google credentials found."
        )
    else:
        import uvicorn

        settings = get_settings()
        uvicorn.run("jarvy.interface.api:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
