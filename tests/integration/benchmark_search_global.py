#!/usr/bin/env python3
"""
Benchmark suite for search_global and get_messages multi-term optimization.

Tests performance of comma-separated multi-term queries in both
global search (SearchGlobalRequest) and in-chat search (iter_messages).

The P0-style optimization replaces round-robin generator iteration
with asyncio.gather, measuring wall-clock time improvement.

Usage:
    uv run python3 tests/integration/benchmark_search_global.py
    uv run python3 tests/integration/benchmark_search_global.py --output results.json
    uv run python3 tests/integration/benchmark_search_global.py --iterations 5
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
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("benchmark_search")

from src.client.connection import get_connected_client, set_request_token
from src.tools.search import search_messages_impl


# ── Data types ────────────────────────────────────────────────────────────


@dataclass
class BenchmarkResult:
    scenario: str
    iteration: int
    duration_s: float
    results_count: int
    error: str | None = None


@dataclass
class BenchmarkReport:
    """Aggregated report for a scenario across iterations."""

    scenario: str
    n_iterations: int
    durations_s: list[float] = field(default_factory=list)
    results_counts: list[int] = field(default_factory=list)
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
        result = await search_messages_impl(query="test", limit=1)
        logger.info("Warmup search: %s", "OK" if "error" not in result else result.get("error"))
    except Exception as e:
        logger.info("Warmup search (optional): %s", e)


async def scenario_single_term(query: str = "alexey", limit: int = 10) -> dict:
    """Single-term global search — baseline (no parallel gain expected)."""
    result = await search_messages_impl(query=query, limit=limit)
    return result


async def scenario_two_terms(query: str = "alexey, test", limit: int = 10) -> dict:
    """Two-term global search — first multi-term case."""
    result = await search_messages_impl(query=query, limit=limit)
    return result


async def scenario_three_terms(query: str = "alexey, test, channel", limit: int = 10) -> dict:
    """Three-term global search — typical real usage."""
    result = await search_messages_impl(query=query, limit=limit)
    return result


async def scenario_five_terms(
    query: str = "alexey, test, channel, bot, group",
    limit: int = 10,
) -> dict:
    """Five-term global search — stress test."""
    result = await search_messages_impl(query=query, limit=limit)
    return result


async def scenario_three_terms_large(
    query: str = "alexey, test, channel",
    limit: int = 50,
) -> dict:
    """Three terms with larger limit."""
    result = await search_messages_impl(query=query, limit=limit)
    return result


async def scenario_dedup_check(
    query: str = "alexey, alex, alexander",
    limit: int = 15,
) -> dict:
    """Multi-term global search — validate no duplicate message IDs.

    Overlapping terms like "alexey", "alex", "alexander" may match
    the same messages. The merge MUST deduplicate by message ID.
    """
    result = await search_messages_impl(query=query, limit=limit)
    if "error" in result:
        return result
    messages = result.get("messages", [])
    ids = [m.get("id") for m in messages if m.get("id") is not None]
    if len(ids) != len(set(ids)):
        dupes = [id_ for id_ in ids if ids.count(id_) > 1]
        return {
            **result,
            "error": f"DEDUP_FAIL: {len(ids)} results but {len(ids) - len(set(ids))} "
                     f"duplicates (ids: {sorted(set(dupes))})",
            "_assertion_ok": False,
            "_assertion_type": "dedup_check",
        }
    result["_assertion_ok"] = True
    result["_assertion_type"] = "dedup_check"
    return result


async def scenario_fairness_check(
    query: str = "test, channel, bot",
    limit: int = 20,
) -> dict:
    """Multi-term global search — validate results from ALL terms.

    Each term in "test, channel, bot" should contribute results.
    We check by running each term alone and verifying overlap.
    """
    result = await search_messages_impl(query=query, limit=limit)
    if "error" in result:
        return result
    messages = result.get("messages", [])

    terms = [t.strip() for t in query.split(",")]
    per_term = {}
    for term in terms:
        tr = await search_messages_impl(query=term, limit=limit)
        if "error" not in tr:
            term_ids = {m.get("id") for m in tr.get("messages", [])}
            per_term[term] = term_ids

    combined_ids = {m.get("id") for m in messages if m.get("id") is not None}
    missing_terms = []
    for term, term_ids in per_term.items():
        if term_ids and not term_ids.intersection(combined_ids):
            missing_terms.append(term)

    if missing_terms:
        return {
            **result,
            "warning": f"FAIRNESS: terms [{', '.join(missing_terms)}] have results but "
                       f"none appear in multi-term output. Expected if limit < per-term counts.",
            "_assertion_type": "fairness_check",
            "_assertion_ok": False,
        }
    result["_assertion_ok"] = True
    result["_assertion_type"] = "fairness_check"
    return result


async def scenario_three_terms_large_limit(
    query: str = "alexey, test, channel",
    limit: int = 50,
) -> dict:
    """Three terms with large limit — stress both gather and merge."""
    result = await search_messages_impl(query=query, limit=limit)
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
                    results_count = len(result.get("messages", []))
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
    lines.append(
        f"{'Scenario':30s} {'Mean':>8s} {'Min':>8s} {'Max':>8s} "
        f"{'P90':>8s} {'Results':>7s} {'Status':>10s}"
    )
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
                "errors": [str(e) if e else None for e in r.errors],
                "all_ok": r.all_ok,
            }
            for r in reports
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────


async def main() -> int:
    parser = argparse.ArgumentParser(description="Global message search benchmark suite")
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
        help="Scenarios to skip",
    )
    parser.add_argument(
        "--only",
        type=str,
        nargs="*",
        default=[],
        help="Only run these scenarios",
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
        help="Bearer token for session lookup",
    )
    parser.add_argument(
        "--folder-name",
        type=str,
        default=None,
        help="Folder name (unused here, kept for compatibility)",
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
        ("single_term", lambda: scenario_single_term(limit=10)),
        ("two_terms", lambda: scenario_two_terms(limit=10)),
        ("three_terms", lambda: scenario_three_terms(limit=10)),
        ("five_terms", lambda: scenario_five_terms(limit=10)),
        ("three_terms_large", lambda: scenario_three_terms_large(limit=50)),
        ("dedup_check", lambda: scenario_dedup_check(limit=15)),
        ("fairness_check", lambda: scenario_fairness_check(limit=20)),
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
    compact = deepcopy(json_data)
    for s in compact["scenarios"].values():
        del s["durations_s"]
        del s["results_counts"]
        del s["errors"]
    print(json.dumps(compact, indent=2, ensure_ascii=False))

    if args.output:
        out_path = Path(args.output)
        out_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False))
        print(f"\nFull report saved to: {out_path.resolve()}")

    return 0


if __name__ == "__main__":
    exit(asyncio.run(main()))
