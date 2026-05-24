"""Register FastMCP middleware based on server configuration."""

from fastmcp import FastMCP

from src.config.server_config import ServerConfig
from src.server_components.account_tool_prefix_middleware import (
    AccountPrefixedToolsMiddleware,
)


def register_mcp_middleware(mcp: FastMCP, config: ServerConfig) -> None:
    """Attach optional FastMCP middleware when enabled in config."""
    if config.prefix_mcp_tools_with_username:
        mcp.add_middleware(AccountPrefixedToolsMiddleware())
