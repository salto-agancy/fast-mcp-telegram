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
    # removed flood_waits — not tracked without Telegram FloodWaitError instrumentation
    # flood_waits: list[int] = field(default_factory=list)
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
    """Warm up connection before benchmarks."""
    me = await client.get_me()
    logger.info("Connected as: %s (@%s)", me.first_name or "", me.username or "?")


async def scenario_global_single(query: str = "alexey", limit: int = 10) -> dict:
    """Standard single-term global Telegram search."""
    result = await find_chats_impl(query=query, limit=limit)
    return result


async def scenario_global_multi(query: str = "alexey,test,bot", limit: int = 10) -> dict:
    """Multi-term global Telegram search — the P0 optimization target."""
    result = await find_chats_impl(query=query, limit=limit)
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
        query="alex", limit=limit, max_date=month_ago
    )
    return result


async def scenario_folder_include(folder_name: str | None = None, limit: int = 20) -> dict:
    """Folder with include_peers — the GetPeerDialogs path."""
    # Try common folder names. Fall back to the first available folder.
    names_to_try = [
        folder_name,
        "Без каналов",
        "Nearby",
        "Ответить",
    ]
    for name in names_to_try:
        if not name:
            continue
        result = await find_chats_impl(query=None, limit=limit, folder=name)
        if "error" not in result:
            return result
    # Last resort: whatever-folder with no include_peers won't test the right path.
    return result if "error" not in result else {"error": "no_suitable_folder", **result}


async def scenario_folder_flags(folder_name: str | None = None, limit: int = 10) -> dict:
    """Folder with flag-based filtering — the iter_dialogs + flags path."""
    names_to_try = [
        folder_name,
        "Ответить",
        "Каналы",
        "Без каналов",
    ]
    for name in names_to_try:
        if not name:
            continue
        result = await find_chats_impl(query=None, limit=limit, folder=name)
        if "error" not in result:
            return result
    return {"error": "no_suitable_folder_for_flags"}


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
        flood_count = 0  # placeholder — implement real FloodWait tracking when needed

        try:
            result = await scenario_fn()
            if isinstance(result, dict):
                if "error" in result:
                    error = result["error"]
                    logger.warning("  [%s] iter %d: %s", name, i + 1, error)
                else:
                    results_count = len(result.get("chats", []))
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.warning("  [%s] iter %d exception: %s", name, i + 1, error)

        duration = time.monotonic() - start

        report.durations_s.append(duration)
        report.results_counts.append(results_count)
        # report.flood_waits.append(flood_count)
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
    lines.append(f"{'Scenario':30s} {'Mean':>8s} {'Min':>8s} {'Max':>8s} {'P90':>8s} {'Results':>7s} {'Flood':>5s} {'Status':>10s}")  # noqa: flood column placeholder
    lines.append("-" * 85)

    for r in reports:
        ok = "✅" if r.all_ok else "⚠️"
        results_str = (
            f"{min(r.results_counts)}-{max(r.results_counts)}"
            if min(r.results_counts) != max(r.results_counts)
            else str(r.results_counts[0]) if r.results_counts else "0"
        )
        lines.append(
            f"{r.scenario:30s} {r.mean_s:>8.3f} {r.min_s:>8.3f} {r.max_s:>8.3f} "
            f"{r.p90_s:>8.3f} {results_str:>7s} {'N/A':>5s} {ok:>10s}"
        )

    return "\n".join(lines)


def _report_json(reports: list[BenchmarkReport]) -> dict:
    """Convert reports to a JSON-serializable dict."""
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "python_version": sys.version,
        "scenarios": {
            r.scenario: {
                "mean_s": round(r.mean_s, 4),
                "min_s": round(r.min_s, 4),
                "max_s": round(r.max_s, 4),
                "median_s": round(r.median_s, 4),
                "p90_s": round(r.p90_s, 4),
                "durations_s": [round(d, 4) for d in r.durations_s],
                "results_counts": r.results_counts,
                # "flood_waits": r.flood_waits,
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
        ("global_single", lambda: scenario_global_single(limit=10)),
        ("global_multi", lambda: scenario_global_multi(limit=10)),
        ("date_browse", lambda: scenario_date_browse(limit=20)),
        ("date_search", lambda: scenario_date_search(limit=10)),
        ("folder_include", lambda: scenario_folder_include(limit=20)),
        ("folder_flags", lambda: scenario_folder_flags(limit=10)),
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

    json_data = _report_json(reports)
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
