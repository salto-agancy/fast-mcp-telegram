# fast-mcp-telegram

Telegram MCP server (stdio, http-no-auth, http-auth). Full setup, ACL matrix, and dev workflow: [CONTRIBUTING.md](CONTRIBUTING.md).

## Session Corrections

### 2026-06-09 — OIDC Phase 1 (Storage + Elicitation) Refactor

- **Issue**: Assumed primary deployment was stdio when analyzing lockfile need
  - **Correct approach**: Primary deployment is HTTP/SSE. Lockfile was rejected because DB atomic UPDATE + Telethon MTProto serialization is sufficient. No lockfile.
- **Issue**: Proposed telegram_session DB table for Telethon metadata mirroring
  - **Correct approach**: Removed entirely — per-user `.session` files natively persist auth keys, entities, and update state. No DB mirror needed.
- **Issue**: Wrapped PyJWKClient in custom TokenVerifier for JWT validation
  - **Correct approach**: Use FastMCP's built-in `JWTVerifier` — no custom wrapper. Configured via `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE`.
- **Issue**: Used `.sessions/` default for Telethon session files (test-friendly but not Docker-volume-friendly)
  - **Correct approach**: Default to `~/.config/fast-mcp-telegram/sessions/` (design path), keep `TG_SESSION_DIR` env var for override.
- **Issue**: Removed `oidc_` prefix from session filenames for aesthetic reasons
  - **Correct approach**: Keep `oidc_` prefix — distinguishes OIDC sessions from bearer sessions at the filename level.
- **Issue**: Saved OIDC identity BEFORE state transition (`oidc_setup_code`, `oidc_setup_password`)
  - **Correct approach**: Transition state FIRST, save identity AFTER. If identity save fails, the state is still in a valid intermediate state rather than orphaned in COMPLETED with no identity row.
- **Issue**: TTL sweep task remained in code importing a deleted function (`delete_expired`)
  - **Correct approach**: Removed entire `ttl_sweep_task()` from `oidc_integration.py` and `server.py`. TTL enforced inline via `WHERE updated_at >= ?` on every state UPDATE. No background sweep.
- **Issue**: TOCTOU race in `start_elicitation()` — SELECT then INSERT
  - **Correct approach**: Single atomic `INSERT OR IGNORE` + SELECT. Concurrent double-insert avoided.
- **Issue**: TOCTOU race in `record_retry()` — read retry_count then UPDATE
  - **Correct approach**: Single atomic `UPDATE ... SET retry_count = retry_count + 1 WHERE retry_count < :max`. Rowcount check for exhausted vs active state.
- **Issue**: `tg_code_hash` parameter in `transition_state()` never passed by any caller
  - **Correct approach**: Removed parameter from function signature and body. Column stays in schema for backward compat but no code path populates it (phone_code_hash flows through metadata JSON).
- **Issue**: Dead metadata write in `oidc_setup_start()` (oidc_sub/issuer written to metadata JSON despite being passed as params to every subsequent call)
  - **Correct approach**: Removed — sub/issuer are function parameters throughout the elicitation flow, never read back from metadata.
- **Issue**: First self-review attempt used wrong model (qwen3.7-max failed)
  - **Correct approach**: Use DeepSeek Flash (`deepseek-v4-flash`) via the `opencode` provider. 3 sub-agents: code quality, safe simplification, ADR/docs review.
- **Issue**: Sourcery used via GitHub PR integration instead of CLI
  - **Correct approach**: Run `sourcery review --fix` from CLI for auto-fixes, then `ruff format` + `ruff check --fix --unsafe-fixes`. User explicitly requested CLI mode.

### 2026-05-27
- **Issue**: Tried ACL live testing via Cursor MCP (`telegram-dev` or http-auth URL with fixed `Authorization`)
  - **Correct approach**: ACL is not viable through Cursor MCP (stdio has no ACL; URL MCP uses a fixed bearer; stale MCP subprocesses). Use `pytest tests/test_session_acl.py tests/test_mcp_tool_acl_integration.py`, HTTP curl against a local http-auth server, and [`scripts/acl_mcp_smoke.sh`](scripts/acl_mcp_smoke.sh) (reads bearer tokens from `acl.dev.yaml`, not hardcoded placeholders) — see [CONTRIBUTING.md § ACL development and testing](CONTRIBUTING.md#acl-development-and-testing-not-via-cursor-mcp)
- **Issue**: Started http-auth ACL server from wrong cwd, or `source .env.local` in zsh for `MTPROTO_PROXY`
  - **Correct approach**: Start the server from the **project root** so Python loads `.env` / `.env.local` via dotenv; do not `source .env.local` in zsh — proxy URLs with `&` break shell parsing
- **Issue**: Assumed ACL applies in stdio or without per-bearer sessions
  - **Correct approach**: ACL enforces only with `SERVER_MODE=http-auth` and `ACL_ENABLED=true`; each bearer in `acl.dev.yaml` needs a matching `{token}.session` via `http://127.0.0.1:8765/setup`
- **Issue**: Expected `connection.py` stdio fix without restarting Cursor MCP
  - **Correct approach**: After changing server code used by **telegram-dev**, restart **telegram-dev** in Cursor’s MCP panel — stale subprocess keeps old behavior
- **Issue**: Risk of committing real ACL bearer tokens
  - **Correct approach**: Keep `acl.dev.yaml` gitignored; never commit real bearer tokens (template only: `acl.dev.yaml.example`)
- **Issue**: "Repeat the sourcery-pr-cycle" interpreted as running another full PR → Sourcery → fix loop
  - **Correct approach**: User may mean publish/commit the skill to master, not re-execute the review cycle. Confirm intent when ambiguous.
- **Issue**: Treated phase closeout as blocked until all follow-up PRs merge, or assumed phase 1 code cannot land on master while follow-ups are open
  - **Correct approach**: Phase 1 code can be on master while follow-up PRs (e.g. #56) remain open; closeout means merge all **blocking** follow-ups first, not every open PR.
- **Issue**: Posted Telegram release announcement before release CI finished
  - **Correct approach**: After `gh release create`, verify CI/checks are green on the release tag or master commit (`gh run list`, `gh release view`); do not post Telegram until release is published **and** CI is green unless the user explicitly overrides — see [release-notes skill](.cursor/skills/release-notes/SKILL.md)
