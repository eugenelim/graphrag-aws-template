"""graphrag.mcp — MCP tool server with 6 typed tools + offline mock.

Exports the ``FastMCP`` instance so importers can reference the shared
``mcp`` object without importing from the private ``_tools`` module.
"""

from graphrag.mcp._tools import mcp

__all__ = ["mcp"]
