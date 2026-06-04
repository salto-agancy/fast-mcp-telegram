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
]


@pytest.mark.integration
@pytest.mark.parametrize("kwargs", FIND_CHATS_SMOKES)
async def test_benchmark_find_chats_smoke(kwargs):
    """Quick smoke test: 1 iteration per find_chats scenario.

    Tests at least the global branches of find_chats. Folder/date scenarios
    are environment-dependent and tested exclusively via the standalone
    benchmark CLI (uv run python3 tests/integration/benchmark_find_chats.py).
    """
    client = await get_connected_client()
    me = await client.get_me()

    result = await find_chats_impl(**kwargs)

    assert "error" not in result or not result.get("error"), (
        f"Scenario {kwargs} failed: {result.get('error')}"
    )
    assert "chats" in result
    assert len(result["chats"]) > 0, (
        f"Scenario {kwargs} returned empty chats list"
    )
