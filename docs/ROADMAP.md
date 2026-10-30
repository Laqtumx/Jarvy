# Delivery status

- **Phase 0 — foundation:** completed: settings, Gemini/local router, async agent loop, typed registry, CLI/API, clock demo.
- **Phase 1 — memory/safety/files:** initial slice completed: SQLite profile/episodes, assistant memory tools plus view/edit/delete API, audit log, risk policy, allowlisted list/read/create file tools. No shell, app-control, semantic embeddings, or OS keyring yet.
- **Phase 2 — web:** Gemini Google Search grounding returns source URLs and a synthesized summary; optional Playwright adds public-page reading and confirmation-gated accessible clicks. Forms, downloads, multi-tab research, monitors, and richer citation-span mapping remain unimplemented.
- **Phase 3 — chat UI:** initial same-origin web chat implemented, including step summaries and explicit T2 approval button. Token streaming/live progress, cancel controls, and authenticated remote use remain unimplemented.
- **Phase 4 — integrations:** not implemented; OAuth consent and credential vault required.
- **Phase 5 — scheduler:** persistent one-shot reminders, retries-safe due processing, cancel/list tools, API, and UI notifications are implemented. Cron/recurring schedules, external event triggers, and OS notifications remain unimplemented.
- **Phase 6 — voice:** not implemented. The mission asks for wake word “Jarvy”; the original phase text says “Ultron.” Jarvy is the product wake phrase when voice arrives.
- **Phase 7 — vision/macros/self-skills:** not implemented.
- **Phase 8 — eval/hardening:** 10 unit/integration tests and a 30-scenario deterministic baseline eval exist; provider-backed capability evals, load testing, threat modeling, and production deployment hardening remain.

Privacy note: local state stays under `JARVY_DATA_DIR`. With Gemini configured, prompts, tool schemas, and tool results used in reasoning are sent to Google's API.
