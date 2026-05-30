# Config Sweep Results — search_global Parallelization

## Command Run
`--max-concurrent 2,4,8 × --search-timeout 5,10` all with `--iterations 3`

## Summary Table

| Config | single | two | three | five | 3large | dedup | fair | Score | vs baseline |
|--------|--------|-----|-------|------|--------|-------|------|-------|-------------|
| **baseline** | 0.891 | 1.036 | 1.318 | 1.723 | 4.085 | 2.180 | 8.220 | 4.190 | — |
| **c2_t5**  | 1.033 | 0.855 | 1.057 | 1.785 | 3.683 | 1.615 | 5.917 | 3.283 | **−21.6%** |
| **c2_t10** | 0.601 | 0.786 | 1.099 | 1.403 | 2.921 | 1.649 | 5.503 | 2.920 | **−30.3%** |
| **c4_t5**  | 0.677 | 0.664 | 0.847 | 1.565 | 4.915 | 1.537 | 6.781 | 3.746 | −10.6% |
| **c4_t10** | 0.792 | 0.797 | 0.812 | 1.340 | 3.404 | 1.382 | 5.454 | 2.964 | −29.3% |
| **c8_t5**  | 0.857 | 0.984 | 1.377 | 1.264 | 3.109 | 1.251 | 6.074 | 3.123 | −25.4% |
| **c8_t10** | 0.758 | 0.947 | 1.030 | 1.839 | 4.620 | 1.927 | 10.475 | 4.937 | +17.8% |

> Score = weighted average (weights: single/two/three/five = 1, dedup = 2, 3large = 3, fair = 4)

## Best Config: `max_concurrent=2, search_timeout=10` 🏆

- Weighted score **−30.3%** vs baseline
- Most consistent across all 7 scenarios
- No outlier behaviour (c4_t10 had 61s outlier, c8_t10 had 10.5s fairness)
- All assertions pass
- Great fairness improvement: **8.22→5.50s (−33%)**

## Runner-up: `max_concurrent=2, search_timeout=5` (score −21.6%)

Tighter timeout, slightly less gain. Safer for very slow connections.

## Default Values Selected

```python
_DEFAULT_MAX_CONCURRENT = 2
_DEFAULT_SEARCH_TIMEOUT = 10.0
```

Chosen because:
1. **concurrent=2** avoids MTProto contention while still allowing parallel execution
2. **timeout=10** prevents hung connections without falsely triggering on normal queries
3. The combos handles all scenarios: simple queries (0.6s), multi-term (1.4s), batch iterations (2.9s), fairness checks (5.5s)
4. c=4 showed diminishing returns and occasional timeout firestorms
5. c=8 actually regressed some scenarios due to connection pool exhaustion
