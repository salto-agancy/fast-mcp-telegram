# ADR 0003: OIDC Phase 4 — In-Tool Auth & Elicitation

> ⚠️ **SUPERSEDED** — This draft was never finalized. The entire OIDC/elicitation approach has been replaced by Telethon QR login. See [ADR 0004](0004-qr-login-auth.md).

## Status

SUPERSEDED — Replaced by [ADR 0004](0004-qr-login-auth.md)

## Context

ADR 0002 defined a 4-tool elicitation flow (oidc_setup_start, oidc_setup_phone,
oidc_setup_code, oidc_setup_password) for linking a Telegram account to an OIDC
identity. It had a fundamental architectural gap: users with valid OIDC JWTs
but no Telegram mapping could not call any MCP tools — including the elicitation
tools — because `OidcTokenVerifier.verify_token()` returned `None` for unmapped
users. Chicken-and-egg.

Additionally, the Phase 4 plan in ADR 0002 ("Drop bearer token support. Retire
`web_setup.py`. Update documentation and examples. Bump major version.") was
re-evaluated against the actual client ecosystem.

### Client Capability Research

The MCP elicitation protocol (modelcontextprotocol.io) defines two modes:
- **Form mode** — server sends a JSON schema, client renders a form, user fills
  it in, server receives the result
- **URL mode** — server sends a URL, client opens it for out-of-band
  interaction (OAuth flows, credential collection); defined in SEP-1036

Clients declare support via `elicitation.form` and/or `elicitation.url` during
the MCP `initialize` handshake.

Research findings on elicitation support across popular MCP clients:

| Client | Elicitation Support |
|---|---|
| Cursor 2.0+ | ✅ Full |
| VS Code Insiders / Stable | ✅ Native Command Palette-style UI |
| Claude Code CLI | ✅ Supports `elicitation/create` |
| Amazon Bedrock AgentCore Gateway | ✅ All 3 modes (form, URL request, URL exception) |
| mcp-use (mcp-use/mcp-use) | ✅ Dedicated callback |
| MCP Inspector | ✅ Since v0.16.2 |
| **Claude Desktop** | **❌** GitHub issue #41110 — feature request only |
| **Cline** | **❌** Discussion #4522, no implementation |
| **Continue.dev** | **❌** Client initialized with `capabilities: {}`, no handler |

Most popular agent clients (Cline, Continue, Claude Desktop) do **not** support
form-mode elicitation, making a URL-based fallback essential.

### Auth Delivery Mechanisms

The codebase already has three mechanisms for delivering credentials:

1. **Bearer token (Authorization header)** — used by OIDC JWTs and web setup
   tokens. Validated by `OidcTokenVerifier` (OIDC) or `SessionFileTokenVerifier`
   (legacy web setup).

2. **URL path auth** (`/v1/url_auth/{token}/mcp/...`) — defined in
   `auth_middleware.py`. The middleware extracts the token from the URL path,
   rewrites the request to `/v1/mcp/...`, and injects `Authorization: Bearer
   {token}`. For clients that cannot set custom HTTP headers (browser SSE,
   basic curl, minimal HTTP libraries).

3. **stdio transport** — no auth needed (trusted local transport).

---

## Decisions

### 1. No Dedicated `setup` MCP Tool

The 4-tool elicitation approach (oidc_setup_start, oidc_setup_phone,
oidc_setup_code, oidc_setup_password) is removed. There is no replacement
`setup` tool. Instead, the elicitation and auth guidance flows are embedded
into every tool via a shared mechanism.

**Rationale:** A dedicated setup tool creates a two-step UX (discover setup,
call setup, complete setup, then use real tools). Embedding auth into every
tool collapses this into one step: call any tool → get guided to auth. The
tool list itself serves as advertising of the server's capabilities, even to
unauthenticated users.

### 2. Middleware / Decorator for Auth Detection

Every tool handler is wrapped with a shared component (implemented as either
FastMCP middleware, a Python decorator, or a wrapper function) that runs
before the tool's business logic. This component:

a. **Checks auth state:**
   - No credential presented → anonymous
   - Valid OIDC JWT, no Telegram mapping → OIDC-authenticated, unmapped
   - Valid OIDC JWT, Telegram mapping exists → fully authenticated
   - Valid bearer token from web setup → fully authenticated
   - Expired/disconnected Telegram session → session re-auth needed

b. **Returns auth guidance** for unauthenticated/unmapped states:
   - Tier 1: "Configure OIDC on your client (set issuer + audience). If your
     client supports elicitation, the setup flow will start automatically."
   - Tier 2: "Visit the web setup page at [URL/setup]."
   - Tier 3: "Use URL path auth: /v1/url_auth/{token}/mcp/..."

c. **Starts elicitation** when the client supports it (declared
   `elicitation.form` in capabilities). The inline flow collects phone →
   verification code → [2FA password] via `ctx.elicit_form()` calls, runs the
   elicitation state machine, and links the OIDC identity.

d. **Auto-reauth** when an existing Telegram session has expired or
   disconnected. Same `ctx.elicit_form()` flow, re-establishing the Telegram
   client session without requiring the user to re-obtain a credential.

**Rationale:** Centralizing auth detection in one place avoids duplicating it
across N tool handlers. The middleware/decorator is a single point to update
if the auth flow changes. Tool authors only implement business logic.

### 3. All Tools Always Visible

The MCP tool list is not filtered by auth state. Unauthenticated clients see
the full set of tools. When they call any tool, the middleware returns auth
guidance instead of the tool's actual result.

**Rationale:** The tool list is free advertising — it shows what the server
can do, enticing users to authenticate. Returning informative guidance instead
of a generic 401 error provides a better onboarding experience.

### 4. Three-Tier Auth Capacity Spectrum

The README and documentation describe a spectrum of auth capabilities, from
most capable to least:

| Tier | Mechanism | Client requirement | Client examples |
|---|---|---|---|
| **1 — OIDC + Elicitation** | JWT bearer token + inline `ctx.elicit_form()` flow | Supports OIDC auth + MCP elicitation protocol | Cursor 2.0+, VS Code Insiders, Claude Code CLI, Amazon Bedrock, mcp-use |
| **2 — Bearer Token** | Token generated by web setup, sent as `Authorization: Bearer {token}` | Can set HTTP headers | Any HTTP-capable MCP client |
| **3 — URL Path Auth** | Token embedded in URL: `/v1/url_auth/{token}/mcp/...` | Can make HTTP requests with custom URLs | Browser SSE, basic curl, minimal HTTP libraries |

All three tiers share the same backend auth validation (the auth provider in
FastMCP). Only the delivery mechanism differs.

### 5. Dual Auth Retained

The bearer token / web setup path is kept alongside OIDC. No bearer token
support is dropped. `web_setup.py` is not retired. Version remains
pre-release (0.30.0).

**Rationale:** Most popular clients (Claude Desktop, Cline, Continue.dev) do
not support OIDC or elicitation. Dropping the bearer token path would break
them. Keeping both paths means every client type is covered.

### 6. URL Path Auth — No Code Changes

The current URL path auth middleware (`/v1/url_auth/{token}/mcp/...`) in
`auth_middleware.py` is sufficient. It extracts the token from the URL path,
validates it against the auth provider (`OidcTokenVerifier` or
`SessionFileTokenVerifier`), rewrites the URL to `/v1/mcp/...`, and injects
the `Authorization: Bearer {token}` header.

No changes needed. The middleware simply needs to be kept operational during
the Phase 4 transition.

### 7. Web Setup Output Includes URL Path Auth Link

After successful completion of the web setup flow (phone → code → [2FA]),
the result page provides both:
- The bearer token for Tier 2 clients
- The URL path auth link (`/v1/url_auth/{token}/mcp/...`) for Tier 3 clients

**Rationale:** Tier 3 clients cannot set HTTP headers, so providing only the
bearer token is useless to them. The URL path link is a drop-in replacement
they can paste directly into their client configuration.

---

## Supersedes

The Phase 4 section of ADR 0002 ("Drop bearer token support. Retire
`web_setup.py`. Update documentation and examples. Bump major version.") is
overridden by this ADR.

---

## Open Questions

None. The design phase is complete. Next steps are:
1. Update ADR 0002 Phase 4 section to reference this ADR
2. Update README with the 3-tier auth spectrum
3. Implementation of the middleware/decorator and auth flow
