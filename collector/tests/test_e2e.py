"""End-to-end tests for the telemetry collector against real PostgreSQL.

Prerequisites (local):
    docker compose -f collector/docker-compose.dev.yml up -d postgres

In CI these tests run against a GitHub Actions postgres service container.

All tests in this file are marked ``e2e`` — skip them with ``-m "not e2e"``
when PostgreSQL is not available.
"""

from __future__ import annotations

import json
import os

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

# -- Helpers -----------------------------------------------------------------


def _pg_dsn() -> str:
    """Build a PostgreSQL DSN from environment variables with defaults.

    Local dev: docker compose remaps PG to port 5433, uses telemetry/telemetry.
    CI:       postgres service container on port 5432, same credentials.
    """
    pg_host = os.environ.get("TELEMETRY_PG_HOST", "localhost")
    pg_port = os.environ.get("TELEMETRY_PG_PORT", "5433")
    pg_user = os.environ.get("TELEMETRY_PG_USER", "telemetry")
    pg_pass = os.environ.get("TELEMETRY_PG_PASSWORD", "telemetry")
    pg_db = os.environ.get("TELEMETRY_PG_DB", "telemetry")
    return f"postgres://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"


def _pg_is_available() -> bool:
    """Quick check whether PostgreSQL is reachable.

    Skips the test file entirely when PG is not running (local dev
    without ``docker compose up``).
    """
    try:
        import asyncpg
        import asyncio

        dsn = _pg_dsn()

        async def _probe() -> bool:
            try:
                conn = await asyncpg.connect(dsn, timeout=3)
                await conn.close()
                return True
            except (OSError, asyncpg.CannotConnectNowError):
                return False

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_probe())
        finally:
            loop.close()
    except ImportError:
        return False


if not _pg_is_available():
    pytest.skip("PostgreSQL not reachable — set TELEMETRY_PG_HOST/PORT or start docker compose", allow_module_level=True)

# -- Fixtures -----------------------------------------------------------------


@pytest.fixture
async def pg_storage():
    """AsyncPGStorage connected to a real PostgreSQL instance.

    Cleans up the telemetry table after all tests in the module.
    """
    from app.database import AsyncPGStorage

    dsn = _pg_dsn()
    storage = AsyncPGStorage(dsn)
    await storage.connect()

    # Wipe any leftover data from previous runs
    async with storage._pool.acquire() as conn:
        await conn.execute("DELETE FROM telemetry")

    yield storage

    # Cleanup
    async with storage._pool.acquire() as conn:
        await conn.execute("DELETE FROM telemetry")
    await storage.close()


@pytest.fixture
async def e2e_app(pg_storage):
    """FastAPI app wired to the real PostgreSQL storage."""
    from app.main import create_app
    return create_app(storage_backend=pg_storage)


@pytest.fixture
def client(e2e_app):
    """Sync TestClient for the sync health check."""
    from fastapi.testclient import TestClient
    with TestClient(e2e_app) as c:
        yield c


@pytest.fixture
async def async_client(e2e_app):
    """Async HTTP client for async tests using httpx.ASGITransport.

    ``TestClient`` wraps the ASGI app with a sync interface that runs
    the app on a *different* event loop than the async test function.
    This causes ``RuntimeError: attached to a different loop`` when a
    test makes a sync HTTP call then awaits an async fixture method.

    ``httpx.AsyncClient`` + ``ASGITransport`` keeps everything on the
    same event loop, avoiding the conflict.
    """
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=e2e_app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def valid_payload():
    """A valid telemetry payload dict."""
    from collector.tests._helpers import make_nested_payload
    return make_nested_payload()


async def _count_rows(storage) -> int:
    """Count rows in the telemetry table."""
    async with storage._pool.acquire() as conn:
        row = await conn.fetchval("SELECT COUNT(*) FROM telemetry")
        return row


async def _fetch_all(storage) -> list[dict]:
    """Fetch all rows for inspection."""
    async with storage._pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM telemetry ORDER BY id")
        return [dict(r) for r in rows]


# -- Tests --------------------------------------------------------------------


class TestE2EHealth:
    """Health endpoint works with real storage attached."""

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestE2ECollect:
    """Collecting events against real PostgreSQL."""

    async def test_store_and_retrieve(self, async_client, pg_storage, valid_payload):
        """A valid payload is stored and queryable via PostgreSQL."""
        resp = await async_client.post("/v1/event", json=valid_payload)
        assert resp.status_code == 204

        count = await _count_rows(pg_storage)
        assert count == 1

        rows = await _fetch_all(pg_storage)
        row = rows[0]
        # Check extracted instance_id column
        assert row["instance_id"] == valid_payload["iid"]
        # Check JSONB payload
        stored = json.loads(row["payload"])
        assert stored["iid"] == valid_payload["iid"]
        assert stored["v"] == 1
        # Verify indexes exist and hash columns are populated
        assert len(row["payload_hash"]) == 64  # SHA-256 hex
        assert len(row["source_ip_hash"]) == 64  # SHA-256 hex

    async def test_multiple_events(self, async_client, pg_storage):
        """Multiple valid events are all stored."""
        from collector.tests._helpers import make_nested_payload

        n = 5
        for i in range(n):
            data = make_nested_payload(
                iid=f"550e8400-e29b-41d4-a716-4466554400{i:02d}",
                counters={"total_calls": i, "errors": 0},
            )
            resp = await async_client.post("/v1/event", json=data)
            assert resp.status_code == 204

        count = await _count_rows(pg_storage)
        assert count == n

    async def test_invalid_payload_rejected(self, async_client, pg_storage, valid_payload):
        """Invalid payload returns 422 and nothing is stored."""
        del valid_payload["iid"]
        resp = await async_client.post("/v1/event", json=valid_payload)
        assert resp.status_code == 422

        count = await _count_rows(pg_storage)
        assert count == 0

    async def test_duplicate_dedup_postgres(self, async_client, pg_storage, valid_payload):
        """Same payload sent twice within dedup window is deduped (single row)."""
        resp1 = await async_client.post("/v1/event", json=valid_payload)
        assert resp1.status_code == 204

        resp2 = await async_client.post("/v1/event", json=valid_payload)
        assert resp2.status_code == 204  # Silent dedup

        count = await _count_rows(pg_storage)
        assert count == 1, f"Expected 1 row after dedup, got {count}"

    async def test_rate_limit_postgres(self, async_client, pg_storage):
        """Exceeding per-instance rate limit returns 429."""
        from app.services import INSTANCE_RATE_LIMIT
        from collector.tests._helpers import make_nested_payload

        # Send up to the limit
        for i in range(INSTANCE_RATE_LIMIT):
            data = make_nested_payload(
                iid="rate-limited-test-uuid",
                counters={"total_calls": i, "errors": 0},
            )
            resp = await async_client.post("/v1/event", json=data)
            assert resp.status_code == 204

        # One more should be rate-limited
        data = make_nested_payload(
            iid="rate-limited-test-uuid",
            counters={"total_calls": 9999, "errors": 0},
        )
        resp = await async_client.post("/v1/event", json=data)
        assert resp.status_code == 429

        # Verify only the limit number of rows exists
        count = await _count_rows(pg_storage)
        assert count == INSTANCE_RATE_LIMIT

    async def test_different_instance_not_rate_limited(self, async_client, pg_storage):
        """Events from different iids don't interfere with rate limiting."""
        from app.services import INSTANCE_RATE_LIMIT
        from collector.tests._helpers import make_nested_payload

        # Saturate one iid
        for i in range(INSTANCE_RATE_LIMIT):
            data = make_nested_payload(
                iid="saturated-instance",
                counters={"total_calls": i, "errors": 0},
            )
            resp = await async_client.post("/v1/event", json=data)
            assert resp.status_code == 204

        # A different iid should still be allowed
        fresh = make_nested_payload(
            iid="fresh-instance",
            counters={"total_calls": 0, "errors": 0},
        )
        resp = await async_client.post("/v1/event", json=fresh)
        assert resp.status_code == 204

    async def test_column_types_and_indexes(self, pg_storage):
        """Verify table schema and indexes exist."""
        async with pg_storage._pool.acquire() as conn:
            # Check columns exist with expected types
            cols = await conn.fetch(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'telemetry'
                ORDER BY ordinal_position
                """
            )
            col_map = {c["column_name"]: c for c in cols}
            assert "id" in col_map
            assert col_map["id"]["data_type"] in ("bigint", "integer")
            assert col_map["id"]["is_nullable"] == "NO"

            assert "instance_id" in col_map
            assert col_map["instance_id"]["data_type"] == "text"

            assert "payload" in col_map
            assert col_map["payload"]["data_type"] == "jsonb"

            assert "payload_hash" in col_map
            assert "source_ip_hash" in col_map
            assert "received_at" in col_map

            # Check key indexes exist
            indexes = await conn.fetch(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = 'telemetry'
                """
            )
            index_names = {r["indexname"] for r in indexes}
            assert "telemetry_pkey" in index_names
            assert "idx_telemetry_instance_id" in index_names
            assert "idx_telemetry_received_at" in index_names
            assert "idx_telemetry_payload_hash" in index_names

    async def test_row_cap_postgres(self, pg_storage):
        """enforce_row_cap correctly removes oldest rows."""
        from collector.tests._helpers import make_nested_payload

        cap = 5
        # Insert more than cap
        for i in range(cap + 3):
            data = make_nested_payload(
                iid=f"cap-test-{i:02d}",
                counters={"total_calls": i, "errors": 0},
            )
            # Bypass the HTTP layer — store directly
            from app.services import compute_payload_hash, hash_source_ip
            from app.models import TelemetryPayload

            payload = TelemetryPayload(**data)
            await pg_storage.store(payload, hash_source_ip("10.0.0.1"), compute_payload_hash(payload))

        count_before = await _count_rows(pg_storage)
        assert count_before == cap + 3  # all rows present before cap

        removed = await pg_storage.enforce_row_cap(max_rows=cap)
        assert removed == 3

        count_after = await _count_rows(pg_storage)
        assert count_after == cap

    async def test_ttl_cleanup_postgres(self, pg_storage):
        """cleanup_ttl removes rows older than retention window."""
        from datetime import datetime, timedelta, timezone

        # Insert a row with an old received_at
        async with pg_storage._pool.acquire() as conn:
            # We need to insert directly to control received_at
            await conn.execute(
                """
                INSERT INTO telemetry (instance_id, payload, payload_hash, source_ip_hash, received_at)
                VALUES ('old-test', '{}'::jsonb, 'oldhash', 'oldip',
                        NOW() - INTERVAL '200 days')
                """
            )

        removed = await pg_storage.cleanup_ttl(retention_days=90)
        assert removed >= 1

        # Verify it's gone
        async with pg_storage._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM telemetry WHERE instance_id = 'old-test'"
            )
            assert row is None
