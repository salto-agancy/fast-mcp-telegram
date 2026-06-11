"""FastAPI application for the telemetry collector.

Serves the /v1/event endpoint and /health check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from collector.app.services import (
    RateLimitError,
    StorageBackend,
    ValidationError,
    process_event,
)


def create_app(
    storage_backend: StorageBackend | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    In tests, pass an in-memory ``storage_backend`` directly.
    In production, omit the argument — the lifespan will initialise
    a PostgreSQL-backed ``AsyncPGStorage`` from environment variables.
    """
    _storage: StorageBackend = storage_backend or _default_storage_placeholder()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Connect to the database on startup, clean up on shutdown."""
        if isinstance(_storage, _LazyPostgresStorage):
            await _storage.connect()
        try:
            yield
        finally:
            if isinstance(_storage, _AsyncPGStorageLike):
                await _storage.close()

    app = FastAPI(
        title="fast-mcp-telegram Telemetry Collector",
        description="Anonymous feature-adoption telemetry collection endpoint.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # ---- routes ----

    @app.get("/health")
    async def health():
        """Kubernetes/Docker health check."""
        return {"status": "ok", "service": "telemetry-collector"}

    @app.post("/v1/event", status_code=204)
    async def collect_event(request: Request):
        """Receive and process a telemetry event.

        Returns 204 No Content on success (including silent dedup).
        Returns 422 for invalid payloads.
        Returns 429 when rate-limited.
        """
        client_ip = request.client.host if request.client else "unknown"

        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                status_code=422,
                content={"detail": "Invalid JSON body"},
            )

        if not isinstance(body, dict):
            return JSONResponse(
                status_code=422,
                content={"detail": "Body must be a JSON object"},
            )

        try:
            await process_event(body, client_ip, _storage)
            return Response(status_code=204)
        except ValidationError as exc:
            return JSONResponse(
                status_code=422,
                content={"detail": str(exc)},
            )
        except RateLimitError as exc:
            return JSONResponse(
                status_code=429,
                content={"detail": str(exc)},
            )

    return app


# ---- Late binding for the production storage backend ----


class _AsyncPGStorageLike:
    """Structural type for the production storage (matches AsyncPGStorage)."""

    async def connect(self) -> None: ...
    async def close(self) -> None: ...


class _LazyPostgresStorage(_AsyncPGStorageLike):
    """Defer asyncpg import + DSN read until lifespan startup."""

    def __init__(self) -> None:
        from collector.app.database import AsyncPGStorage
        from collector.app.settings import Settings

        self._impl = AsyncPGStorage(Settings().dsn)

    async def connect(self) -> None:
        await self._impl.connect()

    async def close(self) -> None:
        await self._impl.close()


def _default_storage_placeholder() -> StorageBackend:
    """Build the lazy storage placeholder used in production.

    The actual ``connect()`` happens in the lifespan handler so
    startup errors (DSN missing, Postgres unreachable) surface as
    real exceptions rather than import-time failures.
    """
    return _LazyPostgresStorage()  # type: ignore[return-value]


# ---- Entry point for uvicorn (via `--factory`) ----

app: FastAPI = create_app()
