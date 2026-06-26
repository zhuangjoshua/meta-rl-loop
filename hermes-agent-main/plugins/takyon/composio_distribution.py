"""Composio-backed provider transport for Takyon distribution rails."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import safebox

_COMPOSIO_BASE_URL_DEFAULT = "https://backend.composio.dev/api/v3.1"
_REDDIT_TOOLKIT_SLUG = "reddit_ads"
_REDDIT_ORGANIC_TOOLKIT_SLUG = "reddit"
_TWITTER_TOOLKIT_SLUG = "twitter"
_METAADS_TOOLKIT_SLUG = "metaads"
_REDDIT_DEFAULT_USER_ID = "takyon_prod_operator"
_REDDIT_DEFAULT_ALIAS = "takyon-prod-reddit-ads"
_REDDIT_ORGANIC_DEFAULT_USER_ID = "takyon_prod_operator"
_REDDIT_ORGANIC_DEFAULT_ALIAS = "takyon-prod-reddit"
_TWITTER_DEFAULT_USER_ID = "takyon_prod_operator"
_TWITTER_DEFAULT_ALIAS = "takyon-prod-twitter"
_METAADS_DEFAULT_USER_ID = "takyon_prod_operator"
_METAADS_DEFAULT_ALIAS = "takyon-prod-meta-ads"


class ComposioDistributionError(RuntimeError):
    """Raised when a Composio-backed distribution call cannot be completed."""


def _load_httpx():
    try:
        import httpx
    except Exception as exc:  # pragma: no cover - dependency missing
        raise ComposioDistributionError("Composio distribution transport requires the httpx package") from exc
    return httpx


def _env_value(name: str) -> str:
    if safebox.is_sensitive_env_key(name):
        try:
            value = safebox.read_env_backed_value(name) or ""
        except Exception:
            value = os.getenv(name) or ""
    else:
        value = os.getenv(name) or ""
    return str(value).strip()


def _api_key() -> str:
    key = _env_value("COMPOSIO_API_KEY")
    if not key:
        raise ComposioDistributionError("missing COMPOSIO_API_KEY")
    return key


def _base_url() -> str:
    return (_env_value("COMPOSIO_BASE_URL") or _COMPOSIO_BASE_URL_DEFAULT).rstrip("/")


def _request(
    method: str,
    path: str,
    *,
    json_body: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | Iterable[tuple[str, Any]] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    if safebox._use_remote_authority():
        # COMPOSIO_API_KEY is a provider secret the safebox holds and refuses to egress over
        # /v1/env, so a runtime plane cannot resolve it (os.environ is empty there). Broker the WHOLE
        # call through the safebox: it resolves the key LOCALLY and forwards to Composio, returning
        # the key-free JSON. Mirrors the creative provider routes — and because every channel
        # (twitter/reddit/reddit_ads/metaads) AND the connected-account lookup funnel through
        # _request, this single seam fixes them all.
        norm_params = None
        if params is not None:
            pairs = list(params.items()) if isinstance(params, Mapping) else list(params)
            norm_params = [[str(k), v] for k, v in pairs]
        return safebox.composio_forward(
            method=method,
            path=path,
            json_body=(dict(json_body) if json_body is not None else None),
            params=norm_params,
            timeout=timeout,
        )
    httpx = _load_httpx()
    url = path if path.startswith("http://") or path.startswith("https://") else f"{_base_url()}/{path.lstrip('/')}"
    try:
        response = httpx.request(
            method.upper(),
            url,
            headers={
                "x-api-key": _api_key(),
                "Content-Type": "application/json",
            },
            json=dict(json_body) if json_body is not None else None,
            params=params,
            timeout=timeout,
        )
    except Exception as exc:
        raise ComposioDistributionError(f"request to {url} failed: {exc}") from exc

    try:
        payload = response.json()
    except Exception:
        payload = None

    if response.status_code >= 400:
        message = ""
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                message = str(error.get("message") or "").strip()
                errors = error.get("errors")
                if not message and isinstance(errors, list) and errors:
                    message = "; ".join(str(item).strip() for item in errors if str(item).strip())
        if not message:
            message = response.text.strip()
        raise ComposioDistributionError(
            f"Composio API {method.upper()} {url} failed ({response.status_code})"
            + (f": {message}" if message else "")
        )
    if not isinstance(payload, dict):
        raise ComposioDistributionError(f"Composio API {method.upper()} {url} returned invalid JSON")
    return payload


def _active_connected_accounts(*, toolkit_slug: str, user_id: str) -> list[dict[str, Any]]:
    payload = _request(
        "GET",
        "connected_accounts",
        params=[
            ("toolkit_slugs", toolkit_slug),
            ("statuses", "ACTIVE"),
            ("user_ids", user_id),
        ],
        timeout=30.0,
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise ComposioDistributionError("connected account lookup returned an unexpected payload")
    return [
        item
        for item in items
        if isinstance(item, dict)
        and str(((item.get("toolkit") or {}).get("slug") or "")).strip() == toolkit_slug
        and str(item.get("status") or "").strip().upper() == "ACTIVE"
    ]


def _resolve_connected_account_id(
    *,
    toolkit_slug: str,
    explicit_env_key: str,
    user_id_env_keys: tuple[str, ...],
    alias_env_key: str,
    default_user_id: str,
    default_alias: str,
) -> str:
    explicit = _env_value(explicit_env_key)
    if explicit:
        return explicit

    user_id = ""
    for env_key in user_id_env_keys:
        user_id = _env_value(env_key)
        if user_id:
            break
    if not user_id:
        user_id = default_user_id
    preferred_alias = _env_value(alias_env_key) or default_alias
    active = _active_connected_accounts(toolkit_slug=toolkit_slug, user_id=user_id)
    if not active:
        raise ComposioDistributionError(
            f"no active Composio {toolkit_slug} connected account found for user_id={user_id}"
        )

    if preferred_alias:
        aliased = [item for item in active if str(item.get("alias") or "").strip() == preferred_alias]
        if len(aliased) == 1:
            return str(aliased[0].get("id") or "").strip()
        if len(aliased) > 1:
            raise ComposioDistributionError(
                f"multiple active {toolkit_slug} connected accounts match alias={preferred_alias}; "
                f"set {explicit_env_key} explicitly"
            )

    if len(active) == 1:
        return str(active[0].get("id") or "").strip()
    raise ComposioDistributionError(
        f"multiple active {toolkit_slug} connected accounts found; "
        f"set {explicit_env_key} explicitly"
    )


def resolve_reddit_ads_connected_account_id() -> str:
    return _resolve_connected_account_id(
        toolkit_slug=_REDDIT_TOOLKIT_SLUG,
        explicit_env_key="COMPOSIO_REDDIT_ADS_CONNECTED_ACCOUNT_ID",
        user_id_env_keys=("COMPOSIO_REDDIT_ADS_USER_ID", "COMPOSIO_USER_ID"),
        alias_env_key="COMPOSIO_REDDIT_ADS_ALIAS",
        default_user_id=_REDDIT_DEFAULT_USER_ID,
        default_alias=_REDDIT_DEFAULT_ALIAS,
    )


def resolve_twitter_connected_account_id() -> str:
    return _resolve_connected_account_id(
        toolkit_slug=_TWITTER_TOOLKIT_SLUG,
        explicit_env_key="COMPOSIO_TWITTER_CONNECTED_ACCOUNT_ID",
        user_id_env_keys=("COMPOSIO_TWITTER_USER_ID", "COMPOSIO_USER_ID"),
        alias_env_key="COMPOSIO_TWITTER_ALIAS",
        default_user_id=_TWITTER_DEFAULT_USER_ID,
        default_alias=_TWITTER_DEFAULT_ALIAS,
    )


def resolve_reddit_organic_connected_account_id() -> str:
    return _resolve_connected_account_id(
        toolkit_slug=_REDDIT_ORGANIC_TOOLKIT_SLUG,
        explicit_env_key="COMPOSIO_REDDIT_CONNECTED_ACCOUNT_ID",
        user_id_env_keys=("COMPOSIO_REDDIT_USER_ID", "COMPOSIO_USER_ID"),
        alias_env_key="COMPOSIO_REDDIT_ALIAS",
        default_user_id=_REDDIT_ORGANIC_DEFAULT_USER_ID,
        default_alias=_REDDIT_ORGANIC_DEFAULT_ALIAS,
    )


def resolve_metaads_connected_account_id() -> str:
    raise ComposioDistributionError(
        "Composio Meta Ads is disabled for Takyon Meta v2; use official Meta Ads MCP "
        "with META_MCP_OAUTH_TOKEN"
    )


def _composio_error_message(tool_slug: str, response: Mapping[str, Any]) -> str:
    """Extract the human-readable provider error from a ``successful:false`` Composio response.

    The v3.1 execute envelope carries the upstream failure in ``error`` (a string) and/or
    ``data.message`` (the provider's own text, e.g. Twitter's "duplicate content" / "Unauthorized").
    Fall back through every place the message can live so the surfaced error is the real provider
    cause, never the bare tool slug or an empty string."""
    error = response.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if isinstance(error, Mapping):
        msg = str(error.get("message") or "").strip()
        if msg:
            return msg
    data = response.get("data")
    if isinstance(data, Mapping):
        msg = str(data.get("message") or data.get("error") or "").strip()
        status_code = data.get("status_code")
        if msg:
            return f"{msg} (status {status_code})" if status_code else msg
    return f"{tool_slug} failed without an error message"


def _proxy_error_message(tool_slug: str, response: Mapping[str, Any]) -> str:
    data = response.get("data")
    if isinstance(data, Mapping):
        error = data.get("error")
        if isinstance(error, Mapping):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            error_type = str(error.get("type") or "").strip()
            if message:
                details = ", ".join(part for part in (error_type, f"code {code}" if code else "") if part)
                return f"{message} ({details})" if details else message
        if isinstance(error, str) and error.strip():
            return error.strip()
        message = str(data.get("message") or "").strip()
        if message:
            return message
    return _composio_error_message(tool_slug, response)


def execute_tool(
    tool_slug: str,
    *,
    arguments: Mapping[str, Any] | None = None,
    connected_account_id: str | None = None,
    user_id: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if connected_account_id:
        payload["connected_account_id"] = str(connected_account_id).strip()
    if user_id:
        payload["user_id"] = str(user_id).strip()
    if arguments is not None:
        payload["arguments"] = dict(arguments)
    response = _request(
        "POST",
        f"tools/execute/{tool_slug}",
        json_body=payload,
        timeout=timeout,
    )
    # Composio returns HTTP 200 with ``successful:false`` for upstream provider rejections
    # (e.g. Twitter "duplicate content", "Unauthorized", rate limits). Without this guard the
    # caller sees a no-id success envelope, mis-treats it as a posted tweet, and commits a
    # credit against a tweet that never shipped. Fail closed and surface the real provider error.
    if isinstance(response, Mapping) and response.get("successful") is False:
        message = _composio_error_message(tool_slug, response)
        log_id = str(response.get("log_id") or "").strip()
        raise ComposioDistributionError(
            f"Composio {tool_slug} returned successful=false: {message}"
            + (f" [log_id={log_id}]" if log_id else "")
        )
    return response


def upload_file_descriptor(
    *,
    toolkit_slug: str,
    tool_slug: str,
    file_path: Path,
    timeout: float = 120.0,
) -> dict[str, str]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise ComposioDistributionError(f"file not found for Composio upload: {path}")
    data = path.read_bytes()
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    request_payload = _request(
        "POST",
        "files/upload/request",
        json_body={
            "toolkit_slug": toolkit_slug,
            "tool_slug": tool_slug,
            "filename": path.name,
            "mimetype": mimetype,
            "md5": hashlib.md5(data).hexdigest(),
        },
        timeout=timeout,
    )
    upload_url = str(
        request_payload.get("new_presigned_url")
        or request_payload.get("newPresignedUrl")
        or ""
    ).strip()
    s3key = str(request_payload.get("key") or "").strip()
    if not upload_url or not s3key:
        raise ComposioDistributionError("Composio file upload request returned no upload URL or key")
    request = urllib.request.Request(
        upload_url,
        data=data,
        method="PUT",
        headers={"Content-Type": "application/octet-stream"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if int(getattr(response, "status", 200) or 200) >= 400:
                raise ComposioDistributionError(
                    f"Composio staged upload failed for {path.name} with status {response.status}"
                )
    except Exception as exc:
        raise ComposioDistributionError(f"Composio staged upload failed for {path.name}: {exc}") from exc
    return {
        "name": path.name,
        "mimetype": mimetype,
        "s3key": s3key,
    }


def reddit_proxy_request(
    *,
    method: str,
    endpoint: str,
    connected_account_id: str | None = None,
    body: Mapping[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "connected_account_id": connected_account_id or resolve_reddit_ads_connected_account_id(),
        "endpoint": endpoint,
        "method": method.upper(),
    }
    if body is not None:
        payload["body"] = dict(body)
    if parameters:
        payload["parameters"] = parameters
    return _request(
        "POST",
        "tools/execute/proxy",
        json_body=payload,
        timeout=timeout,
    )


def twitter_execute_tool(
    tool_slug: str,
    *,
    arguments: Mapping[str, Any] | None = None,
    connected_account_id: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    return execute_tool(
        tool_slug,
        arguments=arguments,
        connected_account_id=connected_account_id or resolve_twitter_connected_account_id(),
        user_id=_env_value("COMPOSIO_TWITTER_USER_ID") or _env_value("COMPOSIO_USER_ID") or _TWITTER_DEFAULT_USER_ID,
        timeout=timeout,
    )


def reddit_execute_tool(
    tool_slug: str,
    *,
    arguments: Mapping[str, Any] | None = None,
    connected_account_id: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    return execute_tool(
        tool_slug,
        arguments=arguments,
        connected_account_id=connected_account_id or resolve_reddit_organic_connected_account_id(),
        user_id=_env_value("COMPOSIO_REDDIT_USER_ID") or _env_value("COMPOSIO_USER_ID") or _REDDIT_ORGANIC_DEFAULT_USER_ID,
        timeout=timeout,
    )


def metaads_execute_tool(
    tool_slug: str,
    *,
    arguments: Mapping[str, Any] | None = None,
    connected_account_id: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    raise ComposioDistributionError(
        "Composio Meta Ads tools are disabled for Takyon Meta v2; use official Meta Ads MCP"
    )


def metaads_proxy_request(
    *,
    method: str,
    endpoint: str,
    connected_account_id: str | None = None,
    body: Mapping[str, Any] | None = None,
    parameters: list[dict[str, Any]] | None = None,
    binary_body: Mapping[str, Any] | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    raise ComposioDistributionError(
        "Composio METAADS_PROXY is disabled for Takyon Meta v2; use official Meta Ads MCP"
    )
