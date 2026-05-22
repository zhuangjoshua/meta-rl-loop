"""Hermes tool for publishing VPS-created deliverables."""

from __future__ import annotations

import json
from typing import Any, Dict

from gateway.artifacts import ArtifactError, publish_artifact
from tools.local_tool_bridge import call_local_tool
from tools.registry import registry


PUBLISH_ARTIFACT_SCHEMA = {
    "name": "publish_artifact",
    "description": (
        "Publish a user-facing file or folder created on the VPS. The source "
        "must be under /opt/data/workspace. This zips it into /opt/data/artifacts, "
        "returns a signed download URL, and deletes the source by default. Use "
        "this for generated apps, project folders, reports, datasets, images, "
        "archives, or other deliverables that should reach the user's frontend. "
        "Do not use raw /opt/data or /var/lib/argon paths as final delivery."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute source file/folder path under /opt/data/workspace.",
            },
            "name": {
                "type": "string",
                "description": "Optional downloadable artifact name. .zip is appended if omitted.",
            },
            "cleanup_source": {
                "type": "boolean",
                "description": "Delete the source path after the artifact is published. Defaults to true.",
                "default": True,
            },
            "ttl_seconds": {
                "type": "integer",
                "description": "Optional artifact URL TTL in seconds. Defaults to ARGON_ARTIFACT_TTL_SECONDS.",
            },
        },
        "required": ["path"],
    },
}


DELIVER_ARTIFACT_TO_MAC_SCHEMA = {
    "name": "deliver_artifact_to_mac",
    "description": (
        "Publish a VPS-created file or folder as a signed artifact and then "
        "download it to the user's Mac. Use this for normal user-facing "
        "deliverables when the user expects to receive the files locally. The "
        "source must be under /opt/data/workspace. Returns both artifact_url "
        "and local_path. Fails loudly if the Mac is offline or cannot verify "
        "the download."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Absolute source file/folder path under /opt/data/workspace.",
            },
            "name": {
                "type": "string",
                "description": "Optional artifact/download name. .zip is appended if omitted.",
            },
            "cleanup_source": {
                "type": "boolean",
                "description": "Delete the source path after publishing. Defaults to true.",
                "default": True,
            },
            "device_id": {
                "type": "string",
                "description": "Target Mac device id. Defaults to local-mac.",
            },
            "timeout_seconds": {
                "type": "number",
                "description": "Seconds to wait for the Mac download. Defaults to 300.",
            },
            "ttl_seconds": {
                "type": "integer",
                "description": "Optional artifact URL TTL in seconds. Defaults to ARGON_ARTIFACT_TTL_SECONDS.",
            },
        },
        "required": ["path"],
    },
}


def _handle_publish_artifact(args: Dict[str, Any], **_kwargs: Any) -> str:
    try:
        result = publish_artifact(
            path=str(args.get("path") or ""),
            name=str(args.get("name") or ""),
            cleanup_source=bool(args.get("cleanup_source", True)),
            ttl_seconds=args.get("ttl_seconds") if args.get("ttl_seconds") is not None else None,
        )
        return json.dumps(result, ensure_ascii=False)
    except ArtifactError as exc:
        return json.dumps({"success": False, "error": str(exc), "status": "failed"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"publish_artifact failed: {exc}", "status": "failed"}, ensure_ascii=False)


def _handle_deliver_artifact_to_mac(args: Dict[str, Any], **_kwargs: Any) -> str:
    try:
        artifact = publish_artifact(
            path=str(args.get("path") or ""),
            name=str(args.get("name") or ""),
            cleanup_source=bool(args.get("cleanup_source", True)),
            ttl_seconds=args.get("ttl_seconds") if args.get("ttl_seconds") is not None else None,
        )
        timeout = float(args.get("timeout_seconds") or 300)
        raw = call_local_tool(
            action="local_artifact_download",
            payload={
                "artifact_id": artifact.get("artifact_id"),
                "url": artifact.get("url"),
                "filename": artifact.get("filename"),
                "sha256": artifact.get("sha256"),
                "size_bytes": artifact.get("size_bytes"),
            },
            device_id=str(args.get("device_id") or ""),
            timeout_seconds=timeout,
            created_by="deliver_artifact_to_mac",
        )
        download = json.loads(raw)
        if not download.get("success"):
            if download.get("status") == "blocked":
                return json.dumps({
                    "success": False,
                    "status": "blocked",
                    "blocked_on": download.get("blocked_on") or [],
                    "error": download.get("error") or "Mac delivery is blocked on a local capability",
                    "artifact": artifact,
                    "artifact_url": artifact.get("url"),
                    "artifact_id": artifact.get("artifact_id"),
                    "source_cleaned": artifact.get("source_cleaned"),
                    "download": download,
                }, ensure_ascii=False)
            return json.dumps({
                "success": False,
                "status": "mac_delivery_failed",
                "error": download.get("error") or download,
                "artifact": artifact,
                "artifact_url": artifact.get("url"),
                "artifact_id": artifact.get("artifact_id"),
                "source_cleaned": artifact.get("source_cleaned"),
            }, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "artifact": artifact,
            "artifact_id": artifact.get("artifact_id"),
            "artifact_url": artifact.get("url"),
            "local_path": download.get("local_path"),
            "filename": download.get("filename") or artifact.get("filename"),
            "size_bytes": download.get("size_bytes") or artifact.get("size_bytes"),
            "sha256": download.get("sha256") or artifact.get("sha256"),
            "source_cleaned": artifact.get("source_cleaned"),
            "download": download,
        }, ensure_ascii=False)
    except ArtifactError as exc:
        return json.dumps({"success": False, "error": str(exc), "status": "failed"}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"success": False, "error": f"deliver_artifact_to_mac failed: {exc}", "status": "failed"}, ensure_ascii=False)


registry.register(
    name="publish_artifact",
    toolset="artifact",
    schema=PUBLISH_ARTIFACT_SCHEMA,
    handler=_handle_publish_artifact,
    emoji="Artifact",
    max_result_size_chars=20_000,
)

registry.register(
    name="deliver_artifact_to_mac",
    toolset="artifact",
    schema=DELIVER_ARTIFACT_TO_MAC_SCHEMA,
    handler=_handle_deliver_artifact_to_mac,
    emoji="Artifact",
    max_result_size_chars=30_000,
)
