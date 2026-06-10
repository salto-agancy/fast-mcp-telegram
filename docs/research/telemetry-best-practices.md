# Telemetry Best Practices Research

> **Research date:** 2026-06-10
> **Context:** How to implement anonymous feature-adoption telemetry in fast-mcp-telegram without annoying users or violating trust.
> **Sources:** Web searches on CLI/DevTool telemetry patterns; direct analysis of Fallow, Vercel CLI, SonarQube CLI.

## Research Summary

### 1. Fallow CLI — Opt-in pattern

Fallow (CLI tool) takes the strictest approach:
- **Off by default** — user must explicitly opt in
- **`fallow telemetry disable`** — CLI toggle command
- **`FALLOW_TELEMETRY_DEBUG=1`** — inspect what would be sent
- **No installation identifiers** — even `instance_id` is absent
- **Published tracking plan** — full transparency about what's collected
- **Fire-and-forget** — never blocks the CLI
- **Trade-off:** ~1–5% adoption rate among users

### 2. Vercel CLI — Opt-out pattern

Vercel CLI (most popular in the ecosystem):
- **On by default** — opt-out model
- **`vercel telemetry disable`** — persisted per-project
- **`VERCEL_TELEMETRY_DEBUG=1`** — inspect payloads going to stderr
- **`DO_NOT_TRACK=1`** — cross-tool kill switch
- **Published tracking plan** — docs page with what/why
- **No PII** — no project paths, env vars, command arguments (especially secret-bearing ones)
- **Trade-off:** ~50–70% adoption rate among users

### 3. SonarQube CLI — Fleet management pattern

SonarQube CLI (enterprise context):
- **On by default** — opt-out model
- **Single command** `sonar config telemetry --disabled` covers both telemetry AND crash reports
- **`SONAR_TELEMETRY_DEBUG=1`** — inspect mode
- **Fleet management** — enterprise admins can disable globally via bootstrap/env vars
- **Published tracking plan** — GDPR-ready documentation
- **Trade-off:** enterprise users expect opt-out; CLI tools get away with on-by-default

### 4. Cross-tool DO_NOT_TRACK convention (consoledonottrack.com)

- Community standard: `DO_NOT_TRACK=1` stops telemetry in Fallow, Vercel, and others
- Recommended as minimum — every tool should respect it
- Single env var saves users from configuring telemetry per-tool

### 5. Industry consensus — privacy guardrails

| Collect | Never Collect |
|---|---|
| Feature flags (booleans/enums) | Credentials, tokens, API keys |
| Aggregate counters (call count, error count) | Message content, file content |
| Version strings, OS family | File paths, project names |
| Instance UUID (random, non-reversible) | User IDs, chat IDs, IPs |
| Uptime / process lifetime anchor | Environment variable values |

### 6. OpenTelemetry Analysis — Rejected for this use case

- **Dependency cost:** 4+ packages (`opentelemetry-api`, `-sdk`, `-exporter-otlp`, `-instrumentation`), +3–5 MB install size
- **Runtime cost:** SDK initializes at 10–20 MB memory even with no tracing
- **Opt-out complexity:** requires `OTEL_SDK_DISABLED=true` + `OTEL_TRACES_EXPORTER=none` + several more env vars
- **Architecture mismatch:** OTel solves distributed tracing across microservices, not a heartbeat POST every 6 hours
- **Verdict:** overkill for feature-adoption-style telemetry

### 7. Traefik Plugins (webhooks) — Evaluated but rejected

- **`traefik-plugin-webhooks`** (JoaoVictorLouro, v0.5.0) — async middleware that fires POST webhooks with request body
- **`request-response-logger`** — logs request/response bodies to stdout, experimental
- **`httplog`** — production-unsafe (`"BE WARNED: DO NOT USE IN PRODUCTION"`)
- **Rejection rationale:** all plugins require `experimental.plugins` in static Traefik config (restart), have no delivery guarantee (async), and still need a backend service. A tiny collector container is simpler, synchronous, and hot-reloadable via Traefik labels.

### 8. PostgREST — Evaluated as zero-code alternative

- Auto-exposes PostgreSQL tables as REST API from a schema definition
- Could eliminate the Python collector entirely: POST → PostgREST → INSERT
- **Rejected for v1** because it adds another Docker container (PostgREST) and SQL function, vs. a 15-line Python collector that does the same thing with zero new infra dependencies

## Decision for fast-mcp-telegram

| Decision | Rationale |
|---|---|
| **Opt-out (on by default)** | Maximise data quality for adoption decisions; aligns with Vercel/Sonar precedent |
| **Simple HTTP POST** | 300 bytes per heartbeat; no OTel overhead |
| **Tiny collector container** | Synchronous write to PostgreSQL; no Traefik plugins or PostgREST needed |
| **DO_NOT_TRACK + MCP_TELEMETRY_DISABLED** | Industry-standard kill switches |
| **Debug mode** | `MCP_TELEMETRY_DEBUG=1` — user can inspect payload |
| **No interactive prompt** | Disclosure in release notes + README only |
| **Lifetime-of-process counters** | No delta tracking on client; collector derives from consecutive heartbeats |
| **`started_at` anchors** | Wall-clock boot timestamp enables precise session boundaries |
| **Disclosure in README** | Full tracking plan documented in the repo |

## References

- [Fallow telemetry docs](https://github.com/psarna/fallow/telemetry)
- [Vercel CLI telemetry](https://vercel.com/docs/telemetry)
- [SonarQube CLI telemetry](https://docs.sonarsource.com/sonarcloud/telemtry)
- [consoledonottrack.com](https://consoledonottrack.com)
- [OpenTelemetry Python SDK](https://opentelemetry.io/docs/instrumentation/python/)
- [traefik-plugin-webhooks](https://github.com/JoaoVictorLouro/traefik-plugin-webhooks)
- [PostgREST](https://postgrest.org)
