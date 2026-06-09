# PR #113 ADR & Design Doc Review — `feature/oidc-phase1-storage`

**Branch:** `feature/oidc-phase1-storage`  
**Base:** `master`  
**Commits reviewed:** `3006192` through `d01b733` (full diff)  
**Review date:** 2026-06-13

---

## 1. ADRs and Design Docs Found

| # | Document | Path | Status | Applicable |
|---|----------|------|--------|------------|
| 1 | ADR 0001: Agent-scoped session ACL | `docs/adr/0001-agent-scoped-session-acl.md` | ✅ accepted | Cross-reference only |
| 2 | ADR 0002: OIDC self-service auth | `docs/adr/0002-oidc-self-service-auth.md` | ✅ proposed | **Primary ADR** |
| 3 | OIDC Self-Service Design Brief | `docs/research/oidc-self-service-design.md` | 📘 research | **Primary design doc** |
| 4 | ADR Index | `docs/adr/README.md` | ✅ N/A | Lists ADRs |
| 5 | Roadmap | `docs/Roadmap.md` | ✅ living | Lists OIDC as backlog |
| 6 | Strategic Market Positioning | `docs/Strategic-Market-Positioning.md` | ✅ living | Notes OAuth2 not shipped |
| 7 | ACL Design Brief | `docs/research/acl-design-brief.md` | 📘 research | Cross-reference only |
| 8 | SECURITY.md | `SECURITY.md` | ✅ living | References bearer model |
| 9 | CONTRIBUTING.md | `CONTRIBUTING.md` | ✅ living | Development workflow |
| 10 | CLAUDE.md | `CLAUDE.md` | ✅ living | Session corrections |
| 11 | Project Structure | `docs/Project-Structure.md` | ✅ living | Directory layout |

---

## 2. Per-Document Verdict

### ADR 0002: OIDC Self-Service Auth — **Partially implemented**

The PR implements the **storage layer** (Phase 1), the **verifier integration** (Phase 2), and the **elicitation state machine** (Phase 3) all in one branch, plus the **server integration** (Phase 4). This is more ambitious than the phased plan in the ADR, which called for four sequential feature branches. The ADR is still marked "proposed" — it has not been formally accepted, meaning the PR is implementing an as-yet-unratified design. This is not a code error but is an architectural governance gap.

### OIDC Self-Service Design Brief — **Partially implemented with deviations**

The design brief provides a detailed spec for Phase 1 sub-phases. The implementation follows the broad strokes but contains several specific deviations documented below.

---

## 3. Concrete Mismatches — ADR vs Code

### M-1: OIDC Key derivation is sha256 hexdigest[:32]; ADR says sha256 hexdigest[:32]

**Location:** `src/auth/queries/oidc_identity.py:23`  
**ADR says** (ADR 0002 § Storage Layer): `SHA-256(sub + issuer)` → `oidc_key`  
**Code does:** `hashlib.sha256(f"{oidc_sub}:{oidc_issuer}".encode()).hexdigest()[:32]`  
**Verdict:** ✅ **Matches.** The delimiter `:` between sub and issuer is not specified in the ADR but is a harmless implementation detail. The hexdigest[:32] truncation matches.

### M-2: `setup_state` CHECK constraint includes `EXPIRED` — not in ADR schema

**Location:** `src/auth/migrations/001_initial_schema.sql:33`  
**ADR says** (ADR 0002 § Storage Layer):
```sql
state TEXT NOT NULL CHECK (state IN ('WAITING_PHONE','WAITING_CODE','WAITING_PASS','COMPLETED','FAILED'))
```
**Code has:**
```sql
state TEXT NOT NULL CHECK (state IN ('WAITING_PHONE','WAITING_CODE','WAITING_PASS','COMPLETED','FAILED','EXPIRED'))
```
**Verdict:** ⚠️ **Deviation.** `EXPIRED` is not an ADR-defined state. The ADR's state machine says "Any state → FAILED (sweep task cleans up)". The code adds `EXPIRED` as a separate terminal state distinct from `FAILED`. While this is arguably an improvement (allowing distinction between user errors and TTL expiry), it's not in the ADR and should be documented.

### M-3: No lockfile — ADR explicitly requires one

**Location:** Multiple files, notably `telegram_auth_service.py:1` (docstring says "no filesystem locks")  
**ADR says** (ADR 0002 § 4. Elicitation State Machine):
> Concurrent sign-in protection via `{oidc_key}.setup.lock` lockfile + in-process single-flight.

**Design brief says:**
> Lockfile: `{data_dir}/{oidc_key}.setup.lock` prevents parallel elicitation for same OIDC sub.  
> In-process single-flight: dict keyed by `oidc_key` ensures only one coroutine handles elicitation per user.  
> Stale lock detection: if lockfile mtime > 10 min, delete and retry.

**Code does:** Removed lockfile in commit `782fb4a` ("make_oidc_key helper + remove lockfile, per Sourcery review"). The `telegram_auth_service.py` docstring says: "Concurrency: relies on DB-based atomic locking in setup_state table. The caller must acquire the lock via atomic UPDATE before calling these methods. No filesystem locks are used."

**Verdict:** ❌ **Major deviation.** The ADR and design brief explicitly require a lockfile + single-flight. The code removed the lockfile entirely and claims DB-based atomic UPDATE is sufficient. However, atomic UPDATE on a single row does **not** prevent concurrent elicitation for the same user because:
- The `submit_phone` → `send_code()` → `transition_state` sequence spans multiple DB operations and an async network call.
- Between the UPDATE and the next operation, another coroutine could interleave.
- There is no in-process single-flight dict either.

The atomic UPDATE mitigates race conditions only at the exact moment of state transition, not over the entire elicitation flow. The ADR's combined lockfile + single-flight was the intended design.

### M-4: Session file naming — `oidc_` prefix not in design

**Location:**
- `src/auth/elicitation_tools.py:83`: `f"oidc_{safe_name}.session"`
- `src/auth/telegram_auth_service.py:74`: `f"oidc_{safe_name}"` (Telethon client session)

**Design brief says:**
```python
def session_filename(oidc_key: str) -> str:
    return hashlib.sha256(oidc_key.encode()).hexdigest()[:16] + ".session"
```
Example: `a1b2c3d4e5f67890.session`

**Code produces:** `oidc_a1b2c3d4e5f67890.session`

**Verdict:** ⚠️ **Minor deviation.** The `oidc_` prefix is not in the design. This could conflict with future session naming conventions (e.g., legacy bearer sessions). Should be documented or aligned.

### M-5: Session directory — design says `{data_dir}/sessions/`, code uses `.sessions`

**Location:** `src/auth/elicitation_tools.py:83`, `src/auth/telegram_auth_service.py:48`  
**Design brief says:**
> They remain as-is in `{data_dir}/sessions/`.

**Code uses:** `os.environ.get("TG_SESSION_DIR", ".sessions")`

**Verdict:** ⚠️ **Minor deviation.** The default `.sessions` (relative to CWD) is not `{data_dir}/sessions/`. The env var override is flexible but the default should match the design.

### M-6: `telegram_session` table stores dummy data, defeating its purpose

**Location:** `src/auth/elicitation_tools.py:106-113`  
**ADR says** (ADR 0002 § Storage Layer): The `telegram_session` table stores `dc_id`, `server_address`, `port`, `auth_key BLOB` — actual Telethon connection metadata for cache preservation.

**Code does:**
```python
insert_session(
    oidc_key=oidc_key,
    session_filename=session_file,
    dc_id=0,
    server_address="",
    port=0,
    auth_key=b"",
    db_path=db_path,
)
```

**Verdict:** ❌ **Critical deviation.** All four connection-metadata fields are hardcoded to zeros/empty. The `telegram_session` table exists in the schema, its CRUD module is fully implemented, and its tests verify real values — but the only callsite that populates it (`_record_session_metadata`) passes placeholders. This means:
- The `auth_key` BLOB is never actually stored (defeating Telethon session preservation).
- `dc_id`, `server_address`, `port` are useless.
- Future code that reads `telegram_session` expecting real data will get garbage.

This is a **significant gap** between design intent and implementation. The design brief explicitly says the table should store "session file metadata" and "provide bearer→OIDC linking".

### M-7: FastMCP's `OAuthProvider` not used — custom `TokenVerifier` instead

**Location:** `src/auth/oauth_provider_adapter.py`, `src/auth/jwt_verifier.py`  
**ADR says** (ADR 0002 § 3. Auth Flow):
> Use FastMCP's built-in `OAuthProvider` for token verification and JWKS handling.

**Code does:** Implements a custom `OidcTokenVerifier` extending `fastmcp.server.auth.TokenVerifier`, with a hand-rolled JWT verifier using `PyJWKClient` directly.

**Verdict:** ⚠️ **Notable deviation.** The ADR explicitly says to use FastMCP's `OAuthProvider`. The code instead uses FastMCP's lower-level `TokenVerifier` interface. The rationale (not documented in code) is presumably that `OAuthProvider` does not expose the principal-resolution hook needed. But this architectural choice is not documented anywhere — no comment, no ADR update, no design brief amendment. If `OAuthProvider` was unsuitable, that should be recorded as a design decision.

### M-8: Design says "4 tables" but only tests for 3 + schema_version

**Location:** `tests/unit/auth/test_db.py:28`  
**Design brief says** (Phase 1.1):
> Write failing test: `test_migrations_create_tables` — verifies all 4 tables exist after `run_migrations()`.

The design's 4 tables are: `oidc_identity`, `telegram_session`, `setup_state`, `schema_version`.

**Code tests for:** `oidc_identity`, `telegram_session`, `setup_state`, `schema_version` — ✅ matches.

**Verdict:** ✅ **Matches.** However, the test calls them "4 tables" but the design includes `schema_version` as a table; the code correctly creates it.

### M-9: Retry count logic — design says re-elicit once per state, code does per-session

**Location:** `src/auth/elicitation_state_machine.py:14`  
**Design brief says** (Elicitation State Machine):
```
WAITING_CODE ──invalid code──▶ WAITING_CODE (re-elicit once) or FAILED
WAITING_PASS ──invalid pass──▶ WAITING_PASS (re-elicit once) or FAILED
```
Each state gets **1 retry** before FAILED.

**Code has:** `MAX_RETRIES = 1` (global, `elicitation_state_machine.py:19`), and `record_retry()` increments a single `retry_count` counter. After 2 total retries across all states, the session fails.

**Verdict:** ⚠️ **Deviation.** The design says **per-state** retry — you get 1 retry on `WAITING_CODE`, and if you reach `WAITING_PASS`, you get another 1 retry there. Total of up to 4 attempts (2 per state). The code implements **per-session** retry — 1 retry total, so you get 1 attempt in `WAITING_CODE` and if you fail there, the session fails before you ever reach `WAITING_PASS`. This is a stricter policy than the design intends.

---

## 4. Drift Items — Patterns Without Design Doc Backing

### D-1: `get_state_row()` — not in design brief API

**Location:** `src/auth/queries/setup_state.py:117`  
**Design brief says** (Phase 1.3 API): `create_state()`, `transition_state()`, `get_active_states()`, `delete_expired()`.  
**Code adds:** `get_state_row()` (fetch by oidc_key) and `increment_retry_count()`.

**Assessment:** Both are necessary additions — the design brief's API was incomplete for the state machine's needs. But their addition is undocumented. The commit `d01b733` (latest) consolidated `_get_state_row` from the state machine into the queries module, which is good hygiene.

### D-2: `oidc_setup_start` stores sub/issuer in `setup_state` metadata

**Location:** `src/auth/elicitation_tools.py:134-139`  
The `oidc_setup_start` function stores `{"oidc_sub": ..., "oidc_issuer": ...}` in the `setup_state.metadata` JSON field. This duplicates information already derivable from the `oidc_key`. The design brief does not mention storing sub/issuer in the state row.

**Assessment:** Pragmatic — avoids needing to pass sub/issuer through every elicitation tool call. But undocumented.

### D-3: `_handle_auth_error` concurrency heuristic

**Location:** `src/auth/elicitation_tools.py:87-98`  
The function checks if the error message contains "Concurrent sign-in" to distinguish concurrency conflicts from user errors. This is a fragile string-match heuristic. The design brief's lockfile mechanism would have prevented concurrent sign-in entirely, making this code path unnecessary.

**Assessment:** A consequence of removing the lockfile (M-3). Fragile and undocumented.

### D-4: `delete_expired()` physically deletes rows instead of marking EXPIRED

**Location:** `src/auth/queries/setup_state.py:76-85`  
The design brief's TTL sweep says "Delete `setup_state` rows where `updated_at < now() - 5 minutes`." The code does physically delete expired rows. However, the state machine also has an `EXPIRED` state that it transitions to. This creates an inconsistency: the state machine can set `state = EXPIRED`, but the TTL sweep physically deletes rows without first transitioning them to EXPIRED. The state machine's `_handle_failed_update` transitions to EXPIRED when it finds an expired row, but the TTL sweep may have already deleted it.

**Assessment:** The TTL sweep deletes, the state machine marks as EXPIRED. These are two different expiry paths with overlapping behavior. Not necessarily wrong, but undocumented.

### D-5: `oidc_setup_phone` stores metadata in a second `transition_state` call

**Location:** `src/auth/elicitation_tools.py:182-190`  
After `submit_phone()` transitions to WAITING_CODE, the code calls `transition_state` again to store `phone_code_hash` in metadata. This means the state is set to WAITING_CODE twice (once in `submit_phone`, once in the tools layer). The second call is redundant for the state field but necessary for metadata.

**Assessment:** Papers over the fact that `transition_state` in the queries module accepts metadata but `submit_phone` in the state machine does not. Better design: the state machine should accept metadata as a parameter.

---

## 5. Architectural Gaps — Concerns Neither ADRs Nor Code Comments Address

### G-1: No defined trust boundary between OIDC verifier and elicitation tools

The `OidcTokenVerifier` returns `None` when no principal mapping exists, which is indistinguishable from an invalid token. The elicitation tools are supposed to be called instead. But how does the client know to call elicitation tools vs getting a 401? The code has no middleware layer that returns a 200 with "elicitation needed" vs a 401. As implemented, unlinked users get the same Auth-N failure as bad-token users. The elicitation tools require `oidc_sub` and `oidc_issuer` as parameters — how does the client obtain these if authentication failed? This circular dependency is not addressed anywhere.

### G-2: No audit trail or logging for identity linking events

The ADR defers audit logging ("Do we need a separate audit log table?"). The PR ships with no audit trail for when an OIDC identity is linked to a Telegram account, when it changes, or when elicitation completes. For a security-sensitive auth flow, this is concerning.

### G-3: No telemetry for OIDC auth failures

The Roadmap's **Telemetry lane** is planned, but OIDC auth failures, JWKS fetch failures, and elicitation failures are logged but not structured as signals. This makes it impossible to detect auth provider outages or brute-force attempts without log scraping.

### G-4: Session file encryption not addressed

The design brief lists this as an open question ("Current .session files store auth_key in plaintext. Worth encrypting at rest?"). The PR ships session files in plaintext at `{TG_SESSION_DIR}/oidc_*.session`. The auth_key BLOB is not stored in the DB (dummy data as per M-6), but the session files on disk contain the full Telethon session, including auth_key. No encryption is applied.

### G-5: No tenant isolation for multi-issuer

The ADR says "Tenant allowlist (`allowed_oidc_issuers`) dropped from v1 scope" and "Single-tenant SaaS assumption." But the code does not enforce this — any issuer can be used as long as TG_OIDC_ISSUER is set. This is fine for single-tenant, but there's no validation that prevents a misconfigured deployment from accepting tokens from any issuer.

### G-6: `telegram_session` table is dead code

The `telegram_session` table is fully schematized, has CRUD operations, has tests — but the only caller passes dummy data (M-6). This is dead code in its current form. Either the table should be removed or the recording path should store real data. The current state adds maintenance burden without providing value.

### G-7: Dual-auth mode has no test coverage

The `server.py` changes for dual-auth mode (`if oidc_enabled()` / else) have no test coverage. There are no tests that verify:
- When TG_OIDC_ISSUER is set, OIDC verifier is created instead of SessionFileTokenVerifier
- When TG_OIDC_ISSUER is not set, legacy bearer auth works unchanged
- TTL sweep task starts only when OIDC is enabled
- Elicitation tools are registered only when OIDC is enabled

### G-8: `scripts/migrate_legacy.py` has no dry-run mode

The migration script modifies the database in-place with no `--dry-run` or `--confirm` flag. The test verifies it works, but an operator running it against a production database has no way to preview changes.

---

## 6. Summary Table

| Category | Items |
|----------|-------|
| ADRs/design docs found | 11 documents |
| ADR 0002 verdict | ⚠️ Partially implemented; ADR still "proposed" |
| Design brief verdict | ⚠️ Partially implemented with 9 deviations |
| **Critical deviations** | M-3 (no lockfile), M-6 (telegram_session dummy data) |
| **Notable deviations** | M-7 (no OAuthProvider), M-9 (retry logic), M-2 (EXPIRED state) |
| **Minor deviations** | M-4 (session naming), M-5 (session dir), M-1 (hash delimiter) |
| **Drift items** | 5 undocumented API additions/behaviors |
| **Architectural gaps** | 8 concerns not addressed by ADRs or code |

---

## 7. Recommendations

1. **ADR 0002 should be accepted and updated** to reflect:
   - Removal of lockfile (and rationale for DB-only concurrency)
   - Addition of `EXPIRED` state
   - Per-session vs per-state retry semantics
   - Custom `TokenVerifier` instead of `OAuthProvider`

2. **`telegram_session` table must either store real data or be removed.** The current dummy-data state is worse than not having the table at all.

3. **Lockfile removal should be re-evaluated.** The DB atomic UPDATE pattern does not provide the same guarantees as a filesystem lockfile + in-process single-flight. At minimum, the in-process single-flight dict should be implemented.

4. **The elicitation → auth circular dependency** needs an architectural solution (e.g., a "pending elicitation" token type) before the auth flow can work in practice.

5. **Add integration tests for the dual-auth server mode** in `server.py`.

6. **The retry logic should match the design intent** (per-state retries, not per-session).

7. **Document all API additions** that drift from the design brief (`get_state_row`, `increment_retry_count`, metadata storage in setup_state).
