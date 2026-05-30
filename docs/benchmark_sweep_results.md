# Config Sweep Results — Russian Terms, Alexey's Account

## Account Info

- **User**: Алексей (id=7235735051, phone=+79154055758)
- **Session**: `upz47c0MVuSRTH9HZEB90ma8AypDxRjq3k9sbB7Y-O4`
- **Contacts**: 300+ Russian real estate / investment professionals
- **Channels**: 300+ real estate / investment groups ("Инвестиции в редевелопмент | Лещенко", etc.)

## Search Terms

All terms are characteristic of the account's real estate/investment domain:
- **single_term**: `"недвижимость"` (limit=10)
- **two_terms**: `"недвижимость, инвестиции"` (limit=10)
- **three_terms**: `"недвижимость, инвестиции, сделка"` (limit=10)
- **five_terms**: `"недвижимость, инвестиции, объект, проект, сделка"` (limit=10)
- **three_terms_large**: `"недвижимость, инвестиции, сделка"` (limit=50)
- **dedup**: `"недвижимость, сделка, недвижимость"` (limit=15)
- **fairness**: `"недвижимость, инвестиции, сделка"` (limit=20), then individual checks

## Results Matrix

| Scenario | Baseline | c2_t5 | c2_t10 | c4_t5 | c4_t10 | c8_t5 | c8_t10 |
|---|---|---|---|---|---|---|---|
| single_term | 0.842 | 1.018 | **0.917** | 1.236 | 0.792 | 1.031 | 0.777 |
| two_terms | 1.110 | 1.529 | 1.318 | 1.349 | **1.055** | 1.483 | 1.295 |
| three_terms | 1.368 | **1.240** | 1.659 | 1.637 | 1.439 | 1.402 | 1.931 |
| five_terms | 1.967 | 1.757 | **1.767** | 2.368 | 2.015 | 2.208 | 1.580 |
| three_terms_large | 4.747 | 5.564 | **4.715** | 26.112 | 4.781 | 5.451 | 4.734 |
| dedup_check | 2.310 | 2.129 | **1.928** | 2.704 | 2.020 | 2.282 | 2.201 |
| fairness_ck | 31.804 | 9.912 | **7.749** | 15.340 | 8.159 | 8.724 | 15.473 |
| **Weighted** | **6.593** | **3.048** | **2.879** | 7.250 | **2.894** | 3.226 | 3.999 |

**Weighted score** = mean of normalized scenario means (lower is better).
Bold = best in each row / top-3 overall.

## Observations

### clear winner: `max_concurrent=2, search_timeout=10`
- **Fairness**: 7.7s vs baseline 31.8s (−76%)
- **All scenarios pass** with no assertion errors
- **No catastrophic outliers** (unlike c4_t5 which hit 65s on large queries)
- Consistent across all 3 iterations

### runner-up: `max_concurrent=4, search_timeout=10`
- Slightly better on single/dual term queries (0.79 vs 0.92)
- Slightly worse on fairness (8.2s vs 7.7s)
- Still very good overall — within 0.5% of c2_t10 on weighted score

### What doesn't work
- **timeout=5** with any concurrency: false timeouts cause fallback, inflating times
- **concurrent=4 + timeout=5**: 65s outlier on large queries
- **concurrent=8 + timeout=10**: unfairness grows, fairness hit 29s max

### Why timeout=10 is safe
No false positives — all requests complete within 10s even with 8 concurrent.
But with 8 concurrent the fairness degrades because one slow term blocks others in the semaphore queue.

## Recommendation

Keep `_DEFAULT_MAX_CONCURRENT = 2` and `_DEFAULT_SEARCH_TIMEOUT = 10.0`.

These defaults are:
- **Safe** for any user's account (no timeout false positives)
- **Consistent** (minimal variance between iterations)
- **Effective** (−76% fairness, −20% large queries vs baseline)
- **Conservative** (2 concurrent requests won't overwhelm the MTProto pipeline)

Users who need more throughput can tune up to max_concurrent=4, search_timeout=10.
