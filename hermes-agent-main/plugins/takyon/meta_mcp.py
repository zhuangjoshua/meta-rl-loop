"""Official Meta Ads MCP client helpers.

This module is intentionally tiny and authority-side only: callers pass an
already-resolved OAuth bearer from the safebox, and this code talks to Meta's
official remote MCP endpoint. Runtime planes must use the safebox broker route
instead of resolving META_MCP_OAUTH_TOKEN themselves.
"""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any, Mapping

DEFAULT_META_MCP_ENDPOINT = "https://mcp.facebook.com/ads"
META_MCP_TOKEN_ALIASES = ("META_MCP_OAUTH_TOKEN",)
META_MCP_ENDPOINT_ALIASES = ("META_MCP_ENDPOINT", "META_ADS_MCP_ENDPOINT")


class MetaMCPError(RuntimeError):
    """Base error for official Meta MCP calls."""


class MetaMCPAuthRequired(MetaMCPError):
    """Raised when the official Meta MCP OAuth token is missing or rejected."""


class MetaMCPUnavailable(MetaMCPError):
    """Raised when the MCP client dependency/transport is unavailable."""


def _exception_summary(exc: BaseException, *, _depth: int = 0) -> str:
    """Render nested MCP/HTTP exception groups without leaking OAuth tokens."""
    text = str(exc).strip() or type(exc).__name__
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code:
        text = f"{text} [http_status={status_code}]"
    children = getattr(exc, "exceptions", None)
    if children and _depth < 3:
        child_text = "; ".join(
            _exception_summary(child, _depth=_depth + 1)
            for child in children
        )
        if child_text:
            text = f"{text}; children=[{child_text}]"
    return text


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            try:
                return json.loads(stripped)
            except Exception:
                return value
    return value


def _extract_content_text(content: Any) -> str:
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
            continue
        if isinstance(block, Mapping) and block.get("text"):
            parts.append(str(block.get("text")))
    return "\n".join(parts).strip()


def _extract_call_payload(result: Any) -> dict[str, Any]:
    if bool(getattr(result, "isError", False)):
        detail = _extract_content_text(getattr(result, "content", None)) or "Meta MCP tool returned an error"
        raise MetaMCPError(detail)

    structured = (
        getattr(result, "structuredContent", None)
        or getattr(result, "structured_content", None)
        or (getattr(result, "model_extra", {}) or {}).get("structuredContent")
    )
    if structured is not None:
        parsed = _jsonish(structured)
        return parsed if isinstance(parsed, dict) else {"result": parsed}

    text = _extract_content_text(getattr(result, "content", None))
    parsed = _jsonish(text)
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _run_coro(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - defensive bridge
            box["error"] = exc

    thread = threading.Thread(target=runner, name="takyon-meta-mcp-call", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _import_mcp_http():
    try:
        from mcp import ClientSession
        try:
            from mcp.types import LATEST_PROTOCOL_VERSION
        except Exception:
            LATEST_PROTOCOL_VERSION = "2025-03-26"
        try:
            from mcp.client.streamable_http import streamable_http_client
            return ClientSession, LATEST_PROTOCOL_VERSION, streamable_http_client, None
        except Exception:
            from mcp.client.streamable_http import streamablehttp_client
            return ClientSession, LATEST_PROTOCOL_VERSION, None, streamablehttp_client
    except Exception as exc:
        raise MetaMCPUnavailable(
            "official Meta MCP requires the Python mcp package with Streamable HTTP support"
        ) from exc


def _auth_error(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    try:
        if int(getattr(response, "status_code", 0) or 0) == 401:
            return True
    except Exception:
        pass
    children = getattr(exc, "exceptions", None)
    if children and any(_auth_error(child) for child in children):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ("401", "unauthorized", "invalid_token", "oauth"))


async def _call_tool_async(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    token: str,
    endpoint: str,
    timeout: float,
) -> dict[str, Any]:
    ClientSession, protocol_version, streamable_http_client, streamablehttp_client = _import_mcp_http()
    try:
        import httpx
    except Exception as exc:
        raise MetaMCPUnavailable("official Meta MCP requires httpx") from exc

    clean_tool = str(tool_name or "").strip()
    if not clean_tool:
        raise MetaMCPError("Meta MCP tool_name is required")
    bearer = str(token or "").strip()
    if not bearer:
        raise MetaMCPAuthRequired("META_MCP_OAUTH_TOKEN is not configured")
    url = str(endpoint or DEFAULT_META_MCP_ENDPOINT).strip() or DEFAULT_META_MCP_ENDPOINT
    headers = {
        "Authorization": f"Bearer {bearer}",
        "mcp-protocol-version": str(protocol_version or "2025-03-26"),
    }

    try:
        if streamable_http_client is not None:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(float(timeout), read=float(timeout)),
            ) as http_client:
                async with streamable_http_client(url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.call_tool(clean_tool, dict(arguments or {}))
                        return _extract_call_payload(result)
        async with streamablehttp_client(
            url,
            headers=headers,
            timeout=float(timeout),
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(clean_tool, dict(arguments or {}))
                return _extract_call_payload(result)
    except MetaMCPError:
        raise
    except Exception as exc:
        detail = _exception_summary(exc)
        if _auth_error(exc):
            raise MetaMCPAuthRequired(f"Meta MCP OAuth failed: {detail}") from exc
        raise MetaMCPError(f"Meta MCP tool {clean_tool} failed: {detail}") from exc


async def _list_tools_async(*, token: str, endpoint: str, timeout: float) -> dict[str, Any]:
    ClientSession, protocol_version, streamable_http_client, streamablehttp_client = _import_mcp_http()
    try:
        import httpx
    except Exception as exc:
        raise MetaMCPUnavailable("official Meta MCP requires httpx") from exc
    bearer = str(token or "").strip()
    if not bearer:
        raise MetaMCPAuthRequired("META_MCP_OAUTH_TOKEN is not configured")
    url = str(endpoint or DEFAULT_META_MCP_ENDPOINT).strip() or DEFAULT_META_MCP_ENDPOINT
    headers = {
        "Authorization": f"Bearer {bearer}",
        "mcp-protocol-version": str(protocol_version or "2025-03-26"),
    }

    def serialize_tool(tool: Any) -> dict[str, Any]:
        if hasattr(tool, "model_dump"):
            data = tool.model_dump()
            return data if isinstance(data, dict) else {"name": str(tool)}
        return {
            "name": str(getattr(tool, "name", "") or ""),
            "description": str(getattr(tool, "description", "") or ""),
            "inputSchema": getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None),
        }

    try:
        if streamable_http_client is not None:
            async with httpx.AsyncClient(
                headers=headers,
                follow_redirects=True,
                timeout=httpx.Timeout(float(timeout), read=float(timeout)),
            ) as http_client:
                async with streamable_http_client(url, http_client=http_client) as (
                    read_stream,
                    write_stream,
                    _get_session_id,
                ):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        result = await session.list_tools()
                        return {"tools": [serialize_tool(tool) for tool in (result.tools or [])]}
        async with streamablehttp_client(
            url,
            headers=headers,
            timeout=float(timeout),
        ) as (read_stream, write_stream, _get_session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
                return {"tools": [serialize_tool(tool) for tool in (result.tools or [])]}
    except MetaMCPError:
        raise
    except Exception as exc:
        detail = _exception_summary(exc)
        if _auth_error(exc):
            raise MetaMCPAuthRequired(f"Meta MCP OAuth failed: {detail}") from exc
        raise MetaMCPError(f"Meta MCP list_tools failed: {detail}") from exc


def call_tool(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    token: str,
    endpoint: str = DEFAULT_META_MCP_ENDPOINT,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return _run_coro(
        _call_tool_async(
            tool_name,
            arguments,
            token=token,
            endpoint=endpoint,
            timeout=float(timeout),
        )
    )


def list_tools(
    *,
    token: str,
    endpoint: str = DEFAULT_META_MCP_ENDPOINT,
    timeout: float = 60.0,
) -> dict[str, Any]:
    return _run_coro(_list_tools_async(token=token, endpoint=endpoint, timeout=float(timeout)))
