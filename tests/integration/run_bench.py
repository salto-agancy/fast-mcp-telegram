#!/usr/bin/env python3
"""
Unified benchmark CLI for fast-mcp-telegram.

Usage
-----
    # List all scenarios
    uv run python3 -m tests.integration.run_bench --list-scenarios

    # Run smoke scenarios (quick validation, 1 iteration each)
    uv run python3 -m tests.integration.run_bench --smoke

    # Run all find_chats scenarios
    uv run python3 -m tests.integration.run_bench --tool find_chats

    # Run specific scenario, save results
    uv run python3 -m tests.integration.run_bench --only folder_flags_date --json results.json
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# ── Ensure src is importable ──────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# ── Protect sys.argv from fastmcp's import-time arg parsing ──────────────
_saved_argv = sys.argv[:]
sys.argv = [sys.argv[0] if sys.argv else "run_bench"]
try:
    from src.client.connection import get_connected_client
    from src.tools.chat_discovery.find_chats import find_chats_impl
    from src.tools.search import search_messages_impl
    from tests.integration.bench_core import ScenarioRunner, report_json, report_table
    from tests.integration.scenarios import get_scenarios, list_scenarios
finally:
    sys.argv = _saved_argv

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logger = logging.getLogger("run_bench")


# ── Client ──────────────────────────────────────────────────────────────────


async def _get_client():
    """Create and start a Telethon client (same pattern as existing bench scripts)."""
    client = await get_connected_client()
    me = await client.get_me()
    print(f"Authenticated as: {me.first_name or ''} (@{me.username or '?'})")
    return client


# ── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified benchmark harness for fast-mcp-telegram",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --list-scenarios\n"
            "  %(prog)s --smoke\n"
            "  %(prog)s --tool find_chats --only folder\n"
            "  %(prog)s --smoke --json results.json\n"
        ),
    )

    parser.add_argument(
        "--tool",
        choices=["find_chats", "search_messages", "all"],
        default="all",
        help="Which tool's scenarios to run (default: all).",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="Substring filter on scenario name (e.g. 'folder', 'date').",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run only smoke-tagged scenarios (1 iteration each).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="Override iterations for all scenarios.",
    )
    parser.add_argument(
        "--json",
        default=None,
        help="Path to write JSON results file.",
    )
    parser.add_argument(
        "--list-scenarios",
        action="store_true",
        help="Print scenario table and exit.",
    )
    parser.add_argument(
        "--floodwait-max-retry",
        type=int,
        default=3,
        help="Max FloodWait retries per iteration (default: 3).",
    )
    parser.add_argument(
        "--floodwait-cap",
        type=int,
        default=60,
        help="FloodWait cap in seconds (default: 60).",
    )
    return parser.parse_args(argv)


# ── Main ────────────────────────────────────────────────────────────────────


async def _main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # ── List mode ──────────────────────────────────────────────────────
    if args.list_scenarios:
        print(list_scenarios())
        return 0

    # ── Resolve scenarios ──────────────────────────────────────────────
    tool = None if args.tool == "all" else args.tool
    scenarios = get_scenarios(tool=tool, pattern=args.only, smoke_only=args.smoke)

    if not scenarios:
        print("No scenarios matched the given filters.")
        return 1

    # Apply iteration override
    if args.iterations is not None:
        for s in scenarios:
            s.iterations = args.iterations

    print(f"Running {len(scenarios)} scenario(s):")
    for s in scenarios:
        print(f"  {s.name:30s} ({s.tool}, {1 if s.smoke else s.iterations} iter)")
    print()

    # ── Create client ──────────────────────────────────────────────────
    try:
        client = await _get_client()
    except Exception as e:
        print(f"Failed to create Telethon client: {e}")
        return 2

    # ── Run ─────────────────────────────────────────────────────────────
    runner = ScenarioRunner(
        client=client,
        impl_map={
            "find_chats": find_chats_impl,
            "search_messages": search_messages_impl,
        },
        floodwait_max_retry=args.floodwait_max_retry,
        floodwait_cap=args.floodwait_cap,
    )

    try:
        await runner.warmup()
        print()
        reports = await runner.run_many(scenarios)
        print()

        # ── Report ──────────────────────────────────────────────────
        print("── Results ──────────────────────────────────────────────")
        print(report_table(reports))

        if args.json:
            report_json(reports, path=args.json)
            print(f"Results saved to {args.json}")

        # Exit code: 0 if all ok, 1 if any failure
        return 0 if all(r.ok and not r.unreliable for r in reports) else 1
    finally:
        await client.disconnect()


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == "__main__":
    sys.exit(main())
