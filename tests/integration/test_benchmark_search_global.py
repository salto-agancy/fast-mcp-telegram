"""Smoke test for search_global benchmark scenarios.

Validates that the benchmark scenarios and search_messages API work.
Not a real benchmark — 1 iteration, no timing measurement.
Run with: pytest -m integration -k test_benchmark_search
"""

import pytest

from src.client.connection import get_connected_client
from src.tools.search import search_messages_impl


SEARCH_SMOKES = [
    pytest.param({"query": "недвижимость", "limit": 10}, id="single_term"),
    pytest.param(
        {"query": "недвижимость, инвестиции", "limit": 10}, id="two_terms"
    ),
]


@pytest.mark.integration
@pytest.mark.parametrize("kwargs", SEARCH_SMOKES)
async def test_benchmark_search_smoke(kwargs):
    """Quick smoke test: 1 iteration per search scenario.

    Tests single and multi-term search. More detailed scenarios (dedup,
    fairness, flood wait handling) are tested exclusively via the
    standalone benchmark CLI
    (uv run python3 tests/integration/benchmark_search_global.py).
    """
    client = await get_connected_client()

    result = await search_messages_impl(**kwargs)

    assert "error" not in result or not result.get("error"), (
        f"Scenario {kwargs} failed: {result.get('error')}"
    )
    assert "messages" in result
