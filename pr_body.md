## Summary

Complete OIDC self-service authentication feature for fast-mcp-telegram. Replaces bearer token auth with SaaS OIDC (Auth0/Clerk/WorkOS) while preserving Telethon session caching and YAML-based ACL.

## Phases Delivered

### Phase 1: Storage Layer
- SQLite schema (oidc_identity, telegram_session, setup_state tables)
- Migration runner with version tracking
- CRUD query modules for all three tables
- Legacy bearer-to-OIDC migration script
- ISO 8601 UTC timestamp convention

### Phase 2: OAuthProvider Integration
- JWT verifier with JWKS caching
- Principal resolver (oidc_sub to Telegram identity to ACL principal)
- FastMCP TokenVerifier adapter

### Phase 3: Elicitation State Machine
- Multi-round sign-in flow (phone, code, password)
- Persistent state with 5min TTL
- Telegram auth service wrapping Telethon
- 4 elicitation tools registered on FastMCP

### Phase 4: Server Integration
- Dual-auth: OIDC when TG_OIDC_ISSUER/TG_OIDC_AUDIENCE set, legacy bearer otherwise
- DB migrations run on startup
- TTL sweep background task for expired elicitation states
- Elicitation tools auto-registered when OIDC enabled

## Test Coverage
- 37 non-Telethon unit tests passing
- Adapter fixture isolation fixed via _jwks_cache.clear()
- All CRUD, state machine, JWT verification, and principal resolution covered

## Design Docs
- ADR 0002: docs/adr/0002-oidc-self-service-auth.md
- Design Brief: docs/research/oidc-self-service-design.md

## Migration Path
1. Deploy with OIDC env vars unset: existing bearer auth unchanged
2. Set TG_OIDC_ISSUER + TG_OIDC_AUDIENCE: dual-auth mode
3. Run scripts/migrate_legacy.py to link existing bearers
4. Users sign in via OIDC, complete Telegram elicitation
5. Future major version: drop bearer support
