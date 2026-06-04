"""
Parametrized pytest smoke tests for all benchmark scenarios.

Every scenario from ``scenarios.py`` is parametrized into a single test
function.  Run with::

    pytest -m integration -k bench
    pytest -m integration -k bench_scenario_folder
    pytest -m integration -k 'bench_scenario and (folder_flags or global_single)'

This replaces the old per-scenario test files.
"""

import sys
from pathlib import Path

import pytest

# ── Ensure src is importable ──────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# ── Protect sys.argv from fastmcp's import-time arg parsing ──────────────
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0] if sys.argv else "test_bench_scenarios"]
try:
    from src.tools.chat_discovery.find_chats import find_chats_impl
    from src.tools.search import search_messages_impl
    from tests.integration.scenarios import get_scenarios
finally:
    sys.argv = _saved_argv


# ── Build parametrized cases ──────────────────────────────────────────────

_IMPL_MAP: dict[str, callable] = {
    "find_chats": find_chats_impl,
    "search_messages": search_messages_impl,
}

_SCENARIO_CASES = []
for scenario in get_scenarios():
    _SCENARIO_CASES.append(
        pytest.param(
            scenario.name,
            _IMPL_MAP[scenario.tool],
            scenario.params,
            id=scenario.name,
        )
    )


# ── Test ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize(
    "scenario_name,impl_func,params",
    _SCENARIO_CASES,
)
async def test_bench_scenario(telethon_client, scenario_name, impl_func, params):
    """Execute one benchmark scenario as a quick smoke test.

    Verifies that the implementation returns a dict without errors.
    Timing is not measured here — use the standalone CLI (``run_bench.py``)
    for timing benchmarks.
    """
    result = await impl_func(telethon_client, **params)
    assert isinstance(result, dict), (
        f"[{scenario_name}] Expected dict, got {type(result).__name__}"
    )
    assert "error" not in result or not result.get("error"), (
        f"[{scenario_name}] Failed: {result.get('error')}"
    )
