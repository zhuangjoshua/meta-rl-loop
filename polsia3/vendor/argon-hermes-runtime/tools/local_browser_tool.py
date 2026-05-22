"""Tools that route browser work to the user's Mac Chrome surfaces."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Dict

from tools.local_tool_bridge import call_local_tool
from tools.registry import registry


def _schema(name: str, description: str, properties: Dict[str, Any], required: list[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


_SESSION_PROPS = {
    "session_id": {"type": "string", "description": "Active visible Chrome session id. If omitted, the Mac daemon uses or creates the single active-user-browser lease for this Hermes task."},
    "device_id": {"type": "string", "description": "Target Mac device id. Defaults to local-mac."},
}

_MANAGED_SESSION_PROPS = {
    "session_id": {"type": "string", "description": "Managed Chrome/CDP session id. If omitted, the Mac daemon uses or creates the Argon-managed Chrome tab for this Hermes task. This uses an Argon-owned Chrome profile, not the user's default Chrome profile."},
    "device_id": {"type": "string", "description": "Target Mac device id. Defaults to local-mac."},
}


LOCAL_BROWSER_SCHEMAS = [
    _schema(
        "local_browser_navigate",
        "Navigate the user's active visible Chrome session by setting the current tab URL only. This does not inspect the page and should not require Chrome JavaScript-from-Apple-Events. Use only when the user explicitly wants the current/visible Chrome tab. For credentialed/private browser work by default, use local_managed_browser_navigate instead.",
        {
            "url": {"type": "string", "description": "URL to open in the user's active visible Chrome session."},
            **_SESSION_PROPS,
        },
        ["url"],
    ),
    _schema(
        "local_browser_snapshot",
        "Get a text/DOM snapshot from the user's active visible Chrome tab with refs for local_browser_click and local_browser_type. Use only when the active visible Chrome tab is explicitly the target.",
        {
            "full": {"type": "boolean", "default": False, "description": "Return more page text when true."},
            **_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_browser_click",
        "Click an element in the user's active visible Chrome tab by ref from local_browser_snapshot. Use only when direct interaction with the visible browser is intended.",
        {
            "ref": {"type": "string", "description": "Element reference such as @e5."},
            **_SESSION_PROPS,
        },
        ["ref"],
    ),
    _schema(
        "local_browser_type",
        "Type into an element in the user's active visible Chrome tab by ref from local_browser_snapshot. Use only when direct interaction with the visible browser is intended.",
        {
            "ref": {"type": "string", "description": "Element reference such as @e3."},
            "text": {"type": "string", "description": "Text to enter."},
            **_SESSION_PROPS,
        },
        ["ref", "text"],
    ),
    _schema(
        "local_browser_scroll",
        "Scroll the user's active visible Chrome tab. Use only when the visible browser is explicitly the target.",
        {
            "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
            **_SESSION_PROPS,
        },
        ["direction"],
    ),
    _schema(
        "local_browser_back",
        "Go back in the user's active visible Chrome tab history. Use only when the visible browser is explicitly the target.",
        _SESSION_PROPS,
        [],
    ),
    _schema(
        "local_browser_press",
        "Press a keyboard key in the user's active visible Chrome tab. Use only when the visible browser is explicitly the target.",
        {
            "key": {"type": "string", "description": "Key such as Enter, Tab, Escape, ArrowDown."},
            **_SESSION_PROPS,
        },
        ["key"],
    ),
    _schema(
        "local_browser_get_images",
        "Get visible and page-declared images from the user's active visible Chrome page with URLs, alt text, natural size, and displayed size. Use local_managed_browser_get_images for default credentialed/private browser work.",
        _SESSION_PROPS,
        [],
    ),
    _schema(
        "local_browser_screenshot",
        "Capture the user's active visible Chrome tab. Use only when the visible Chrome tab is explicitly the target. For default credentialed/private browser visual state, use local_managed_browser_screenshot.",
        {
            "annotate": {"type": "boolean", "default": False, "description": "Overlay numbered [N] labels on interactive elements before screenshot capture."},
            **_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_browser_console",
        "Read console events captured from the user's active visible Chrome tab, or evaluate a JavaScript expression there. Use local_managed_browser_console for default credentialed/private browser work.",
        {
            "clear": {"type": "boolean", "default": False, "description": "Clear captured console events after reading."},
            "expression": {"type": "string", "description": "Optional JavaScript expression to evaluate in the page."},
            **_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_browser_dialog",
        "Respond to a pending native dialog in the user's active visible Chrome tab. Call local_browser_snapshot first and use pending_dialogs[].id when more than one dialog is present. Use only when the visible browser is explicitly the target.",
        {
            "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "Accept presses OK/Yes/Allow/Leave. Dismiss presses Cancel/No/Stay when present."},
            "prompt_text": {"type": "string", "description": "Text to enter for prompt() dialogs before accepting or dismissing."},
            "dialog_id": {"type": "string", "description": "Dialog id from local_browser_snapshot.pending_dialogs[].id."},
            **_SESSION_PROPS,
        },
        ["action"],
    ),
    _schema(
        "local_browser_stop",
        "Release the active visible Chrome lease for this local browser session.",
        _SESSION_PROPS,
        [],
    ),
]


LOCAL_BROWSER_VISION_SCHEMA = _schema(
    "local_browser_vision",
    "Capture the user's active visible Chrome tab visually and analyze it with the VPS vision model. Use only when the visible browser is explicitly the target. For default credentialed/private browser work, use local_managed_browser_vision.",
    {
        "question": {"type": "string", "description": "Question to answer about the visible browser page."},
        "annotate": {"type": "boolean", "default": False, "description": "Overlay numbered [N] labels on interactive elements before screenshot capture. Each label maps to ref @eN for later local_browser_click/type calls."},
        **_SESSION_PROPS,
    },
    ["question"],
)


LOCAL_MANAGED_BROWSER_SCHEMAS = [
    _schema(
        "local_managed_browser_start",
        "Start or attach to Argon's managed Chrome/CDP tab using an Argon-owned Chrome profile created with --user-data-dir. Use this for background/noninteractive browser work after the user has logged into that Argon Chrome profile. It does not use the user's default Chrome profile cookies. For the user's already-open logged-in Chrome session, use local_browser_* only when the user explicitly wants the visible browser.",
        _MANAGED_SESSION_PROPS,
        [],
    ),
    _schema(
        "local_managed_browser_navigate",
        "Navigate Argon's managed Chrome/CDP tab in the Argon-owned Chrome profile. This is background/noninteractive and does not use the user's default Chrome profile cookies.",
        {
            "url": {"type": "string", "description": "URL to open in the managed Argon Chrome tab."},
            **_MANAGED_SESSION_PROPS,
        },
        ["url"],
    ),
    _schema(
        "local_managed_browser_snapshot",
        "Get a text/DOM snapshot from Argon's managed Chrome/CDP tab with refs for click/type. This uses the Argon-owned Chrome profile, not the user's default Chrome profile.",
        {
            "full": {"type": "boolean", "default": False, "description": "Return more page text when true."},
            **_MANAGED_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_managed_browser_click",
        "Click an element in Argon's managed Chrome/CDP tab by ref from local_managed_browser_snapshot.",
        {
            "ref": {"type": "string", "description": "Element reference such as @e5."},
            **_MANAGED_SESSION_PROPS,
        },
        ["ref"],
    ),
    _schema(
        "local_managed_browser_type",
        "Type into an element in Argon's managed Chrome/CDP tab by ref from local_managed_browser_snapshot.",
        {
            "ref": {"type": "string", "description": "Element reference such as @e3."},
            "text": {"type": "string", "description": "Text to enter."},
            **_MANAGED_SESSION_PROPS,
        },
        ["ref", "text"],
    ),
    _schema(
        "local_managed_browser_scroll",
        "Scroll Argon's managed Chrome/CDP tab.",
        {
            "direction": {"type": "string", "enum": ["up", "down"], "description": "Scroll direction."},
            **_MANAGED_SESSION_PROPS,
        },
        ["direction"],
    ),
    _schema(
        "local_managed_browser_back",
        "Go back in Argon's managed Chrome/CDP tab history.",
        _MANAGED_SESSION_PROPS,
        [],
    ),
    _schema(
        "local_managed_browser_press",
        "Press a keyboard key in Argon's managed Chrome/CDP tab.",
        {
            "key": {"type": "string", "description": "Key such as Enter, Tab, Escape, ArrowDown."},
            **_MANAGED_SESSION_PROPS,
        },
        ["key"],
    ),
    _schema(
        "local_managed_browser_get_images",
        "Get visible and page-declared images from Argon's managed Chrome/CDP tab.",
        _MANAGED_SESSION_PROPS,
        [],
    ),
    _schema(
        "local_managed_browser_screenshot",
        "Capture Argon's managed Chrome/CDP tab without macOS screen capture. Returns base64 image data and optional annotations.",
        {
            "annotate": {"type": "boolean", "default": False, "description": "Overlay numbered [N] labels on interactive elements before screenshot capture."},
            **_MANAGED_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_managed_browser_console",
        "Read console events captured in Argon's managed Chrome/CDP tab, or evaluate a JavaScript expression in the page.",
        {
            "clear": {"type": "boolean", "default": False, "description": "Clear captured console events after reading."},
            "expression": {"type": "string", "description": "Optional JavaScript expression to evaluate in the page."},
            **_MANAGED_SESSION_PROPS,
        },
        [],
    ),
    _schema(
        "local_managed_browser_dialog",
        "Respond to a pending JavaScript dialog in Argon's managed Chrome/CDP tab.",
        {
            "action": {"type": "string", "enum": ["accept", "dismiss"], "description": "Accept or dismiss the pending JavaScript dialog."},
            "prompt_text": {"type": "string", "description": "Text to enter for prompt() dialogs before accepting."},
            **_MANAGED_SESSION_PROPS,
        },
        ["action"],
    ),
    _schema(
        "local_managed_browser_stop",
        "Release Argon's managed Chrome/CDP browser lease. Pass close_tab=true to close the Argon tab after credentialed/private Mac browser work is complete.",
        {
            "close_tab": {"type": "boolean", "default": False, "description": "Close the Argon managed tab before releasing the session."},
            **_MANAGED_SESSION_PROPS,
        },
        [],
    ),
]


LOCAL_MANAGED_BROWSER_VISION_SCHEMA = _schema(
    "local_managed_browser_vision",
    "Capture Argon's managed Chrome/CDP tab and analyze it with the VPS vision model. This uses the Argon-owned Chrome profile, not the user's default Chrome profile.",
    {
        "question": {"type": "string", "description": "Question to answer about the managed browser page."},
        "annotate": {"type": "boolean", "default": False, "description": "Overlay numbered [N] labels on interactive elements before screenshot capture."},
        **_MANAGED_SESSION_PROPS,
    },
    ["question"],
)


def _handle(action: str, args: Dict[str, Any], **kwargs: Any) -> str:
    payload = dict(args)
    if kwargs.get("task_id"):
        payload.setdefault("owner_task_id", str(kwargs["task_id"]))
    device_id = str(payload.pop("device_id", "") or "")
    return call_local_tool(action=action, payload=payload, device_id=device_id, timeout_seconds=90, created_by=action, raise_on_failure=True)


async def _handle_vision(args: Dict[str, Any], **kwargs: Any) -> str:
    payload = dict(args)
    if kwargs.get("task_id"):
        payload.setdefault("owner_task_id", str(kwargs["task_id"]))
    device_id = str(payload.pop("device_id", "") or "")
    raw = call_local_tool(
        action="local_browser_screenshot",
        payload=payload,
        device_id=device_id,
        timeout_seconds=90,
        created_by="local_browser_vision",
    )
    data = json.loads(raw)
    if not data.get("success"):
        return raw
    image_b64 = str(data.get("image_base64") or "")
    if not image_b64:
        return json.dumps({"success": False, "status": "failed", "error": "local browser screenshot returned no image"}, ensure_ascii=False)

    image_bytes = __import__("base64").b64decode(image_b64)
    tmp = Path(tempfile.gettempdir()) / f"argon-local-browser-{data.get('job_id', 'screenshot')}.jpg"
    tmp.write_bytes(image_bytes)
    from tools.vision_tools import vision_analyze_tool

    analysis = await vision_analyze_tool(
        image_url=str(tmp),
        user_prompt=str(args.get("question") or "Describe the visible browser page."),
        model=None,
    )
    return json.dumps({
        "success": True,
        "session_id": data.get("session_id"),
        "screenshot_path": str(tmp),
        "analysis": analysis,
        "annotations": data.get("annotations") or [],
    }, ensure_ascii=False)


async def _handle_managed_vision(args: Dict[str, Any], **kwargs: Any) -> str:
    payload = dict(args)
    if kwargs.get("task_id"):
        payload.setdefault("owner_task_id", str(kwargs["task_id"]))
    device_id = str(payload.pop("device_id", "") or "")
    raw = call_local_tool(
        action="local_managed_browser_screenshot",
        payload=payload,
        device_id=device_id,
        timeout_seconds=90,
        created_by="local_managed_browser_vision",
    )
    data = json.loads(raw)
    if not data.get("success"):
        return raw
    image_b64 = str(data.get("image_base64") or "")
    if not image_b64:
        return json.dumps({"success": False, "status": "failed", "error": "local managed browser screenshot returned no image"}, ensure_ascii=False)

    image_bytes = __import__("base64").b64decode(image_b64)
    tmp = Path(tempfile.gettempdir()) / f"argon-local-managed-browser-{data.get('job_id', 'screenshot')}.jpg"
    tmp.write_bytes(image_bytes)
    from tools.vision_tools import vision_analyze_tool

    analysis = await vision_analyze_tool(
        image_url=str(tmp),
        user_prompt=str(args.get("question") or "Describe the visible managed browser page."),
        model=None,
    )
    return json.dumps({
        "success": True,
        "session_id": data.get("session_id"),
        "screenshot_path": str(tmp),
        "analysis": analysis,
        "annotations": data.get("annotations") or [],
    }, ensure_ascii=False)


for _tool_schema in LOCAL_BROWSER_SCHEMAS:
    _name = _tool_schema["name"]
    registry.register(
        name=_name,
        toolset="local_browser",
        schema=_tool_schema,
        handler=lambda args, _action=_name, **kw: _handle(_action, args, **kw),
        emoji="MacBrowser",
        max_result_size_chars=300_000,
    )

registry.register(
    name="local_browser_vision",
    toolset="local_browser",
    schema=LOCAL_BROWSER_VISION_SCHEMA,
    handler=_handle_vision,
    is_async=True,
    emoji="MacBrowser",
    max_result_size_chars=300_000,
)

for _tool_schema in LOCAL_MANAGED_BROWSER_SCHEMAS:
    _name = _tool_schema["name"]
    registry.register(
        name=_name,
        toolset="local_managed_browser",
        schema=_tool_schema,
        handler=lambda args, _action=_name, **kw: _handle(_action, args, **kw),
        emoji="MacBrowser",
        max_result_size_chars=300_000,
    )

registry.register(
    name="local_managed_browser_vision",
    toolset="local_managed_browser",
    schema=LOCAL_MANAGED_BROWSER_VISION_SCHEMA,
    handler=_handle_managed_vision,
    is_async=True,
    emoji="MacBrowser",
    max_result_size_chars=300_000,
)
