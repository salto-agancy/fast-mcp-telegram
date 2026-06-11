"""PostgreSQL storage backend for the telemetry collector.

Stores the full client payload as JSONB and extracts ``instance_id`` to
a top-level indexed column for efficient rate-limit queries.

Schema:
    id              BIGSERIAL — primary key
    received_at     TIMESTAMPTZ — server-side insert time
    instance_id     TEXT — extracted from payload.iid (indexed)
    payload         JSONB — the full nested payload from the client
    payload_hash    TEXT — SHA-256 of canonicalized payload (indexed)
    source_ip_hash  TEXT — SHA-256 of the request source IP
"""

from __future__ import annotations

import json

import asyncpg

from collector.app.models import TelemetryPayload

_SQL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS telemetry (
    id              BIGSERIAL PRIMARY KEY,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    instance_id     TEXT NOT NULL,
    payload         JSONB NOT NULL,
    payload_hash    TEXT NOT NULL,
    source_ip_hash  TEXT NOT NULL
);
"""

_SQL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_telemetry_instance_id ON telemetry(instance_id)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_received_at ON telemetry(received_at)",
    "CREATE INDEX IF NOT EXISTS idx_telemetry_payload_hash ON telemetry(payload_hash)",
]


class AsyncPGStorage:
    """Production PostgreSQL backend for the collector.

    Usage:
        storage = AsyncPGStorage(dsn="postgres://user:pass@host/db")
        await storage.connect()
        await storage.store(payload, ip_hash, payload_hash)
        await storage.close()
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    # ---- lifecycle ----

    async def connect(self) -> None:
        """Create connection pool and ensure table exists."""
        self._pool = await asyncpg.create_pool(
            self._dsn, min_size=2, max_size=10
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_SQL_CREATE_TABLE)
            for idx in _SQL_INDEXES:
                await conn.execute(idx)

    async def close(self) -> None:
        """Release the pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ---- storage interface ----

    async def store(
        self,
        payload: TelemetryPayload,
        source_ip_hash: str,
        payload_hash: str,
    ) -> None:
        """Insert one telemetry event.

        ``payload_hash`` is computed once by the service layer and
        passed in — the storage backend does not recompute it.
        """
        payload_json = json.dumps(payload.model_dump(mode="json"))
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO telemetry
                    (instance_id, payload, payload_hash, source_ip_hash)
                VALUES ($1, $2::jsonb, $3, $4)
                """,
                payload.iid,
                payload_json,
                payload_hash,
                source_ip_hash,
            )

    async def count_recent_events(
        self, instance_id: str, window_hours: int = 24
    ) -> int:
        """Count events for a given instance_id within a time window."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COUNT(*) AS cnt FROM telemetry
                WHERE instance_id = $1
                  AND received_at >= NOW() - make_interval(hours => $2)
                """,
                instance_id,
                window_hours,
            )
            return row["cnt"] if row else 0

    async def has_exact_payload(
        self, payload_hash: str, window_seconds: int = 300
    ) -> bool:
        """Check if an identical payload hash exists within the time window."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM telemetry
                WHERE payload_hash = $1
                  AND received_at >= NOW() - make_interval(secs => $2)
                LIMIT 1
                """,
                payload_hash,
                window_seconds,
            )
            return row is not None

    async def enforce_row_cap(self, max_rows: int = 10_000_000) -> int:
        """Delete oldest rows when count exceeds max_rows.

        Orders by id DESC (newest first) and finds the boundary ID
        at the cap position, then does a range delete — this is
        significantly faster than ``OFFSET`` in a subquery for
        tables with millions of rows.

        Reference: https://dba.stackexchange.com/a/183096
        """
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                WITH boundary AS (
                    SELECT id FROM telemetry
                    ORDER BY id DESC
                    OFFSET $1 LIMIT 1
                )
                DELETE FROM telemetry
                WHERE id <= (SELECT id FROM boundary)
                  AND EXISTS (SELECT 1 FROM boundary)
                """,
                max_rows,
            )
            return _parse_delete_count(result)

    async def cleanup_ttl(self, retention_days: int = 90) -> int:
        """Purge rows older than retention_days."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                DELETE FROM telemetry
                WHERE received_at < NOW() - make_interval(days => $1)
                """,
                retention_days,
            )
            return _parse_delete_count(result)


def _parse_delete_count(result: str) -> int:
    """asyncpg returns 'DELETE N' — extract the integer count."""
    if result.startswith("DELETE "):
        return int(result.split()[-1])
    return 0
