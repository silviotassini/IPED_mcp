"""IPED MCP — MCP server para explorar casos IPED via a API REST padrão do IPED."""

from iped_mcp.config import CaseConfig
from iped_mcp.webapi import IPEDAPIError, IPEDWebAPI, WebAPIServer

__all__ = [
    "CaseConfig",
    "IPEDAPIError",
    "IPEDWebAPI",
    "WebAPIServer",
]
