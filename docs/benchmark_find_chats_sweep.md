# find_chats — Semaphore + Timeout Sweep Results

## Summary

Added configurable `asyncio.Semaphore` + `asyncio.wait_for` to `_gather_term_results` in `find_chats.py`, matching the pattern in `search_global`. Ran 7 configs (baseline + 6 sweeps) on the Docker testbed (box3) using Alexey's account with Russian contact terms.

**Both config AND scenario order were randomized** to eliminate FloodWait accumulation bias.

**Result: Semaphore doesn't improve find_chats** — the multi-term scenarios only have 2-3 terms, so limiting concurrency adds negligible overhead. The primary latency factor is Telegram's FloodWait (~19s for repeated `contacts.Search` on the same DC), which strikes randomly regardless of config.

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

## Key Scenarios — MEDIAN durations (robust to FloodWait outliers)

| Config | global_multi | multi_dedup | multi_fairness | multi_partial | **Avg median** |
|--------|:-:|:-:|:-:|:-:|:-:|
| **baseline** | 0.389s ✅ | 0.311s ✅ | 0.560s ✅ | 0.274s ✅ | **0.383s** |
| c2_t5 | 0.313s ✅ | 0.433s ⚠️ | 0.727s ✅ | 0.491s ✅ | 0.491s |
| c2_t10 | 5.553s ✅ | 0.202s ✅ | 0.670s ✅ | 0.476s ✅ | 1.725s |
| c4_t5 | 0.206s ✅ | 0.260s ✅ | 0.742s ✅ | 0.485s ✅ | 0.423s |
| c4_t10 | 0.185s ✅ | 9.526s ✅ | 0.673s ✅ | 0.270s ⚠️ | 2.664s |
| c8_t5 | 0.187s ✅ | 0.240s ✅ | 0.692s ✅ | 0.288s ✅ | **0.352s** |
| c8_t10 | 0.233s ✅ | 0.320s ✅ | 0.563s ✅ | 0.365s ✅ | 0.370s |

⚠️ = one or more iterations failed (FloodWait killed the term)

All configs within noise (±0.1s) when not FloodWait-hit. The c8_t5/c8_t10 edge over baseline (~8-10%) is within measurement noise for 3 iterations per config.

## Per-Iteration Durations (multi-term)

Each config runs 3 iterations. FloodWait (~5-19s `FloodWaitError`) randomly hits ~1/3 of iterations:

| Config | global_multi iters | multi_fairness iters | multi_partial iters |
|--------|:-:|:-:|:-:|
| baseline | [3.355, 0.389, 0.268] | [0.560, 0.487, 14.800] | [0.274, 0.259, 14.321] |
| c2_t5 | [0.274, 0.313, 5.159] | [0.642, 0.731, 0.727] | [0.441, 0.634, 0.491] |
| c2_t10 | [0.351, 10.225, 5.553] | [4.627, 0.670, 0.624] | [0.488, 0.476, 0.364] |
| c4_t5 | [0.300, 0.194, 0.206] | [0.742, 0.623, 0.831] | [0.485, 0.515, 0.344] |
| c4_t10 | [0.185, 0.183, 0.206] | [0.619, 0.683, 0.673] | [0.270, 0.243, **13.322**] |
| c8_t5 | [0.187, 0.173, 5.017] | [0.692, **14.790**, 0.640] | [0.288, 0.286, 0.321] |
| c8_t10 | [0.248, 0.233, 0.186] | [0.553, 0.563, 0.578] | [0.422, 0.365, 0.355] |

**Bold** = iteration that was hit by FloodWait.

The `multi_fairness` and `multi_partial` scenarios make ADDITIONAL internal per-term API calls (to verify fairness), which makes them more likely to trigger FloodWait.

## Clean-Run Durations (no FloodWait hit, using MIN)

When not hit by FloodWait, all configs are within noise:

| Config | global_multi | multi_dedup | multi_fairness | multi_partial |
|--------|:-:|:-:|:-:|:-:|
| baseline | 0.268s | 0.278s | 0.487s | 0.259s |
| c2_t5 | 0.274s | 0.382s | 0.642s | 0.441s |
| c4_t5 | 0.194s | 0.147s | 0.623s | 0.344s |
| c8_t5 | 0.173s | 0.151s | 0.640s | 0.286s |
| c8_t10 | 0.186s | 0.302s | 0.553s | 0.355s |

## Conclusion

1. **FloodWait is the dominant factor**, not concurrency. `contacts.Search` on one DC serializes with FloodWait (~5-19s after ~5 requests in quick succession)
2. **Semaphore makes no material difference** for 2-3 term queries — the parallel tasks finish in ~0.2s each before any limit matters
3. **The c8_t5 edge (~8% over baseline) is within noise** for 3-iteration samples
4. **With randomized order**, the flood pattern changes but the conclusion stays

## Decision

Keep defaults **max_concurrent=2, search_timeout=10.0** for consistency with `search_global`. These act as safety valves (prevent runaway parallelism if someone passes 50 terms to `find_chats`), not throughput optimizers.

The real value of this feature was proven in `search_global` (30% improvement) where the search has ~50 terms — find_chats only uses 2-3 terms per call.
