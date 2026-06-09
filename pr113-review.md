# PR #113 Code Quality Review — `feature/oidc-phase1-storage`

**Branch:** `feature/oidc-phase1-storage`  
**Scope:** OIDC self-service auth storage layer (Phases 1–4)  
**Files changed:** ~30 files (+3609 lines)  
**Review focus:** Error handling, type safety, code structure, imports, naming, comments, tests

---

## 1. Error Handling Patterns

### HIGH: `jwt_verifier.py` bare `Exception` catch masks system-level errors

**File:** `src/auth/jwt_verifier.py:82-84`  
**Issue:** The final `except Exception` block on line 82 catches everything, including `SystemExit`, `KeyboardInterrupt`, and `GeneratorExit`. If a developer accidentally triggers a system-level interrupt during JWKS fetch, it would be swallowed with only a `logger.warning`.

```python
except Exception as e:
    logger.warning("OIDC JWKS fetch or unexpected error: %s", e)
    return None
```

**Fix:** Use `except (requests.RequestException, jwt.PyJWTError, OSError)` or at minimum re-raise `BaseException` subclasses that aren't `Exception`. Alternatively, use `except Exception` but log at a higher level.

**Severity:** HIGH — system interrupts could be silently swallowed in production.

---

### MEDIUM: `elicitation_tools.py` `_record_session_metadata` uses blind `except Exception`

**File:** `src/auth/elicitation_tools.py:137-140`  
**Issue:** The function catches all exceptions and logs a warning, then continues. While documented as "non-critical," a bare `except Exception` will also catch `sqlite3.IntegrityError` (which should propagate) and `TypeError` (which indicates a programming bug). The function already imports `sqlite3` transitively via `db` — it could catch `sqlite3.Error` + `Exception` for truly unexpected errors.

```python
except Exception:
    logger.warning("Session metadata insert failed for %s (non-critical)", oidc_key[:8])
```

**Severity:** MEDIUM — could mask real DB issues during session persistence.

---

### MEDIUM: `telegram_auth_service.py` `__init__` raises `KeyError` instead of a domain-specific exception

**File:** `src/auth/telegram_auth_service.py:66-70`  
**Issue:** When `TG_API_ID` or `TG_API_HASH` are missing, the constructor raises a bare `KeyError` (wrapped with a helpful message). Callers must catch `KeyError` specifically, which is unusual for a configuration error. A custom `ConfigurationError` or `ValueError` would be more appropriate.

```python
except KeyError as e:
    raise KeyError(
        f"Missing required env var: {e}. "
        f"Set TG_API_ID and TG_API_HASH for Telethon sign-in."
    ) from e
```

**Severity:** MEDIUM — not critical but poor API design; forces callers to catch a generic exception type.

---

### LOW: `oidc_integration.py` `ttl_sweep_task` catches `asyncio.CancelledError` but also bare `Exception`

**File:** `src/auth/server_components/oidc_integration.py:68-76`  
**Issue:** The `except Exception` block will catch `asyncio.CancelledError` on Python < 3.8, but since the code targets Python 3.10+ (uses `str | None` syntax in some files), this is acceptable. However, the bare `Exception` is broad; any transient DB error is logged but the loop continues, which is the correct behavior for a background sweep task. Acceptable pattern for a background task, but worth noting.

**Severity:** LOW — acceptable for background sweep but could be more specific.

---

## 2. Type Safety

### HIGH: `_save_identity_and_session` parameter has no type annotation

**File:** `src/auth/elicitation_tools.py:152`  
**Issue:** The `sign_in_result` parameter has no type annotation at all. The function accesses `.user_id`, `.username`, `.session_string` on it, which means it expects a `SignInResult` from `telegram_auth_service`, but this is not enforced by the type system.

```python
def _save_identity_and_session(
    oidc_key: str,
    oidc_sub: str,
    oidc_issuer: str,
    sign_in_result,  # <-- missing type annotation
    phone_number: str,
    db_path: Optional[str] = None,
) -> None:
```

**Severity:** HIGH — breaks type safety for a core persistence function.

---

### HIGH: Mixed type hint conventions across modules (Optional[str] vs str | None)

**Files:** Multiple files across `src/auth/`  
**Issue:** The codebase inconsistently mixes old-style (`Optional[str]`) and new-style (`str | None`) type annotations. For example:

| File | Style |
|------|-------|
| `db.py` | `str \| None` |
| `elicitation_state_machine.py` | `Optional[str]` |
| `elicitation_tools.py` | `Optional[str]` |
| `queries/oidc_identity.py` | `Optional[str]` |
| `queries/setup_state.py` | **MIXED** (`Optional[str]` and `str \| None` in same file) |
| `queries/telegram_session.py` | `str \| None` |
| `jwt_verifier.py` | `Optional[str]` |
| `oauth_provider_adapter.py` | `Optional[str]` |
| `principal_resolver.py` | `Optional[str]` |

The worst offender is `queries/setup_state.py` where `create_state` uses `Optional[str]` while `create_setup_state` (defined 50 lines later) uses `str | None` — in the same file.

**Severity:** HIGH — inconsistency will cause confusion and potential mypy errors; the project should pick one style.

---

### MEDIUM: `oidc_integration.py` missing return type on `create_oidc_verifier`

**File:** `src/auth/server_components/oidc_integration.py:27-39`  
**Issue:** The function has no return type annotation. It returns either `OidcTokenVerifier` or `None`.

```python
def create_oidc_verifier(db_path: Optional[str] = None):
    """Create OidcTokenVerifier instance from env vars."""
```

**Severity:** MEDIUM — return type should be `Optional[OidcTokenVerifier]`.

---

### MEDIUM: `oidc_integration.py` `register_elicitation_tools` missing parameter type

**File:** `src/auth/server_components/oidc_integration.py:42`  
**Issue:** The `mcp` parameter has no type annotation.

```python
def register_elicitation_tools(mcp) -> None:
```

**Severity:** MEDIUM — should be annotated with the FastMCP type.

---

### LOW: `queries/setup_state.py` `create_setup_state` parameter type slightly wrong

**File:** `src/auth/queries/setup_state.py:196-205`  
**Issue:** Parameter typed as `metadata: dict | None = None` but the function passes `json.dumps(metadata) if metadata else None`. An empty dict `{}` would be treated as falsy and stored as `None`, which is likely not intended.

```python
def create_setup_state(
    oidc_key: str,
    state: str,
    phone_number: str | None = None,
    metadata: dict | None = None,  # <-- empty dict is falsy, stored as None
    ...
```

**Severity:** LOW — edge case, unlikely to pass empty dict intentionally.

---

## 3. Code Structure

### HIGH: Duplicate/overlapping APIs in `queries/setup_state.py`

**File:** `src/auth/queries/setup_state.py`  
**Issue:** The file contains TWO parallel sets of CRUD functions operating on the same table:

| First set (original) | Second set ("consolidated") |
|---|---|
| `create_state()` | `create_setup_state()` |
| `transition_state()` | `update_setup_state()` |
| `get_active_states()` | `get_setup_state()` |
| `delete_expired()` | `expire_old_states()` |
| `increment_retry_count()` | — |
| `get_all_active_states()` | — |
| `get_expired_states()` | — |

The second set (`create_setup_state`, `get_setup_state`, `update_setup_state`, `expire_old_states`) has `# consolidated` in the docstrings, but the first set is NOT marked as deprecated and is still actively used by `elicitation_state_machine.py`. This creates a maintenance burden — bugs fixed in one API may not be reflected in the other, and developers must guess which API to use.

**Example of duplication:** `create_state` (line 16) and `create_setup_state` (line 196) both insert into `setup_state` but with different column handling. `create_state` uses SQL defaults for timestamps, while `create_setup_state` generates timestamps in Python. `create_state` doesn't accept `metadata`, while `create_setup_state` does — but `transition_state` (first set) does accept `metadata`. The API surface is confusing.

**Severity:** HIGH — will cause maintenance issues and potential subtle bugs.

---

### MEDIUM: `db.py` has misleading stub comment with no re-exports

**File:** `src/auth/db.py:78-83`  
**Issue:** The file ends with:

```python
# ---------------------------------------------------------------------------
# setup_state CRUD — re-exported from queries module (source of truth)
# ---------------------------------------------------------------------------
```

But there are no actual re-exports following this comment. It's a leftover stub that confuses readers about what `db.py` actually exports.

**Severity:** MEDIUM — misleading documentation.

---

### MEDIUM: `elicitation_state_machine.py` contains dead code — `sweep_expired()` is a no-op

**File:** `src/auth/elicitation_state_machine.py:236-245`  
**Issue:** The function `sweep_expired()` is marked as deprecated and simply returns `0`. It is not called anywhere in the codebase (confirmed by searching). Maintaining dead code adds to cognitive load.

```python
def sweep_expired(db_path: Optional[str] = None) -> int:  # noqa: D401
    """DEPRECATED: use queries.setup_state.delete_expired() instead. ..."""
    return 0
```

**Severity:** MEDIUM — dead code should be removed, not kept as a no-op stub.

---

### MEDIUM: `elicitation_state_machine.py` `_get_state_row()` docstring is misleading

**File:** `src/auth/elicitation_state_machine.py:85`  
**Issue:** The docstring says "via get_active_states or direct query" but the implementation only does a direct SQL query:

```python
def _get_state_row(oidc_key: str, db_path: Optional[str] = None) -> Optional[dict]:
    """Fetch current state row as dict via get_active_states or direct query."""
```

It never calls `get_active_states`. It always does a direct `SELECT ... WHERE oidc_key = ?`.

**Severity:** MEDIUM — misleading documentation.

---

## 4. Imports

### HIGH: `elicitation_state_machine.py` has 2 unused imports

**File:** `src/auth/elicitation_state_machine.py:16,21`  
**Issue:** Both `import json` (line 16) and `from .queries import oidc_identity as id_queries` (line 21) are never used in the module. `json` is not referenced in any function body. `id_queries` (the `oidc_identity` module alias) is imported but never called — only `ss_queries` (setup_state) is used.

```
Unused: import json
Unused: from .queries import oidc_identity as id_queries
```

**Severity:** HIGH — unused imports create confusion about dependencies and should be removed.

---

### MEDIUM: `queries/oidc_identity.py` has unused `import sqlite3`

**File:** `src/auth/queries/oidc_identity.py:4`  
**Issue:** `sqlite3` is imported but only referenced in a docstring (line 32: "Raises sqlite3.IntegrityError..."). It is never used in code — no `sqlite3.` calls exist in the module body. The `IntegrityError` is raised by `get_connection`'s `conn.execute()` call and propagates naturally without being explicitly caught.

```python
import sqlite3  # <-- only used in a docstring
```

**Severity:** MEDIUM — unused import; confuses static analysis.

---

### MEDIUM: `oauth_provider_adapter.py` uses absolute imports while sibling modules use relative imports

**File:** `src/auth/oauth_provider_adapter.py:8-9`  
**Issue:** Uses:
```python
from src.auth.jwt_verifier import verify_oidc_token
from src.auth.principal_resolver import resolve_principal
```

While every other module in `src/auth/` uses relative imports:
```python
# e.g., in elicitation_tools.py:
from .queries import oidc_identity as id_queries
from . import db
```

The absolute paths will break if the package is restructured (e.g., if the project becomes a namespace package). Since `oauth_provider_adapter.py` lives inside `src/auth/`, it should use relative imports like its siblings.

**Severity:** MEDIUM — import path fragility; inconsistent with project conventions.

---

## 5. Variable Naming

### LOW: Minor naming inconsistency

**File:** `src/auth/elicitation_state_machine.py:236`  
**Issue:** The `sweep_expired()` function name is misleading — it doesn't actually sweep anything, it's a no-op returning `0`. If kept for backwards compatibility, the name should at least suggest it's a stub (e.g., `sweep_expired_stub`). However, since it's dead code, it should just be removed.

**Severity:** LOW — dead code should be removed rather than renamed.

---

### No other naming issues found

The rest of the codebase has clear, consistent naming:
- `ElicitState` enum values are self-documenting
- Function names like `start_elicitation`, `submit_phone`, `submit_code`, `submit_password` clearly describe their purpose
- `make_oidc_key`, `verify_oidc_token`, `resolve_principal` follow consistent verb-noun patterns
- `_result_to_dict`, `_fetch_session_metadata`, `_save_session_file` use the `_` prefix convention for private helpers

---

## 6. Comment Quality

### MEDIUM: `db.py` stub comment is misleading (same as section 3)

**File:** `src/auth/db.py:78-83`  
**Issue:** See Section 3. The comment suggests `db.py` re-exports `setup_state` CRUD, but it doesn't. This is a leftover from refactoring.

**Severity:** MEDIUM — misleading documentation that will confuse future developers.

---

### MEDIUM: `elicitation_state_machine.py` `_get_state_row()` docstring is inaccurate

**File:** `src/auth/elicitation_state_machine.py:85-86`  
**Issue:** See Section 3. Docstring claims the function uses `get_active_states` but it only does a direct SQL query.

**Severity:** MEDIUM — inaccurate documentation.

---

### HIGH: Test comment describes wrong assertion

**File:** `tests/unit/auth/test_jwt_verifier.py:210-215`  
**Issue:** The test `test_jwks_caches_client_for_ttl` has a comment saying:
```python
# PyJWKClient should only be instantiated once
```
But the assertion checks `mock_jwks_client.get_signing_key_from_jwt.call_count == 2`. These are different things:
- "PyJWKClient instantiated once" should check `PyJWKClient` constructor call count is 1
- The actual assertion checks that `get_signing_key_from_jwt` was called twice (once per verification)

The test logic is actually correct — `get_signing_key_from_jwt` should be called twice because each `verify_oidc_token` call invokes it. The comment is wrong/misleading.

```python
# PyJWKClient should only be instantiated once
from src.auth.jwt_verifier import PyJWKClient
# We patched it, so check the mock call count
assert mock_jwks_client.get_signing_key_from_jwt.call_count == 2
```

**Severity:** HIGH — misleading comment in test that will confuse future maintainers about what's being tested.

---

### LOW: Good quality comments otherwise

- The `# noqa: S608` comments with safety explanations are excellent
- The module-level docstrings are comprehensive and useful
- The state diagram in `elicitation_state_machine.py` docstring is clear
- The `pr_body.md` and ADR are well-documented

---

## 7. Test Quality

### HIGH: `test_jwks_caches_client_for_ttl` comment/assertion mismatch (same as section 6)

**File:** `tests/unit/auth/test_jwt_verifier.py:210-223`  
**Issue:** The test comment says one thing, the assertion checks another. The test SHOULD verify that `PyJWKClient` constructor is called only once (by checking `PyJWKClient.call_count == 1` or `mock_cls.call_count == 1`). Instead, it checks `get_signing_key_from_jwt.call_count == 2`, which is about a different concern (key fetching, not client construction).

The test works but is poorly documented — the next developer might think the cache test is broken because the assertion doesn't match the comment.

**Severity:** HIGH — undermines trust in the test suite.

---

### MEDIUM: `test_update_oidc_identity_timestamp` uses fragile `time.sleep(0.01)`

**File:** `tests/unit/auth/test_oidc_identity_queries.py:74`  
**Issue:** The test sleeps for 10ms to ensure `updated_at` timestamps differ:
```python
import time
time.sleep(0.01)
update_identity(oidc_key="key2", ...)
```

SQLite's `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` has **second** precision, not millisecond. The 10ms sleep does nothing useful — both calls would still happen within the same second. The test would pass even without the sleep because of the `>=` comparison:
```python
assert updated["updated_at"] >= original["updated_at"]
```

The sleep is both unnecessary (the `>=` assertion handles equality) and insufficient (10ms doesn't cross a second boundary reliably). It should be removed.

**Severity:** MEDIUM — unnecessary sleep that slows tests without providing value.

---

### MEDIUM: No test for `oidc_setup_code` with missing session metadata

**File:** `tests/unit/auth/test_elicitation_tools.py`  
**Issue:** The test `test_verify_code_success_no_2fa` tests the happy path where metadata exists with `phone_number` and `phone_code_hash`. But there's no test for what happens when:
- A user calls `oidc_setup_code` without having called `oidc_setup_phone` first (missing metadata)
- Metadata exists but is malformed JSON

The `_fetch_session_metadata` function handles these edge cases (returns `{}` on decode failure, returns `None` for missing row), and `oidc_setup_code` checks for missing fields, but there are no tests for these paths.

**Severity:** MEDIUM — uncovered error paths.

---

### MEDIUM: `test_expires_old_session` bypasses `get_connection` context manager

**Files:** `tests/unit/auth/test_elicitation_state_machine.py:76-78` and similar in `test_setup_state_queries.py`  
**Issue:** Multiple tests manually open raw `sqlite3.connect()` connections to backdate timestamps, bypassing the `get_connection()` context manager:
```python
import sqlite3
conn = sqlite3.connect(clean_db)
conn.execute("UPDATE setup_state SET updated_at = ? WHERE oidc_key = ?", (old_time, oidc_key))
conn.commit()
conn.close()
```

This is a test smell — it means the test DB setup is leaky. While acceptable for timestamp manipulation (since direct SQL is the simplest approach), it creates a pattern where tests don't use the production code paths. A helper fixture or utility function for backdating timestamps would be cleaner.

**Severity:** MEDIUM — test coupling to raw SQLite rather than using the abstraction layers under test.

---

### LOW: `test_jwt_verifier.py` `test_missing_sub_claim_returns_none` tests PyJWT behavior, not custom logic

**File:** `tests/unit/auth/test_jwt_verifier.py:248-268`  
**Issue:** The test "verifies" that `verify_oidc_token` returns `None` when the `sub` claim is missing. However, this is entirely enforced by PyJWT's `options={"require": ["sub"]}` — our code doesn't validate `sub` presence independently. The test is valid (it tests the observable behavior) but documents a requirement that's not enforced by our code. If someone removes `"sub"` from the `require` list, this test would fail, which is good — but the test name implies our code specifically handles `sub` validation.

**Severity:** LOW — technically accurate but could be misleading about where the validation lives.

---

### LOW: Good test coverage overall

Strengths of the test suite:
- All test files use `tmp_path` fixtures with isolated databases
- Fixture cleanup is handled automatically (temp dirs removed after test)
- `@pytest.mark.asyncio` is properly applied to async tests
- Mock isolation is handled correctly (e.g., `_jwks_cache.clear()` in adapter tests)
- Both happy paths and error paths are tested for core CRUD operations
- The legacy migration test runs the actual script as a subprocess, testing integration
- State machine tests cover all transitions and edge cases (no session, wrong state, expired)

---

## Summary of Findings

| # | Severity | Category | File:Lines | Description |
|---|----------|----------|------------|-------------|
| 1 | **HIGH** | Error handling | `jwt_verifier.py:82-84` | Bare `except Exception` swallows `SystemExit`/`KeyboardInterrupt` |
| 2 | **HIGH** | Type safety | `elicitation_tools.py:152` | `sign_in_result` parameter has no type annotation |
| 3 | **HIGH** | Type safety | Multiple files | Mixed `Optional[str]`/`str\|None` conventions across codebase |
| 4 | **HIGH** | Code structure | `queries/setup_state.py` | Two overlapping CRUD API sets for the same table |
| 5 | **HIGH** | Imports | `elicitation_state_machine.py:16,21` | 2 unused imports (`json`, `id_queries`) |
| 6 | **HIGH** | Comment quality | `test_jwt_verifier.py:210-223` | Comment says "instantiated once", assertion checks call_count=2 |
| 7 | **HIGH** | Test quality | `test_jwt_verifier.py:210-223` | Comment/assertion mismatch undermines test documentation |
| 8 | **MEDIUM** | Error handling | `elicitation_tools.py:137-140` | Blind `except Exception` in session metadata insert |
| 9 | **MEDIUM** | Error handling | `telegram_auth_service.py:66-70` | Raises `KeyError` instead of domain exception |
| 10 | **MEDIUM** | Type safety | `oidc_integration.py:27` | Missing return type on `create_oidc_verifier` |
| 11 | **MEDIUM** | Type safety | `oidc_integration.py:42` | `mcp` parameter missing type annotation |
| 12 | **MEDIUM** | Code structure | `db.py:78-83` | Misleading stub comment about non-existent re-exports |
| 13 | **MEDIUM** | Code structure | `elicitation_state_machine.py:236-245` | Dead code: `sweep_expired()` no-op stub |
| 14 | **MEDIUM** | Code structure | `elicitation_state_machine.py:85-86` | `_get_state_row()` docstring claims it uses `get_active_states` but doesn't |
| 15 | **MEDIUM** | Imports | `queries/oidc_identity.py:4` | Unused `import sqlite3` (only in docstring) |
| 16 | **MEDIUM** | Imports | `oauth_provider_adapter.py:8-9` | Absolute imports in a package that uses relative imports everywhere else |
| 17 | **MEDIUM** | Comment quality | `db.py:78-83` | Misleading stub (same as #12) |
| 18 | **MEDIUM** | Comment quality | `elicitation_state_machine.py:85-86` | Inaccurate docstring (same as #14) |
| 19 | **MEDIUM** | Test quality | `test_oidc_identity_queries.py:74` | Unnecessary `time.sleep(0.01)` that doesn't cross second boundary |
| 20 | **MEDIUM** | Test quality | `test_elicitation_tools.py` | Missing tests for `oidc_setup_code` with missing/metadata error paths |
| 21 | **MEDIUM** | Test quality | Multiple test files | Tests bypass `get_connection()` to backdate timestamps via raw sqlite3 |
| 22 | **LOW** | Error handling | `oidc_integration.py:68-76` | Bare `except Exception` in TTL sweep (acceptable for background task) |
| 23 | **LOW** | Type safety | `queries/setup_state.py:196-205` | Empty dict `{}` treated as falsy → stored as `None` |
| 24 | **LOW** | Naming | `elicitation_state_machine.py:236` | `sweep_expired` is dead code no-op |
| 25 | **LOW** | Test quality | `test_jwt_verifier.py:248-268` | Test documents code behavior but validation is in PyJWT, not custom code |

**Overall assessment:** The PR introduces substantial, well-tested functionality with good documentation. The main areas for improvement are: (1) consolidating the duplicate CRUD APIs in `setup_state.py`, (2) fixing the unused imports, (3) aligning type hint conventions, (4) removing dead code, and (5) fixing the misleading test comment. The error handling patterns are generally good with a few overly broad exception catches. Test coverage is excellent for the happy paths.
