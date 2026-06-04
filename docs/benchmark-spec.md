# find_chats — Performance Optimization Spec

## 1. Overview

`find_chats` is the most complex tool in fast-mcp-telegram — 4 code branches, 7 submodules, 650+ lines. It makes Telegram API calls across multiple paths:

| Branch | Mechanism | Telegram API calls |
|--------|-----------|-------------------|
| **Global (single term)** | `contacts.SearchRequest` | 1 API call per query |
| **Global (multi-term)** | N × `SearchRequest` + round-robin interleaving | N API calls, sequential interleaving |
| **Date-based** | `messages.iter_dialogs` + per-entity date fallback | 1 iter_dialogs + M fallback `iter_messages` |
| **Folder — include_peers** | `get_entity` × N + `GetPeerDialogsRequest` × ceil(N/50) + optional `iter_dialogs` | Parallel get_entity + serial GetPeerDialogs chunks |
| **Folder — flag-based** | `iter_dialogs` + flag matching + per-entity date fallback | 1 iter_dialogs + M fallback `iter_messages` |

### Current performance characteristics

- All async, but mostly **sequential** within each branch
- Only `include_peers` uses `asyncio.gather()` — for entity resolution (Semaphore 8)
- Multi-term global search uses **interleaving** (round-robin `anext()`), not parallel gather
- Date fallback (`iter_messages(entity, limit=1)`) fires **one at a time** — a classic N×RTT sequential bottleneck

---

## 2. Optimization Targets (Priority Order)

### P0 — Multi-term global search parallel gather

**File:** `src/tools/chat_discovery/find_chats.py` (lines 145-178)

**Current:**
```python
generators = [
    search_contacts_native(term, limit, chat_type, public)
    for term in terms
]
# round-robin interleaving — one anext() per generator per tick
```

**Problem:** For N=3 terms with L=20 results each, we make 60 sequential `anext()` rounds. Each round adds ~100-300ms of Telegram API latency.

**Optimization:**
```python
async def _find_chats_global_multi_term(terms, limit, chat_type, public):
    # Launch all SearchRequest in parallel
    tasks = [search_contacts_as_list(t, limit, chat_type, public)
             for t in terms]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Merge + deduplicate by entity ID
    merged = merge_and_deduplicate(results, limit)
    return {"chats": merged}
```

**Expected speedup:** 2-3× for 3 terms, 4-5× for 5+ terms.

**Risk:** Very low. `SearchRequest` is a lightweight read-only call. No flood wait observed at this scale.

---

### P1 — Fallback gather for date_helpers

**Files:**
- `src/tools/chat_discovery/date_helpers.py` (lines 73-88 `_dialog_in_date_range`)
- `src/tools/chat_discovery/filter_flags.py` (called from `_filter_flags_entity_allowed_by_date_bounds`)
- `src/tools/chat_discovery/dialog_search.py` (called from `search_dialogs_impl`)

**Current:**
```python
async def _dialog_in_date_range(entity, client, dialog_date, min_date_dt, max_date_dt):
    if dialog_date:
        # fast path — no API call
        return ...

    # FALLBACK: sequential per-entity iter_messages
    fallback_date = await _get_last_message_date(entity, client)
    ...
```

**Problem:** When iterating 500 dialogs and most lack `dialog.date` (common for old/archived chats), each fallback is a separate `iter_messages` API call — up to 500 sequential RTTs.

**Optimization:** Buffer entities that need fallback, then `gather()` all fallback queries.

The challenge: both `dialog_search.py` and `filter_flags.py` use `_dialog_in_date_range` as a **filter predicate** inside a loop that decides per-entity whether to include or skip. We can't blindly gather all — we need to restructure the loop.

**Approach 1: Two-pass (for flag-based)**
1. First pass: collect all entity IDs → filter by flags and date bounds with early skip when `dialog.date` exists.
2. Batch gather: for entities without `dialog.date`, run all fallbacks in parallel.
3. Second pass: filter by fallback results.

**Approach 2: Lookahead buffer (for dialog_search)**
Since `iter_dialogs` is a generator with `limit × 10` — we can pre-fetch a batch, gather fallbacks, then yield.

**Expected speedup:** 10-50× on the date fallback path (500 RTTs → 1 RTT + 1 gather).

**Risk:** Low for flag-based (two-pass), medium for dialog_search (generator semantics must be preserved including early break).

---

### P2 — Parallel GetPeerDialogs chunks

**File:** `src/tools/chat_discovery/include_peers.py` (lines 148-184)

**Current:**
```python
for chunk_start in range(0, len(ordered_peer_ids), GET_PEER_DIALOGS_CHUNK_SIZE):
    # sequential chunk processing
    result = await client(GetPeerDialogsRequest(peers=input_peers))
```

**Problem:** For 200 include_peers → 4 serial API calls (50 per chunk).

**Optimization:**
```python
tasks = [
    client(GetPeerDialogsRequest(peers=make_peers(chunk)))
    for chunk in chunks
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

**Risk: MEDIUM.** `messages.getPeerDialogs` can hit `FLOOD_WAIT` if we send too many parallel requests. Mitigation: use `Semaphore(2-3)` and fall back to sequential on RateLimitError.

**Expected speedup:** 2-4× for large folders.

---

### P3 — Flag-based iter_dialogs fallback buffering

**File:** `src/tools/chat_discovery/filter_flags.py`

**Current:** Single sequential `iter_dialogs` loop with per-entity filtering AND fallback date check.

**Optimization:** Two-pass:
1. First pass over `iter_dialogs` → collect entities, separate by `dialog.date` presence.
2. `gather()` fallbacks for entities without `dialog.date`.
3. Build final filtered list.

The trick: we must maintain the `limit` parameter semantics. First pass may need to go through more dialogs to find enough matches.

---

## 3. Benchmark Design

### 3.1. Benchmark script

A standalone integration script at `tests/integration/benchmark_find_chats.py` that:

1. **Tests all 4 branches** with real Telegram API calls
2. Uses `time.perf_counter()` or `time.monotonic()` for precision
3. Reports per-branch timing + flood wait events
4. Is designed to run **before and after** changes for comparison

### 3.2. Test scenarios

| Scenario | Branch | Query | Limit | Expected calls |
|----------|--------|-------|-------|---------------|
| Global single | Global | "alexey" | 10 | 1 SearchRequest |
| Global multi | Global | "alexey,test,bot" | 10 | 3 SearchRequest |
| Date browse | Date-based | None + min_date=7d ago | 20 | 1 iter_dialogs |
| Date search | Date-based | "alex" + max_date=30d ago | 10 | 1 iter_dialogs + M fallbacks |
| Folder include | Folder/include | "Без каналов" | 20 | N get_entity + ceil(N/50) GetPeerDialogs |
| Folder flags | Folder/flags | "Ответить" | 10 | 1 iter_dialogs + M fallbacks |

### 3.3. Metrics

- **Total wall clock time** per scenario
- **API call count** (counted via logging or Telethon event system)
- **Flood wait events** (counted from `FloodWaitError` catches)
- **Results count** (to verify correctness is preserved)

### 3.4. Output format

```json
{
  "timestamp": "2026-05-29T00:00:00Z",
  "build_id": "before" | "<commit-hash>",
  "scenarios": [
    {
      "name": "global_multi",
      "duration_s": 0.842,
      "api_calls": 3,
      "results": 10,
      "flood_waits": 0
    },
    ...
  ]
}
```

### 3.5. Statistics

Each scenario runs **3-5 iterations**, reports:
- min / max / mean duration
- iteration-by-iteration breakdown

### 3.6. Project Structure

```
tests/integration/
├── benchmark_find_chats.py           # Standalone CLI — full benchmark (9 scenarios, warmup, iterations, JSON output)
├── benchmark_search_global.py        # Standalone CLI — search benchmark (6 scenarios, flood wait retry, cross-run compare)
├── test_benchmark_find_chats.py      # Pytest smoke test — @pytest.mark.integration, parametrized, covers all 4 code paths (global, flags, folder+date, date)
├── test_benchmark_search_global.py   # Pytest smoke test — @pytest.mark.integration, parametrized, 1 iteration
├── test_date_filtering.py            # Standalone validation script (not a benchmark)
├── test_filter_resolution.py         # Standalone validation script
├── test_find_chats_date_filtering.py # Standalone validation script
└── test_get_messages_timing.py       # Standalone validation script
docs/
└── benchmark-spec.md                  # This document — spec, targets, running instructions
```

### 3.7. Running

**Full benchmark (5 iterations, all scenarios):**
```bash
uv run python3 tests/integration/benchmark_find_chats.py
uv run python3 tests/integration/benchmark_search_global.py
```

**Quick validation (1 iteration, selected scenarios):**
```bash
pytest -m integration -k test_benchmark_find_chats
pytest -m integration -k test_benchmark_search
```

**Custom iterations:**
```bash
uv run python3 tests/integration/benchmark_find_chats.py --iterations 3
uv run python3 tests/integration/benchmark_search_global.py --iterations 3
```

**Skip specific scenarios:**
```bash
uv run python3 tests/integration/benchmark_find_chats.py --skip folder_include
```

**Save JSON output:**
```bash
uv run python3 tests/integration/benchmark_find_chats.py --output results.json
uv run python3 tests/integration/benchmark_search_global.py --output results.json
```

The standalone CLI scripts (`benchmark_*.py`) are the primary benchmarking tool — full warmup, iteration control, per-scenario timeout, JSON output, cross-run comparison. The pytest smoke tests (`test_benchmark_*.py`) are quick pass/fail validators that ensure the API endpoints work.

---

## 4. Flood Wait Risk Assessment

| API Call | Risk | Rationale |
|----------|------|-----------|
| `contacts.SearchRequest` | **None** | Unauthenticated read — Telegram doesn't rate-limit this. Used 1-3× per call. Parallelization adds no risk. |
| `messages.iter_dialogs` | **None** | Read-only enumeration, Telethon handles pagination. Sequential by nature. |
| `messages.iter_messages(entity, limit=1)` | **Low** | Reading 1 message per entity. The risk is **not** per-call but **volume** — 500 fallbacks × 1 message = flood wait on servers. Currently sequential = slow but avoids flood. Parallel gather could trigger if 500 fallbacks fire instantly. **Mitigation:** cap concurrency with Semaphore. |
| `messages.getPeerDialogs` | **Medium** | This is the riskiest. Telegram docs warn about flood wait. Current chunk size of 50 is conservative. Parallel chunks could trigger. **Mitigation:** Semaphore(2-3), fallback to sequential on FloodWaitError. |
| `contacts.resolveUsername` | **Low** | Used implicitly in `get_entity`. Low volume. |

**Conclusion:** Only P2 (parallel GetPeerDialogs) has a meaningful flood wait risk. P0 and P1 are safe with mild concurrency limits.

---

## 5. Implementation Order

1. **P0** — Multi-term gather (few lines, high impact, zero risk)
2. **Benchmark script** — So we can measure everything
3. **P1** — Fallback gather (structural refactor, test carefully)
4. **P2** — Parallel GetPeerDialogs chunks (Semaphore + flood fallback)
5. **P3** — Flag-based two-pass (refinement after P1 proves the gather pattern)

---

## 6. Success Criteria

| Metric | Current (baseline) | Target |
|--------|-------------------|--------|
| 3-term global search | ~N × RTT = 3 × ~500ms = ~1500ms | ~RTT = ~500ms |
| 500-dialog date filter | ~500 × RTT = ~250s (unusable) | ~1 iter_dialogs + 1 gather = ~3s |
| 200-peer GetPeerDialogs | ~4 × RTT = ~2s | ~1 RTT = ~500ms |
| Flood wait events | 0 | 0 (same as current) |
| Results match | identical | identical (verification in benchmark) |
