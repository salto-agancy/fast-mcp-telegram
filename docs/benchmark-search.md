# Benchmark Spec: P0-style gather for global search / in-chat search

## Current Architecture

### Entry Points

| Tool Function | Impl Function | Generator Path |
|---|---|---|
| `search_messages_globally(query, ...)` | `search_messages_impl(query, chat_id=None)` | `_search_global_messages_generator` → `SearchGlobalRequest` |
| `get_messages(chat_id, query, ...)` | `search_messages_impl(query, chat_id=chat_id)` | `_search_chat_messages_generator` → `iter_messages` |

Both go through `_handle_query_mode` → split query by commas → `_execute_parallel_searches_generators` (round-robin).

### Round-Robin Problem

```python
# _execute_parallel_searches_generators (current)
while active_gens and len(collected) < target_limit:
    for i, gen in active_gens:
        result = await gen.__anext__()  # blocks while API call is in flight
```

With 3 query terms:
- Gen1 starts → `SearchGlobalRequest` / `iter_messages` first page → 200-500ms network wait → yields 1 message
- Gen2 starts → same delay
- Gen3 starts → same delay
- THEN all three have cached results and round-robin is fast

**Total sequential delay: terms × API_latency**

## Optimization: P0-style `asyncio.gather`

### Global search (`search_messages_globally`)

`_search_global_messages_generator` each batch calls:
```python
result = await client(SearchGlobalRequest(q=query, ...))
```

This is a direct MTProto call → same shape as `client.messages.search()` in find_chats P0.

**Approach:** For first batch (up to 1+auto_expand_batches pages):
1. Build all `SearchGlobalRequest` calls for all terms simultaneously
2. `asyncio.gather(*calls)`
3. Round-robin merge with dedup
4. For subsequent batches (if auto_expand_batches > 0), repeat gather

### In-chat search (`get_messages` in a chat)

`_search_chat_messages_generator` uses:
```python
async for message in client.iter_messages(entity, search=query, offset_id=...):
```

`iter_messages` is a streaming generator — each iteration yields one message with internal pagination.

**Approach (Option A):** Pre-collect each term's results:
```python
async def _collect_term_results(client, entity, query, limit, ...):
    results = []
    async for msg in client.iter_messages(entity, search=query, limit=limit):
        results.append(msg)
    return results

# Then:
all_results = await asyncio.gather(*[_collect_term_results(...) for term in terms])
# Round-robin merge
```

Memory: `limit × terms` messages in memory at once. For limit=50, terms=5 → 250 messages → negligible.

### Expected Speedup

| Scenario | Current (est.) | Optimized (est.) | Speedup |
|---|---|---|---|
| global 1 term | 0.4s | 0.4s | 0% |
| global 2 terms | 0.7s | 0.45s | ~55% |
| global 3 terms | 1.0s | 0.5s | ~50% |
| global 5 terms | 1.6s | 0.6s | ~62% |
| chat 2 terms | 1.0s | 0.6s | ~40% |
| chat 3 terms | 1.5s | 0.7s | ~53% |
| chat 5 terms | 2.5s | 0.9s | ~64% |

### Benchmark Scenarios

| Scenario | Query | Limit | What it tests |
|---|---|---|---|
| `single_term` | "alexey" | 10 | Baseline overhead (no parallel gain) |
| `two_terms` | "alexey, test" | 10 | First multi-term case (expected ~50%) |
| `three_terms` | "alexey, test, channel" | 10 | Typical real usage (expected ~50%) |
| `five_terms` | "alexey, test, channel, bot, group" | 10 | Stress multi-term |
| `three_terms_large` | "alexey, test, channel" | 50 | Realistic large result |
| `chat_two_terms` | "hello, world" (in a chat) | 10 | In-chat multi-term |
| `chat_three_terms` | "hello, world, test" (in a chat) | 10 | In-chat multi-term |

### Assertion Scenarios (Correctness Checks)

- `dedup_check`: Verify no duplicate messages from different terms matching same message
- `fairness_check`: Verify results from ALL terms are present (not just first term's results)
- `partial_fail`: Not applicable here (no partial failure in gather)

## Implementation Plan

1. ✅ Task #1: Code analysis complete
2. ✅ Task #2: This document
3. Build benchmark (`tests/integration/benchmark_search_global.py`)
4. Run baseline on master (Docker container, box 3)
5. Create branch `feat/sg0-global-gather`
6. Implement gather in `_collect_messages_global` (and optionally `_collect_messages_in_chat`)
7. Benchmark optimized
8. Compare results
