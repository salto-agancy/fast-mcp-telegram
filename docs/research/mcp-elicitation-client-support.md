# MCP Elicitation Protocol — Client Support Research

Research on which popular MCP clients support the elicitation protocol
(form-mode and URL-mode), conducted 2026-06-09.

## Protocol Overview

The MCP elicitation protocol (modelcontextprotocol.io) defines two modes:

- **Form mode** — server sends a JSON schema + message, client renders a form
  to the user, user fills it in, server receives the result.
- **URL mode** — server sends a URL, client opens it for out-of-band
  interaction (OAuth flows, credential collection, QR codes). Defined in
  SEP-1036. Can be triggered by the server returning a
  `URLElicitationRequiredError` (code -32042).

Clients declare support via `elicitation.form` and/or `elicitation.url` in
their `ClientCapabilities` during the MCP `initialize` handshake.

## Client Support Matrix

| Client | Elicitation Support | Notes |
|---|---|---|
| Cursor 2.0+ | ✅ Full | Both form and URL mode |
| VS Code Insiders / Stable | ✅ Native | Command Palette-style UI for form elicitation |
| Claude Code CLI | ✅ | Supports `elicitation/create` |
| Amazon Bedrock AgentCore Gateway | ✅ All 3 modes | Form, URL request-based, URL exception-based. Uses `MultiServerMCPClient` with `on_elicitation` callback |
| mcp-use (mcp-use/mcp-use) | ✅ | Dedicated `elicitation_callback`, documented |
| MCP Inspector | ✅ | Since v0.16.2 |
| **Claude Desktop** | **❌** | GitHub issue #41110 — elicitation is a feature request, only available in CLI |
| **Cline** | **❌** | Discussion #4522, no implementation; their MCP docs don't mention elicitation |
| **Continue.dev** | **❌** | Client initialized with `capabilities: {}`, no elicitation handler registered anywhere |

## Key Takeaways

- **Most popular agent clients (Cline, Continue, Claude Desktop) do NOT support
  form-mode elicitation.** An auth path relying on form elicitation would be
  inaccessible to the majority of users.
- **URL-mode elicitation** (`URLElicitationRequiredError`) is a broader
  fallback — it doesn't require the client to declare `elicitation.url` in
  capabilities. The server returns a standard error with a URL, and the client
  is expected to display/open it.
- **Servers should implement a fallback path** when elicitation is not
  available (per AWS Bedrock documentation: if the client doesn't declare
  elicitation capability, the server should not send elicitation requests).

## Sources

- MCP Specification: https://spec.modelcontextprotocol.io/specification/2025-03-26/basic/elicitation-considerations/
- GitHub issue #41110 (Claude Desktop): https://github.com/orgs/modelcontextprotocol/discussions/41110
- Cline discussion #4522: https://github.com/cline/cline/discussions/4522
- AWS Bedrock AgentCore: https://docs.aws.amazon.com/bedrock/latest/userguide/agent-mcp.html
- VS Code blog: Stop Guessing — MCP Elicitations Come To Visual Studio Code
