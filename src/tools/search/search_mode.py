"""Query and browse mode for get_messages (MessageRetrievalMode.SEARCH)."""

import logging
from datetime import datetime
from typing import Any

from src.client.connection import get_connected_client
from src.utils.datetime_parse import parse_iso_datetime_utc
from src.utils.entity import _get_chat_message_count, get_entity_by_id
from src.utils.error_handling import log_and_build_error, log_connection_error_response
from src.utils.helpers import _append_dedup_until_limit
from src.utils.message_format import transcribe_voice_messages

from .search_generators import (
    _search_chat_messages_generator,
    _search_global_messages_generator,
)

logger = logging.getLogger(__name__)


async def _execute_parallel_searches_generators(
    generators: list, collected: list[dict[str, Any]], seen_keys: set, limit: int
) -> None:
    """Round-robin parallel generators; collect limit+1 for has_more."""
    active_gens = list(enumerate(generators))
    target_limit = limit + 1

    while active_gens and len(collected) < target_limit:
        next_active = []

        for i, gen in active_gens:
            try:
                result = await gen.__anext__()
                _append_dedup_until_limit(collected, seen_keys, [result], target_limit)
                if len(collected) >= target_limit:
                    break
                next_active.append((i, gen))
            except StopAsyncIteration:
                continue
            except Exception as e:
                logger.warning(f"Error in search generator {i}: {e}")
                continue

        active_gens = next_active


async def _collect_messages_in_chat(
    client,
    chat_id: str,
    queries: list[str],
    limit: int,
    min_datetime: datetime | None,
    max_datetime: datetime | None,
    chat_type: str | None,
    public: bool | None,
    auto_expand_batches: int,
    include_total_count: bool,
    collected: list[dict[str, Any]],
    seen_keys: set[Any],
    include_chat_entity: bool = False,
) -> int | None:
    entity = await get_entity_by_id(chat_id)
    if not entity:
        raise ValueError(f"Could not find chat with ID '{chat_id}'")
    per_chat_queries = queries or [""]
    generators = [
        _search_chat_messages_generator(
            client,
            entity,
            (q or ""),
            limit,
            min_datetime,
            max_datetime,
            chat_type,
            public,
            auto_expand_batches,
            include_chat_entity,
        )
        for q in per_chat_queries
    ]
    await _execute_parallel_searches_generators(generators, collected, seen_keys, limit)
    await transcribe_voice_messages(collected, entity, client=client)
    return await _get_chat_message_count(chat_id) if include_total_count else None


async def _collect_messages_global(
    client,
    queries: list[str],
    limit: int,
    min_datetime: datetime | None,
    max_datetime: datetime | None,
    chat_type: str | None,
    public: bool | None,
    auto_expand_batches: int,
    collected: list[dict[str, Any]],
    seen_keys: set[Any],
    include_chat_entity: bool = True,
) -> None:
    generators = [
        _search_global_messages_generator(
            client,
            q,
            limit,
            min_datetime,
            max_datetime,
            chat_type,
            public,
            auto_expand_batches,
            include_chat_entity,
        )
        for q in queries
        if q and str(q).strip()
    ]
    await _execute_parallel_searches_generators(generators, collected, seen_keys, limit)


async def _handle_query_mode(
    *,
    query: str | None,
    chat_id: str | None,
    limit: int,
    min_date: str | None,
    max_date: str | None,
    chat_type: str | None,
    public: bool | None,
    auto_expand_batches: int,
    include_total_count: bool,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Handle search/browse mode for messages (MessageRetrievalMode.SEARCH)."""
    queries: list[str] = (
        [q.strip() for q in query.split(",") if q.strip()] if query else []
    )

    if not chat_id and not queries:
        return log_and_build_error(
            operation="get_messages",
            error_message="Search query must not be empty for global search",
            params=params,
            exception=ValueError("Search query must not be empty for global search"),
        )

    min_datetime = parse_iso_datetime_utc(min_date) if min_date else None
    if min_date and min_datetime is None:
        return log_and_build_error(
            operation="get_messages",
            error_message=(
                f"Invalid min_date format: '{min_date}'. "
                "Use ISO format (e.g., '2024-01-01')"
            ),
            params=params,
            exception=ValueError(f"Invalid min_date format: '{min_date}'"),
        )

    max_datetime = parse_iso_datetime_utc(max_date) if max_date else None
    if max_date and max_datetime is None:
        return log_and_build_error(
            operation="get_messages",
            error_message=(
                f"Invalid max_date format: '{max_date}'. "
                "Use ISO format (e.g., '2024-12-31')"
            ),
            params=params,
            exception=ValueError(f"Invalid max_date format: '{max_date}'"),
        )

    def _connection_error_or_build(
        exc: Exception, fallback_message: str
    ) -> dict[str, Any]:
        if (
            r := log_connection_error_response("get_messages", params, exc)
        ) is not None:
            return r
        return log_and_build_error(
            operation="get_messages",
            error_message=fallback_message,
            params=params,
            exception=exc,
        )

    try:
        client = await get_connected_client()
        total_count = None
        collected: list[dict[str, Any]] = []
        seen_keys: set[Any] = set()

        if chat_id:
            try:
                total_count = await _collect_messages_in_chat(
                    client,
                    chat_id,
                    queries,
                    limit,
                    min_datetime,
                    max_datetime,
                    chat_type,
                    public,
                    auto_expand_batches,
                    include_total_count,
                    collected,
                    seen_keys,
                    include_chat_entity=False,
                )
            except Exception as e:
                return _connection_error_or_build(
                    e, f"Failed to search in chat '{chat_id}': {e!s}"
                )
        else:
            try:
                await _collect_messages_global(
                    client,
                    queries,
                    limit,
                    min_datetime,
                    max_datetime,
                    chat_type,
                    public,
                    auto_expand_batches,
                    collected,
                    seen_keys,
                    include_chat_entity=True,
                )
            except Exception as e:
                return _connection_error_or_build(
                    e, f"Failed to perform global search: {e!s}"
                )

        window = collected[:limit] if limit is not None else collected

        logger.info(f"Found {len(window)} messages matching query: {query}")

        has_more = len(collected) > len(window) or (
            len(collected) == limit and len(collected) > 0
        )

        if not window:
            q_nonempty = bool(query and query.strip())
            if chat_id and not q_nonempty:
                if min_date or max_date:
                    err = (
                        "No exportable messages found for the requested date range in this chat. "
                        "If Telegram shows recent dialog activity, it may be service-only "
                        "(e.g. pins, invites, title changes) now surfaced as [Service: …] rows."
                    )
                else:
                    err = "No exportable messages found in this chat."
            elif q_nonempty:
                err = f"No messages found matching query '{query}'"
            else:
                err = "No messages found for the given filters."

            return log_and_build_error(
                operation="get_messages",
                error_message=err,
                params=params,
                exception=ValueError(err),
            )

        response: dict[str, Any] = {"messages": window, "has_more": has_more}
        if total_count is not None:
            response["total_count"] = total_count
        return response

    except Exception as e:
        return _connection_error_or_build(e, f"Message retrieval failed: {e!s}")
