# Session ACL design brief — agent guardrails

**Status:** Approved direction (2026-05-27). Supersedes zero-trust / account-lockdown framing.  
**ADR:** [ADR 0001 — Agent-scoped session ACL](../adr/0001-agent-scoped-session-acl.md).

## First principles


| Principle                       | Implication                                                                                                                                                   |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Telegram is multi-device**    | Humans use the account outside MCP; ACL does not “secure the account.”                                                                                        |
| **MCP is an agent surface**     | Guardrails limit what **connected agents** can do via tools, not human clients.                                                                               |
| **Lanes, not lockdown**         | Each Bearer token maps to a **workspace lane** (chat set + capability profile).                                                                               |
| **Opt-in for personal hosting** | Unlisted tokens keep full tool access; avoids breaking demos and single-user setups.                                                                          |
| **Shared hosting needs lanes**  | Team/automation tokens get explicit profiles so agents do not mix work and personal chats.                                                                    |
| **Shared token, human threat**  | Teammates with the same Bearer token can abuse MCP tools; **sensitive peers** (login codes, BotFather) need a server denylist **outside** the chat allowlist. |


**Not goals:** enterprise zero-trust perimeter, Prgebish default-deny parity, or blocking legitimate human Telegram use. Sensitive peer blocking does **not** stop humans using the official Telegram app or calling Telegram APIs outside this server.

## Learning fields (build–measure–learn)


| Field              | Content                                                                                                                                                                                                 |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hypothesis**     | Static per-token lanes plus clear agent profiles prevent accidental cross-chat access and unintended send/MTProto actions without harming personal single-token deployments.                            |
| **Success signal** | Operators run multiple agents on one http-auth host with confidence; ACL denials are understandable; no reported “empty chats leaked everything” or silent full-access regressions for unlisted tokens. |
| **Kill / stop**    | If guardrails require default-deny for all tokens to be safe, or if enforcement cannot be centralized without tool drift — narrow to Phase 1 correctness only and defer profile expansion.              |


## Policy model

### Scope = workspace lane

- `**chats`**: allowlist of chat ids / `@username` / `me` (Saved Messages) for this token’s lane.
- Tokens **omitted** from the file: full lane (all chats) when `ACL_ENABLED=true`.
- Tokens **listed** with empty `chats`: deny chat-scoped operations (explicit empty lane — must not leak all chats).

### Capabilities = agent profiles

Expressed as flags today; named profiles for operators:


| Agent profile                         | Typical `chats` | `read_only` | `allow_global_search` | `allow_mtproto` (Phase 2) |
| ------------------------------------- | --------------- | ----------- | --------------------- | ------------------------- |
| **full_access** *(default, unlisted)* | —               | false       | true                  | true                      |
| **analyst**                           | lane list       | true        | true                  | false                     |
| **team_lane**                         | work ids        | false       | true                  | false                     |
| **bot**                               | peer ids        | false       | false                 | false                     |


Phase 2 adds `**allow_mtproto`** (default **false** for listed tokens). When `allow_global_search` is false, MTProto remains blocked for that profile even if `read_only` is false.

### Sensitive peers = deployment denylist (Phase 1.5)


| Dimension           | `chats` (lane)                                               | `blocked_peers` (sensitive)                                          |
| ------------------- | ------------------------------------------------------------ | -------------------------------------------------------------------- |
| **Question**        | Which chats may this token use?                              | Which peers must **never** be reachable via MCP tools?               |
| **Default**         | Unlisted token → all chats; listed empty → deny all chat ops | **Omitted** → no sensitive blocking; lane ACL only                   |
| **Operator config** | Per-token `chats`                                            | Optional **deployment-wide** list (operators own full denylist)      |
| **Override**        | Per-token allowlist can include work ids                     | **Deny wins** if peer is both in `chats` and on `blocked_peers`      |
| **Threat**          | Agent crosses work/personal boundary                         | Malicious teammate reads login PINs or revokes bots via shared token |


Recommended defaults for shared hosts (example + SECURITY.md only — not enforced unless copied into config):

| Peer id | Handle | Why block |
| ------- | ------ | --------- |
| `777000` | Telegram service | Login codes, security alerts |
| `93372553` | @BotFather | Bot tokens, settings |
| `178220800` | @SpamBot | Spam/limit appeals |

Human operators still use Telegram clients normally.

**Minimal config shape:**

```yaml
blocked_peers:
  - 777000
  - 93372553
  - "@BotFather"

tokens:
  "<bearer-token>":
    chats: [ ... ]
    read_only: false
```

- **`blocked_peers` omitted:** no sensitive blocking.
- **`blocked_peers: []`:** explicit empty.
- **Non-empty list:** enforced exactly as configured (int, numeric string, `@username` via same normalization as `chats`).
- **Rejected:** `blocked_peers.extend`, per-token blocked lists, runtime MCP mutation.

**Enforcement:** blocked-peer checks run **before** lane ACL for all tokens when the list is non-empty. Post-check on `get_chat_info` / `get_messages` matches resolved **numeric id and username**. Shallow MTProto param scan for numeric ids; invalid non-empty `params_json` → fail-closed when denylist is active.


| Tool / route                            | Behavior                                                                                                                   |
| --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `get_messages`, `get_chat_info`         | Pre-check: input `chat_id`; post-check: resolved `id` / `username` (and message `chat` fallback for `get_messages`)      |
| `send_message`, `edit_message`          | Pre-check: target chat not blocked                                                                                         |
| `find_chats`                            | Post-filter: drop blocked peers from `chats[]`                                                                           |
| `search_messages_globally`              | Post-filter: drop messages whose peer is blocked                                                                         |
| `invoke_mtproto`, `POST /mtproto-api/*` | Blocked-peer param scan **before** listed-token gate; shallow numeric id keys only                                       |


Errors: `Session ACL: blocked peer (<ref>) is denied for this deployment. See SECURITY.md.`

### Environment defaults (Phase 2)


| Variable      | Default       | Notes                                                                             |
| ------------- | ------------- | --------------------------------------------------------------------------------- |
| `ACL_ENABLED` | false         | Opt-in                                                                            |
| `ACL_DEFAULT` | `full_access` | Optional `deny` for strict multi-tenant; **not** recommended default for personal |


## Configuration (current + planned)

Enable with `ACL_ENABLED=true`. Path: `ACL_CONFIG_PATH` or `{session_directory}/acl.yaml`. JSON supported.

```yaml
# Agent guardrails: per-token lane + profile. Unlisted tokens = full_access.
# Optional deployment denylist (shared hosts — see SECURITY.md):
# blocked_peers:
#   - 777000
#   - 93372553

tokens:
  "<bearer-token>":
    chats:
      - me
      - "@workgroup"
      - -1001234567890
    read_only: true
    allow_global_search: true
    # allow_mtproto: false   # Phase 2 — default false when token is listed
```

See [acl.yaml.example](../../acl.yaml.example).

## Enforcement map (MVP + registry target)


| Tool / route                   | Pre-check                               | Post-filter                 |
| ------------------------------ | --------------------------------------- | --------------------------- |
| `get_messages`                 | `chat_id` in lane                       | —                           |
| `get_chat_info`                | `chat_id` in lane                       | —                           |
| `send_message`, `edit_message` | lane + not `read_only`                  | —                           |
| `send_message_to_phone`        | blocked if lane configured              | —                           |
| `find_chats`                   | —                                       | filter `chats[]` to lane    |
| `search_messages_globally`     | `allow_global_search`                   | filter `messages[]` to lane |
| `invoke_mtproto`               | `read_only` / `allow_mtproto` (Phase 2) | —                           |
| `POST /mtproto-api/*`          | same as invoke                          | —                           |


Central **enforcement registry** (Phase 2): one map from tool/route → checks to avoid drift.

Errors: MCP `ok: false` with actionable text; HTTP 403 on MTProto bridge.

## Implementation pointers

- [session_acl.py](../../src/server_components/session_acl.py)
- `enforce_session_acl` in [tools_register.py](../../src/server_components/tools_register.py)
- [server_config.py](../../src/config/server_config.py) — `ACL_ENABLED`, `ACL_CONFIG_PATH`
- [tests/test_session_acl.py](../../tests/test_session_acl.py)
- Operator runbook: [SECURITY.md](../../SECURITY.md) (Phase 1 alignment)

## Phased delivery

### Phase 1 — merge blockers (Trust lane)


| Item                                    | Rationale                                              |
| --------------------------------------- | ------------------------------------------------------ |
| Empty `chats` leak fix                  | Listed token with `chats: []` must deny, not allow all |
| Malformed token handling                | Safe parsing; clear errors                             |
| `read_only` requires `chats` validation | Analyst profile must define a lane                     |
| SECURITY.md operator runbook            | How to enable ACL, profiles, troubleshooting           |
| ADR + brief alignment                   | Agent-guardrails vocabulary in docs                    |


### Phase 1.5 — sensitive peer denylist (Trust lane)


| Item                       | Rationale                                              |
| -------------------------- | ------------------------------------------------------ |
| Operator `blocked_peers` list | Deployment-owned denylist; recommended defaults in example + SECURITY.md |
| Dual pre/post enforcement  | Closes @username ↔ numeric id bypass on chat tools     |
| Tool + MTProto enforcement | Blocked scan before lane gate; shallow MTProto ids     |
| SECURITY.md section        | Shared-host checklist, numeric-id rule, post-check behavior |
| Tests                      | Deny/filter matrix in `test_session_acl.py`            |


**Placement:** After Phase 1 merge blockers, **before** Phase 2 `allow_mtproto` / registry — so raw MTProto cannot read login-code chats while profile flags are still catching up.

### Phase 2 — v1.5 guardrails


| Item                                 | Rationale                                               |
| ------------------------------------ | ------------------------------------------------------- |
| `ACL_DEFAULT` env                    | `full_access` default; optional `deny` for multi-tenant |
| `allow_mtproto`                      | Default false for listed tokens                         |
| `allow_global_search` blocks MTProto | Bot profile cannot bypass via raw MTProto               |
| Enforcement registry                 | DRY tool/route registration                             |
| Config load warnings                 | Unknown keys, empty lane, risky combos                  |


### Phase 3 — roadmap (deferred, lower priority)

- File-watch reload without restart
- External ACL store (e.g. object storage / API)
- Per-chat permission matrix (read/send/edit split)
- **Chat metadata registry** — operator-curated titles, descriptions, tags, and navigation hints for lane chats; complements allowlists so team agents can interpret `find_chats` results (see [Roadmap](../Roadmap.md) Trust lane)

## Suggested default denylist (research)

Document in SECURITY.md at implementation time; verify ids on a live account if disputes arise.


| Peer id             | Handle / role                                            | Why block MCP access                                                                                                                                                                                          |
| ------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **777000**          | Telegram service / login notifications                   | Delivers **login verification codes** and security alerts ([Telegram API auth](https://core.telegram.org/api/auth) — “login notification service user”). Reading via MCP = credential theft on shared tokens. |
| **93372553**        | [@BotFather](https://t.me/BotFather)                     | Create/revoke bots, reveal **bot tokens**, change bot settings — full bot-account takeover.                                                                                                                   |
| **178220800**       | [@SpamBot](https://t.me/spambot)                         | Official spam/limit status and appeals — account state manipulation; common in restriction workflows.                                                                                                         |
| *Research / tier-2* | @VerifyBot, @PremiumBot, @PressBot, “Telegram Tips” bots | Official flows; add after confirming stable numeric ids on production accounts.                                                                                                                               |
| *Research / tier-2* | Human “Telegram Support” in-app chat                     | May not be a stable user id across locales; prefer in-app only.                                                                                                                                               |


**Caveats:**

- **777000** may appear as `from` on some channel/group messages (Telegram backward-compat); enforcement should use the **target chat** of the tool call, not heuristic blocking of unrelated group traffic unless product decision says otherwise.
- Denylist is **numeric id** at enforcement time; `@username` in `extend` follows the same deferred-resolve pattern as `chats`.
- New official security bots should be added via server release + changelog, not silently relying on operators to extend.

## Non-goals

- ACL on stdio or http-no-auth (current)
- Per-token sensitive-peer overrides (allow BotFather for one token) — undermines shared-team model; use separate Telegram account + token instead
- MCP tool to mutate ACL at runtime
- Chat resolution by username at config load (match at enforcement time)
- Replacing Telegram account security or human client access

## Competitor notes (reference only)

[acl-operator-research.md](acl-operator-research.md) documents Prgebish default-deny and others for **mechanism ideas**, not as the product north star.

---

[← ADR 0001](../adr/0001-agent-scoped-session-acl.md) · [Operator research](acl-operator-research.md) · [Roadmap](../Roadmap.md)