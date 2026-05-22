"""Artifact publishing for Argon/Hermes API-server deployments.

User-facing generated files should be staged under ``/opt/data/workspace`` and
published into ``/opt/data/artifacts`` before the final response. Download URLs
are signed so public notification clicks do not require custom auth headers.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple


class ArtifactError(RuntimeError):
    pass


def workspace_root() -> Path:
    return Path(os.environ.get("ARGON_WORKSPACE_ROOT") or "/opt/data/workspace").expanduser().resolve()


def artifacts_root() -> Path:
    return Path(os.environ.get("ARGON_ARTIFACTS_ROOT") or "/opt/data/artifacts").expanduser().resolve()


def artifact_ttl_seconds() -> int:
    return int(os.environ.get("ARGON_ARTIFACT_TTL_SECONDS") or str(7 * 24 * 60 * 60))


def workspace_ttl_seconds() -> int:
    return int(os.environ.get("ARGON_WORKSPACE_TTL_SECONDS") or str(7 * 24 * 60 * 60))


def artifact_max_bytes() -> int:
    return int(os.environ.get("ARGON_ARTIFACT_MAX_BYTES") or str(500 * 1024 * 1024))


def public_base_url() -> str:
    explicit = (
        os.environ.get("ARGON_PUBLIC_BASE_URL")
        or os.environ.get("VOICE_ARGON_PUBLIC_BASE_URL")
        or os.environ.get("API_SERVER_PUBLIC_BASE_URL")
    )
    if explicit:
        return explicit.rstrip("/")
    host = os.environ.get("ARGON_PUBLIC_HOST")
    if host:
        return f"https://{host.strip().strip('/')}"
    raise ArtifactError("artifact publishing requires ARGON_PUBLIC_BASE_URL or ARGON_PUBLIC_HOST")


def signing_secret() -> bytes:
    secret = os.environ.get("ARGON_ARTIFACT_SIGNING_KEY") or os.environ.get("API_SERVER_KEY")
    if not secret:
        raise ArtifactError("artifact publishing requires ARGON_ARTIFACT_SIGNING_KEY or API_SERVER_KEY")
    return secret.encode("utf-8")


def _safe_name(value: str, default: str = "artifact") -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip(".-")
    return clean[:90] or default


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _source_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for item in path.rglob("*"):
        if item.is_symlink():
            raise ArtifactError(f"artifact source contains symlink, refusing to publish: {item}")
        if item.is_file():
            total += item.stat().st_size
    return total


def _zip_source(source: Path, output: Path, top_level_name: str) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        if source.is_file():
            zf.write(source, arcname=source.name)
            return
        for item in sorted(source.rglob("*")):
            if item.is_dir():
                continue
            if item.is_symlink():
                raise ArtifactError(f"artifact source contains symlink, refusing to publish: {item}")
            arcname = Path(top_level_name) / item.relative_to(source)
            zf.write(item, arcname=str(arcname))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sign_token(artifact_id: str, expires_at: int) -> str:
    payload = f"{artifact_id}:{expires_at}"
    sig = hmac.new(signing_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}:{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")


def verify_token(token: str, artifact_id: str) -> Tuple[bool, str]:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        token_artifact_id, raw_expires, sig = raw.split(":", 2)
        expires_at = int(raw_expires)
    except Exception:
        return False, "invalid artifact token"
    if token_artifact_id != artifact_id:
        return False, "artifact token does not match artifact"
    if expires_at < int(time.time()):
        return False, "artifact token expired"
    expected = hmac.new(signing_secret(), f"{artifact_id}:{expires_at}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "artifact token signature mismatch"
    return True, ""


def manifest_path(artifact_id: str) -> Path:
    return artifacts_root() / _safe_name(artifact_id, "artifact") / "manifest.json"


def load_manifest(artifact_id: str) -> Dict[str, Any]:
    path = manifest_path(artifact_id)
    if not path.is_file():
        raise ArtifactError(f"artifact not found: {artifact_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ArtifactError(f"artifact manifest is unreadable: {artifact_id}") from exc
    if not isinstance(data, dict):
        raise ArtifactError(f"artifact manifest is invalid: {artifact_id}")
    return data


def artifact_file_path(artifact_id: str) -> Path:
    data = load_manifest(artifact_id)
    path = Path(str(data.get("path") or "")).expanduser().resolve()
    root = artifacts_root()
    if not _is_relative_to(path, root):
        raise ArtifactError("artifact manifest path escapes artifact root")
    if not path.is_file():
        raise ArtifactError(f"artifact file is missing: {artifact_id}")
    return path


def cleanup_expired_artifacts() -> Dict[str, Any]:
    root = artifacts_root()
    if not root.exists():
        return {"deleted": [], "errors": []}
    now = int(time.time())
    deleted: List[str] = []
    errors: List[Dict[str, str]] = []
    for manifest in root.glob("*/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            expires_at = int(data.get("expires_at") or 0)
            if expires_at and expires_at < now:
                shutil.rmtree(manifest.parent)
                deleted.append(manifest.parent.name)
        except Exception as exc:
            errors.append({"path": str(manifest), "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


def cleanup_stale_workspaces(ttl_seconds: int | None = None) -> Dict[str, Any]:
    root = workspace_root()
    if not root.exists():
        return {"deleted": [], "errors": []}
    ttl = workspace_ttl_seconds() if ttl_seconds is None else int(ttl_seconds)
    if ttl <= 0:
        return {"deleted": [], "errors": []}
    now = time.time()
    deleted: List[str] = []
    errors: List[Dict[str, str]] = []
    for item in root.iterdir():
        try:
            if item.is_symlink():
                continue
            age = now - item.stat().st_mtime
            if age <= ttl:
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            deleted.append(item.name)
        except Exception as exc:
            errors.append({"path": str(item), "error": str(exc)})
    return {"deleted": deleted, "errors": errors}


def publish_artifact(path: str, name: str = "", cleanup_source: bool = True, ttl_seconds: int | None = None) -> Dict[str, Any]:
    source = Path(path).expanduser().resolve()
    root = workspace_root()
    if not source.exists():
        raise ArtifactError(f"artifact source does not exist: {source}")
    if not _is_relative_to(source, root):
        raise ArtifactError(f"artifact source must be under {root}: {source}")
    if source == root:
        raise ArtifactError("refusing to publish the workspace root itself")

    size = _source_size(source)
    max_bytes = artifact_max_bytes()
    if size > max_bytes:
        raise ArtifactError(f"artifact source is too large: {size} bytes > {max_bytes} bytes")

    cleanup_report = cleanup_expired_artifacts()
    artifact_id = f"art_{uuid.uuid4().hex[:24]}"
    output_name = _safe_name(name or source.name, "artifact")
    if not output_name.endswith(".zip"):
        filename = f"{output_name}.zip"
        top_level = output_name
    else:
        filename = output_name
        top_level = output_name[:-4] or "artifact"

    artifact_dir = artifacts_root() / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=False)
    output = artifact_dir / filename
    _zip_source(source, output, top_level)
    output_size = output.stat().st_size
    digest = _sha256_file(output)
    expires_at = int(time.time()) + int(ttl_seconds if ttl_seconds is not None else artifact_ttl_seconds())
    token = _sign_token(artifact_id, expires_at)
    url = f"{public_base_url()}/v1/artifacts/{artifact_id}/download?token={token}"
    manifest = {
        "schema_version": 1,
        "artifact_id": artifact_id,
        "created_at": int(time.time()),
        "expires_at": expires_at,
        "source_path": str(source),
        "path": str(output),
        "filename": filename,
        "source_size_bytes": size,
        "size_bytes": output_size,
        "sha256": digest,
        "url": url,
    }
    (artifact_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    source_cleaned = False
    if cleanup_source:
        if source.is_dir():
            shutil.rmtree(source)
        else:
            source.unlink()
        source_cleaned = True

    return {
        "success": True,
        "artifact_id": artifact_id,
        "url": url,
        "filename": filename,
        "path": str(output),
        "size_bytes": output_size,
        "source_size_bytes": size,
        "sha256": digest,
        "expires_at": expires_at,
        "source_cleaned": source_cleaned,
        "cleanup_report": cleanup_report,
    }
