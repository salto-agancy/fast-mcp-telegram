"""Smoke test for find_chats benchmark scenarios.

Validates that the benchmark scenarios and find_chats API work.
Not a real benchmark — 1 iteration, no timing measurement.
Run with: pytest -m integration -k test_benchmark_find_chats
"""

import pytest

from src.client.connection import get_connected_client
from src.tools.chat_discovery.find_chats import find_chats_impl


FIND_CHATS_SMOKES = [
    pytest.param({"query": "alexey", "limit": 10}, id="global_single"),
    pytest.param({"query": "alexey,test,bot", "limit": 10}, id="global_multi"),
    pytest.param(
        {"query": None, "limit": 10, "folder": "Без каналов"},
        id="folder_flags_no_date",
    ),
    pytest.param(
        {"query": None, "limit": 10, "folder": "Без каналов", "min_date": "2024-01-01"},
        id="folder_flags_date",
    ),
    pytest.param(
        {"query": None, "limit": 10, "min_date": "2024-01-01"},
        id="date_browse",
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize("kwargs", FIND_CHATS_SMOKES)
async def test_benchmark_find_chats_smoke(kwargs):
    """Quick smoke test: 1 iteration per find_chats scenario.

    Covers all 4 code paths:
      global_single/multi     → global search (no iter_dialogs)
      folder_flags_no_date    → flags filter, no date (iter_dialogs + _filter_matches_flags)
      folder_flags_date       → flags filter + date (iter_dialogs + peer_dl_date fix from v0.28.2)
      date_browse             → date filter, no query (iter_dialogs + GetPeerDialogsRequest fallback)
    """
    client = await get_connected_client()
    me = await client.get_me()

    result = await find_chats_impl(**kwargs)

    assert "error" not in result or not result.get("error"), (
        f"Scenario {kwargs} failed: {result.get('error')}"
    )
    assert "chats" in result
