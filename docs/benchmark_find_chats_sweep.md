# find_chats — Semaphore + Timeout Sweep Results

## Summary

Added configurable `asyncio.Semaphore` + `asyncio.wait_for` to `_gather_term_results` in `find_chats.py`, matching the pattern in `search_global`. Ran 7 configs (baseline + 6 sweeps) on the Docker testbed (box3) using Alexey's account with Russian contact terms.

**Result: Semaphore doesn't improve find_chats** — the multi-term scenarios only have 2-3 terms, so limiting concurrency just adds overhead. The primary latency factor is Telegram's FloodWait (~19s for repeated `contacts.Search` on the same DC).

## Configs Compared

| Config | max_concurrent | search_timeout |
|--------|:-:|:-:|
| baseline | none (full parallel) | none |
| c2_t5 | 2 | 5s |
| c2_t10 | 2 | 10s |
| c4_t5 | 4 | 5s |
| c4_t10 | 4 | 10s |
| c8_t5 | 8 | 5s |
| c8_t10 | 8 | 10s |

## Key Scenarios (multi-term only)

| Config | global_multi | multi_dedup | multi_fairness | multi_partial |
|--------|:-:|:-:|:-:|:-:|
| **baseline** | 0.247s ✅ | 0.266s ✅ | 3.851s ✅ | 6.644s ✅ |
| c2_t5 | 0.344s ✅ | 0.327s ✅ | 4.122s ✅ | 6.295s ⚠️ |
| c2_t10 | 0.308s ✅ | 0.293s ✅ | 4.066s ✅ | 6.668s ⚠️ |
| c4_t5 | 0.367s ✅ | 0.293s ✅ | 3.849s ✅ | 7.016s ⚠️ |
| c4_t10 | 0.348s ✅ | 0.249s ✅ | 4.019s ✅ | 6.689s ⚠️ |
| c8_t5 | 0.343s ✅ | 0.336s ✅ | 3.453s ⚠️ | 0.382s ✅ |
| c8_t10 | 0.233s ✅ | 0.252s ✅ | 3.992s ✅ | 6.593s ⚠️ |

`⚠️` = scenario failed (lost results or partial failure)

## Analysis

1. **All configs within noise** (±5-30%) — no config materially beats baseline for multi-term searches
2. **c8_t5 looks good artificially**: `multi_partial` drops from 6.6→0.38s because the 5s timeout kills the slow nonsense term, but `multi_fairness` ALSO loses results — the timeout is too aggressive for FloodWait (~19s)
3. **c8_t10 performs best for global_multi/multi_dedup** (0.233s, 0.252s) but still loses `multi_partial` results
4. **FloodWait is the dominant factor**: `contacts.Search` on the same DC serializes, and a FloodWait of 19s blocks everything

## Decision

Keep defaults **max_concurrent=2, search_timeout=10.0** for consistency with `search_global`. These act as safety valves (prevent runaway parallelism if someone passes 50 terms to `find_chats`), not throughput optimizers.

The real value of this feature was proven in `search_global` (30% improvement) where the search has ~50 terms — find_chats only uses 2-3 terms per call.
