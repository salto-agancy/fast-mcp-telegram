# Product Roadmap

Official priorities for fast-mcp-telegram. Capability facts live in [Strategic-Market-Positioning.md](Strategic-Market-Positioning.md). Third-party Gemini research is under [research/](research/).

Last updated: 2026-05-27.

## Branches

| Branch | Contents |
| --- | --- |
| `master` | Roadmap, strategic positioning index, Gemini research |
| `feature/acl` | Opt-in session ACL implementation and design docs |
| `feature/evals` | Gategrid eval harness, CI workflow, gate cases |

## North star

**Shared `http-auth` multi-user hosting** — one gateway, many Bearer tokens, one Telegram account per token. Optimize tenant isolation, blast-radius control, stability, and agent correctness.

## Decisions (2026-05-26)

| Topic | Decision |
| --- | --- |
| ACL | Opt-in via `ACL_ENABLED`; per-token rules in static file — implementation on `feature/acl` |
| ACL config | Static `acl.yaml` only; no MCP configure tool in v1 |
| ACL scope | `chats`, `read_only`, `allow_global_search` — see [research/acl-design-brief.md](research/acl-design-brief.md) on `feature/acl` |
| Evals | Gategrid on `feature/evals` |
| Post-ACL focus | Eval expansion before rate limits / SQLite |

## Current sequence

| Step | Status | Branch | Deliverable |
| --- | --- | --- | --- |
| 1. Roadmap & research | Done | `master` | [Roadmap.md](Roadmap.md), Gemini docs under [research/](research/) |
| 2. ACL competitor audit | Done | `feature/acl` | [research/acl-operator-research.md](research/acl-operator-research.md) |
| 3. ACL design brief | Done | `feature/acl` | [research/acl-design-brief.md](research/acl-design-brief.md) |
| 4. ACL MVP | Done | `feature/acl` | [session_acl.py](../src/server_components/session_acl.py), [acl.yaml.example](../acl.yaml.example) |
| 5. Eval expansion + CI | In progress | `feature/evals` | PR gate when baseline stable |
| 6. Merge feature branches | Pending | — | PRs: `feature/acl`, then `feature/evals` |

## Shipped on `master`

- [Strategic-Market-Positioning.md](Strategic-Market-Positioning.md) capability index
- Gemini research split under [research/](research/) (third-party reference)

Session ACL and Gategrid evals: merge from `feature/acl` and `feature/evals` when ready.

## Backlog (not sequenced)

| Item | Notes |
| --- | --- |
| Per-token rate limits | Host + account protection |
| SQLite read cache | kfastov-style; pairs with ACL whitelists |
| ACL v2 permission matrix | Prgebish-style read/send per chat |
| Prompt-injection scanner | After ACL + evals |
| OAuth2 / IdP | Enterprise federation |
| Stdio path sandbox | Local stdio users |
| Multi-replica attachment tickets | Redis or shared store |
| Media OCR pipeline | Beyond voice transcription |

## References

- [Strategic-Market-Positioning.md](Strategic-Market-Positioning.md)
- [research/gemini-roadmap-proposal.md](research/gemini-roadmap-proposal.md)
- [Installation.md](Installation.md)
