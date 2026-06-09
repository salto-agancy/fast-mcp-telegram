# ADR 0004: QR Login Auth — Simplified Self-Service Onboarding

## Status

Proposed

## Date

2026-06-09

## Context

The OIDC + elicitation approach (ADR 0002, ADR 0003) was over-engineered for the actual use case. Research revealed:

1. **Elicitation protocol support is scarce** — The most popular MCP clients (Claude Desktop, Cline, Continue.dev) do NOT support form-mode elicitation. Only Cursor 2.0+, VS Code Insiders, and Claude Code CLI support it. This makes an elicitation-dependent auth path effectively client-locked.

2. **Telethon has built-in QR login** — `client.qr_login()` generates a `tg://login?token=...` URL. User scans it from Telegram mobile → MTProto session created. No phone number, no verification code, no 2FA input needed. This eliminates the entire elicitation state machine.

3. **Telegram has a proper OIDC provider** — `https://oauth.telegram.org/.well-known/openid-configuration` supports Authorization Code + PKCE, RS256 JWTs, and issues `id_token` with `sub` (Telegram user_id). This can serve as the OIDC provider, removing the need for external Auth0/Clerk.

4. **External OIDC providers add friction without benefit** — Users of a Telegram MCP server are already on Telegram. Making them also log in via Google/GitHub/Auth0 before accessing Telegram tools is pointless indirection.

5. **The JWT has no value for the web setup path** — Web setup (`/setup`) is already public. Anyone can visit, enter phone/code/2FA, and get a bearer token. The OIDC JWT doesn't gate this path.

### Session Persistence Research: MCP Stateful Sessions

Research on MCP session persistence via the Streamable HTTP transport revealed that FastMCP 3.4.0 and the underlying MCP SDK (2025-11-25 spec) have full built-in session management:

**MCP Spec (2025-11-25):**
- Server MAY assign a session ID at initialization time via the `Mcp-Session-Id` header on the `InitializeResult` response.
- Clients MUST echo the `Mcp-Session-Id` header on all subsequent HTTP requests to that session.
- Server terminates a session by responding with HTTP 404; client then re-initializes without a session ID to start fresh.
- Clients SHOULD send HTTP DELETE with `Mcp-Session-Id` to explicitly terminate the session.
- SSE streams are resumable via `Last-Event-ID` header (event replay on reconnect).
- Session IDs should be globally unique and cryptographically secure (UUID, JWT, or hash).

**MCP Spec (2025-06-18 / DRAFT — no session management):**
- The latest draft spec **removes** session management entirely: "None of these mechanisms are part of this revision."
- Servers supporting only this revision should ignore `Mcp-Session-Id`, and respond 405 to GET/DELETE.
- Version negotiation exists: older clients can use the 2025-11-25 spec for session support.

**FastMCP 3.4.0 Implementation (implements 2025-11-25 spec):**
- `StreamableHTTPSessionManager._handle_stateful_request()` handles session lifecycle:
  - On first request (no `Mcp-Session-Id`): generates a new session ID (UUID hex), stores transport in `_server_instances` dict, returns session ID in response header.
  - On subsequent requests (with `Mcp-Session-Id`): looks up existing transport, handles the request.
  - On unknown/expired session ID: returns HTTP 404 (per spec).
  - On DELETE with session ID: terminates the session.
  - Session ownership: `_session_owners` maps session_id → `AuthorizationContext` (the credential). Only the credential that created the session can use it — mismatched credential returns 404 "Session not found".
  - Configurable idle timeout (default: None; recommended: 1800s).
- `Context.session_id` property reads the `mcp-session-id` header for StreamableHTTP, or generates a UUID for STDIO/SSE. Cached on session object.
- `Context.get_state(key)` / `ctx.set_state(key, value)`: session-scoped state store. Keys are auto-prefixed with `session_id:`. Serializable values persist across requests within the same session. 1-day TTL default.
- State set during `on_initialize` middleware persists to subsequent tool calls when using the same session (same session ID on reconnect).

**Key implication for QR login:**
The `Mcp-Session-Id` header means the MCP client already identifies itself with a persistent session ID on every request. This session ID can be used for the "no-paste" QR login flow: the session_id is embedded in the QR callback URL, so when the user scans the QR, the callback knows which MCP session to link to the Telegram identity — without the user pasting anything. The session_id is already flowing on every HTTP request from the client.

## Decision

Scrap the OIDC + elicitation approach entirely. Replace with Telethon QR login as the primary auth path, with web setup (phone/code/2FA) and URL path auth as fallbacks.

### Decision 1: No OIDC, No Elicitation Tools, No Setup Tool

Remove all OIDC-related code:
- `TG_OIDC_ISSUER`, `TG_OIDC_AUDIENCE` env vars
- `OidcTokenVerifier`, `JWTVerifier` integration
- Elicitation state machine (`setup_state` transitions, TTL enforcement, retry tracking)
- Elicitation MCP tools (`oidc_setup_start`, `oidc_setup_phone`, `oidc_setup_code`, `oidc_setup_password`)
- `oidc_identity` table and `setup_state` table in SQLite
- `register_elicitation_tools()` and `create_oidc_verifier()` paths in server.py
- `principal_resolver.py` (OIDC sub → Telegram identity mapping)
- All related test files

The `feature/oidc-phase1-storage` branch is archived for reference.

### Decision 2: QR Login as Primary Auth Path

When an unauthenticated user calls any MCP tool:

1. The tool detects no auth context (no valid bearer token / no Telethon session).
2. The tool returns a response containing both:
   - **QR login URL**: a server endpoint that renders a QR code (Telethon `qr_login().url`).
   - **Web setup URL**: the existing `/setup` page.
3. User scans the QR from Telegram mobile → Telethon creates a MTProto session → `.session` file saved.
4. Server generates a bearer token bound to this session.
5. Bearer token is returned to the user (via web page, bot DM, or URL callback).
6. User configures the bearer token (or URL path auth link) in their MCP client.
7. Subsequent requests include the credential → server validates → full access.

### Decision 3: No Dedicated `setup` Tool

The elicitation/auth flow is embedded in every tool via a **shared middleware or decorator** (`require_auth`). This decorator:

1. Checks whether the request has a valid authenticated session (bearer token → Telethon session).
2. If not authenticated: returns a structured response with auth guidance — the QR login URL and web setup URL — instead of executing the tool's logic.
3. If authenticated but the Telethon session needs re-auth (expired, disconnected): triggers the same auto-auth flow (returns QR URL + web setup URL).
4. If fully authenticated: passes through to the tool's actual handler.

Pattern:
```python
@mcp.tool()
@require_auth
async def send_message(chat_id: str, text: str, ctx: Context) -> str:
    # Only reached if user is authenticated
    ...
```

### Decision 4: 3-Tier Auth Capacity Spectrum

All tools are always visible in the tool list, regardless of auth state. Unauthenticated users see the full capability set (advertising). Each tool returns auth guidance when called without credentials.

The three tiers, from least to most capable client:

| Tier | Mechanism | How it works | For whom |
|------|-----------|-------------|----------|
| **3 — URL path auth** | Token embedded in URL | Server rewrites `/v1/url_auth/{token}/mcp/...` → injects `Authorization: Bearer {token}` header | Clients that can't set HTTP headers but can manipulate URLs (SSE, basic HTTP, curl). Token pre-obtained via web setup. |
| **2 — Bearer token** | `Authorization: Bearer {token}` header | User completes web setup (phone/code/2FA) → gets bearer token → configures it in MCP client | Any HTTP-capable MCP client. Existing flow, unchanged. |
| **1 — QR login** | Scan QR from Telegram mobile | User calls any tool → gets QR URL → scans from phone → session created → bearer token generated → reconnects with token | Simplest UX. No phone typing, no code entry, no 2FA. Requires Telegram mobile app. |

### Decision 5: Unified QR/Form Web Setup Page + URL Path Auth Enhancement

The existing web setup flow (`/setup`) is replaced with a single unified page that shows both auth methods:

- **QR code display** (Telethon `qr_login().url` rendered as QR image)
- **Phone input** (existing phone/code/2FA form, for users without Telegram mobile)

Both paths are presented on the same page. The user chooses whichever is most convenient.

On successful auth completion (QR scan or phone form), the success page shows:
- The bearer token (existing)
- A URL path auth link: `/v1/url_auth/{token}/mcp/...` (new)
- Instructions for configuring the MCP client

The unified page ensures even the least capable clients can connect, and the QR path gets maximum exposure.

### Decision 6: Session Linking via MCP `session_id` (No-Paste for Live Connections)

Each Streamable HTTP connection carries a persistent `Mcp-Session-Id` header set by the MCP client. This session ID is already flowing on every HTTP request — we use it for the "no-paste" flow:

1. Unauthenticated MCP tool call arrives with `Mcp-Session-Id: abc123`.
2. Server generates a **QR login URL** with the session ID embedded: `/qr?session=abc123`.
3. User opens the QR URL (from tool response or web page) and scans it from Telegram mobile.
4. Telethon QR login callback fires — the server reads the `session=abc123` parameter from the callback URL.
5. The callback maps: `session_id abc123 → {telegram_user_id, .session file, generated bearer_token}`.
6. The MCP client's **next request** (same `Mcp-Session-Id: abc123`) is now authenticated — the server sees the session is linked. No paste needed.

For the **web setup path** (phone/code/2FA), the same session ID is embedded in the setup URL:
```
/setup?session=abc123
```
On form completion, the callback uses the same mapping.

**Reconnection with bearer token:** When the MCP client disconnects and a new session starts (new `Mcp-Session-Id`), the bearer token is used for reconnection. The user pastes the token one time (obtained from the success page). Server validates the token, resolves it to the Telegram identity, and links the new session ID to the existing identity.

**Dual model summary:**
- **Live connection (same session_id):** QR scan links the session_id → Telegram identity. No paste.
- **Reconnection (new session_id):** Bearer token (from success page) links the new session_id. One paste.
- **New device/browser:** Bearer token from previous web setup. One paste.

### Decision 7: Session Re-Auth Detection

The `require_auth` decorator also checks whether the Telethon session is still valid (connected, not expired). If a previously authenticated user's session has expired or disconnected:

- The tool returns the same auth guidance (QR URL + web setup URL) as for unauthenticated users.
- No distinction between "never authed" and "session expired" at the UX level — both get the same redirect to re-auth.

### Decision 8: Keep Dual Auth, Pre-Release Version

- Bearer token support stays (no drop).
- Web setup stays (no retirement, not deprecated).
- Version stays pre-release (0.30.0, not 1.0.0).

### Decision 9: No Changes to URL Path Auth Middleware

The existing middleware at `auth_middleware.py` (rewriting `/v1/url_auth/{token}/mcp/...` → `/v1/mcp/...` with Bearer header injection) works as-is. No changes needed.

### Decision 10: Stateful Sessions via Mcp-Session-Id

FastMCP 3.4.0 (and the MCP SDK it depends on) implements the 2025-11-25 Streamable HTTP spec, which provides full session management:

- **Session creation:** The `StreamableHTTPSessionManager` generates a UUID session ID on first request (no `Mcp-Session-Id` header) and returns it in the `Mcp-Session-Id` response header.
- **Session echo:** The MCP client MUST echo `Mcp-Session-Id` on all subsequent HTTP requests. This is the client's responsibility per spec — well-behaved clients already do this.
- **Session-scoped state:** `ctx.get_state(key)` / `ctx.set_state(key, value)` provide per-session key-value storage with 1-day TTL. State survives across requests within the same session.
- **Owner binding:** `_session_owners` maps session_id → credential. Only the credential that created the session can use it (security against session hijacking).
- **Session termination:** Server returns 404 to end a session; client re-initializes. Client can also send DELETE.
- **Idle timeout:** Configurable timeout (recommended: 30 min). Expired sessions are automatically cleaned up.

We use `ctx.set_state()` to store the Telegram user identity linked to this session. On tool entry, `require_auth` checks session state first (fast path), then falls back to bearer token validation.

### Decision 11: No Bot DM for Token Delivery

Sending the bearer token via Telegram bot DM was considered but rejected:

- **User would need to start the bot first** — the bot can't initiate a DM with an unknown user. A deep link (`tg://resolve?domain=...`) adds extra steps.
- **Token is already on the web page** — the success page shows the token after QR/phone auth. No additional delivery channel needed.
- **QR login doesn't reveal user identity** — the QR callback gives us a Telethon session, but we don't know the user's Telegram handle to DM them. We'd need to ask the API which adds latency and complexity.
- **Deep links require client app support** — not all MCP clients can open `tg://` links reliably.
- **Stateful session handles the "no paste" case** — for the live MCP connection, the session ID links the QR auth directly. No token delivery needed at all.

Decision: No bot DM. Token delivery is exclusively through the unified web setup page.

## Consequences

### Positive

- ✅ **Radically simpler architecture** — no JWT verifier, no JWKS, no OIDC provider, no elicitation state machine, no OIDC tables, no 4 MCP tools. Just: check auth → QR or web setup → done.
- ✅ **Fewer env vars** — no `TG_OIDC_ISSUER`, no `TG_OIDC_AUDIENCE`, no `TG_DATABASE_URL`. The server needs nothing beyond existing config.
- ✅ **No elicitation protocol dependency** — QR login works through URL-mode elicitation or a direct web page. Any MCP client that can open a URL works.
- ✅ **No phone/code/2FA input for QR path** — user scans QR from phone. That's it. 2FA is handled transparently by Telegram mobile.
- ✅ **All tools advertise server capabilities** — unauthenticated users see the full tool list (8+ tools) in their client's tool explorer.
- ✅ **3-tier spectrum covers every client type** — from the most basic (URL path auth) to the most streamlined (QR login).
- ✅ **No-paste for live connections** — the MCP `Mcp-Session-Id` header allows session linking via QR callback. User scans from phone, session becomes authenticated on next request.
- ✅ **Stateful sessions with persistence** — `ctx.get_state()/set_state()` provides session-scoped state across requests. Bearer tokens bridge reconnection to new sessions.
- ✅ **Dual model covers all scenarios** — session_id for live (no paste), bearer token for reconnect (one paste). User keeps both.
- ✅ **Backward compatible** — existing bearer token deployments continue working. Existing web setup flow unchanged.

### Negative

- ⚠️ **QR login requires Telegram mobile app** — the QR URL is a `tg://` deep link that only works from Telegram's mobile app. Users on Telegram Desktop need to open their phone.
- ⚠️ **QR login is async user-interaction** — the Telethon client must stay connected waiting for the QR scan. Requires a polling or callback mechanism.
- ⚠️ **No SSO/enterprise integration** — if someone wants Auth0/Google/GitHub auth, they don't get it. This can be added later as a separate path if demand surfaces.

### Neutral

- The current `feature/oidc-phase1-storage` branch contains working OIDC storage code. If the OIDC path is ever revisited, it's archived at commit `e07ca62`.
- Telethon sessions continue to use `.session` files — no change to session storage.
- ACL system is unaffected — still uses `@username`/`+phone`/`user_id` principals.

## Alternatives Considered

### Keep OIDC as Optional Enterprise Path

Rejected: Maintains N+1 auth mechanisms. The current implementation only covers storage (Phase 1) — verifier, elicitation, and tools are incomplete. Adding an "optional OIDC" path later would be cleaner than maintaining two unfinished auth branches.

### Single `setup` MCP Tool Instead of In-Tool Auth

Rejected earlier in the design process. The user decided that all tools should surface auth guidance (the tool list advertises capabilities), making a dedicated `setup` tool redundant.

### Continue with 4 Elicitation MCP Tools

Rejected: 3 out of 5 most popular MCP clients don't support form-mode elicitation (Claude Desktop, Cline, Continue.dev). This would make the primary auth path inaccessible to most users.

## Migrating from OIDC Branch

The `feature/oidc-phase1-storage` branch is archived. No migration needed — the OIDC code was never merged to master. Existing deployments on master have no OIDC dependencies and no DB tables.

## References

- [ADR 0002](./0002-oidc-self-service-auth.md) — Superseded OIDC approach
- [ADR 0003](./0003-oidc-phase4-scope-based-auth.md) — Superseded Phase 4 draft
- [Telethon QR Login Documentation](https://docs.telethon.dev/en/stable/modules/client.html#TelethonClient.qr_login)
- [Telegram OIDC Provider](https://oauth.telegram.org/.well-known/openid-configuration)
- [MCP Elicitation Specification](https://spec.modelcontextprotocol.io/specification/2025-03-26/basic/elicitation-considerations/)
- [MCP Streamable HTTP — 2025-11-25 (with session management)](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
- [MCP Streamable HTTP — DRAFT (without session management)](https://modelcontextprotocol.io/specification/draft/basic/transports/streamable-http)
- [MCP Streamable HTTP RFC — Session ID design discussion](https://github.com/modelcontextprotocol/modelcontextprotocol/pull/206)
- [MCP Everything Server — Streamable HTTP reference implementation](https://github.com/modelcontextprotocol/servers/blob/main/src/everything/transports/streamableHttp.ts)
- [FastMCP Context — session state example](https://github.com/prefecthq/fastmcp/blob/main/docs/servers/context.mdx)
- [FastMCP Persistent State Example](https://github.com/prefecthq/fastmcp/blob/main/examples/persistent_state/README.md)
