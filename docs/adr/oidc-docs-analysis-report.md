# OIDC Self-Service Auth: User-Facing Documentation Analysis

**Date:** 2026-06-09
**Source ADR:** `docs/adr/0002-oidc-self-service-auth.md`
**Research brief:** `docs/research/oidc-self-service-design.md`
**Status code:** Implemented in `src/auth/` (Phases 1–3)

---

## 1. Inventory: User-Facing Documentation

| # | File | Audience | Currently Covers Auth? | Priority for OIDC Update |
|---|------|----------|----------------------|--------------------------|
| 1 | `README.md` | All users | ✅ Features table, Quick Start, demo server | **High** — main landing page |
| 2 | `docs/Installation.md` | Deployers/ops | ✅ Setup, env vars, web setup, ACL, config ref | **Critical** — env vars, setup flow, config ref |
| 3 | `docs/Tools-Reference.md` | MCP client users | ✅ 8 tools documented with schemas | **Medium** — new auth tools need docs |
| 4 | `docs/MTProto-Bridge.md` | API users | ✅ Bearer token auth | **Low** — OIDC doesn't change bridge endpoints |
| 5 | `docs/Search-Guidelines.md` | MCP client users | ❌ No auth content | **None** — orthogonal |
| 6 | `docs/Filters-vs-Folders.md` | MCP client users | ❌ No auth content | **None** — orthogonal |
| 7 | `SECURITY.md` | Deployers/ops | ✅ Bearer tokens, ACL, attachment security | **High** — OIDC is a new auth mechanism |
| 8 | `docs/Roadmap.md` | Contributors/planners | ✅ Mentions "OAuth2 / IdP" in backlog | **Medium** — OIDC is now implemented, not planned |
| 9 | `docs/Project-Structure.md` | Developers | ✅ Auth directory with OIDC components | **Low** — already up to date with OIDC structure |
| 10 | `docs/Strategic-Market-Positioning.md` | Evaluators | ✅ Auth capabilities table | **Medium** — update capability table |
| 11 | `docs/published-resources.md` | Contributors | ❌ No auth content | **None** — orthogonal |
| 12 | `CONTRIBUTING.md` | Contributors | ❌ Development setup | **Low** — OIDC dev env vars could be noted |
| 13 | `RELEASE_NOTES.md` | All users | ❌ History log | **Low** — future release note entry |

### Excluded (internal-only)
- `CLAUDE.md` — AI coding instructions
- `docs/adr/` — Architecture Decision Records
- `docs/research/` — Research briefs
- `docs/benchmark-spec.md`, `docs/sg_sweep_comparison.md`

---

## 2. What Information About OIDC Auth Should Be Documented

### 2.1 User-Facing Concepts (must document)

| Concept | Why users need it |
|---------|-------------------|
| **OIDC authentication mode** (`http-auth` now supports JWT Bearer tokens from an OIDC provider) | Operators need to know they have a new auth option |
| **New env vars** (`TG_OIDC_ISSUER`, `TG_OIDC_AUDIENCE`, `TG_DATABASE_URL`) | Required configuration |
| **Elicitation flow** (phone → code → optional 2FA, done via MCP tools or web UI) | Users need to know how to onboard |
| **New MCP tools** (`oidc_setup_start`, `oidc_setup_phone`, `oidc_setup_code`, `oidc_setup_password`) | MCP client users need tool reference |
| **Dual auth during transition** (both Bearer tokens and OIDC JWT accepted) | Deployers need to know backward compat |
| **Stdio / bot tokens skip OIDC** (existing auth unchanged for these paths) | Users on those paths need reassurance |
| **ACL unchanged** (still Telegram identity keys, not OIDC identifiers) | Operators already familiar with ACL |
| **Web setup still works** (web UI works with OIDC, bearer tokens still generated) | Existing workflow continuity |

### 2.2 Internal Implementation Details (do NOT document)

| Detail | Why exclude |
|--------|-------------|
| State machine internals (WAITING_PHONE, WAITING_CODE, etc.) | Implementation detail — users see the 4 tools |
| TOCTOU race analysis | Engineering reasoning, not user-facing |
| Specific SQL schema or query internals | Not relevant to operation |
| Connection pool configuration | Ops tune via env vars, not DB internals |
| Dead parameter removals, code review history | Noise |
| `get_state_row()` function signatures | Internal API |
| TTL enforcement mechanics (WHERE updated_at) | Implementation detail |
| Concurrency model (Telethon MTProto serialization) | Not user-relevant |
| `oidc_` prefix rationale for session files | Internal naming convention |
| Migration script (`migrate_legacy.py`) details | Phase 4 ops, document when available |

---

## 3. File-by-File Analysis and Recommendations

### 3.1 README.md — HIGH PRIORITY

**Current state:** Features table has "Web Setup Interface" and "Multi-User Authentication" rows. Quick Start references `bearer token`. Demo server (tg-mcp.l1979.ru) uses Bearer tokens.

**What to add:**

#### A. Features table — new row for OIDC

Suggested placement: After the existing "Multi-User Authentication" row (line ~40). Add:

```
| :key: **OIDC Self-Service Auth** | JWT-based authentication via any OIDC provider (Auth0, Clerk, WorkOS). Self-service onboarding without admin token distribution. Elicitation flow via MCP tools or web UI. |
```

#### B. Quick Start — note that OIDC is an alternative

After the "Install and authenticate" section, add a note:

```
> **OIDC alternative:** For `http-auth` deployments, you can use an OIDC provider instead of pre-shared bearer tokens. Set `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE` in your server environment. Users authenticate through the OIDC login flow and receive a JWT to use as Bearer token. See [Installation Guide](docs/Installation.md#oidc-authentication).
```

#### C. Demo server section — add OIDC path

```
The demo server at tg-mcp.l1979.ru supports both Bearer tokens and OIDC authentication.
Open https://tg-mcp.l1979.ru/setup for the web setup flow or use the MCP-based elicitation tools.
```

**Detail level:** Feature table row (one line), plus a callout paragraph in Quick Start. Full details in Installation.md.

---

### 3.2 `docs/Installation.md` — CRITICAL PRIORITY

**Current state:** Has sections for Local Setup (stdio), Remote Setup (http-auth), Web Setup Interface, Session ACL, Configuration Reference (env vars). No mention of OIDC.

**What to add:**

#### A. New section: "OIDC Authentication" (after "Remote Setup" before "Web Setup Interface")

Suggested content (medium-length subsection, ~2-3 paragraphs):

```markdown
## OIDC Authentication (http-auth only)

As an alternative to pre-shared bearer tokens, you can use **OIDC (OpenID Connect)** authentication. 
Users authenticate via an external OIDC provider (Auth0, Clerk, WorkOS, or any provider supporting 
the OIDC discovery protocol) and receive a JWT to use as their Bearer token. This enables 
self-service onboarding — no admin needs to generate and distribute tokens.

### Prerequisites

1. An OIDC provider with a client application configured.
2. The provider's **Issuer URL** (e.g. `https://your-tenant.auth0.com/`) and **Audience** 
   (or client ID) for your application.

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TG_OIDC_ISSUER` | Yes (for OIDC mode) | — | OIDC provider issuer URL (e.g. `https://your-tenant.auth0.com/`) |
| `TG_OIDC_AUDIENCE` | Yes (for OIDC mode) | — | Expected audience (or client ID) in the JWT |
| `TG_DATABASE_URL` | No | `./data/auth.db` | Path to the SQLite database that stores OIDC identity mappings |

### How It Works

1. The server verifies incoming JWTs using FastMCP's built-in `JWTVerifier`, which fetches the 
   provider's JWKS keys and validates the token's signature, expiry, issuer, and audience.
2. On first authentication, the server creates a Telegram session for the user via an **elicitation 
   flow**: phone number → verification code → optional 2FA password. This can be done either:
   - Through the **web setup interface** (`/setup`) — same UI as bearer token creation.
   - Via **MCP tools** (`oidc_setup_start`, `oidc_setup_phone`, `oidc_setup_code`, 
     `oidc_setup_password`) — for agent-driven setup.
3. Once linked, the OIDC identity is permanently mapped to the Telegram account. Subsequent 
   requests with the same JWT automatically resume the session.

> **Note:** OIDC authentication applies only to **`http-auth`** mode. Stdio transport and bot API 
> tokens (`BOT_API_TOKEN`) continue to work unchanged — they do not require an OIDC provider.

### Dual Auth During Transition

During the transition period, both legacy bearer tokens and OIDC JWTs are accepted. This allows 
you to migrate gradually. Existing bearer tokens remain valid; no changes to `acl.yaml` or client 
configurations are needed. Hard cutover (dropping bearer token support) will happen in a future 
major version.
```

#### B. Configuration Reference — add OIDC env vars

In the Environment Variables table (existing section "Configuration Reference"), add:

```
# OIDC Authentication (http-auth only) — alternative to bearer tokens
TG_OIDC_ISSUER=                   # OIDC provider issuer URL (required for OIDC mode)
TG_OIDC_AUDIENCE=                 # Expected JWT audience (required for OIDC mode)
TG_DATABASE_URL=./data/auth.db    # OIDC identity database path
```

#### C. Web Setup Interface — add OIDC note

In the existing "Web Setup Interface" section, add a note at the top:

```
> **OIDC users:** If the server is configured with `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE`, 
> the web setup flow works with your OIDC JWT instead of a bearer token. The elicitation 
> process (phone → code → 2FA) is identical.
```

**Detail level:** Full subsection (~3 paragraphs + table) for the new OIDC Authentication section. One-line additions to Configuration Reference table and Web Setup Interface note.

---

### 3.3 `docs/Tools-Reference.md` — MEDIUM PRIORITY

**Current state:** Lists 8 tools. No OIDC auth tools.

**What to add:**

#### A. New section after "4. Advanced" — "5. Authentication Tools"

```markdown
## 5. Authentication (OIDC Elicitation)

These tools provide an agent-driven OIDC authentication flow. They are used when the server is 
configured with `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE` (see [Installation — OIDC Authentication](Installation.md#oidc-authentication)).

### oidc_setup_start
**Begin the OIDC elicitation flow — submit phone number**

```typescript
oidc_setup_start(
  phone_number: str              // Phone with country code (+1234567890)
)
```

**Examples:**
```json
{"tool": "oidc_setup_start", "params": {"phone_number": "+1234567890"}}
```

### oidc_setup_phone
**Submit the verification code received via Telegram**

```typescript
oidc_setup_phone(
  code: str                      // Verification code from Telegram
)
```

**Examples:**
```json
{"tool": "oidc_setup_phone", "params": {"code": "12345"}}
```

### oidc_setup_code
**Submit phone verification code (alternative name)**

```typescript
oidc_setup_code(
  code: str                      // Verification code from Telegram
)
```

### oidc_setup_password
**Submit 2FA password (only required if 2FA is enabled on the account)**

```typescript
oidc_setup_password(
  password: str                  // 2FA password
)
```

**Examples:**
```json
{"tool": "oidc_setup_password", "params": {"password": "your_2fa_password"}}
```

**Flow summary:**
1. `oidc_setup_start` – enter phone number
2. `oidc_setup_code` (or `oidc_setup_phone`) – enter the verification code Telegram sends
3. `oidc_setup_password` – only if 2FA is enabled; skip otherwise
```

> **Tip:** The elicitation flow has a 5-minute TTL. If you don't complete all steps within that 
> window, start again from `oidc_setup_start`.

#### B. Overview — update tool count

Change "8 consolidated tools" → "12 consolidated tools" (or the final count including 4 new auth tools).

#### C. Available Tools table — add new rows

| Tool | Purpose | Key Features |
|------|---------|--------------|
| `oidc_setup_start` | Begin OIDC elicitation | Submit phone number, start state machine |
| `oidc_setup_code` | Verify phone code | Submit Telegram verification code |
| `oidc_setup_password` | Submit 2FA password | Complete 2FA if enabled |

**Detail level:** Full new section (5. Authentication) with four tool signatures, examples, and flow summary. Update overview and tool table.

---

### 3.4 `SECURITY.md` — HIGH PRIORITY

**Current state:** Documents Bearer Token Authentication System, Multi-User Security Model, Session ACL. No mention of OIDC.

**What to add:**

#### A. New subsection: "OIDC Authentication" (after "Authentication Methods")

```markdown
## OIDC Authentication

When the server is configured with `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE`, it accepts JWTs 
from your OIDC provider as Bearer tokens. The JWT is validated by FastMCP's built-in 
`JWTVerifier`:

- **Signature verification**: RS256 via JWKS keys fetched from the OIDC provider's discovery endpoint.
- **Expiry**: Expired tokens are rejected (standard JWT `exp` claim).
- **Issuer**: Must match `TG_OIDC_ISSUER`.
- **Audience**: Must match `TG_OIDC_AUDIENCE`.

Once a JWT is validated, the server links it to a Telegram session via the elicitation flow 
(phone → code → 2FA). The mapping is stored in the OIDC identity database (`TG_DATABASE_URL`).

### Security Considerations

- **JWT as Bearer token**: The JWT is used in the `Authorization: Bearer <token>` header, 
  just like a pre-shared bearer token. Protect it the same way.
- **Token lifetime**: OIDC provider controls JWT expiry. Short-lived tokens (e.g. 1 hour) 
  are recommended. The server re-validates on each request.
- **OIDC sub changes**: If a user's OIDC `sub` changes, they are treated as a new user in v1. 
  A warning is logged. Orphan cleanup is planned for a future release.
- **Database security**: The SQLite database (`TG_DATABASE_URL`) maps OIDC identities to 
  Telegram accounts. Restrict file permissions (e.g. `0600`). It contains no credentials, 
  but an attacker who reads it can correlate identities.
```

#### B. "Multi-User Security Model" — add OIDC to scope

Add after the first paragraph:

```
When OIDC authentication is enabled, the "Bearer token" is a JWT issued by your OIDC provider. 
The same session isolation model applies: each OIDC identity gets its own Telegram session file.
```

**Detail level:** One new subsection (3 paragraphs + bullet points). Minor addition to Multi-User Security Model section.

---

### 3.5 `docs/Roadmap.md` — MEDIUM PRIORITY

**Current state:** Backlog includes "OAuth2 / IdP | Enterprise | Federation path". The current sequence and shipped items don't mention OIDC.

**What to add:**

#### A. Update backlog item

Change:
```
| OAuth2 / IdP | Enterprise | Federation path |
```
To:
```
| ~~OAuth2 / IdP~~ ✅ **Done** | **Shipped in v0.30.0**: OIDC self-service auth with JWTVerifier, elicitation state machine, SQLite identity store, dual-auth transition. See [ADR 0002](docs/adr/0002-oidc-self-service-auth.md). |
```

#### B. Update "Shipped on master" section

Add line:
- ✅ **OIDC self-service authentication** — JWTVerifier-based OIDC auth with SQLite identity store, elicitation flow (phone → code → 2FA), dual-auth during transition — see [ADR 0002](docs/adr/0002-oidc-self-service-auth.md)

#### C. Add to backlog (Phase 4 remaining work)

```
| OIDC major cutover | Docs / Trust | Drop bearer token support, retire web_setup.py, bump major version. See ADR 0002 Phase 4. |
```

**Detail level:** A few line edits. Mark OAuth2/IdP as done, add to shipped list, add Phase 4 cutover as pending backlog item.

---

### 3.6 `docs/Strategic-Market-Positioning.md` — MEDIUM PRIORITY

**Current state:** Has "Authentication and sessions" section with bearer tokens, web setup, ACL. No OIDC.

**What to add:**

#### A. Update "Authentication and sessions" bullet

Add after the first bullet:

- **OIDC JWT authentication** — Alternative to bearer tokens. JWTs validated via FastMCP's built-in `JWTVerifier` with JWKS, expiry, issuer, and audience validation. Configured via `TG_OIDC_ISSUER` and `TG_OIDC_AUDIENCE`. See [Installation.md](Installation.md#oidc-authentication).

#### B. Update "Server modes" table

No change needed — OIDC is a sub-option of `http-auth`, not a new mode.

#### C. Update tool count in MCP tools table

Add rows for the 4 new auth tools if the table is updated to reflect current count.

**Detail level:** One new bullet point in the "Authentication and sessions" section.

---

### 3.7 `docs/Project-Structure.md` — LOW PRIORITY

**Current state:** Already has comprehensive auth/ directory documentation covering OIDC components (db.py, elicitation_state_machine.py, elicitation_tools.py, oauth_provider_adapter.py, principal_resolver.py, telegram_auth_service.py, migrations, queries). 

**What to add (if anything):**
- The existing content is already detailed. No changes needed for v1 — it accurately reflects the OIDC module structure.
- Future update: when `web_setup.py` is retired in Phase 4, note it here.

**Detail level:** No changes required now.

---

### 3.8 `CONTRIBUTING.md` — LOW PRIORITY

**Current state:** Development setup section doesn't mention OIDC.

**What to add:**

In the "Getting Started" prerequisites or setup section, add:

```
- **OIDC development**: For testing OIDC auth locally, see `.env.example` for `TG_OIDC_ISSUER` 
  and `TG_OIDC_AUDIENCE` — or run without them (OIDC is optional; bearer tokens still work).
```

**Detail level:** One sentence note in prerequisites.

---

### 3.9 `docs/MTProto-Bridge.md`, `docs/Search-Guidelines.md`, `docs/Filters-vs-Folders.md`, `docs/published-resources.md` — NO CHANGES

These docs are orthogonal to authentication. OIDC doesn't change:
- MTProto Bridge endpoints (still use Bearer token)
- Search behavior
- Folder/filter mechanics
- Published resources list

**No changes needed.**

---

## 4. Summary of Recommendations

| File | Priority | Addition | Placement | Detail Level |
|------|----------|----------|-----------|-------------|
| `README.md` | **High** | OIDC features table row + quick start callout | Features table (after multi-user auth) + Quick Start | One line + one callout |
| `docs/Installation.md` | **Critical** | New OIDC Authentication section; env vars in config ref; web setup note | After Remote Setup, before Web Setup Interface; Config Reference table | Full subsection (~3 paragraphs + env var table) |
| `docs/Tools-Reference.md` | **Medium** | New section "5. Authentication Tools" with 4 tools + flow summary; update tool count | After section 4 (Advanced) | Full new section with signatures, examples, flow |
| `SECURITY.md` | **High** | New "OIDC Authentication" subsection; update Multi-User model note | After "Authentication Methods" | One subsection (3 paragraphs + bullets) |
| `docs/Roadmap.md` | **Medium** | Mark OAuth2/IdP as shipped; add to shipped list; add Phase 4 cutover backlog | Backlog table + Shipped section | 3 line edits |
| `docs/Strategic-Market-Positioning.md` | **Medium** | Add OIDC bullet to Authentication section | "Authentication and sessions" bullet list | One bullet point |
| `docs/Project-Structure.md` | **Low** | No changes needed — already accurate | — | — |
| `CONTRIBUTING.md` | **Low** | OIDC dev note | Prerequisites section | One sentence |
| `docs/MTProto-Bridge.md` | **None** | No changes | — | — |
| `docs/Search-Guidelines.md` | **None** | No changes | — | — |
| `docs/Filters-vs-Folders.md` | **None** | No changes | — | — |
| `docs/published-resources.md` | **None** | No changes | — | — |

---

## 5. What NOT to Document in User-Facing Files

Based on the ADR, the following belong in internal docs only (ADR, research, code comments):

1. **State machine state names** (`WAITING_PHONE`, `WAITING_CODE`, `WAITING_PASS`, `COMPLETED`, `EXPIRED`, `FAILED`) — users see the 4 tools, not the state enum
2. **TTL enforcement mechanism** (inline `WHERE updated_at >= ?` on UPDATE) — implementation detail
3. **Concurrency model** (Telethon MTProto serialization + DB atomic writes) — not user-relevant
4. **TOCTOU race analysis** — engineering reasoning, not operation guidance
5. **Schema details** (column names, SQL types — users don't query the DB)
6. **`get_state_row()` function** — internal diagnostic API
7. **Migration runner internals** — ops run the script, they don't need to know version tracking
8. **Dead/rejected alternatives** (custom JWT verifier, SQLAlchemy, Postgres/Redis, OIDC keys in ACL, tenant allowlist, telegram_session table) — these are ADR context only
9. **`oidc_` prefix on session filenames** — internal naming convention
10. **Connection pool configuration** — tuned via env vars, not discussed at DB level

---

## 6. Execution Order (Suggested)

1. **`docs/Installation.md`** — most critical; admins need env vars and setup flow before they can deploy
2. **`README.md`** — features table and quick start visibility
3. **`SECURITY.md`** — security implications of OIDC auth model
4. **`docs/Tools-Reference.md`** — new tool documentation for MCP users
5. **`docs/Roadmap.md`** — reflect shipped status
6. **`docs/Strategic-Market-Positioning.md`** — keep capability index accurate
7. **`CONTRIBUTING.md`** — minor dev note (lowest priority)

*Files with "None" priority can be skipped entirely.*
