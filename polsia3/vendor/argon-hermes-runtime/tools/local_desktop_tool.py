"""Mac-local move-only file tools exposed as first-class Hermes tools."""

from __future__ import annotations

from typing import Any, Dict

from tools.local_tool_bridge import call_local_tool
from tools.registry import registry


LOCAL_LIST_FILES_SCHEMA = {
    "name": "local_list_files",
    "description": (
        "List files/folders on the user's Mac through the Argon Local Executor. "
        "Use for simple inspection of user-local folders such as Desktop, Downloads, "
        "Documents, or home subfolders. Do not use VPS file tools for user-local files. "
        "For broader Mac-local filesystem, build, shell, or app workflows, use the local_computer skill and local_shell_exec/local_fs_* tools."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "enum": ["desktop", "downloads", "documents", "home"],
                "description": "Mac-local root to inspect. Defaults to desktop.",
            },
            "relative_path": {
                "type": "string",
                "description": "Optional folder path under root. Must be relative and must not contain '..'.",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden dotfiles. Defaults to false.",
                "default": False,
            },
            "max_entries": {
                "type": "integer",
                "description": "Maximum entries to return. Defaults to 200, capped by the Mac app.",
            },
            "device_id": {
                "type": "string",
                "description": "Target Mac device id. Defaults to local-mac.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "How long to wait for the Mac to complete the job. Defaults to 90 seconds.",
            },
        },
        "required": [],
    },
}


LOCAL_MOVE_FILES_SCHEMA = {
    "name": "local_move_files",
    "description": (
        "Bulk move files/folders on the user's Mac through the Argon Local Executor. "
        "This is the allowed Mac cleanup primitive: move/quarantine only, never "
        "delete/trash/unlink. Sources and destination must be relative to allowed "
        "Mac-local roots. Use local_list_files first unless exact relative paths are known. "
        "For non-root-scoped workdirs or shell-driven workflows, use local_fs_move or local_shell_exec."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "root": {
                "type": "string",
                "enum": ["desktop", "downloads", "documents", "home"],
                "description": "Mac-local root containing the source files. Defaults to desktop.",
            },
            "sources": {
                "type": "array",
                "items": {"type": "string"},
                "description": "One or more source relative paths under root. Paths must not be absolute or contain '..'.",
            },
            "destination_root": {
                "type": "string",
                "enum": ["desktop", "downloads", "documents", "home"],
                "description": "Mac-local destination root. Defaults to root.",
            },
            "destination_relative_path": {
                "type": "string",
                "description": "Destination folder relative to destination_root. Created when apply=true.",
            },
            "apply": {
                "type": "boolean",
                "description": "When false, dry-run and report the planned move. When true, perform the move.",
                "default": False,
            },
            "collision_policy": {
                "type": "string",
                "enum": ["fail", "rename"],
                "description": "What to do if a destination filename already exists. Defaults to fail.",
                "default": "fail",
            },
            "device_id": {
                "type": "string",
                "description": "Target Mac device id. Defaults to local-mac.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "How long to wait for the Mac to complete the job. Defaults to 90 seconds.",
            },
        },
        "required": ["sources", "destination_relative_path"],
    },
}


LOCAL_SCAN_DESKTOP_SCHEMA = {
    "name": "local_scan_desktop",
    "description": "Compatibility alias for local_list_files(root='desktop'). Prefer local_list_files for new work.",
    "parameters": {
        "type": "object",
        "properties": {
            "max_entries": {"type": "integer", "description": "Maximum Desktop entries to return."},
            "device_id": {"type": "string", "description": "Target Mac device id. Defaults to local-mac."},
            "timeout_seconds": {"type": "number", "description": "How long to wait for the Mac to complete the job."},
        },
        "required": [],
    },
}


LOCAL_MOVE_DESKTOP_ITEM_SCHEMA = {
    "name": "local_move_desktop_item",
    "description": "Compatibility alias for local_move_files(root='desktop', sources=[source_name], destination_relative_path=destination_folder). Prefer local_move_files for new work.",
    "parameters": {
        "type": "object",
        "properties": {
            "source_name": {"type": "string", "description": "Exact direct child filename/folder name on the Desktop."},
            "destination_folder": {"type": "string", "description": "Direct destination folder name on the Desktop."},
            "apply": {"type": "boolean", "description": "When false, dry-run. When true, perform the move.", "default": False},
            "device_id": {"type": "string", "description": "Target Mac device id. Defaults to local-mac."},
            "timeout_seconds": {"type": "number", "description": "How long to wait for the Mac to complete the job."},
        },
        "required": ["source_name", "destination_folder"],
    },
}


def _timeout(args: Dict[str, Any]) -> float:
    return float(args.get("timeout_seconds") or 90)


async def _handle_local_list_files(args: Dict[str, Any], **_kwargs: Any) -> str:
    payload = {
        "root": args.get("root", "desktop"),
        "relative_path": args.get("relative_path", ""),
        "include_hidden": bool(args.get("include_hidden", False)),
        "max_entries": args.get("max_entries", 200),
    }
    return call_local_tool(
        action="local_list_files",
        payload=payload,
        device_id=str(args.get("device_id") or ""),
        timeout_seconds=_timeout(args),
        created_by="local_list_files",
    )


async def _handle_local_move_files(args: Dict[str, Any], **_kwargs: Any) -> str:
    payload = {
        "root": args.get("root", "desktop"),
        "sources": args.get("sources") or [],
        "destination_root": args.get("destination_root") or args.get("root") or "desktop",
        "destination_relative_path": args.get("destination_relative_path"),
        "apply": bool(args.get("apply", False)),
        "collision_policy": args.get("collision_policy", "fail"),
    }
    return call_local_tool(
        action="local_move_files",
        payload=payload,
        device_id=str(args.get("device_id") or ""),
        timeout_seconds=_timeout(args),
        created_by="local_move_files",
    )


async def _handle_local_scan_desktop(args: Dict[str, Any], **_kwargs: Any) -> str:
    next_args = dict(args)
    next_args["root"] = "desktop"
    return await _handle_local_list_files(next_args, **_kwargs)


async def _handle_local_move_desktop_item(args: Dict[str, Any], **_kwargs: Any) -> str:
    next_args = dict(args)
    next_args.update({
        "root": "desktop",
        "sources": [args.get("source_name")],
        "destination_root": "desktop",
        "destination_relative_path": args.get("destination_folder"),
    })
    return await _handle_local_move_files(next_args, **_kwargs)


registry.register(
    name="local_list_files",
    toolset="local_files",
    schema=LOCAL_LIST_FILES_SCHEMA,
    handler=_handle_local_list_files,
    is_async=True,
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_move_files",
    toolset="local_files",
    schema=LOCAL_MOVE_FILES_SCHEMA,
    handler=_handle_local_move_files,
    is_async=True,
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_scan_desktop",
    toolset="local_files_compat",
    schema=LOCAL_SCAN_DESKTOP_SCHEMA,
    handler=_handle_local_scan_desktop,
    is_async=True,
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_move_desktop_item",
    toolset="local_files_compat",
    schema=LOCAL_MOVE_DESKTOP_ITEM_SCHEMA,
    handler=_handle_local_move_desktop_item,
    is_async=True,
    emoji="Mac",
    max_result_size_chars=800_000,
)
