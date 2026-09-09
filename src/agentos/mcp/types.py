"""MCP client type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerConfig:
    name: str
    transport: str  # "stdio" | "sse" | "streamable_http"
    command: str | None = None  # for stdio
    args: list[str] = field(default_factory=list)  # for stdio
    url: str | None = None  # for HTTP transports
    env: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    oauth: bool = False
    oauth_redirect_uri: str | None = None
    state_dir: str | None = None
    tool_timeout_seconds: float = 30.0


@dataclass
class MCPToolDef:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class MCPToolResult:
    content: str
    is_error: bool = False


@dataclass
class MCPResource:
    uri: str
    name: str
    description: str = ""
    mime_type: str | None = None


MCPResourceDef = MCPResource


@dataclass
class MCPResourceContent:
    uri: str
    mime_type: str | None = None
    text: str | None = None
    blob: str | None = None


@dataclass
class MCPPromptArgument:
    name: str
    description: str = ""
    required: bool = False


@dataclass
class MCPPrompt:
    name: str
    description: str = ""
    arguments: list[MCPPromptArgument] = field(default_factory=list)


MCPPromptDef = MCPPrompt


@dataclass
class MCPPromptMessage:
    role: str
    content: str | dict[str, Any]


@dataclass
class MCPGetPromptResult:
    description: str = ""
    messages: list[MCPPromptMessage] = field(default_factory=list)


MCPPromptResult = MCPGetPromptResult
