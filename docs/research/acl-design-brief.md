# Session ACL design brief (v1)

Status: **Approved for implementation** (2026-05-26). Implements opt-in static ACL for `http-auth` only.

## Goals

- Limit blast radius for shared http-auth hosting without breaking tokens that have no ACL entry.
- Support four operator personas from [acl-operator-research.md](acl-operator-research.md).
- No MCP tool to change ACL in v1 (server-side file only).

## Non-goals (v1)

- Default-deny for all tokens
- Dynamic/runtime ACL updates via agents
- Per-operation matrix beyond `read_only` + `allow_global_search`
- ACL on stdio or http-no-auth modes
- Chat resolution by username at config load (match normalized ids/usernames at enforcement time)

## Configuration

Enable with `ACL_ENABLED=true`. Optional path override: `ACL_CONFIG_PATH` (default `{session_directory}/acl.yaml`). JSON (`.json`) also supported.

### Schema

```yaml
# Tokens omitted from this file retain full account access when ACL is enabled.
tokens:
  "<bearer-token>":
    chats:
      - me                    # Saved Messages
      - "@workgroup"          # username without requiring live resolve at load
      - -1001234567890        # numeric chat/channel id
    read_only: false         # default false
    allow_global_search: true  # default true
```

### Persona presets (operator examples)

| Persona | Example rule |
| --- | --- |
| personal_demo | *(omit token from file)* |
| team_shared | `chats: [work ids…]`, `read_only: false`, `allow_global_search: true` |
| readonly_analyst | `chats: [...]`, `read_only: true`, `allow_global_search: true` |
| automation_bot | `chats: [channel ids…]`, `read_only: false`, `allow_global_search: false` |

When `chats` is empty for a listed token, all chat-scoped operations are denied.

## Enforcement map

| Tool / route | Pre-check | Post-filter |
| --- | --- | --- |
| `get_messages` | `chat_id` in whitelist | — |
| `get_chat_info` | `chat_id` in whitelist | — |
| `send_message`, `edit_message` | whitelist + not `read_only` | — |
| `send_message_to_phone` | blocked if whitelist configured | — |
| `find_chats` | — | filter `chats[]` to whitelist |
| `search_messages_globally` | `allow_global_search` | filter `messages[]` to whitelist |
| `invoke_mtproto` | blocked when `read_only` | — |
| `POST /mtproto-api/*` | blocked when `read_only` | — |

Errors use MCP-style `ok: false` with HTTP 403 on MTProto bridge.

## Implementation

- Module: [src/server_components/session_acl.py](../../src/server_components/session_acl.py)
- Decorator: `enforce_session_acl` in [tools_register.py](../../src/server_components/tools_register.py)
- Config: [server_config.py](../../src/config/server_config.py) — `ACL_ENABLED`, `ACL_CONFIG_PATH`
- Example: [acl.yaml.example](../../acl.yaml.example)
- Tests: [tests/test_session_acl.py](../../tests/test_session_acl.py)

## Future (v2 candidates)

- Permission matrix: `read`, `send`, `edit` per chat (Prgebish parity)
- Admin HTTP API to reload ACL without restart
- Username→id resolution at config load
- ACL-aware SQLite cache (only sync whitelisted chats)

---

[← Operator research](acl-operator-research.md) · [Roadmap](../Roadmap.md)
