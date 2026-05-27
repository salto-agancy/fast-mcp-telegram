# Installation Guide

Get your Telegram MCP server running in minutes!

## Overview

Fast MCP Telegram runs in two modes:

| Mode | Security | Best For | Setup Method |
|------|----------|----------|--------------|
| **Local** (`stdio`) | File-based | Local MCP clients | CLI |
| **Production** (`http-auth`) | Token-based | Remote servers | Web or CLI |

---

## Local Setup (stdio)

**Step 1 — Authenticate**
```bash
uvx --from fast-mcp-telegram fast-mcp-telegram-setup \
  --api-id="your_api_id" \
  --api-hash="your_api_hash" \
  --phone-number="+1234567890"
```

**Step 2 — Configure your MCP client:**

Add to your `mcp.json`:
```json
{
  "mcpServers": {
    "telegram": {
      "command": "uvx",
      "args": ["fast-mcp-telegram"],
      "env": {
        "API_ID": "your_api_id",
        "API_HASH": "your_api_hash"
      }
    }
  }
}
```

**Step 3 — Start using it!**

Configure your MCP client to connect. See [Tools Reference](Tools-Reference.md) for available tools.

## Remote Setup (http-auth)

Deploy on a VDS with Docker Compose and Traefik — SSL is managed centrally, no per-service TLS config needed.

**Step 1 — Get the Docker Compose file**

Option A (clone):
```bash
git clone https://github.com/leshchenko1979/fast-mcp-telegram.git
cd fast-mcp-telegram
```

Option B (download only):
```bash
curl -O https://raw.githubusercontent.com/leshchenko1979/fast-mcp-telegram/main/docker-compose.yml
curl -O https://raw.githubusercontent.com/leshchenko1979/fast-mcp-telegram/main/.env.example
mv .env.example .env
```

**Step 2 — Configure environment**

Edit `.env` with at minimum:
```bash
API_ID=your_api_id
API_HASH=your_api_hash
DOMAIN=your-domain.com
```

**Step 3 — Add Traefik labels**

Edit your `docker-compose.yml` and add these labels to the existing `fast-mcp-telegram` service:

```yaml
services:
  fast-mcp-telegram:
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.fast-mcp-telegram.rule=Host(`your-domain.com`)"
      - "traefik.http.routers.fast-mcp-telegram.entrypoints=websecure"
      - "traefik.http.routers.fast-mcp-telegram.tls.certresolver=le"
```

The service must be on the `traefik-public` network (already configured). Traefik handles SSL via `certResolver: le`.

**Step 4 — Start the server**

```bash
docker compose up -d --pull
docker compose logs -f
```

**Step 5 — Authenticate via web interface**

See [Web Setup Interface](#web-setup-interface) for detailed instructions.

**Step 6 — Connect your MCP client**

**Header auth (standard):**
```json
{
  "mcpServers": {
    "telegram": {
      "url": "https://your-domain.com/v1/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_TOKEN"
      }
    }
  }
}
```

**URL path auth (for clients without header support):**
```json
{
  "mcpServers": {
    "telegram": {
      "url": "https://your-domain.com/v1/url_auth/YOUR_TOKEN/mcp"
    }
  }
}
```

**Health check:** `curl https://your-domain.com/health`

### HTTP auth: two deployment patterns

Remote `http-auth` supports two different setups. Do not confuse them when reading docs or choosing env vars.

| Pattern | Who | MCP wiring | `PREFIX_MCP_TOOLS_WITH_ACCOUNT` |
|---------|-----|------------|--------------------------------|
| **Multi-user server** | Many users on one hosted server | Each user: **one** MCP connection, **one** Bearer token → **one** Telegram account | **Off** (default). Each connection already sees only that account's tools. |
| **One agent, multiple accounts** | One operator / one agent session | **Several** MCP entries to the **same** server URL, **different** tokens (see [below](#multi-account-mcp-tool-prefix)) | **On** when the client merges tool lists and names collide (`send_message` × N). |

**Multi-user server** is the usual production model: authenticate at [web setup](#web-setup-interface), use your token in a single MCP client entry. **Do not enable** `PREFIX_MCP_TOOLS_WITH_ACCOUNT` for that case.

**One agent, multiple accounts** is for a single agent that must talk to several Telegram identities via one server in one session — enable the prefix only then.

---

## One agent, multiple accounts (tool name prefix)

This section applies only to **one agent, multiple accounts** (second row above), not to ordinary multi-user hosting.

When one agent's MCP config lists **multiple connections** to the same server URL (each with its own Bearer token from [web setup](#web-setup-interface)), tool names would otherwise be identical across connections (`send_message`, `find_chats`, …). Enable per-session prefixes so each connection exposes distinct names (e.g. `alice_send_message` vs `bob_send_message`).

**Enable in `.env` or docker-compose:**

```bash
PREFIX_MCP_TOOLS_WITH_ACCOUNT=true
```

**How it works:**

1. In **one agent's** MCP config, add multiple server entries pointing at the same URL — one entry per Telegram account, each with its own Bearer token.
2. On `tools/list`, the server prefixes each tool name with that connection's account label.
3. Prefix label: Telegram **@username** when set, otherwise **numeric user ID** (e.g. `123456789_send_message`). Setting a @username is recommended for readable tool names in the agent.
4. On `call_tool`, use the prefixed name that matches the connection's token.

**Trade-off:** The agent may see `N × num_tools` entries (typically manageable for 2–3 accounts). Default is off — single-connection and multi-user-server deployments are unchanged.

---

## Web Setup Interface

The web setup interface manages Telegram sessions directly from your browser. Access it at `https://your-domain.com/setup` when running in `http-auth` mode.

### Create or Manage Sessions

**Create New Session:** Click **"Create New Session"**, enter your phone number (with country code, e.g., `+1234567890`), then enter the verification code Telegram sends. If 2FA is enabled, enter your password. Download the generated `mcp.json` and use it in your MCP client.

**Reauthorize Existing Session:** Click **"Reauthorize Existing Session"**, enter your bearer token, confirm your phone number, then enter the verification code. If 2FA is enabled, enter your password. Your session refreshes with the same token.

**Delete Session:** Click **"Delete Session"**, enter your bearer token, then confirm deletion (cannot be undone).

---

## Session ACL (http-auth)

Opt-in **agent guardrails** for shared `http-auth` hosts: restrict specific Bearer tokens to chat lanes and capability profiles (analyst read-only, team lane, bot channel-only). Humans keep using Telegram in official clients; ACL limits what **connected agents** can do via MCP on this server.

**Scope:** `ACL_ENABLED=true` applies only in **`http-auth`** mode. Stdio and `http-no-auth` are unchanged.

**Enable:**

1. Set `ACL_ENABLED=true` (and optionally `ACL_CONFIG_PATH`) in the server environment.
2. Create the ACL file — default `{session_directory}/acl.yaml`, or the path from `ACL_CONFIG_PATH`. Start from [acl.yaml.example](../acl.yaml.example).
3. List **only** Bearer tokens you want to restrict. **Unlisted tokens keep full tool access** unless `ACL_DENY_UNLISTED_TOKENS=true`.
4. Restart or redeploy. Startup **fails closed** if ACL is enabled but the file is missing or invalid (`read_only` requires a non-empty `chats` list).

**Lane rules (summary):**

| Setting | Effect |
| --- | --- |
| `chats` | Allowlist of chat ids, `@username`, or `me` for this token |
| Empty or omitted `chats` on a **listed** token | **Hard deny** all chat-scoped operations (not an empty result list) |
| `read_only: true` | Blocks send, edit, `invoke_mtproto`, and the HTTP MTProto bridge |
| `allow_global_search: false` | Blocks `search_messages_globally` and raw MTProto |
| `allow_mtproto: false` | Default for listed tokens; blocks `invoke_mtproto` and `/mtproto-api/*` unless explicitly `true` with `read_only: false` and `allow_global_search: true` |
| `ACL_DENY_UNLISTED_TOKENS=true` | Bearer tokens not in `tokens:` get synthetic empty-lane deny |

**Operator runbook:** [SECURITY.md](../SECURITY.md#opt-in-session-acl-http-auth) · **Design:** [ADR 0001](adr/0001-agent-scoped-session-acl.md) · **Local testing:** [CONTRIBUTING.md](../CONTRIBUTING.md#acl-development-and-testing-not-via-cursor-mcp)

---

## Configuration Reference

### Environment Variables

```bash
# Required
API_ID=your_api_id
API_HASH=your_api_hash

# Optional
SERVER_MODE=http-auth             # stdio (default) or http-auth for remote
PORT=8000                          # Server port (http-auth mode)
LOG_LEVEL=INFO                    # Logging verbosity
SESSION_NAME=telegram             # Session file name (stdio mode only)
SESSION_DIR=~/.config/fast-mcp-telegram  # Custom session directory
MTPROTO_PROXY=tg://proxy?server=your-proxy.com&port=443&secret=your-secret  # Firewall proxy

# Session ACL (http-auth only) — see #session-acl-http-auth
ACL_ENABLED=false                  # Opt-in per-token agent guardrails
ACL_CONFIG_PATH=                   # Override default {session_directory}/acl.yaml
ACL_DENY_UNLISTED_TOKENS=false     # Deny Bearer tokens omitted from tokens: map
```

**Tip:** The CLI setup automatically loads `.env` files from your current directory.

### MTProto Proxy

For connections behind a firewall. Supported formats:
- `tg://proxy?server=&port=&secret=` (URL)
- `host:port:secret` (simple)
- `ee` or `7` prefix for fake TLS (auto-detected)

> **Note:** Fake TLS (EE prefix) support requires the `TelethonFakeTLS` package: `pip install TelethonFakeTLS`. Without it, Fake TLS proxies fall back to standard TCP.

### Multiple Accounts

Use different Telegram accounts for personal, work, or testing:

```bash
# Create sessions for different accounts
SESSION_NAME=personal fast-mcp-telegram-setup \
  --api-id="xxx" --api-hash="yyy" --phone-number="+111"

SESSION_NAME=work fast-mcp-telegram-setup \
  --api-id="xxx" --api-hash="yyy" --phone-number="+222"
```

**Configure in MCP client:**
```json
{
  "mcpServers": {
    "telegram-personal": {
      "command": "uvx",
      "args": ["fast-mcp-telegram"],
      "env": {
        "API_ID": "your_api_id",
        "API_HASH": "your_api_hash",
        "SESSION_NAME": "personal"
      }
    },
    "telegram-work": {
      "command": "uvx",
      "args": ["fast-mcp-telegram"],
      "env": {
        "API_ID": "your_api_id",
        "API_HASH": "your_api_hash",
        "SESSION_NAME": "work"
      }
    }
  }
}
```

---

## More Resources

- **[Tools Reference](docs/Tools-Reference.md)** - Available MCP tools and usage
- **[SECURITY.md](../SECURITY.md)** - Security best practices
- **[CONTRIBUTING.md](../CONTRIBUTING.md)** - Development setup for contributors

---

**Need help?** Open an [issue](https://github.com/leshchenko1979/fast-mcp-telegram/issues) on GitHub!