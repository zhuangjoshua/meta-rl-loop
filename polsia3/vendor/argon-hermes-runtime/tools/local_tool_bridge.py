"""Shared bridge for VPS Hermes tools that execute on the user's Mac."""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from gateway.local_tool_jobs import get_local_tool_job_store


def call_local_tool(
    *,
    action: str,
    payload: Dict[str, Any] | None = None,
    device_id: str = "",
    timeout_seconds: float = 90.0,
    created_by: str = "local_tool",
    raise_on_failure: bool = False,
) -> str:
    store = get_local_tool_job_store()
    target_device = (device_id or os.getenv("VOICE_ARGON_DEVICE_ID") or "local-mac").strip() or "local-mac"
    job = store.create(
        action=action,
        payload=payload or {},
        device_id=target_device,
        timeout_seconds=float(timeout_seconds),
        created_by=created_by,
    )
    completed = store.wait(job["job_id"], float(timeout_seconds) + 3.0)
    if not completed:
        missing = {
            "success": False,
            "job_id": job["job_id"],
            "status": "missing",
            "error": "Local tool job disappeared before completion",
        }
        if raise_on_failure:
            raise RuntimeError(_local_tool_error_message({**missing, "action": action}))
        return json.dumps(missing, ensure_ascii=False)

    result = completed.get("result")
    if isinstance(result, dict):
        merged = dict(result)
        merged.setdefault("success", completed.get("status") == "completed" and result.get("success") is not False)
        merged.setdefault("job_id", completed.get("job_id"))
        merged.setdefault("device_id", completed.get("device_id"))
        merged.setdefault("action", completed.get("action"))
        merged.setdefault("status", completed.get("status"))
        if completed.get("error") and "error" not in merged:
            merged["error"] = completed.get("error")
        if raise_on_failure and (completed.get("status") != "completed" or merged.get("success") is False):
            raise RuntimeError(_local_tool_error_message(merged))
        return json.dumps(merged, ensure_ascii=False)

    merged = {
        "success": completed.get("status") == "completed",
        "job_id": completed.get("job_id"),
        "device_id": completed.get("device_id"),
        "action": completed.get("action"),
        "status": completed.get("status"),
        "result": result,
        "error": completed.get("error"),
    }
    if raise_on_failure and completed.get("status") != "completed":
        raise RuntimeError(_local_tool_error_message(merged))
    return json.dumps(merged, ensure_ascii=False)


def _local_tool_error_message(payload: Dict[str, Any]) -> str:
    action = str(payload.get("action") or "local tool")
    status = str(payload.get("status") or "failed")
    error = payload.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error)
    else:
        message = str(error or "").strip()
    blocked = payload.get("blocked_on")
    if blocked:
        return f"{action} blocked with status {status}: {message or blocked}"
    return f"{action} failed with status {status}" + (f": {message}" if message else "")
