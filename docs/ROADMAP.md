# Delivery status

- **Phase 0 — foundation:** completed: settings, Gemini/local router, async agent loop, typed registry, CLI/API, clock demo.
- **Phase 1 — memory/safety/files:** initial slice completed: SQLite profile/episodes, assistant memory tools plus view/edit/delete API, audit log, risk policy, allowlisted list/read/create file tools. No shell, app-control, or semantic embeddings yet.
- **Phase 2 — web:** Gemini Google Search grounding returns source URLs and a synthesized summary; optional Playwright adds public-page reading, confirmation-gated accessible clicks, labelled form filling without submission, and bounded (25 MiB) downloads. Multi-tab research and richer citation-span mapping remain unimplemented; page monitors now poll public HTML/text pages, baseline content, and raise local alerts on changes.
- **Phase 3 — chat UI:** same-origin web chat now streams live planning/tool events over SSE, shows step summaries, and offers explicit T2 approval. Provider token streaming, cancel controls, and authenticated remote use remain unimplemented.
- **Phase 4 — integrations:** initial Google Workspace connector implemented: Gmail search/read/draft/send, primary-calendar list/create, and Drive search/read-text/create-text; OAuth installed-app flow stores tokens in OS keyring. Requires user-created Google OAuth Desktop client, consent, and optional dependencies; Drive update/delete and live provider verification remain.
- **Phase 5 — scheduler:** persistent one-shot reminders, retries-safe due processing, cancel/list tools, API, and UI notifications are implemented. Cron/recurring schedules, external event triggers, and OS notifications remain unimplemented.
- **Phase 6 — voice:** not implemented. The mission asks for wake word “Jarvy”; the original phase text says “Ultron.” Jarvy is the product wake phrase when voice arrives.
- **Phase 7 — vision/macros/self-skills:** not implemented.
- **Phase 8 — eval/hardening:** Unit/integration tests and a 30-scenario deterministic baseline eval exist; provider-backed capability evals, load testing, threat modeling, and production deployment hardening remain.

Privacy note: local state stays under `JARVY_DATA_DIR`. With Gemini/Groq configured, prompts and tool results used in reasoning leave the device for that provider. Google Workspace requests go to Google APIs using refresh credentials stored in the OS keyring; email/calendar data may also be sent to the selected LLM when used as reasoning context.
