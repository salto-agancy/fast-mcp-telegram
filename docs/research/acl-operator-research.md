# Session ACL: competitor audit and operator personas

Research date: 2026-05-26. Sources: public docs and READMEs for Prgebish/mcp-telegram, chigwell/telegram-mcp, kfastov/telegram-mcp-server.

## Competitor models

### Prgebish/mcp-telegram (Go)

| Aspect | Model |
| --- | --- |
| Default policy | **Default-deny** — no chat accessible unless whitelisted in `acl.chats` |
| Config | Single YAML file with env expansion |
| Match keys | `@username`, `user:ID`, `channel:ID`, phone |
| Permissions | Per-chat: `read`, `send`, `draft`, `mark_read` |
| Tool gating | `tg_history`/`tg_search` need `read`; `tg_send` needs `send` |
| Dialog listing | `tg_dialogs` returns **only** ACL-visible chats |
| Filesystem | `allowed_upload_dirs`, `media.directory` boundaries |
| Rate limiting | Built-in token bucket middleware |
| Multi-user | Single-user process (one config file) |

**Takeaway:** Strongest ACL story; chat + operation matrix; default-deny is safe but breaks backward compatibility for open demos.

### chigwell/telegram-mcp (Python / Telethon)

| Aspect | Model |
| --- | --- |
| Access control | **No per-chat ACL** — full account access |
| File paths | MCP Roots / CLI allowlist for file-path tools (deny-all until roots configured) |
| Tool count | 60+ narrow tools (high context cost) |
| Multi-user | Local stdio / single instance |

**Takeaway:** Security focus is filesystem roots, not Telegram chat scope. Not a direct ACL competitor for http-auth hosting.

### kfastov/telegram-mcp-server (Node / MtCute)

| Aspect | Model |
| --- | --- |
| Access control | **No formal ACL** |
| Persistence | SQLite `data/messages.db` + background sync jobs |
| Sync tools | `scheduleMessageSync`, `listMessageSyncJobs` |
| Transport | Streamable HTTP `/mcp` |
| Multi-user | Single-user local deployment |

**Takeaway:** Performance via offline archive, not tenant scoping. Relevant for Phase 2 SQLite backlog.

### fast-mcp-telegram (this project, before ACL MVP)

| Aspect | Model |
| --- | --- |
| Multi-user | Per-token `.session` files + Bearer auth on http-auth |
| Scope | **Full account** per valid token |
| Files (HTTP) | Local paths blocked; URL SSRF checks |
| Files (stdio) | Local paths allowed (no sandbox) |

---

## Persona → competitor fit

| Persona | Need | Best reference |
| --- | --- | --- |
| **personal_demo** | Full account try-out on public host | fast-mcp-telegram today (no ACL entry) |
| **team_shared** | Work chats only per token | Prgebish chat whitelist |
| **readonly_analyst** | Read/search, no send/edit/mtproto | Prgebish `read` without `send` |
| **automation_bot** | Send to specific channels; no global exfil | Chat whitelist + block global search |

---

## Recommended direction for fast-mcp-telegram

1. **Opt-in ACL** (not Prgebish default-deny globally) — tokens without config keep full access for tg-mcp demo compatibility.
2. **Per-token rules** keyed by Bearer token string in server-side file (not agent-configurable).
3. **Dimensions for v1:** `chats`, `read_only`, `allow_global_search` — covers all four personas without a full permission matrix yet.
4. **Defer** Prgebish-style `draft`/`mark_read` granularity and filesystem ACL until stdio sandbox track.

See [acl-design-brief.md](acl-design-brief.md) for schema and enforcement map.

---

[← Strategic index](../Strategic-Market-Positioning.md) · [Design brief →](acl-design-brief.md)
