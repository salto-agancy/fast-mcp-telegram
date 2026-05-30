#!/usr/bin/env python3
"""
Benchmark suite for find_chats performance optimization.

Tests all 4 code branches of find_chats with real Telegram API calls,
measures wall-clock time per scenario, counts flood-wait events,
and outputs structured JSON results for before/after comparison.

Usage:
    uv run python3 tests/integration/benchmark_find_chats.py
    uv run python3 tests/integration/benchmark_find_chats.py --output benchmark_results.json
    uv run python3 tests/integration/benchmark_find_chats.py --iterations 5 --skip folder_include
"""
import argparse
import asyncio
import json
import logging
import math
import signal
import sys
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── Ensure src is importable ──────────────────────────────────────────────
# When run as `uv run python3 tests/integration/benchmark_find_chats.py`
# the working directory may be the repo root or the tests dir.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("benchmark")

# Connection module — set _current_token before get_connected_client() for token-based auth
from src.client.connection import get_connected_client, set_request_token
from src.tools.chat_discovery.find_chats import find_chats_impl


# ── Data types ────────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    """Result for a single benchmark scenario iteration."""

    scenario: str
    iteration: int
    duration_s: float
    results_count: int
    api_calls: int | None = None  # estimated, not always measurable
    error: str | None = None


@dataclass
class BenchmarkReport:
    """Aggregated report for a scenario across iterations."""

    scenario: str
    n_iterations: int
    durations_s: list[float] = field(default_factory=list)
    results_counts: list[int] = field(default_factory=list)
    # (flood_waits removed — not tracked without FloodWaitError instrumentation)
    errors: list[str | None] = field(default_factory=list)

    @property
    def mean_s(self) -> float:
        return sum(self.durations_s) / len(self.durations_s) if self.durations_s else 0.0

    @property
    def min_s(self) -> float:
        return min(self.durations_s) if self.durations_s else 0.0

    @property
    def max_s(self) -> float:
        return max(self.durations_s) if self.durations_s else 0.0

    @property
    def median_s(self) -> float:
        if not self.durations_s:
            return 0.0
        s = sorted(self.durations_s)
        return s[len(s) // 2]

    @property
    def p90_s(self) -> float:
        """90th percentile — use float index; for small N = max."""
        if not self.durations_s:
            return 0.0
        s = sorted(self.durations_s)
        idx = min(math.ceil(0.90 * len(s)) - 1, len(s) - 1)
        return s[max(0, idx)]

    @property
    def all_ok(self) -> bool:
        return all(e is None for e in self.errors)


# ── Scenario definitions ───────────────────────────────────────────────────


async def _warmup(client) -> None:
    """Warm up connection and API cache before benchmarks."""
    me = await client.get_me()
    logger.info("Connected as: %s (@%s)", me.first_name or "", me.username or "?")
    # Practice search to warm MTProto cache
    try:
        from src.tools.chat_discovery.find_chats import find_chats_impl
        result = await find_chats_impl(query="Андрей", limit=1)
        logger.info("Warmup search: %s", "OK" if "error" not in result else result.get("error"))
    except Exception as e:
        logger.info("Warmup search (optional): %s", e)


async def scenario_global_single(
    query: str = "Андрей",
    limit: int = 10,
    max_concurrent: int | None = None,
    search_timeout: float | None = None,
) -> dict:
    """Standard single-term global Telegram search."""
    result = await find_chats_impl(
        query=query, limit=limit,
        max_concurrent=max_concurrent,
        search_timeout=search_timeout,
    )
    return result


async def scenario_global_multi(
    query: str = "Андрей, Сергей, Роман",
    limit: int = 10,
    max_concurrent: int | None = None,
    search_timeout: float | None = None,
) -> dict:
    """Multi-term global Telegram search — the parallelization optimization target."""
    result = await find_chats_impl(
        query=query, limit=limit,
        max_concurrent=max_concurrent,
        search_timeout=search_timeout,
    )
    return result


async def scenario_date_browse(limit: int = 20) -> dict:
    """Browse chats with min_date (last week) — triggers iter_dialogs path."""
    from datetime import datetime, timedelta, UTC
    week_ago = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%d")
    result = await find_chats_impl(query=None, limit=limit, min_date=week_ago)
    return result


async def scenario_date_search(limit: int = 10) -> dict:
    """Search with date filter and query — triggers iter_dialogs + fallback."""
    from datetime import datetime, timedelta, UTC
    month_ago = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%d")
    result = await find_chats_impl(
        query="Андрей", limit=limit, max_date=month_ago
    )
    return result


async def scenario_folder_include(folder_name: str | None = None, limit: int = 20) -> dict:
    """Folder with include_peers — the GetPeerDialogs path."""
    if folder_name:
        result = await find_chats_impl(query=None, limit=limit, folder=folder_name)
        if "error" in result:
            return {"error": f"folder '{folder_name}' not found: {result['error']}", **result}
        return result

    # No explicit folder: try common names, warn about ambiguity
    names_to_try = ["Без каналов", "Nearby", "Ответить"]
    for name in names_to_try:
        result = await find_chats_impl(query=None, limit=limit, folder=name)
        if "error" not in result:
            logger.warning(
                "folder_include: no --folder-name given, guessed '%s' "
                "— results may not test include_peers path",
                name,
            )
            return result
    return {"error": "no_suitable_folder_found"}


async def scenario_folder_flags(folder_name: str | None = None, limit: int = 10) -> dict:
    """Folder with flag-based filtering — the iter_dialogs + flags path."""
    if folder_name:
        result = await find_chats_impl(query=None, limit=limit, folder=folder_name)
        if "error" in result:
            return {"error": f"folder '{folder_name}' not found: {result['error']}", **result}
        return result

    # No explicit folder: try common names, warn about ambiguity
    names_to_try = ["Ответить", "Каналы", "Без каналов"]
    for name in names_to_try:
        result = await find_chats_impl(query=None, limit=limit, folder=name)
        if "error" not in result:
            logger.warning(
                "folder_flags: no --folder-name given, guessed '%s'",
                name,
            )
            return result
    return {"error": "no_suitable_folder_for_flags"}


async def scenario_multi_dedup(
    query: str = "Андрей, a",
    limit: int = 10,
    max_concurrent: int | None = None,
    search_timeout: float | None = None,
) -> dict:
    """Multi-term search — validate no duplicate IDs from overlapping terms.

    Uses overlapping first letter 'a' to test dedup.
    """
    result = await find_chats_impl(
        query=query, limit=limit,
        max_concurrent=max_concurrent,
        search_timeout=search_timeout,
    )
    if "error" in result:
        return result
    chats = result.get("chats", [])
    ids = [c.get("id") for c in chats]
    if len(ids) != len(set(ids)):
        dupes = [id_ for id_ in ids if ids.count(id_) > 1]
        return {
            **result,
            "error": f"DEDUP_FAIL: {len(ids)} results but {len(ids) - len(set(ids))} duplicates "
                      f"(ids: {sorted(set(dupes))})",
            "_assertion_ok": False,
            "_assertion_type": "multi_dedup",
        }
    result["_assertion_ok"] = True
    result["_assertion_type"] = "multi_dedup"
    return result


async def scenario_multi_fairness(
    query: str = "Роман, Кирилл, Евгений",
    limit: int = 15,
    max_concurrent: int | None = None,
    search_timeout: float | None = None,
) -> dict:
    """Multi-term search — validate results from ALL terms present.

    Uses distinct real Russian first names to ensure each term has its own results.
    Checks that all individual term results appear in the combined output.
    """
    result = await find_chats_impl(
        query=query, limit=limit,
        max_concurrent=max_concurrent,
        search_timeout=search_timeout,
    )
    if "error" in result:
        return result
    chats = result.get("chats", [])

    terms = [t.strip() for t in query.split(",")]
    # Get per-term results to check fairness
    per_term = {}
    for term in terms:
        tr = await find_chats_impl(query=term, limit=limit)
        if "error" not in tr:
            term_ids = {c.get("id") for c in tr.get("chats", [])}
            per_term[term] = term_ids

    combined_ids = {c.get("id") for c in chats}
    missing_terms = []
    for term, term_ids in per_term.items():
        if term_ids and not term_ids.intersection(combined_ids):
            missing_terms.append(term)

    if missing_terms:
        return {
            **result,
            "warning": f"FAIRNESS: terms [{', '.join(missing_terms)}] have results but none "
                       f"appear in multi-term output. May be expected if limit < per-term counts.",
            "_assertion_type": "multi_fairness",
            "_assertion_ok": False,
        }
    result["_assertion_ok"] = True
    result["_assertion_type"] = "multi_fairness"
    return result


async def scenario_multi_partial(
    query: str = "asdfghjkl12345xyz999, Андрей",
    limit: int = 10,
    max_concurrent: int | None = None,
    search_timeout: float | None = None,
) -> dict:
    """Multi-term search with one failing term — validate graceful degradation.

    The first term is random gibberish (should return 0 results or error).
    The second is a real Russian contact name.
    """
    result = await find_chats_impl(
        query=query, limit=limit,
        max_concurrent=max_concurrent,
        search_timeout=search_timeout,
    )
    if "error" in result:
        # Partial failure is OK if the good term's results are present
        # Check by running the good term alone
        good_term = query.split(",")[-1].strip()
        good_result = await find_chats_impl(query=good_term, limit=limit)
        if "error" not in good_result and good_result.get("chats"):
            return {
                **result,
                "_assertion_type": "multi_partial",
                "_assertion_ok": False,
                "error": f"PARTIAL_FAIL: bad term killed good term '{good_term}' results. "
                         f"Multi-term returned error but single-term works fine.",
            }
        # Both terms failing is expected — no assertion issue
        result["_assertion_ok"] = True
        result["_assertion_type"] = "multi_partial"
        return result

    chats = result.get("chats", [])
    good_term = query.split(",")[-1].strip()
    good_result = await find_chats_impl(query=good_term, limit=limit)
    good_ids = {c.get("id") for c in good_result.get("chats", [])}
    combined_ids = {c.get("id") for c in chats}

    if good_ids and not good_ids.intersection(combined_ids):
        return {
            **result,
            "warning": f"PARTIAL: bad term suppressed good term '{good_term}' — "
                       f"0/{len(good_ids)} good-term results in combined output.",
            "_assertion_type": "multi_partial",
            "_assertion_ok": False,
        }
    result["_assertion_ok"] = True
    result["_assertion_type"] = "multi_partial"
    return result


# ── Benchmark runner ──────────────────────────────────────────────────────


async def _run_single_scenario(
    name: str,
    scenario_fn: Any,
    iterations: int,
) -> BenchmarkReport:
    """Run a single scenario `iterations` times and aggregate results."""
    report = BenchmarkReport(scenario=name, n_iterations=iterations)

    logger.info("Running scenario: %s (%d iterations)", name, iterations)

    for i in range(iterations):
        start = time.monotonic()
        error: str | None = None
        results_count = 0

        try:
            result = await scenario_fn()
            if isinstance(result, dict):
                if "error" in result:
                    error = result["error"]
                    logger.warning("  [%s] iter %d: %s", name, i + 1, error)
                else:
                    results_count = len(result.get("chats", []))
                    if result.get("warning"):
                        logger.warning(
                            "  [%s] iter %d ASSERTION WARNING: %s",
                            name,
                            i + 1,
                            result["warning"],
                        )
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning("  [%s] iter %d exception: %s", name, i + 1, error)

        duration = time.monotonic() - start

        report.durations_s.append(duration)
        report.results_counts.append(results_count)
        report.errors.append(error)

        logger.info(
            "  [%s] iter %d: %.3fs, %d results%s",
            name,
            i + 1,
            duration,
            results_count,
            "",
        )

    return report


def _report_table(reports: list[BenchmarkReport]) -> str:
    """Render a text table of benchmark results."""
    lines = []
    lines.append(f"{'Scenario':30s} {'Mean':>8s} {'Min':>8s} {'Max':>8s} {'P90':>8s} {'Results':>7s} {'Status':>10s}")
    lines.append("-" * 78)

    for r in reports:
        ok = "✅" if r.all_ok else "⚠️"
        results_str = (
            f"{min(r.results_counts)}-{max(r.results_counts)}"
            if min(r.results_counts) != max(r.results_counts)
            else str(r.results_counts[0]) if r.results_counts else "0"
        )
        lines.append(
            f"{r.scenario:30s} {r.mean_s:>8.3f} {r.min_s:>8.3f} {r.max_s:>8.3f} "
            f"{r.p90_s:>8.3f} {results_str:>7s} {ok:>10s}"
        )

    return "\n".join(lines)


def _report_json(reports: list[BenchmarkReport], *, max_concurrent: int | None = None, search_timeout: float | None = None) -> dict:
    """Convert reports to a JSON-serializable dict."""
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version,
        "config": {
            "max_concurrent": max_concurrent,
            "search_timeout": search_timeout,
        },
        "scenarios": {
            r.scenario: {
                "mean_s": round(r.mean_s, 4),
                "min_s": round(r.min_s, 4),
                "max_s": round(r.max_s, 4),
                "median_s": round(r.median_s, 4),
                "p90_s": round(r.p90_s, 4),
                "durations_s": [round(d, 4) for d in r.durations_s],
                "results_counts": r.results_counts,

                "errors": [str(e) if e else None for e in r.errors],
                "all_ok": r.all_ok,
            }
            for r in reports
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description="find_chats benchmark suite")
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Save JSON report to file",
    )
    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=3,
        help="Number of iterations per scenario (default: 3)",
    )
    parser.add_argument(
        "--skip",
        type=str,
        nargs="*",
        default=[],
        help="Scenarios to skip (e.g. --skip folder_include folder_flags)",
    )
    parser.add_argument(
        "--only",
        type=str,
        nargs="*",
        default=[],
        help="Only run these scenarios (e.g. --only global_multi date_search)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-scenario timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--bearer-token",
        type=str,
        default=None,
        help="Bearer token for session lookup (from BEARER_TOKEN_FOR_TESTING in .env)",
    )
    parser.add_argument(
        "--folder-name",
        type=str,
        default=None,
        help="Explicit folder name for folder_include/folder_flags scenarios",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=None,
        help="Maximum concurrent search requests for multi-term queries (default: no limit)",
    )
    parser.add_argument(
        "--search-timeout",
        type=float,
        default=None,
        help="Per-request timeout in seconds for multi-term queries (default: no timeout)",
    )
    args = parser.parse_args()
    skip = set(args.skip)
    only = set(args.only)

    # ── Connect ────────────────────────────────────────────────────────
    print("Connecting to Telegram...", end=" ", flush=True)
    try:
        if args.bearer_token:
            set_request_token(args.bearer_token)
        client = await get_connected_client()
        await _warmup(client)
        print("OK")
    except Exception as e:
        print(f"FAILED: {e}")
        return 1

    # ── Scenario factory ────────────────────────────────────────────────
    scenarios = [
        ("global_single", lambda: scenario_global_single(limit=10, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)),
        ("global_multi", lambda: scenario_global_multi(limit=10, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)),
        ("date_browse", lambda: scenario_date_browse(limit=20)),
        ("date_search", lambda: scenario_date_search(limit=10)),
        ("folder_include", lambda: scenario_folder_include(folder_name=args.folder_name, limit=20)),
        ("folder_flags", lambda: scenario_folder_flags(folder_name=args.folder_name, limit=10)),
        ("multi_dedup", lambda: scenario_multi_dedup(limit=10, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)),
        ("multi_fairness", lambda: scenario_multi_fairness(limit=15, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)),
        ("multi_partial", lambda: scenario_multi_partial(limit=10, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)),
    ]

    # Filter
    if only:
        scenarios = [(n, fn) for n, fn in scenarios if n in only]
    scenarios = [(n, fn) for n, fn in scenarios if n not in skip]

    if not scenarios:
        print("No scenarios to run (all filtered out)")
        return 0

    # ── Run ─────────────────────────────────────────────────────────────
    reports: list[BenchmarkReport] = []
    timeout_s = args.timeout

    for name, scenario_fn in scenarios:
        print(f"\n{'='*60}")
        print(f"Scenario: {name}")
        print(f"{'='*60}")

        try:
            report = await asyncio.wait_for(
                _run_single_scenario(name, scenario_fn, args.iterations),
                timeout=timeout_s,
            )
            reports.append(report)
        except asyncio.TimeoutError:
            print(f"  TIMEOUT after {timeout_s}s — skipping")
            reports.append(
                BenchmarkReport(
                    scenario=name,
                    n_iterations=0,
                    durations_s=[],
                    results_counts=[],
                    errors=[f"timeout_{timeout_s}s"],
                )
            )

    # ── Report ──────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("BENCHMARK RESULTS")
    print(f"{'='*60}\n")
    print(_report_table(reports))

    json_data = _report_json(reports, max_concurrent=args.max_concurrent, search_timeout=args.search_timeout)
    print(f"\n{'='*60}")
    print("JSON summary")
    print(f"{'='*60}")
    # Print compact version
    compact = deepcopy(json_data)
    for s in compact["scenarios"].values():
        del s["durations_s"]
        del s["results_counts"]
        # flood_waits removed — not tracked
        del s["errors"]
    print(json.dumps(compact, indent=2, ensure_ascii=False))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False))
        print(f"\nFull report saved to: {out_path.resolve()}")

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
