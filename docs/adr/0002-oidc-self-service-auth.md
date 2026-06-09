# ADR 0002: OIDC Self-Service Authentication

## Status

Proposed

## Date

2026-06-08

## Context

The current authentication model relies on pre-shared bearer tokens distributed manually by administrators. This creates operational friction:

1.  **No self-service:** Users cannot onboard themselves; admins must generate and distribute tokens.
2.  **Token lifecycle:** Tokens are static secrets with no expiration or rotation mechanism.
3.  **Identity fragmentation:** No link between Telegram identity and external identity providers.
4.  **Roadmap convergence:** The elicitation feature (multi-round sign-in) requires persistent state that bearer tokens cannot provide.

We need an authentication layer that supports self-service onboarding, integrates with SaaS OIDC providers, preserves Telethon session caching, and works orthogonally with the existing ACL system.

## Decision

Implement OIDC-based self-service authentication using FastMCP's built-in `OAuthProvider`, with the following architectural choices:

### 1. Storage: stdlib sqlite3 + Option B

-   Use Python stdlib `sqlite3` with raw SQL migrations. No SQLAlchemy.
-   Shared database stores OIDC identity mappings and elicitation state.
-   Per-user `.session` files remain for Telethon cache preservation (auth_key, entities, sent_files, update_state).
-   Database path configurable via `TG_DATABASE_URL` env var (defaults to `./data/auth.db`).

### 2. ACL Integration: Telegram Identity Keys Only

-   ACL YAML continues to use Telegram identity principals: `@username`, `+phone`, `user_id`.
-   No OIDC keys in ACL configuration. Admins never need to know OIDC identifiers.
-   Principal resolution happens at runtime: OIDC token → DB lookup → Telegram identity → ACL match.
-   Reuse existing `ACL_DENY_UNLISTED_PRINCIPALS` for default policy. No new `default_oidc_policy` variable.

### 3. Auth Flow: FastMCP OAuthProvider

-   Use FastMCP's built-in `OAuthProvider` for token verification and JWKS handling.
-   Required env vars: `TG_OIDC_ISSUER`, `TG_OIDC_AUDIENCE`.
-   Stdio transport skips OIDC entirely (env-configured single user).
-   Bot API tokens skip OIDC (service accounts configured via env).

### 4. Elicitation State Machine

-   Multi-round sign-in: phone number → verification code → optional password.
-   State persisted in `setup_state` table with 5-minute TTL.
-   No explicit concurrent-sign-in control. Telethon's MTProto transport serializes API calls over a single TCP connection — only one `sign_in()` per `phone_code_hash` can succeed at the transport level. The DB atomic `UPDATE` prevents double-insertion. The double-`submit_phone` edge case (two parallel `send_code()` calls overwriting the `phone_code_hash`) is accepted as UX-grade risk (negligible probability, no data integrity impact).
-   Re-elicit once on wrong code/password, then error.

### 5. Migration Strategy

-   Dual auth during transition period: both bearer tokens and OIDC accepted.
-   One-shot migration script links existing bearer tokens to OIDC identities.
-   Hard cutover on major version bump: drop bearer support, retire `web_setup.py`.

## Consequences

### Positive

-   ✅ Self-service onboarding eliminates admin token distribution bottleneck.
-   ✅ Multi-tenant ready (single-tenant SaaS assumption in v1, extensible later).
-   ✅ Telethon cache preserved — no performance regression.
-   ✅ Orthogonal to ACL — no config changes required for existing deployments.
-   ✅ Elicitation state persists across restarts.

### Negative

-   ⚠️ Opaque filenames for session files (hash-based, not human-readable).
-   ⚠️ Elicitation complexity: state machine, TTL sweep.
-   ⚠️ New dependency on SaaS OIDC provider (Auth0/Clerk/WorkOS).
-   ⚠️ Migration window requires dual-auth support.

### Neutral

-   OIDC sub changes treated as new users in v1 (warning logged). Orphan cleanup deferred to Phase 2.
-   Tenant allowlist (`allowed_oidc_issuers`) dropped from v1 scope.

## Alternatives Considered

### Custom JWT Verifier

Rejected: FastMCP already provides battle-tested `OAuthProvider`. Building our own adds maintenance burden with no benefit.

### SQLAlchemy ORM

Rejected: Database schema is 3 tables with simple queries. SQLAlchemy adds dependency weight and abstraction overhead for minimal gain.

### Postgres/Redis for v1

Rejected: Overkill for single-instance deployment. SQLite sufficient. Can migrate later if needed.

### Single Shared DB (Including Telethon Tables)

Rejected: Telethon `.session` files contain 5 tables with internal caching logic. Moving to shared DB risks breaking cache invalidation and complicates Telethon upgrades.

### OIDC Keys in ACL Config

Rejected: Admins would need to extract opaque OIDC identifiers from logs or DB. Unacceptable UX. Telegram identity is stable and human-readable.

### Tenant Allowlist (`allowed_oidc_issuers`)

Rejected: No clear use case for multi-tenant restriction in v1. Single-tenant SaaS assumption simplifies implementation. Can add later if demand emerges.

## Implementation Phases

### Phase 1: Storage Layer (`feature/oidc-storage`)

-   Database schema: `oidc_identity`, `setup_state`.
-   Migration runner with version tracking.
-   Connection pool configuration.
-   `migrate_legacy.py` script for bearer-to-OIDC linking.

### Phase 2: Verifier Integration (`feature/oidc-verifier`)

-   FastMCP `OAuthProvider` configuration.
-   Principal-ID resolution middleware.
-   Environment variable validation.

### Phase 3: Elicitation State Machine (`feature/oidc-elicitation`)

-   Sign-in flow controller.
-   TTL sweep background task.
-   No explicit concurrency control — relies on Telethon MTProto serialization + DB atomic writes.
-   Error handling and re-elicit logic.

### Phase 4: Major Version Cutover (`feature/oidc-major-cutover`)

-   Drop bearer token support.
-   Retire `web_setup.py`.
-   Update documentation and examples.
-   Bump major version.

## References

-   [ADR 0001: Agent-Scoped Session ACL](./0001-agent-scoped-session-acl.md)
-   [OIDC Self-Service Design Brief](../research/oidc-self-service-design.md)
-   [FastMCP OAuthProvider Documentation](https://gofastmcp.com/servers/auth)
