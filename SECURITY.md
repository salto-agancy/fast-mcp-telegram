# 🔒 Security & Authentication

## Bearer Token Authentication System

- **Per-Session Authentication**: Each session requires a unique Bearer token
- **Session Isolation**: Each token creates an isolated Telegram session
- **Token Generation**: Cryptographically secure 256-bit tokens via setup script
- **Reserved Name Protection**: Common session names blocked to prevent conflicts
- **HTTP Authentication**: Mandatory Bearer tokens for HTTP transport (`Authorization: Bearer <token>`)
- **Development Mode**: `DISABLE_AUTH=true` bypasses authentication for development

### Authentication Methods

**Header-Based Auth (Recommended):**
- Token sent in `Authorization: Bearer <token>` header
- Token does not appear in URL or server access logs
- Use this for production environments

**URL-Based Auth (For Limited Clients):**
- Token included in URL path: `/v1/url_auth/{token}/mcp/...`
- Token appears in URLs and server access logs
- Use only when header-based auth is not possible
- Same token validation applies (reserved names blocked)

## Multi-User Security Model

- **Session Separation**: Each user gets their own authenticated session file
- **Token Privacy**: Bearer tokens should be treated as passwords and kept secure
- **Session Files**: Contain complete Telegram access for the associated token
- **Account Access**: Anyone with a valid Bearer token can perform **ANY action** on that associated Telegram account **unless** opt-in session ACL applies to that token

## Opt-in session ACL (http-auth)

When **`ACL_ENABLED=true`**, operators can restrict **specific Bearer tokens** via a server-side config file (default `{session_directory}/acl.yaml`, override with **`ACL_CONFIG_PATH`**). See **`acl.yaml.example`**, [ADR 0001](docs/adr/0001-agent-scoped-session-acl.md), and [acl-design-brief.md](docs/research/acl-design-brief.md).

**Framing:** ACL is **agent guardrails** (workspace lanes + capability profiles), not account lockdown. Humans continue using Telegram in official clients; guardrails limit what **connected agents** can do via MCP tools on this server.

### Enablement

1. Set `ACL_ENABLED=true` in the server environment (http-auth only; not stdio or http-no-auth).
2. Create the ACL file at the default path or set `ACL_CONFIG_PATH`.
3. Restart the server (or redeploy). Startup **fails closed** if ACL is enabled but the file is missing or invalid.
4. List only the Bearer tokens you want to restrict. **Unlisted tokens keep full tool access** unless `ACL_DENY_UNLISTED_TOKENS=true` (synthetic empty lane for every Bearer not listed under `tokens:`).

Treat the ACL file like a secret (contains bearer token strings). Restrict file permissions (e.g. `0600`).

### Agent profiles (operator vocabulary)

| Profile | Typical use | `chats` | `read_only` | `allow_global_search` | `allow_mtproto` |
| --- | --- | --- | --- | --- | --- |
| **full_access** *(default, unlisted token)* | Personal / demo | — | false | true | true *(unlisted)* |
| **analyst** | Read-only lane | non-empty list | true | true | false |
| **team_lane** | Work chats, send allowed | non-empty list | false | true | false |
| **bot** | Channel automation | channel ids | false | false | false |
| **power** *(optional)* | Advanced automation | non-empty list | false | true | true |

`read_only: true` **requires** a non-empty `chats` list (startup validation rejects analyst entries without a lane).

### Lane rules

- **`chats`**: allowlist of chat ids, `@username`, or `me` (Saved Messages) for this token’s workspace lane.
- **Listed token with empty `chats`** (`chats: []` or `chats` omitted): **deny all chat-scoped operations** — hard deny on `find_chats` and `search_messages_globally` (not an empty result list), block reads/writes to any chat, block `send_message_to_phone`.
- **`read_only`**: blocks send, edit, `invoke_mtproto`, and the HTTP MTProto bridge.
- **`allow_global_search`**: when false, blocks `search_messages_globally` pre-check and raw MTProto.
- **`allow_mtproto`**: when false (default for listed tokens), blocks `invoke_mtproto` and the HTTP MTProto bridge. Requires `read_only: false` and `allow_global_search: true` to allow raw MTProto.
- **`ACL_DENY_UNLISTED_TOKENS`**: when true, Bearer tokens omitted from `tokens:` are denied with an explicit unlisted-token message (not the generic empty-lane text). Default false.
- **Unknown token keys**: keys not in the ACL schema log a warning at load; operator metadata may use an `x_` prefix (for example `x_note`) to avoid warnings.

### Sensitive peer denylist (`blocked_peers`, Phase 1.5)

When **`blocked_peers`** is configured (non-empty list in the ACL file), those peers are **denied for every Bearer token** — listed and unlisted. Deny wins over a token’s `chats` lane.

| Config | Behavior |
| --- | --- |
| **`blocked_peers` omitted** | No sensitive blocking; lane ACL only |
| **`blocked_peers: []`** | Explicit empty — no sensitive blocking |
| **Non-empty list** | Enforced exactly as configured (operators own the full list) |

**Recommended defaults** (copy from [`acl.yaml.example`](acl.yaml.example) for shared hosts):

| Peer id | Handle | Why block |
| --- | --- | --- |
| `777000` | Telegram service | Login codes, security alerts |
| `93372553` | @BotFather | Bot tokens, settings |
| `178220800` | @SpamBot | Spam/limit appeals |

**Numeric ids in YAML** give the best coverage (including shallow MTProto param scans). Optional `@username` entries help **pre-check** raw tool input. **Post-check** on `get_chat_info` and `get_messages` matches **both** resolved numeric `id` and `username` from the tool result — so numeric-only YAML still blocks `@BotFather` input after Telegram resolves the peer, and `@username` YAML still blocks numeric input after resolution.

**Pre-check limitation:** raw tool input is matched without entity lookup. Username-only YAML does not block numeric input until post-check (if the tool succeeds).

**MTProto:** shallow scan of merged `params` / `params_json` for numeric blocked ids only (no TL schema walker). Invalid non-empty `params_json` when `blocked_peers` is configured → fail-closed deny. Residual risk: deeply nested or list params — document and accept for Phase 1.5.

**List post-filter:** `find_chats` and `search_messages_globally` drop blocked peers from results; an empty list after filtering is success (not an error).

**Error:** `Session ACL: blocked peer (<ref>) is denied for this deployment. See SECURITY.md.` (MCP `error_code` `-32007`).

**Shared-host footgun:** `ACL_ENABLED=true` without `blocked_peers` leaves BotFather and login-code chats reachable via MCP — use the checklist above and uncomment the example block.

### What is blocked for listed tokens

| Surface | Behavior |
| --- | --- |
| MCP tools (`get_messages`, `send_message`, …) | Pre-check `chat_id` against lane; post-filter list results |
| `find_chats` / `search_messages_globally` | Post-filter to lane; **empty lane → hard deny** |
| `invoke_mtproto` | Blocked unless `allow_mtproto: true`, `allow_global_search: true`, and not `read_only` |
| HTTP MTProto bridge (`/mtproto-api/*`) | Same capability gates as `invoke_mtproto` |

Denials return MCP `ok: false` with actionable text (token, chat id, lane). HTTP bridge returns 403.

### Shared-token limits

ACL lanes reduce **accidental** cross-chat access and **in-server** tool abuse when teammates share one Bearer token on an http-auth host. ACL does **not**:

- Stop someone with the raw Bearer token from calling Telegram outside this MCP server
- Replace Telegram account security (2FA, session revocation in official clients)
- Apply to stdio or http-no-auth deployments

When **`blocked_peers`** is configured, the denylist applies on http-auth for **all** tokens (see above).

### Troubleshooting denials

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| “empty chat lane” | Token listed with `chats: []` or no `chats` key | Add at least one chat to the token entry |
| “not in the allowed list” | Tool targets a chat outside the lane | Add chat id / `@username` to `chats` or use an unlisted token |
| “read-only” | `read_only: true` on the token | Set `read_only: false` or use a write-capable profile |
| “global message search” | `allow_global_search: false` | Set `allow_global_search: true` or use `get_messages` in-lane |
| “allow_mtproto is false” on MTProto | Listed token has not opted in to raw MTProto | Set `allow_mtproto: true` (and ensure `read_only: false`, `allow_global_search: true`) |
| “allow_global_search is false” on MTProto | Bot-style profile blocks raw MTProto | Set `allow_global_search: true` or use in-lane tools |
| Unlisted token denied everywhere | `ACL_DENY_UNLISTED_TOKENS=true` | Add token to `tokens:` with a lane, or set `ACL_DENY_UNLISTED_TOKENS=false` |
| “blocked peer … denied for this deployment” | Peer is on `blocked_peers` denylist | Remove peer from tool args; adjust denylist if policy allows; see numeric-id guidance above |
| “invalid params_json” with blocked_peers | Malformed JSON when denylist is active | Fix JSON or omit `params_json` |
| Server refuses to start | Missing ACL file or invalid YAML | Create file; fix malformed token entries; ensure `read_only` has `chats`; fix `blocked_peers` list shape |

No MCP tool mutates ACL in v1 — edit the file on the server and restart.

**Development and testing:** ACL behavior is validated with **pytest** and a local **http-auth** server exercised via **curl** (Bearer header per profile), not via Cursor MCP — stdio mode has no ACL, and URL MCP entries bind a single fixed token. See [CONTRIBUTING.md — ACL development and testing](CONTRIBUTING.md#acl-development-and-testing-not-via-cursor-mcp).

## Production Security Recommendations

1. **Secure Token Distribution**: Distribute Bearer tokens through secure channels only
2. **Token Rotation**: Regularly generate new tokens and invalidate old ones
3. **Access Monitoring**: Monitor session activity through `/health` HTTP endpoint
4. **Network Security**: Use HTTPS/TLS and consider IP restrictions
5. **Session Management**: Regularly clean up unused sessions and tokens

## Attachment download URLs (HTTP)

When **`DOMAIN`** is set to a non-placeholder public host and the server runs over **HTTP**, tools that return formatted messages may include **`media.attachment_download_url`** pointing to **`GET /v1/attachments/{uuid}`** (same public origin as web setup / MCP URL, derived from **`DOMAIN`**).

- **No Bearer token on download**: The UUID in the path is a **secret capability**. Anyone who obtains the URL can download the associated media until the ticket expires.
- **TTL**: Controlled by **`ATTACHMENT_TICKET_TTL_SECONDS`** (see `.env.example`). Use the shortest TTL that fits your workflow.
- **Single process**: Tickets are stored **in memory**. A process restart invalidates all tickets. Multiple workers or replicas without shared storage will not see each other’s tickets.
- **Session binding**: The server uses the Telegram session that minted the ticket to stream bytes; the HTTP client does not send session credentials.
- **Mitigations**: Use HTTPS at the edge, keep TTL low, avoid pasting links into public logs or chats, and treat leaked URLs like leaked file access.

## File Security

### SSRF Protection
- **URL Security Validation**: Blocks localhost, private IPs, and suspicious domains
- **Enhanced HTTP Client**: Disabled redirects, connection limits, security headers, and timeouts
- **File Size Limits**: Configurable maximum file size with both header and content validation
- **Configuration Options**: `allow_http_urls`, `max_file_size_mb`, `block_private_ips` settings

### Local File Access
- **Local paths**: Only allowed in stdio mode for security
- **URL downloads**: Supported in all modes with SSRF protection
- **Size validation**: Both header and content validation for downloaded files

## Token Validation Security

- **Format**: HTTP_AUTH bearer tokens must be 43-character URL-safe base64 strings (same as setup/CLI generation: `[A-Za-z0-9_-]{43}`). Tokens containing `/`, `\`, or path segments are rejected.
- **Path containment**: Session files are resolved as `{session_directory}/{token}.session` and must stay under `session_directory` after `resolve()`.
- **Reserved Name Blocking**: Prevents common session names from being used as bearer tokens
- **Blocked Names**: `telegram`, `default`, `session`, `bot`, `user`, `main`, `primary`, `test`, `dev`, `prod`
- **Case Insensitive**: Validation ignores case differences
- **Session Conflict Prevention**: Blocks tokens that could create file conflicts with STDIO/HTTP_NO_AUTH sessions
- **Logging**: Rejected tokens are logged with warning messages for security monitoring

## Session File Security

- **Location**: `~/.config/fast-mcp-telegram/` for cross-platform compatibility
- **Format**: `{token}.session` for multi-user isolation
- **Git Security**: Session files excluded from version control
- **Permissions**: Automatic permission fixing for container user access (1000:1000)
- **Backup/Restore**: Sessions automatically backed up and restored across deployments

## Development Security

- **Environment Variables**: Never commit `.env` files with real credentials
- **Session Files**: Excluded from git via `.gitignore`
- **Authentication Bypass**: Use `DISABLE_AUTH=true` only in development environments
- **Token Management**: Use temporary tokens for testing, not production tokens
