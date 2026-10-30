# Security model

- Google Workspace OAuth scopes are Gmail read, compose, and send; Calendar events; Drive readonly search/read; and Drive file access for created text files. Refresh tokens are stored in the OS keyring; plaintext/fail keyring backends are rejected. Gmail send and Calendar event creation are T2; drafts are T1. Revoke consent in the Google account security page and run `jarvy disconnect-google` to remove the local token.

- Tool declarations include explicit risk tiers. T0 is read-only; T1 is bounded reversible work; T2 requires the exact preview string; T3 is denied unconditionally. No T2/T3 tools are shipped yet.
- Local file tools resolve paths and enforce the allowlist after symlink resolution. Reads have response limits; create refuses to overwrite existing files.
- Browser and monitor tools permit public HTTP(S) pages only and reject local/private addresses. Monitors fetch bounded HTML/text only and do not access logged-in sessions. Browser clicks and form fills are T2; fills never submit forms. Downloads are capped at 25 MiB, stored under `JARVY_DOWNLOAD_DIR` (inside an allowed directory), and never executed. The persistent browser profile may contain login cookies and stays under `JARVY_DATA_DIR/browser-profile`; use OS-level disk encryption and protect that directory. Application URL checks are defense-in-depth, not a substitute for network egress controls.
- Task/tool decisions are written to a local JSONL audit file, flushed and fsynced. The file uses restrictive owner-only permissions where supported; prompts, form values, message/text fields, and common credential-key names are redacted.
- Tool execution has a timeout and agent planning has a configurable step bound. Tool outputs are explicitly framed as untrusted data in the model prompt/history.
- Configured cloud providers (Gemini/Groq-compatible) receive user prompts and context passed to them. Keep secrets out of prompts. The API defaults to loopback and has no authentication in this development slice: do not bind it to a public interface without adding authentication, TLS, rate limits, and CSRF protections.
- `Ultron stop` exits the CLI session. It does not currently cancel arbitrary OS work because no such tools are exposed.

## Limits

The audit log is append-only by application behavior, not tamper-proof against the machine owner. T2 approval currently protects tool execution only in the interactive CLI flow; the HTTP API accepts a supplied token and must remain trusted/local. Settings do not execute shell commands. Do not use this baseline for payments, credential handling, or destructive actions.
