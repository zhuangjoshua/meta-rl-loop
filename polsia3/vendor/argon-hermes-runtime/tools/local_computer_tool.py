"""Mac-local shell and scoped filesystem primitives."""

from __future__ import annotations

from typing import Any, Dict

from tools.local_tool_bridge import call_local_tool
from tools.registry import registry


def _schema(name: str, description: str, properties: Dict[str, Any], required: list[str]) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


DEVICE_AND_TIMEOUT = {
    "device_id": {
        "type": "string",
        "description": "Target Mac device id. Defaults to local-mac.",
    },
    "timeout_seconds": {
        "type": "number",
        "description": "How long Hermes should wait for the Mac job. Defaults to 120 seconds.",
    },
}


LOCAL_ROOT_PROPS = {
    "root": {
        "type": "string",
        "enum": ["desktop", "downloads", "documents", "home"],
        "description": (
            "Named Mac-local root. Prefer this for common user folders so Hermes does not guess "
            "the Mac username. Use downloads for 'my Downloads', desktop for 'my Desktop', "
            "documents for 'my Documents', and home for a path under the user's home folder."
        ),
    },
    "workdir_relative_path": {
        "type": "string",
        "description": (
            "Optional relative subfolder under root to use as the working directory. "
            "Must not be absolute or contain '..'."
        ),
    },
}


WORKDIR_PROP = {
    **LOCAL_ROOT_PROPS,
    "workdir": {
        "type": "string",
        "description": (
            "Optional absolute directory path on the user's Mac. Use only when the user explicitly gave "
            "an absolute path or a prior local tool returned that exact path. Prefer root plus "
            "workdir_relative_path for Desktop/Downloads/Documents/home."
        ),
    },
}


LOCAL_COMPUTER_SCHEMAS = [
    _schema(
        "local_shell_exec",
        (
            "Run a noninteractive shell command on the user's Mac through the Argon Local Executor. "
            "Use for Mac-local builds, filesystem workflows, app tooling, git in a local repo, Finder/osascript, "
            "or other local computer tasks that require the user's Mac rather than the VPS. "
            "Hermes still owns planning and model reasoning on the VPS; this tool only executes the command. "
            "For common user folders, pass root/workdir_relative_path instead of inventing /Users/<name>. "
            "Use VPS terminal tools for VPS/cloud files, and use local browser tools for browser automation."
        ),
        {
            **WORKDIR_PROP,
            "command": {
                "type": "string",
                "description": "Shell command to run with workdir as the current directory.",
            },
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional command environment overrides. Do not pass provider API keys.",
            },
            "max_output_bytes": {
                "type": "integer",
                "description": "Maximum stdout/stderr bytes retained per stream. Defaults to 200000.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        ["command"],
    ),
    _schema(
        "local_fs_list",
        (
            "List files under a Mac-local workdir. Use root/workdir_relative_path for common user folders; "
            "use absolute workdir only when explicitly provided or returned by a local tool. Paths are relative "
            "to the resolved workdir."
        ),
        {
            **WORKDIR_PROP,
            "path": {
                "type": "string",
                "description": "Relative directory path under workdir. Defaults to the workdir root.",
            },
            "recursive": {
                "type": "boolean",
                "description": "Whether to recurse. Defaults to false.",
            },
            "include_hidden": {
                "type": "boolean",
                "description": "Include hidden dotfiles. Defaults to false.",
            },
            "max_entries": {
                "type": "integer",
                "description": "Maximum entries to return. Defaults to 200.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        [],
    ),
    _schema(
        "local_fs_read",
        (
            "Read a file under a Mac-local workdir. Use for user-local files only. "
            "Use root/workdir_relative_path for common folders. Paths are relative to the resolved workdir and cannot escape it."
        ),
        {
            **WORKDIR_PROP,
            "path": {"type": "string", "description": "Relative file path under workdir."},
            "max_bytes": {"type": "integer", "description": "Maximum bytes to read. Defaults to 200000."},
            "encoding": {
                "type": "string",
                "enum": ["auto", "utf8", "base64"],
                "description": "Return encoding. Defaults to auto.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        ["path"],
    ),
    _schema(
        "local_fs_write",
        (
            "Write a file under a Mac-local workdir. Use when Hermes has already decided exact file content. "
            "Use root/workdir_relative_path for common folders. Paths are relative to the resolved workdir and cannot escape it."
        ),
        {
            **WORKDIR_PROP,
            "path": {"type": "string", "description": "Relative file path under workdir."},
            "content": {"type": "string", "description": "UTF-8 text content to write."},
            "content_base64": {"type": "string", "description": "Base64 bytes to write instead of content."},
            "mode": {
                "type": "string",
                "enum": ["overwrite", "append", "fail_if_exists"],
                "description": "Write mode. Defaults to overwrite.",
            },
            "create_dirs": {
                "type": "boolean",
                "description": "Create parent directories. Defaults to true.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        ["path"],
    ),
    _schema(
        "local_fs_move",
        (
            "Move or rename one file/folder under a Mac-local workdir. Use for local organization when exact "
            "source and destination paths are known. Use root/workdir_relative_path for common folders. "
            "Paths are relative to the resolved workdir and cannot escape it."
        ),
        {
            **WORKDIR_PROP,
            "source_path": {"type": "string", "description": "Relative source path under workdir."},
            "destination_path": {"type": "string", "description": "Relative destination path under workdir."},
            "collision_policy": {
                "type": "string",
                "enum": ["fail", "replace"],
                "description": "What to do if destination exists. Defaults to fail.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        ["source_path", "destination_path"],
    ),
    _schema(
        "local_fs_delete",
        (
            "Delete a file/folder under a Mac-local workdir only when the user explicitly asks for deletion. "
            "Use root/workdir_relative_path for common folders. Paths are relative to the resolved workdir and cannot escape it. "
            "Prefer move/quarantine for ambiguous cleanup."
        ),
        {
            **WORKDIR_PROP,
            "path": {"type": "string", "description": "Relative path under workdir to delete."},
            "recursive": {
                "type": "boolean",
                "description": "Required for deleting non-empty directories. Defaults to false.",
            },
            **DEVICE_AND_TIMEOUT,
        },
        ["path"],
    ),
]


def _timeout(args: Dict[str, Any], default: float = 120) -> float:
    return float(args.get("timeout_seconds") or default)


def _handle(action: str, args: Dict[str, Any], **kwargs: Any) -> str:
    payload = dict(args)
    if kwargs.get("task_id"):
        payload.setdefault("owner_task_id", str(kwargs["task_id"]))
    device_id = str(payload.pop("device_id", "") or "")
    timeout = _timeout(payload)
    payload["timeout_seconds"] = timeout
    return call_local_tool(
        action=action,
        payload=payload,
        device_id=device_id,
        timeout_seconds=timeout,
        created_by=action,
    )


registry.register(
    name="local_shell_exec",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[0],
    handler=lambda args, **kw: _handle("local_shell_exec", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_fs_list",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[1],
    handler=lambda args, **kw: _handle("local_fs_list", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_fs_read",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[2],
    handler=lambda args, **kw: _handle("local_fs_read", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_fs_write",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[3],
    handler=lambda args, **kw: _handle("local_fs_write", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_fs_move",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[4],
    handler=lambda args, **kw: _handle("local_fs_move", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)

registry.register(
    name="local_fs_delete",
    toolset="local_computer",
    schema=LOCAL_COMPUTER_SCHEMAS[5],
    handler=lambda args, **kw: _handle("local_fs_delete", args, **kw),
    emoji="Mac",
    max_result_size_chars=800_000,
)
