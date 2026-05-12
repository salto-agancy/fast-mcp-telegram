"""Package version: canonical value is ``[project].version`` in ``pyproject.toml`` (``uv version``)."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _version_from_pyproject() -> str:
    root = Path(__file__).resolve().parent.parent
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


try:
    __version__ = version("fast-mcp-telegram")
except PackageNotFoundError:
    __version__ = _version_from_pyproject()

__all__ = ["__version__"]
