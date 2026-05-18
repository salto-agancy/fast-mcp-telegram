# Forum in-topic reply fetch (get_messages + reply_to_id)

Reference for agents implementing or debugging [`src/tools/search/replies.py`](../../src/tools/search/replies.py) `_fetch_replies` and [`src/tools/search/forum_replies.py`](../../src/tools/search/forum_replies.py) `_collect_forum_anchor_replies`. Validated on @telemtrs 2026-05-18.

## Problem (#49)

In a forum megagroup, `get_messages(reply_to_id=…)` must return:

- **Topic root** (`topic_id` from `get_chat_info`, e.g. `14194`) → entire topic thread.
- **Message inside a topic** (e.g. `67599` in topic `14194`) → direct or nested replies to that message.

Telegram **`messages.getReplies`** (`iter_messages(..., reply_to=id)`) accepts **forum topic ids** only. For a normal in-topic message id it returns `TOPIC_ID_INVALID` (verified: `reply_to_id=67599`).

## Three paths (routing)

| Anchor type | Detection | API | Why |
|-------------|-----------|-----|-----|
| **Topic root** | `GetForumTopicsByID` returns topic with `title` or `top_message` (reject empty stubs) | `iter_messages(reply_to=topic_id)` → GetReplies | Native API for whole topic; returns nested `reply_to_msg_id` chains. |
| **In-topic message** | Not a valid topic id; message lives in a topic | `messages.search` + `top_msg_id` + filter `reply_to_msg_id` | Only way to list replies to a non-topic message id. |
| **Supergroup thread** (`thread_scope=full`, non-forum) | Not forum | `SearchRequest(top_msg_id=anchor)` | Documented Telethon pattern for non-forum threads. |

**Do not route on `reply_to_top_id` alone.** Anchors like `67596` have `reply_to_top_id=None` but `forum_topic=True` and `reply_to_msg_id=14194` — they are in-topic, not topic roots. `GetForumTopicsByID(67596)` returns a **stub** topic (no title / no `top_message`); treating that as topic root caused GetReplies failures.

**RPC save:** If `reply_to_top_id` is set on the anchor, skip `GetForumTopicsByID` — already known in-topic.

## Id ordering (substantiated)

In one chat, message ids increase with send time. A **new** reply to anchor `A` was sent after `A` exists → reply id **always** `> A` (e.g. `67616` replies to `67599`). This is causal order, not a heuristic.

## What we tried and rejected: min_id / max_id bracket

**Hypothesis:** `SearchRequest(top_msg_id=topic, min_id=anchor, max_id=anchor+window)` limits search to a local id range (1 RPC).

**Spike @telemtrs / topic 14194 / anchor 67599:**

| Params | Result |
|--------|--------|
| `min_id=67599`, `max_id=67799`, `top_msg_id=14194` | **0 messages** |
| `min_id=67599`, `max_id=0` | **Ignored** — same newest batch as no bounds (`84516+`) |
| `max_id=67799` only | **0 messages** |

**Conclusion:** With `top_msg_id` set, forum search does **not** honor `min_id`/`max_id` as a local window. Do not implement bracket search.

## Validated approach: offset_id jump

**Semantics:** Forum `messages.search` with `top_msg_id` returns messages with id **≤ offset_id**, newest first. Pagination: `offset_id = last_message.id` goes **older**.

Direct replies to anchor `A` lie in id range **`(A, offset_id]`**. First jump must use **`offset_id` strictly above the highest reply id**:

- `offset_id=67600`, anchor `67599` → batch omits reply `67616` → **miss**
- `offset_id=67619` (anchor+20) → batch includes `67616` → **hit**
- `offset_id=67699` (anchor+100) → **hit** in **1 RPC** (vs **11 RPCs** from `offset_id=0`)

**Defaults (tune in code):**

- `offset_id = anchor + 100` for `direct` / `auto`
- `offset_id = anchor + 500` for `thread_scope=full` (wider neighborhood for BFS)
- **Widen on zero matches:** `offset_id = anchor + 200`, `+2000`, `+20000`
- **Last resort:** legacy scan `offset_id=0` (current behavior)

Far replies (`reply_id >> anchor + 20000`) may still need legacy scan — rare; document in Tools-Reference.

## Search stubs, metadata, and reload

`messages.search` often returns **stubs**:

- Body may be in **`.message`**, not **`.text`** — use `message_has_displayable_content()` in `message_format.py` (shared by build + reload) or replies match but return empty (`67599` / `67616`, 2026-05-18).
- **`reply_to`** may be missing or wrong (e.g. `reply_to_msg_id` set to **topic root** instead of anchor) — `_enrich_forum_search_reply_metadata` reloads candidates above the anchor; **id-window fallback** loads `(anchor, anchor+margin]` via `get_messages` when filter still misses.

After filtering, **conditional reload** via `get_messages(ids=…)` only for stubs without displayable content.

## thread_scope (in-topic anchor)

| Scope | In-topic behavior |
|-------|-------------------|
| `direct` / `auto` | Direct replies only: `reply_to_msg_id == anchor` |
| `full` | BFS nested replies among messages from offset jump + widen — **branch under anchor**, not the whole forum topic |

**Whole topic** (all messages + deep chains): `reply_to_id` = **`topic_id`** from `get_chat_info` (e.g. `12799`), `thread_scope=auto` → GetReplies.

**Example** @telemtrs `12799/13204`: anchor `13204` → `auto` returns `13208`, `13209`, `13230`; `full` also returns nested replies (e.g. `13211`→`13209`, `13236`→`13230`). Topic `12799` returns the full topic thread.

## Topic id resolution

- Prefer `reply_to.reply_to_top_id` on the anchor.
- If `topic_id` from metadata equals parent message id (not a real topic), resolve via parent message or `GetForumTopicsByID` (skip `ForumTopicDeleted`).

## Code comment requirement

When editing `_collect_forum_anchor_replies` / `_fetch_replies`, keep a short comment block pointing to this file and stating: GetReplies vs search, offset jump vs min_id rejection, stub reload.

## Status (2026-05-18)

- **Shipped in `src/tools/search/`** (`forum_replies.py`, `replies.py`): Offset jump + widen + legacy scan; routing (GetReplies vs in-topic); enrich + id-window fallback; `message_has_displayable_content` for search stubs; `GetForumTopicsByID` skipped when `reply_to_top_id` set; `ForumTopicDeleted` not treated as topic root.
- **Validated:** `67599`→`67616`, `13204` in topic `12799` (direct + nested `full`).
