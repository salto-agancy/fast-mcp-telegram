import time

from starlette.responses import JSONResponse

from src._version import __version__
from src.client.connection import _session_cache, get_session_health_stats
from src.config.server_config import cfg
from src.server_components.web_setup import _setup_sessions


def register_health_routes(mcp_app):
    @mcp_app.custom_route("/health", methods=["GET"])
    async def health_check(request):
        config = cfg()
        current_time = time.time()
        session_info = []

        for token, (client, last_access) in _session_cache.items():
            hours_since_access = (current_time - last_access) / 3600
            session_info.append(
                {
                    "token_prefix": f"{token[:8]}...",
                    "hours_since_access": round(hours_since_access, 2),
                    "is_connected": client.is_connected() if client else False,
                    "last_access": time.ctime(last_access),
                }
            )

        # Get session health statistics
        health_stats = await get_session_health_stats()

        return JSONResponse(
            {
                "version": __version__,
                "status": "healthy",
                "active_sessions": len(_session_cache),
                "max_sessions": config.max_active_sessions,
                "session_files": sum(
                    bool(p.is_file())
                    for p in config.session_directory.glob("*.session")
                ),
                "setup_sessions": len(_setup_sessions),
                "sessions": session_info,
                "health_stats": health_stats,
            }
        )
