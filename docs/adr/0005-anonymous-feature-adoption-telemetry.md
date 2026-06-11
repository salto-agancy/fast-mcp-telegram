# ADR 0005: Anonymous Feature-Adoption Telemetry

**Status:** proposed
**Date:** 2026-06-10

## Context

### Problem

fast-mcp-telegram is a Python library installed by users via `pip install` on their own machines. The maintainer has no access to those machines — no SSH, no Docker daemon, no log aggregation. There is zero visibility into how users run the software.

The project ships a growing surface of optional features — ACL, rate limiting, OIDC auth, QR login, Gemini integration, session persistence, MTProto proxy — but decisions about further investment are made blind. Without telemetry every priority call is guesswork: should we invest in ACL v2, rate-limiting improvements, Gemini multi-modal, or something else?

ACL is the concrete question that triggered this ADR — the maintainer needs to know adoption depth to plan ACL v2 — but the underlying data gap spans **all** features. The maintainer needs signals across three axes:
- **Adoption** — which features are users actually enabling?
- **Speed** — how responsive are their installations?
- **Errors** — what is breaking in the field?

Previous discussions (2026-05-26) established a telemetry lane in the roadmap, deferred until Trust/ACL phases shipped. As of v0.30.1 ACL is accepted and in use; the maintainer now needs real data to decide the next priority lane.

### Goals

1. **Feature signals across three axes** — collect data about every optional feature along three dimensions: (a) adoption — which features are enabled (primary v1 deliverable); (b) speed — how responsive the installation feels (per-tool breakdown deferred to v2); (c) errors — aggregate error and flood-wait rates. ACL is the immediate decision driver, but the system must be extensible to all features without per-feature releases.
2. **Aggregate health signals** — error counts and flood-wait rates (per-tool breakdown deferred to v2).
3. **Data-driven roadmap** — replace gut-feel prioritisation with actual usage numbers.
4. **Privacy-by-design** — collect nothing that could identify a user, a chat, a bot token, or a message. The payload must be inspectable (debug mode) and the code must be auditable.

### Constraints

- **No external infrastructure** — the collection backend runs on the maintainer's VDS, under the same domain as the demo instance (`tg-mcp.l1979.ru`).
- **Opt-out by default** — telemetry is on after install; the user must disable it via `DO_NOT_TRACK=1`.
- **Zero interference** — telemetry must never block server startup, tool execution, or graceful shutdown. Network errors are silently ignored.
- **No new dependencies** — the telemetry client uses only the Python stdlib (`http.client`, `json`, `uuid`, `os`). The collector requires only `psycopg2-binary`.
- **Stable URL** — the heartbeat endpoint is baked into each release; it must 301-forward if infrastructure moves, so old clients keep working.
- **Snapshot, not stream** — each heartbeat is a self-contained snapshot of the current process state. No streaming, no event queue, no delta protocol.

### Scope (v1 vs roadmap)

This ADR defines a **deliberate v1 scope**: feature-adoption flags and aggregate counters only. The full telemetry vision per the [Roadmap](../Roadmap.md) (per-tool error rates, P95 latency, wrong-tool pattern detection, ACL denial tracking) is deferred to future ADRs. The v1 architecture explicitly keeps the collector interface simple so that richer payloads can be added without changing the storage or processing pipeline.

### Telemetry Axes — Full Landscape

The telemetry system collects signals along three primary axes. The table below shows coverage per axis in v1, plans for future iterations, and what is permanently excluded.

| Axis | What it measures | Status | Rationale |
|------|-----------------|--------|-----------|
| **Adoption** | Feature flags enabled/disabled and configuration depth (principal count, read-only peers) | ✅ **v1** | Core question: which features do users enable |
| **Errors** | Aggregate error count, flood-wait occurrences | ✅ **v1** (aggregate) / 🔄 v2 (per-tool breakdown) | Health signal without per-tool instrumentation in v1 |
| **Speed** | P95 tool latency, response-time distribution | 🔄 **v2** | Requires per-tool timing instrumentation |
| **Usage frequency** | Tool-call volume per feature (e.g. how many calls pass through ACL vs not) | 🔄 **v2** | Depends on per-tool counters from speed/error v2 work |
| **Session health** | Active sessions, cleanup efficiency | 🟡 **v1** (basic): `runtime.sessions` | Basic coverage; session-duration metrics in v2 |
| **Configuration depth** | Beyond on/off: how deeply features are customised | 🟡 **v1 (partial)**: `acl_principals`, `acl_read_only` ✅; per-feature detail beyond ACL 🔄 v2 | ACL depth in v1; other features analysed server-side from flat feature map |
| **Version drift** | Which software versions users run | ✅ **v1**: `ver` in every heartbeat | Trivial to include |
| **Transport/environment** | stdio vs HTTP, OS, Python version | ✅ **v1**: `server_mode`, `os`, `py` | Environmental context for every heartbeat |
| **Auth distribution** | Bot-token auth vs MTProto proxy vs no auth | ✅ **v1**: `bot_api_token`, `mtproto_proxy` | Covers the main authentication patterns |
| **Feature interaction** | Which features are used together (e.g. ACL + rate limiting + proxy) | 🟡 **v1 (derivable)**: flat feature map in payload; server-side SQL/analysis extracts correlations | No client change needed — combinable from existing data |

**Permanently excluded (never collected):**
- Geographic location, IP geolocation
- User identity, chat IDs, message content
- Event-level traces (entire request/response)
- Revenue, business, or engagement metrics beyond tool-call counts
- Any value derivable from `api_id`, `api_hash`, or bot-token contents

## Decision

### Collection model

A **tiny collector container** deployed alongside the existing Traefik on the app host (`tg-mcp.l1979.ru`, Box 3). The collector receives POST requests and writes each payload as a JSONB row to PostgreSQL.

```
fast-mcp-telegram
  ─POST→ fast-mcp-telegram-telemetry.l1979.ru/v1/event
            ─Traefik router (hot-reload, no restart)─→
              collector:8001
                ─psycopg2 INSERT→
                  postgres (database "telemetry", table "telemetry_events")
```

### Payload format

```json
{
  "v": 1,
  "iid": "a1b2c3d4-…",                         // random UUID, generated once per install
  "ts": 1718030000,                              // Unix timestamp — when this heartbeat was sent
  "started_at": 1718038000,                      // Unix timestamp — when this server process booted
  "ver": "0.30.1",
  "os": "Linux x86_64",
  "py": "3.12",
  "features": {
    "server_mode": "http-auth",                  // stdio | http-no-auth | http-auth
    "acl_enabled": true,
    "acl_deny_unlisted_principals": false,
    "acl_principals": 3,                         // number of principals in ACL config
    "acl_read_only": 1,                          // principals with read_only: true
    "bot_api_token": false,                      // bot token is configured (boolean, not the value)
    "mtproto_proxy": false,
    "prefix_mcp_tools_with_account": false,
    "max_active_sessions": 10,
    "inactive_session_days": 30,
    "block_private_ips": true,
    "allow_http_urls": false,
    "shutdown": false                                          // true on the final heartbeat before process exit
  },
  "runtime": {
    "sessions": 4,
    "session_files": 7,
    "setup_sessions": 1
  },
  "counters": {
    "total_calls": 142,                          // lifetime-of-process tool invocations
    "errors": 0,                                 // lifetime-of-process tool errors
    "flood_waits": 0                             // lifetime-of-process FloodWait occurrences
  }
}
```

Every field is documented in the consumer-facing README under a "Telemetry" section.

### Payload versioning

The top-level `v` field identifies the schema version (`Literal[1]` in v1). The collector rejects unknown `v` values with 422 — so a future library that sends `v: 2` will have its heartbeats silently dropped until the collector is updated.

**Policy:**

| Type of change | Bump needed? | Example |
|---|---|---|
| New key in `features`, `runtime`, or `counters` dict | **No** — open dicts accept any keys | Add `gemini_enabled: bool` → stored in JSONB, queryable immediately |
| Remove a key from `features`/`runtime`/`counters` | **No** — collector doesn't enforce key presence | Drop `mtproto_proxy` flag → old collector stores the new payload just fine |
| New field inside an already-typed block (non-dict) | **Yes** — `extra="forbid"` would reject it | Adding `debug: bool` to top-level needs `v` bump |
| Rename/remove an existing top-level field | **Yes** — collector validates exact shape | Renaming `started_at` → `booted_at` |
| Change type of an existing field | **Yes** — Pydantic coerce or reject | Changing `ts` from `int` to `str` |

**Deployment order for breaking changes (bump `v`):**

1. **Deploy updated collector first** — accepts both old `v: 1` and new `v: N`
2. **Release library update** — starts sending `v: N`
3. **Remove `v: 1` support** from collector after a transition window (≥ one heartbeat interval so old clients have time to update)

This guarantees zero data loss during rollouts. The library's fire-and-forget send means early-adopter instances that update before the collector just see a silently dropped heartbeat — no user impact.

The collector should never be deployed without `v: 1` support unless all known library instances in the field have migrated away from it.

### What is never collected

- `api_id`, `api_hash`, `bot_api_token` value, or any credential
- Message content, chat IDs, peer identifiers, Telegram phone numbers
- File paths, project names, environment variable values (except the booleans listed above)
- **IP addresses** — the telemetry payload contains no IP address or network location data of any kind. The source IP of the HTTP request (visible to Traefik as for any connection) is never logged or stored by the collector.
- Session files, `.env` contents, CLI arguments

### Opt-out mechanism

Telemetry is **opt-out by default** (enabled on install). The single off switch is `DO_NOT_TRACK=1` — the cross-tool convention from consoledonottrack.com. Set as an environment variable before starting the server:

```bash
DO_NOT_TRACK=1 mcp-telegram
```

If `DO_NOT_TRACK` is unset, unset, or set to anything other than `1` — telemetry is enabled (default).

No interactive prompt on first run. Disclosure is delivered through:
- **Release notes** for v0.31.0 (the version that introduces telemetry)
- **README.md** telemetry section with full tracking plan
- Debug mode: `MCP_TELEMETRY_DEBUG=1` logs the payload to stderr instead of sending
- **Startup log line:** `INFO: Telemetry: enabled (disable with DO_NOT_TRACK=1)` — visible in server startup output

### Schedule and delivery

- **Heartbeat fires on server startup**, then **every 6 hours** thereafter (configurable via `MCP_TELEMETRY_INTERVAL` env, minimum 1 hour)
- All counters are **lifetime-of-process** (not delta) — the collector derives deltas from consecutive heartbeats with the same `instance_id` and `started_at`
- `started_at` captured at `time.time()` during server bootstrap, before the MCP server starts. Every heartbeat from the same process carries the same `started_at`.
- Fire-and-forget `asyncio.create_task` — never awaits the response
- **Shutdown heartbeat:** on graceful shutdown (SIGTERM), a final heartbeat is sent with `features.shutdown: true` (best-effort, sync, up to 10 s network timeout — does not block fast exits like SIGKILL). The `features.shutdown` flag distinguishes shutdown heartbeats from startup/periodic ones in the collector.
- Network errors are silently logged at DEBUG level only
- No retry logic in v1 (acceptable loss for aggregate trends)

### Instance ID lifecycle

- Generated once on first server startup as UUID v4
- Stored in `~/.config/fast-mcp-telegram/instance_id`
- Survives package reinstall (the file is in user config, not in site-packages)
- Never rotated automatically
- User can reset by deleting the file (a new UUID is generated on next startup)
- Purpose: de-duplicate consecutive heartbeats from the same installation

### Counter infrastructure

A lightweight `MetricsStore` class lives in `src/telemetry.py`:

```python
class MetricsStore:
    """Lifetime-of-process counters for tool-call activity.

    Thread-safe: all mutations and reads go through a ``threading.Lock``
    because ``snapshot()`` is called from a thread-pool executor while
    the event-loop wields ``record_call()`` / ``record_error()``.
    """

    def __init__(self) -> None:
        self.total_calls: int = 0
        self.errors: int = 0
        self.flood_waits: int = 0
        self._lock = threading.Lock()

    def record_call(self) -> None:
        with self._lock:
            self.total_calls += 1

    def record_error(self) -> None:
        with self._lock:
            self.errors += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "total_calls": self.total_calls,
                "errors": self.errors,
                "flood_waits": self.flood_waits,
            }
```

- ``record_call()`` / ``record_error()`` / ``record_flood_wait()`` exist as API but are **not yet wired** into tool-call handlers — they are a v2 integration point. Currently only ``snapshot()`` is used by the telemetry heartbeat loop.
- ``snapshot()`` returns a frozen copy for the telemetry loop.

### Collector container (v1)

| Aspect | Detail |
|--------|--------|
| **Language** | Python with `http.server.ThreadingHTTPServer` + `psycopg2` — synchronous, no ASGI framework. FastAPI/uvicorn/asyncpg/pydantic were all eliminated in favour of stdlib after benchmarking showed pydantic-core alone added ~15–20 MB RSS (see [`collector/README.md`](../collector/README.md#architecture) for rationale). |
| **Base image** | `python:3-slim` — not alpine (psycopg2-binary ships only manylinux/glibc wheels; building on Alpine adds complexity with no RSS benefit), not full `python:3` (unused build toolchain adds 800+ MB to the image). |
| **Port** | 8000 (internal) |
| **Auth** | None in v1 (endpoint is POST-only, no data worth stealing) |
| **Health** | GET /health → 200 OK (used by Docker healthcheck and Traefik) |
| **Env** | `TELEMETRY_DSN` — PostgreSQL connection string |
| **Deploy** | GitHub Actions (`deploy-collector.yml`) builds the image, pushes to `ghcr.io/leshchenko1979/telemetry-collector:main`, then appleboy/ssh-action writes `docker-compose.yml` + `.env` to `/root/services/telemetry-collector` on Box 3 and runs `docker compose up -d --wait`. |
| **Traefik** | Router config in the `vds-servers` repo (dynamic, hot-reload): `Host(fast-mcp-telegram-telemetry.l1979.ru) + Path(/v1/event)` → collector:8000 |

### Architecture in the source tree

```
src/
├── server.py                   # spawn telemetry task in lifespan
├── telemetry.py                # should_send(), send_heartbeat(), gather_payload(),
│                               # MetricsStore, instance_id, HTTP POST
└── config/server_config.py     # add: DO_NOT_TRACK env detection
```

### Why not OpenTelemetry

- OTel Python SDK adds 3–5 MB of dependencies and ~10–20 MB runtime memory for a heartbeat that fires once per launch
- Complex opt-out (`OTEL_SDK_DISABLED=true` + `OTEL_TRACES_EXPORTER=none` + several more env vars)
- Distributed tracing / span propagation solves problems we do not have
- A single HTTP POST with a 300-byte JSON body replaces the entire OTel machinery

### Alternatives considered (rejected)

| Alternative | Reason rejected |
|-------------|----------------|
| **OpenTelemetry full SDK** | Too heavy for one heartbeat/day; see "Why not OpenTelemetry" above |
| **PostgREST** | Adds an entire REST-to-PostgreSQL proxy just to avoid writing 15 lines of Python; more moving parts than a tiny collector |
| **Traefik webhooks plugin** | Requires enabling `experimental.plugins` on production Traefik, async delivery (may lose data), needs a dummy backend anyway; no simpler than a collector container |
| **Structured logs → grep** | No programmatic access; users may not send logs; no historical aggregation |
| **Fallow-style opt-in** | Adoption would be 1-5% — too low for Alexey's core question about feature adoption |
| **n8n webhook** | User explicitly rejected — wants data in PostgreSQL, not n8n |

### Collection endpoint URL

`https://fast-mcp-telegram-telemetry.l1979.ru/v1/event`

- DNS A-record on a domain the maintainer controls — can be re-pointed without a code release
- Traefik on Box 3 hot-reloads the router config (no restart)
- On infrastructure move, a 301 from the old URL suffices

### PostgreSQL schema

The collector connects to PostgreSQL **on the same host as the collector (Box 3)** via Docker internal networking bridge. The database is `telemetry`, the table is `telemetry`.

```sql
CREATE TABLE telemetry (
  id              BIGSERIAL PRIMARY KEY,
  received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  instance_id     TEXT NOT NULL,         -- extracted from payload.iid for indexing
  payload         JSONB NOT NULL,         -- full nested payload from the client
  payload_hash    TEXT NOT NULL,          -- SHA-256 of canonicalized payload (dedup)
  source_ip_hash  TEXT NOT NULL           -- SHA-256 of request source IP
);

CREATE INDEX idx_telemetry_instance_id   ON telemetry(instance_id);
CREATE INDEX idx_telemetry_received_at   ON telemetry(received_at);
CREATE INDEX idx_telemetry_payload_hash  ON telemetry(payload_hash);
```

**Connection:** the collector receives `TELEMETRY_DSN` env var (e.g. `postgres://telemetry:password@postgres:5432/telemetry`). The PostgreSQL container and collector container must share a Docker network.

**Retention:** the collector auto-purges rows older than `RETENTION_DAYS=90` (default) on every successful insert. The hard row cap is `MAX_ROWS=10_000_000` (default). Both are enforced in `collector/app/services.py`. Dedup window: 5 minutes (`DEDUP_WINDOW_SECONDS=300`) — identical payloads in that window are silently dropped.

**No PostgreSQL trigger is used.** Rate limiting, deduplication, row capping, and TTL cleanup all run in the application layer (collector) on each insert. This keeps the schema simple and the abuse-prevention logic in one place that's easy to audit.

No user-identifiable data ever reaches this table.

## Consequences

### Positive

- Maintainer gets real adoption numbers for feature investment decisions
- Data lives in existing PostgreSQL infrastructure — no new external service
- Payload is inspectable by users (`MCP_TELEMETRY_DEBUG=1`)
- Opt-out follows the cross-tool `DO_NOT_TRACK` convention
- Fire-and-forget means zero latency impact on MCP tools
- `started_at` + `iid` permits server-side delta computation without client state

### Neutral

- One new file in `~/.config/fast-mcp-telegram/` (`instance_id`)
- README grows a "Telemetry" section — some documentation surface
- Collector container is one more service in the docker-compose (tiny, no external dependencies)

### Negative

- Users who do not read release notes or docs may not realise telemetry is on (mitigated by `DO_NOT_TRACK` discoverability in the broader ecosystem and startup log line)
- Heartbeat-only means no per-tool-call metrics (acceptable for v1)
- Server must store a local `instance_id` file — adds one file to `~/.config/fast-mcp-telegram/`

### Risks

- Users who object to any outbound network request will be unhappy despite opt-out (mitigated by one-line disable)
- Endpoint downtime means data loss (acceptable for aggregate trends)
- `instance_id` file must survive reinstalls to avoid inflating unique-instance counts (mitigated by `~/.config/` persistence)
- `acl_principals` and `acl_read_only` are sourced through new methods on `SessionACL` (`principal_count()` and `read_only_count()`) — the telemetry code calls these rather than reading the ACL document directly, preserving encapsulation

### Scope vs Roadmap (v1)

v1 covers **feature adoption flags + aggregate error counters**. The full telemetry lane in [Roadmap.md](../Roadmap.md) also includes per-tool error rates, P95 tool latency, wrong-tool patterns, and ACL denial breakdowns — all deferred to v2+. This scope choice is deliberate: v1 answers "which features do people use" and nothing more.

## References

- [Roadmap.md](../Roadmap.md) — Telemetry lane (step 6)
- [research/telemetry-best-practices.md](../research/telemetry-best-practices.md) — notes on Fallow, Vercel CLI, SonarQube patterns
- [ADR 0001](0001-agent-scoped-session-acl.md) — ACL design (prime consumer of adoption metrics)
- [ADR 0006](0006-abuse-prevention-for-collection-endpoint.md) — Abuse prevention for the open collection endpoint
- [`src/config/server_config.py`](../../src/config/server_config.py) — config integration point
- [`src/server.py`](../../src/server.py) — lifespan hook point
