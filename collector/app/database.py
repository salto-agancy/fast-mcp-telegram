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

import psycopg2
import psycopg2.extras

from app.models import TelemetryPayload

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


class PgStorage:
    """Production PostgreSQL backend for the collector.

    Usage:
        storage = PgStorage(dsn="postgres://user:pass@host/db")
        storage.connect()
        storage.store(payload, ip_hash, payload_hash)
        storage.close()
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: psycopg2.connection | None = None

    # ---- lifecycle ----

    def connect(self) -> None:
        """Create connection and ensure table exists."""
        self._conn = psycopg2.connect(self._dsn)
        with self._conn.cursor() as cur:
            cur.execute(_SQL_CREATE_TABLE)
            for idx in _SQL_INDEXES:
                cur.execute(idx)
        self._conn.commit()

    def close(self) -> None:
        """Close the connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    # ---- storage interface ----

    def store(
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
        with self._conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO telemetry
                    (instance_id, payload, payload_hash, source_ip_hash)
                VALUES (%s, %s::jsonb, %s, %s)
                """,
                (payload.iid, payload_json, payload_hash, source_ip_hash),
            )
        self._conn.commit()

    def count_recent_events(
        self, instance_id: str, window_hours: int = 24
    ) -> int:
        """Count events for a given instance_id within a time window."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt FROM telemetry
                WHERE instance_id = %s
                  AND received_at >= NOW() - make_interval(hours => %s)
                """,
                (instance_id, window_hours),
            )
            row = cur.fetchone()
            return row[0] if row else 0

    def has_exact_payload(
        self, payload_hash: str, window_seconds: int = 300
    ) -> bool:
        """Check if an identical payload hash exists within the time window."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM telemetry
                WHERE payload_hash = %s
                  AND received_at >= NOW() - make_interval(secs => %s)
                LIMIT 1
                """,
                (payload_hash, window_seconds),
            )
            return cur.fetchone() is not None

    def enforce_row_cap(self, max_rows: int = 10_000_000) -> int:
        """Delete oldest rows when count exceeds max_rows.

        Orders by id DESC (newest first) and finds the boundary ID
        at the cap position, then does a range delete — this is
        significantly faster than ``OFFSET`` in a subquery for
        tables with millions of rows.

        Reference: https://dba.stackexchange.com/a/183096
        """
        with self._conn.cursor() as cur:
            cur.execute(
                """
                WITH boundary AS (
                    SELECT id FROM telemetry
                    ORDER BY id DESC
                    OFFSET %s LIMIT 1
                )
                DELETE FROM telemetry
                WHERE id <= (SELECT id FROM boundary)
                  AND EXISTS (SELECT 1 FROM boundary)
                """,
                (max_rows,),
            )
            result = cur.rowcount
        self._conn.commit()
        return result

    def cleanup_ttl(self, retention_days: int = 90) -> int:
        """Purge rows older than retention_days."""
        with self._conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM telemetry
                WHERE received_at < NOW() - make_interval(days => %s)
                """,
                (retention_days,),
            )
            result = cur.rowcount
        self._conn.commit()
        return result
