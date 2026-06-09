# OIDC Self-Service Auth: Implementation Plan

> Companion document to [ADR 0002](../adr/0002-oidc-self-service-auth.md). Mirrors structure of [ACL Design Brief](./acl-design-brief.md).

## Goals

1.  Enable self-service onboarding via SaaS OIDC (Auth0/Clerk/WorkOS).
2.  Preserve Telethon session caching (per-user `.session` files).
3.  Keep ACL configuration human-readable (Telegram identity keys only).
4.  Support multi-round elicitation with persistent state.
5.  Zero breaking changes during dual-auth transition period.

## Non-Goals (v1)

-   Multi-tenant issuer allowlist (`allowed_oidc_issuers`).
-   Custom JWT verification logic.
-   SQLAlchemy or async ORM.
-   Postgres/Redis storage backend.
-   Orphaned session cleanup on OIDC sub change.
-   Bot API token OIDC authentication.
-   Stdio transport OIDC support.

## Architecture Overview

Three orthogonal layers:

```
┌─────────────────────────────────────────────────────────────┐
│                    FastMCP JWTVerifier                      │
│         (Token verification, JWKS, issuer validation)        │
└──────────────────────────┬──────────────────────────────────┘
                           │ oidc_sub + claims
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                  Principal Resolution Layer                  │
│    DB lookup: oidc_sub → telegram_identity → ACL principal   │
└──────────────────────────┬───────────────────���──────────────┘
                           │ @username / +phone / user_id
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                      ACL Enforcement                        │
│          YAML config, loaded at startup, unchanged           │
└─────────────────────────────────────────────────────────────┘
```

Storage is separate from all three layers — shared SQLite DB for OIDC/state, per-user `.session` files for Telethon cache.

## Auth Flow

### First-Time Sign-In

1.  User connects via MCP client with OIDC bearer token.
2.  FastMCP `JWTVerifier` validates token signature, issuer, audience, expiry.
3.  Extract `sub` claim → hash to produce `oidc_key`.
4.  Query `oidc_identity` table by `oidc_key`.
5.  **Not found** → enter elicitation flow:
    a.  Prompt for phone number.
    b.  Send Telegram verification code.
    c.  Prompt for code.
    d.  Verify code via Telethon.
    e.  (Optional) Prompt for password if 2FA enabled.
    f.  On success: write `oidc_identity` row, create `.session` file.
6.  Resolve Telegram identity (`@username`, `+phone`, or `user_id`) from DB.
7.  Match against ACL rules.
8.  Grant or deny access.

### Re-Authentication

1.  Token validated by `JWTVerifier`.
2.  `oidc_key` lookup succeeds → retrieve linked Telegram identity.
3.  Skip elicitation entirely.
4.  ACL check proceeds as normal.

### OIDC Sub Change

If a known user presents a new `sub` value:
-   Log warning: `OIDC sub changed for telegram_identity=X old=Y new=Z`.
-   Treat as new user (enter elicitation flow).
-   Old `oidc_identity` row remains (orphan cleanup deferred to Phase 2).

## Elicitation State Machine

### States

| State             | Description                                      |
| :---------------- | :----------------------------------------------- |
| `WAITING_PHONE`   | Prompted for phone, awaiting input               |
| `WAITING_CODE`    | Sent TG code, awaiting verification              |
| `WAITING_PASS`    | Code valid, awaiting 2FA password                |
| `COMPLETED`       | Identity linked, session created                 |
| `FAILED`          | Max retries exceeded or fatal error              |

### Transitions

```
WAITING_PHONE ──valid phone──▶ WAITING_CODE
WAITING_CODE ──valid code──▶ WAITING_PASS (if 2FA) or COMPLETED
WAITING_CODE ──invalid code──▶ WAITING_CODE (re-elicit once) or FAILED
WAITING_PASS ──valid pass──▶ COMPLETED
WAITING_PASS ──invalid pass──▶ WAITING_PASS (re-elicit once) or FAILED
Any state ──5min TTL expired──▶ EXPIRED (inline TTL check on transition)
```

### Concurrency Control

-   DB atomic UPDATE with rowcount check prevents parallel elicitation for the same OIDC sub.
-   TTL enforcement via `updated_at >= cutoff` clause in every state transition — no separate sweep task.
-   In-process single-flight: dict keyed by `oidc_key` ensures only one coroutine handles elicitation per user.
-   Telethon MTProto auto-serializes requests per session file — no lockfile needed.

### TTL Enforcement

TTL is enforced atomically at the query level — no background task:

-   Every state transition includes `AND updated_at >= datetime('now', '-5 minutes')` in the WHERE clause.
-   If the row has expired, the UPDATE affects zero rows → the caller receives a failure response.
-   Expired states are effectively "fail-closed": the user must restart elicitation.
-   No periodic sweep, no cron, no background task running on the process.

## Storage Layer

### Database Schema

```sql
-- OIDC identity mapping
CREATE TABLE IF NOT EXISTS oidc_identity (
    oidc_key TEXT PRIMARY KEY,          -- SHA-256(sub + issuer)
    oidc_sub TEXT NOT NULL,
    oidc_issuer TEXT NOT NULL,
    telegram_user_id INTEGER NOT NULL,
    telegram_username TEXT,
    telegram_phone TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);


-- Elicitation state machine
CREATE TABLE IF NOT EXISTS setup_state (
    oidc_key TEXT PRIMARY KEY,
    state TEXT NOT NULL CHECK (state IN ('WAITING_PHONE','WAITING_CODE','WAITING_PASS','COMPLETED','FAILED')),
    phone_number TEXT,
    tg_code_hash TEXT,                  -- Telethon phone_code_hash
    retry_count INTEGER NOT NULL DEFAULT 0,
    metadata TEXT,                      -- JSON blob for extra state
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Migration tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    description TEXT
);
```

### Migration Runner

```python
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

def run_migrations(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    
    # Ensure version table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            description TEXT
        )
    """)
    
    current = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    
    for migration_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(migration_file.stem.split("_")[0])
        if version > current:
            sql = migration_file.read_text()
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (version, migration_file.stem)
            )
            conn.commit()
    
    conn.close()
```

### Connection Configuration

```python
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("TG_DATABASE_URL", "./data/auth.db")

@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

## ACL Integration

### Principal Resolution Logic

```python
def resolve_principal(oidc_key: str) -> Optional[str]:
    """Return ACL-compatible principal string or None."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT telegram_username, telegram_phone, telegram_user_id FROM oidc_identity WHERE oidc_key = ?",
            (oidc_key,)
        ).fetchone()
    
    if not row:
        return None
    
    # Priority: username > phone > user_id
    if row["telegram_username"]:
        return f"@{row['telegram_username']}"
    if row["telegram_phone"]:
        return f"+{row['telegram_phone']}"
    return str(row["telegram_user_id"])
```

### Default Policy Reuse

No new environment variable. Existing `ACL_DENY_UNLISTED_PRINCIPALS=true` applies equally to OIDC-authenticated users. If an OIDC user's resolved Telegram identity isn't in the ACL YAML, they're denied.

## Telethon Session Files

### Naming Convention

Session files use hashed OIDC key to avoid filesystem issues with special characters:

```python
import hashlib

def session_filename(oidc_key: str) -> str:
    return hashlib.sha256(oidc_key.encode()).hexdigest()[:16] + ".session"
```

Example: `a1b2c3d4e5f67890.session`

### Cache Preservation

Per-user `.session` files retain all 5 Telethon tables:
-   `auth_key` — DC authorization credentials.
-   `entities` — cached peer ID ↔ username/phone mappings.
-   `sent_files` — file reference cache to avoid re-uploads.
-   `update_state` — PTS/QTS/seq for gap handling.
-   `version` — Telethon internal schema version.

These are NOT migrated to the shared DB. They remain as-is in `~/.config/fast-mcp-telegram/sessions/` (configurable via `TG_SESSION_DIR`).

## Environment Variables

| Variable              | Required | Default            | Description                          |
| :-------------------- | :------- | :----------------- | :----------------------------------- |
| `TG_OIDC_ISSUER`      | Yes*     | —                  | OIDC issuer URL (e.g., `https://dev-xxx.auth0.com/`) |
| `TG_OIDC_AUDIENCE`    | Yes*     | —                  | Expected audience claim value        |
| `TG_DATABASE_URL`     | No       | `./data/auth.db`   | SQLite database path                 |
| `TG_SESSION_DIR`      | No       | `~/.config/fast-mcp-telegram/sessions/` | Telethon session file directory |
| `ACL_DENY_UNLISTED_PRINCIPALS` | No | `false`      | Deny access if principal not in ACL  |

\* Required only when OIDC auth is enabled. Stdio/bot-token modes don't need these.

## Migration Plan

### Phase A: Dual Auth (Current → Next Minor)

-   Ship OIDC alongside existing bearer tokens.
-   Both auth methods accepted simultaneously.
-   New users onboard via OIDC; existing users keep bearer tokens.
-   No config changes required for existing deployments.

### Phase B: Linking Script (Next Minor + 1)

Provide `scripts/migrate_legacy.py`:

```bash
python scripts/migrate_legacy.py \
  --bearer-map ./legacy_tokens.yaml \
  --db ./data/auth.db
```

Script reads bearer→telegram mapping from legacy config, inserts corresponding `oidc_identity` rows with placeholder `oidc_sub` values. Admin then asks each user to sign in via OIDC once; script updates placeholder with real `sub`.

### Phase C: Major Version Cutover

-   Bump major version (e.g., 2.0.0).
-   Remove bearer token parsing code.
-   Delete `web_setup.py` and related templates.
-   Update docs to reflect OIDC-only auth.
-   Release notes include migration guide link.

## Test Strategy

### Unit Tests

-   `test_storage.py`: CRUD operations on all 3 tables, migration runner, connection pooling.
-   `test_principal_resolution.py`: Username/phone/user_id priority, missing identity returns None.
-   `test_elicitation_state.py`: State transitions, TTL expiry, retry limits, concurrent access.
-   `test_session_naming.py`: Hash stability, collision resistance.

### Integration Tests

-   Spin up test OIDC provider (Keycloak in Docker).
-   Full sign-in flow: token → elicitation → session creation → ACL check.
-   Re-auth flow: token → DB lookup → skip elicitation.
-   Concurrent sign-in: verify atomic UPDATE prevents races.
-   TTL expiry: confirm expired states rejected on state transition.

### Manual QA Checklist

-   [ ] Fresh install: OIDC sign-in creates DB + session file.
-   [ ] Existing bearer user: linking script preserves access.
-   [ ] Wrong code: re-elicit once, then fail gracefully.
-   [ ] Server restart mid-elicitation: state recovered from DB.
-   [ ] ACL deny: OIDC user not in YAML gets rejected with clear error.
-   [ ] Stdio mode: no OIDC env vars needed, works as before.

## Phase 1 Sub-phases (Storage Layer)

Phase 1 is split into four sub-phases. Each follows the coding process (TDD, no human gates until Phase 1 is complete).

### 1.1 DB Schema & Migrations

**Goal:** Create tables and migration runner.

1.  Write failing test: `test_migrations_create_tables` — verifies all 3 tables exist after `run_migrations()`.
2.  Write failing test: `test_schema_version_tracking` — verifies `schema_version` row inserted per migration.
3.  Implement `src/auth/migrations/001_initial_schema.sql` with CREATE TABLE statements from Storage Layer section.
4.  Implement `src/auth/db.py`: `run_migrations()`, `get_connection()` context manager.
5.  Pass tests.
6.  Write failing test: `test_migration_idempotency` — running migrations twice doesn't fail or duplicate rows.
7.  Pass tests.

**Artifacts:** `src/auth/db.py`, `src/auth/migrations/001_initial_schema.sql`, `tests/test_db.py`.

### 1.2 OIDC Identity CRUD

**Goal:** Insert/query/update `oidc_identity` rows.

1.  Write failing test: `test_insert_oidc_identity` — insert row, query back, verify fields.
2.  Write failing test: `test_get_oidc_identity_by_key` — returns None for missing key.
3.  Write failing test: `test_update_oidc_identity_timestamp` — updated_at changes on update.
4.  Implement `src/auth/queries/oidc_identity.py`: `insert_identity()`, `get_identity()`, `update_identity()`.
5.  Pass tests.
6.  Write failing test: `test_unique_oidc_key_constraint` — duplicate insert raises IntegrityError.
7.  Pass tests.

**Artifacts:** `src/auth/queries/oidc_identity.py`, `tests/test_oidc_identity_queries.py`.

### 1.3 Setup State Machine Persistence

**Goal:** Persist elicitation state with TTL support.

1.  Write failing test: `test_create_setup_state` — initial state WAITING_PHONE.
2.  Write failing test: `test_transition_state` — WAITING_PHONE → WAITING_CODE updates row.
3.  Write failing test: `test_ttl_expiry_query` — `get_active_states(older_than=5min)` returns expired rows.
4.  Write failing test: `test_delete_expired_states` — removes rows + returns count.
5.  Implement `src/auth/queries/setup_state.py`: `create_state()`, `transition_state()`, `get_active_states()`, `delete_expired()`.
6.  Pass tests.
7.  Write failing test: `test_retry_count_increment` — increments on failed code/password attempt.
8.  Pass tests.

**Artifacts:** `src/auth/queries/setup_state.py`, `tests/test_setup_state_queries.py`.

### 1.4 Telegram Session Metadata & Legacy Migration Script

**Goal:** Provide bearer→OIDC linking script.

1.  Write failing test: `test_migrate_legacy_script` — reads YAML, inserts placeholder rows.
2.  Implement `scripts/migrate_legacy.py`.
3.  Pass tests.
4.  Manual QA: run script against sample legacy_tokens.yaml, verify DB contents.

**Artifacts:** `scripts/migrate_legacy.py`, `tests/test_migrate_legacy.py`.

### Phase 1 Completion Gate

After all 4 sub-phases pass:
-   Run full test suite (`pytest`).
-   Run linter (`ruff check src/ tests/`).
-   Human review PR.
-   Merge to main.
-   Proceed to Phase 2 (JWKS caching).

## Open Questions (Deferred)

1.  **Orphan cleanup policy:** When OIDC sub changes, should we auto-delete old session file or archive it?
2.  **Session file encryption:** Current `.session` files store `auth_key` in plaintext. Worth encrypting at rest?
3.  **Rate limiting elicitation:** Should we cap sign-in attempts per IP/OIDC sub to prevent abuse?
4.  **Audit logging:** Do we need a separate audit log table for compliance, or are app logs sufficient?
5.  **Multi-device support:** Can one OIDC identity link to multiple Telegram accounts? (Probably not, but worth documenting.)

## References

-   [ADR 0002: OIDC Self-Service Auth](../adr/0002-oidc-self-service-auth.md)
-   [ADR 0001: Agent-Scoped Session ACL](../adr/0001-agent-scoped-session-acl.md)
-   [ACL Design Brief](./acl-design-brief.md)
-   [FastMCP JWTVerifier Docs](https://gofastmcp.com/servers/auth)
-   [Telethon Session Internals](https://docs.telethon.dev/en/stable/concepts/sessions.html)
