"""Shared numeric limits for chat discovery and folder filtering."""

FLAG_MATCH_MAX_DIALOGS = 500
# messages.getPeerDialogs: conservative batch size; raising requires checking current layer input limits.
GET_PEER_DIALOGS_CHUNK_SIZE = 50
# Per-chunk timeout for GetPeerDialogsRequest.
# Some entities (stale access_hash, deleted accounts) cause Telegram to hang ~30s.
# With timeout we skip the chunk and fall back to per-entity iter_messages (~90ms each).
GET_PEER_DIALOGS_TIMEOUT = 3.0
# Parallel get_entity for include/exclude resolution (semaphore limit).
GET_ENTITY_CONCURRENCY = 8
AVAILABLE_FILTERS_MAX_SHOW = 10
