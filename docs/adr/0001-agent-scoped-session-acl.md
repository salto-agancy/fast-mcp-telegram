# ADR 0001: Agent-scoped session ACL (guardrails, not account lockdown)

**Status:** accepted  
**Date:** 2026-05-27

## Context

fast-mcp-telegram supports **http-auth** hosting: one gateway, many Bearer tokens, typically one Telegram account per token. Operators may run **personal** accounts (one human, one agent) or **shared** accounts (team inbox, automation posting to channels).

Telegram is inherently **multi-device**: the same account remains fully usable in official clients, other bots, and direct API access outside this MCP server. Any policy that assumes “the MCP server is the security perimeter for the Telegram account” is misleading for personal use and incomplete for shared hosting.

**Shared team accounts** add a human threat model beyond agent accidents: multiple people may share one Bearer token on an http-auth host. Chat **lanes** (`chats` allowlist) limit which workspaces an agent may touch, but a malicious or curious teammate with the same token could still read **sensitive Telegram peers** used for account and bot security — e.g. login PIN codes from Telegram’s service user (`777000`), BotFather (`93372553`), and related official bots — unless the server enforces a **sensitive peer denylist** independent of the chat whitelist.

Prior research compared competitors (e.g. default-deny chat ACL in other MCP servers). That informed **mechanisms** (chat whitelist, read-only, search scope) but must not drive our **primary goal**: enterprise zero-trust account lockdown or feature parity as the north star.

## Decision

**Session ACL is agent guardrails** — scoped **lanes** for MCP/MTProto tool use per Bearer token, not account-wide lockdown.

| Lens | Meaning |
| --- | --- |
| **Scope** | Workspace **lane**: which chats an agent profile may touch via tools |
| **Capabilities** | Agent **profiles**: read, write (send/edit), search, MTProto — expressed as `read_only`, `allow_global_search`, future `allow_mtproto`, etc. |
| **Default for personal** | Opt-in ACL; tokens **omitted** from config keep full tool access (human Telegram use unchanged) |
| **Default for multi-tenant** | `ACL_DEFAULT=deny` may be offered later; **not** the recommended default for personal/demo hosting |

ACL applies only when **`ACL_ENABLED=true`** on **http-auth**. No ACL on stdio or http-no-auth in the current design.

Human operators continue to use Telegram normally; guardrails reduce **accidental** cross-lane data mixup and **inadvertent** destructive tool actions by agents connected through this server, and — for shared tokens — **intentional** reads of security-sensitive peers via MCP tools.

**Sensitive peer denylist:** When ACL is enabled, a server-maintained set of peer ids (login/service notifications, BotFather, etc.) is **always denied** for MCP tool access, even if a token’s `chats` allowlist would otherwise include them. Operators may **extend** the denylist per deployment; they cannot disable built-in defaults. This is guardrails for **blast radius on shared tokens**, not a substitute for rotating compromised tokens or Telegram-side 2FA.

## Consequences

### Policy model

- Static server-side file (`acl.yaml` / JSON); no MCP tool to edit ACL in v1.
- Enforcement at tool boundaries (pre-check + post-filter where needed).
- Errors should name the fix (token, chat id, flag) for operators and agents.

### Defaults and phases

- **Phase 1 (merge blockers):** correctness and operator docs — empty `chats` leak fix, malformed token handling, `read_only` requires `chats` validation, SECURITY.md runbook, alignment with this ADR.
- **Phase 1.5 (Trust lane):** **sensitive peer denylist** — server defaults always on when `ACL_ENABLED`; optional operator `blocked_peers.extend`; enforced on chat-scoped tools, `find_chats` / global search post-filters, and `invoke_mtproto` / MTProto bridge (no bypass). See [acl-design-brief.md](../research/acl-design-brief.md).
- **Phase 2 (v1.5):** `ACL_DEFAULT` env (default `full_access`), `allow_mtproto` default false for listed tokens, `allow_global_search` blocks MTProto for agent profiles, enforcement registry, config warnings.
- **Phase 3 (roadmap, deferred):** file-watch reload, external ACL store, per-chat permission matrix — lower priority than agent-profile guardrails.

### Documentation tone

- Describe ACL as **lanes and agent profiles**, not “zero-trust” or “security perimeter.”
- Competitor default-deny is a **reference**, not a product requirement.
- Roadmap **Trust** lane: blast-radius and agent correctness for shared hosting, not replacing Telegram account security.
- Shared-team threat: document that ACL + lanes do **not** stop a teammate with the raw Bearer token from calling the API outside MCP; sensitive peer blocking addresses **in-server** exfiltration of login codes and bot-administration chats only.

### Deferred

- Prgebish-style per-operation matrix (`draft`, `mark_read`, per-chat send/read split) — Phase 3 unless operator demand.
- Dynamic ACL via agents; username→id resolve at load; ACL-aware SQLite cache — roadmap items.
- Stdio filesystem sandbox — separate track.

## References

- [acl-design-brief.md](../research/acl-design-brief.md) — policy model and phases
- [acl-operator-research.md](../research/acl-operator-research.md) — competitor notes (reference only)
- [Roadmap.md](../Roadmap.md) — Trust lane
- [session_acl.py](../../src/server_components/session_acl.py) — implementation
