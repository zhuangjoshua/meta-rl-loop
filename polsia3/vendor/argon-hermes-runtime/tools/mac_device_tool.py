"""Tool for routing allowlisted local Mac work through the VPS API server."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from gateway.device_jobs import get_device_job_store
from tools.registry import registry


SUPPORTED_ACTIONS = [
    "screenshot",
    "accessibility_tree",
    "click",
    "type_text",
    "clipboard_read",
    "clipboard_write",
    "open_url",
    "scan_desktop",
    "desktop_move",
]


MAC_DEVICE_ACTION_SCHEMA = {
    "name": "mac_device_action",
    "description": (
        "Ask the user's Mac app to execute one allowlisted local-device action. "
        "Use this when a VPS-hosted agent needs the user's current Mac screen, "
        "accessibility tree, clipboard, browser-open action, or click/type. "
        "For local file scan/move, prefer local_list_files and "
        "local_move_files; scan_desktop/desktop_move remain private "
        "compatibility actions. The Mac app must be running, authenticated with the same "
        "VPS API key, and granted the relevant macOS permissions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": SUPPORTED_ACTIONS,
                "description": "Allowlisted local action for the Mac app to execute.",
            },
            "payload": {
                "type": "object",
                "description": (
                    "Action parameters. Examples: screenshot {include_image: true, max_dimension: 1280}; "
                    "click {normalized_x: 0.5, normalized_y: 0.5}; type_text {text: 'hello'}; "
                    "clipboard_write {text: 'hello'}; open_url {url: 'https://example.com'}; "
                    "desktop_move {source_name: 'file.pdf', destination_folder: 'Documents', apply: false}."
                ),
            },
            "device_id": {
                "type": "string",
                "description": "Target Mac device id. Defaults to local-mac for one-user VPS alpha deployments.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "How long to wait for the Mac to claim and complete the job. Defaults to 90 seconds.",
            },
        },
        "required": ["action"],
    },
}


def _handle_mac_device_action_sync(args: Dict[str, Any], **_kwargs: Any) -> str:
    action = str(args.get("action") or "").strip()
    if action not in SUPPORTED_ACTIONS:
        return json.dumps({
            "success": False,
            "error": f"Unsupported Mac device action: {action}",
            "supported_actions": SUPPORTED_ACTIONS,
        }, ensure_ascii=False)

    payload = args.get("payload") or {}
    if not isinstance(payload, dict):
        return json.dumps({"success": False, "error": "payload must be an object"}, ensure_ascii=False)

    device_id = str(args.get("device_id") or os.getenv("VOICE_ARGON_DEVICE_ID") or "local-mac").strip() or "local-mac"
    timeout_seconds = args.get("timeout_seconds") or 90
    store = get_device_job_store()
    job = store.create(
        action=action,
        payload=payload,
        device_id=device_id,
        timeout_seconds=float(timeout_seconds),
        created_by="mac_device_action",
    )
    completed = store.wait(job["job_id"], float(timeout_seconds) + 3.0)
    if not completed:
        return json.dumps({
            "success": False,
            "job_id": job["job_id"],
            "status": "missing",
            "error": "Mac device job disappeared before completion",
        }, ensure_ascii=False)

    status = completed.get("status")
    return json.dumps({
        "success": status == "completed",
        "job_id": completed.get("job_id"),
        "device_id": completed.get("device_id"),
        "action": completed.get("action"),
        "status": status,
        "result": completed.get("result"),
        "error": completed.get("error"),
    }, ensure_ascii=False)


async def _handle_mac_device_action(args: Dict[str, Any], **kwargs: Any) -> str:
    return _handle_mac_device_action_sync(args, **kwargs)


registry.register(
    name="mac_device_action",
    toolset="mac_device",
    schema=MAC_DEVICE_ACTION_SCHEMA,
    handler=_handle_mac_device_action,
    is_async=True,
    emoji="Mac",
    max_result_size_chars=800_000,
)
