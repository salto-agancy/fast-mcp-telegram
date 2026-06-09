# QR Login Auth — Implementation Design

**Status:** Draft for review
**ADR:** [0004-qr-login-auth.md](../adr/0004-qr-login-auth.md)
**Branch:** `feature/qr-login-auth`
**Target:** v0.30.0 (pre-release via `pre` distribution channel)

---

## 1. Overview

Replace the postponed OIDC + elicitation auth flow with a simpler Telethon QR login path. The user calls any tool → gets auth guidance → opens `/setup` → scans QR or enters phone → gets bearer token → pastes token in MCP client → tools work. No OIDC, no elicitation, no setup tool.

---

## 2. Components

### 2.1 `@require_auth` Decorator (NEW)

Central middleware applied to **all** MCP tools (confirmed — see §5).

**Behavior:**

```
call_tool()
  → require_auth checks:
    1. Bearer token from Authorization header
    2. Token valid? → resolve Telegram identity → run tool
    3. Token missing? → return structured error with auth guidance
    4. Token exists but Telethon session dead?
       → auto-detected by health check → return structured error
       → no automatic re-auth (user must go through /setup again)
```

**Structured error response:**

```json
{
  "isError": true,
  "content": [
    {
      "type": "text",
      "text": "This server requires Telegram authentication.\n\n"
             "Open /setup in your browser to:\n"
             "  — Scan a QR code from Telegram mobile (no phone entry needed)\n"
             "  — OR enter your phone number (if QR is unavailable)\n\n"
             "Once authenticated, you'll receive a bearer token.\n"
             "Configure it in your MCP client settings."
    }
  ]
}
```

**Explain** tool capabilities when unauthenticated:

```json
{
  "isError": true,
  "content": [
    {
      "type": "text",
      "text": "This server requires Telegram authentication.\n\n"
             "Auth: /setup\n\n"
             "When authenticated, this tool lets you:\n"
             "  — Send messages to any chat\n"
             "  — Read message history\n"
             "  — Manage channels and groups"
    }
  ]
}
```

**Implementation — plain Python decorator:**

```python
def require_auth(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        identity = await resolve_token(request_context)
        if identity is None:
            return auth_error_response(tool_name=func.__name__)
        kwargs["_identity"] = identity
        return await func(*args, **kwargs)
    return wrapper
```

### 2.2 Unified `/setup` Page (MODIFIED from current)

Single page with two auth methods:

```
┌─────────────────────────────────┐
│     Telegram Authentication     │
├─────────────────────────────────┤
│                                 │
│  ┌───────────────────────┐      │
│  │                       │      │
│  │     [QR CODE IMG]     │      │
│  │                       │      │
│  └───────────────────────┘      │
│  Scan with Telegram mobile      │
│                                 │
│  ──────── OR ────────           │
│                                 │
│  Phone: [__________]            │
│  Code:  [____]  [Submit]        │
│                                 │
│  (2FA password if needed)       │
└─────────────────────────────────┘
```

**QR code:** server-rendered using `python-qrcode` library. Output: base64 PNG data URI → `<img>` tag. Auto-refreshes every 60s if not scanned (Telethon `qr_login()` timeout).

**Phone form:** existing phone/code/2FA flow — no changes to the form itself, but it integrates into both **create** (first-time auth) and **re-auth** (existing session died) branches. Same form, two entry points (confirmed).

**Success state:**

```
┌─────────────────────────────────┐
│     Authentication Successful   │
├─────────────────────────────────┤
│                                 │
│  Your bearer token:             │
│  ┌─────────────────────────┐    │
│  │ fd8a3b...e92c           │    │
│  └─────────────────────────┘    │
│  [Copy]                         │
│                                 │
│  Configure in your MCP client   │
│  as Authorization: Bearer <tok> │
│                                 │
│  For clients without HTTP       │
│  header support:                │
│  /v1/url_auth/fd8a3b.../mcp/   │
│  [Copy URL]                     │
└─────────────────────────────────┘
```

**Polling mechanism (I choose simple polling):**
- Page generates `qr_session_id` (UUID4)
- Page polls `GET /auth/status?qr_session=XYZ` every 2s
- Server checks QR login state
- Returns: `pending` / `done` / `expired`
- When `done`: page transitions to success view with token

### 2.3 QR Login Endpoints (NEW)

```
GET  /setup              → renders the unified page
POST /auth/qr_start      → starts QR login, returns qr_session_id + QR image
GET  /auth/status        → polls login status (query: qr_session)
POST /auth/qr_renew      → generates new QR code (old one expired)
```

**QR login flow:**

1. User opens `/setup`
2. Page calls `POST /auth/qr_start`
   - Server creates Telethon client, calls `client.qr_login()`
   - Stores: `qr_sessions[qr_session_id] = (qr_login_obj, expires_at)`
   - Returns: `qr_session_id` + QR image (base64 PNG)
3. Page renders QR code
4. User scans QR from Telegram mobile
5. Page polls `GET /auth/status?qr_session=XYZ` every 2s
6. On scan: Telethon login completes → server saves session → creates bearer token → returns `done` + token
7. Page shows success view

**Single process:** QR state lives in-memory dict. No shared state across workers (confirmed).

**Telethon client management:**
- `/auth/qr_start` creates a temporary Telethon client for QR login
- QR client stored in memory keyed by `qr_session_id`
- After scan: save Telethon session string (keyed by `telegram_user_id`)
- QR client discarded
- Subsequent tool calls: create client from saved session

### 2.4 Bearer Token Management (EXISTING, minor changes)

Current token infrastructure unchanged:
- Tokens stored in SQLite (`bearer_tokens` table)
- Checked by `auth_middleware.py` on each request
- ACL is agent-scoped (per ADR 0001)

What changes:
- Token generation also linked to `telegram_user_id`
- `resolve_token()` looks up `telegram_user_id` from token
- Health check verifies Telethon session is alive

**No auto-refresh** (verified via exa_search):
- FastMCP has no built-in token management
- FastMCP Discord/search confirms: tokens are server-managed, no refresh mechanism exists
- Our tokens are permanent SQLite records — no expiry
- The only failure mode: Telethon session death (server restart, user reset)

**Backward compatibility:** Existing bearer tokens continue to work. No migration needed
(confirmed).
- Telethon death is handled by `require_auth` → structured error → auth guidance
- Adding token expiry + refresh later is orthogonal and doesn't change the design

---

## 3. File Changes

### New files:
| File | Purpose |
|------|---------|
| `src/auth/require_auth.py` | `@require_auth` decorator + `resolve_token()` |
| `src/auth/qr_login.py` | QR login endpoints (`/auth/qr_start`, `/auth/status`, `/auth/qr_renew`) |
| `src/web/templates/setup.html` | Unified QR + phone page (replaces phone-only setup) |
| `src/web/static/qr.js` | QR polling + auto-refresh JS |

### Modified files:
| File | Change |
|------|--------|
| `src/server.py` | Register QR endpoints, apply `@require_auth` to tools |
| `src/server_components/auth_middleware.py` | Add `resolve_token()` returning Telegram identity |
| `src/server_components/web_setup.py` | Add QR route + polling route, unify phone flow |

---

## 4. Auth Flow Walkthrough

```
User opens MCP client (unauthenticated)
  → Client lists tools (list_tools)
     → All tools visible (no auth check for listing)
  → Client calls send_message()
     → require_auth checks: no bearer token
     → Returns structured error: "Auth required. Open /setup"
  → Agent reads error, decides to auth
  → User opens /setup in browser
  → Page shows QR code + phone form
  → Option A: User scans QR from Telegram mobile
     → Poller detects login → creates bearer token
     → Page shows token + URL path auth link
  → Option B: User enters phone → code → 2FA
     → Creates bearer token
     → Page shows token + URL path auth link
  → User copies token → pastes in MCP client config
  → Client calls send_message() again
     → require_auth checks: bearer token valid
     → Resolves identity → runs send_message
  → Result sent back
```

---

## 5. Design Confirmation

Answers confirmed by Alexey (2026-06-09):

| # | Question | Decision |
|---|----------|----------|
| 1 | Which tools get `@require_auth`? | **All** tools. No per-tool configuration. |
| 2 | Existing bearer tokens? | **Keep working** — backward compatible, no migration. |
| 3 | QR 60s timeout with auto-refresh? | **OK** — auto-refresh QR on timeout. |
| 4 | Phone auth form changes? | **As is** — same form, but integrates into both create and re-auth branches. |

---

## 6. Implementation Order

1. `@require_auth` decorator + `resolve_token()` — core middleware
2. QR login endpoint + Telethon wrapper (`qr_login.py`) — the new auth mechanism
3. Unified `/setup` page — QR + phone form on single page
4. Apply `@require_auth` to tools — wire up the middleware
5. Test: QR scan → token → tool call → works
6. Test: phone → token → tool call → works
7. Test: unauthenticated tool call → structured error with guidance
8. Test: dead Telethon session → re-auth guidance
