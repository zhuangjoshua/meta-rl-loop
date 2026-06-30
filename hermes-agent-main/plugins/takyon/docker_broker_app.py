"""Local Docker broker service for the operator plane.

The dashboard and worker services call this service through a repo-tracked
``TAKYON_DOCKER_BINARY`` shim, so the user-facing planes no longer need direct
access to the host Docker daemon.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import queue
import re
import shutil
import subprocess
import threading
from typing import Iterable

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_BROKER_TOKEN_ENV = "TAKYON_DOCKER_BROKER_TOKEN"

_RUN_FLAG_NOARG = {"--rm", "--init", "-i", "-d", "--read-only"}
_RUN_FLAG_WITH_ARG = {
    "--cap-drop",
    "--cap-add",
    "--security-opt",
    "--pids-limit",
    "--tmpfs",
    "--mount",
    "-w",
    "-e",
    "-v",
    "--cpus",
    "--memory",
    "--storage-opt",
    "--network",
    "--user",
    "--name",
    "--entrypoint",
}
_EXEC_FLAG_NOARG = {"-i"}
_EXEC_FLAG_WITH_ARG = {"-e"}
_REMOVE_FLAG_NOARG = {"-f"}
_DOCKER_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]*$")
_DOCKER_CONTAINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class _DockerCommandBody(BaseModel):
    args: list[str]
    image: str
    command: list[str]
    stdin_b64: str | None = None


class _DockerExecBody(BaseModel):
    args: list[str]
    container_id: str
    command: list[str]
    stdin_b64: str | None = None


class _ContainerIdBody(BaseModel):
    container_id: str


class _RemoveBody(BaseModel):
    args: list[str]
    container_ids: list[str]


def _broker_token() -> str:
    # The docker proxy authorizes ONLY container lifecycle, so it has its OWN dedicated credential and
    # never accepts the master ``TAKYON_SAFEBOX_TOKEN``. Sharing the master token here was the
    # red-team's blast-radius bug — a single client-plane env read should not also authorize the
    # docker proxy.
    return str(os.getenv(_BROKER_TOKEN_ENV) or "").strip()


def _require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = _broker_token()
    if not expected:
        raise HTTPException(status_code=401, detail="docker broker token not configured")
    presented = str(authorization or "").strip()
    expected_header = f"Bearer {expected}"
    if not hmac.compare_digest(presented.encode(), expected_header.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


def _validated_image(image: str) -> str:
    value = str(image or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="image is required")
    if value.startswith("-") or any(ch.isspace() for ch in value):
        raise HTTPException(status_code=400, detail="invalid docker image")
    if not _DOCKER_IMAGE_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid docker image")
    return value


def _validated_container_id(container_id: str) -> str:
    value = str(container_id or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="container_id is required")
    if value.startswith("-") or any(ch.isspace() for ch in value):
        raise HTTPException(status_code=400, detail="invalid container_id")
    if not _DOCKER_CONTAINER_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="invalid container_id")
    return value


def _validated_container_ids(container_ids: list[str]) -> list[str]:
    values = [_validated_container_id(container_id) for container_id in container_ids]
    if not values:
        raise HTTPException(status_code=400, detail="container_ids is required")
    return values


def _validated_args(args: list[str], allowed_noarg: set[str], allowed_with_arg: set[str]) -> list[str]:
    normalized: list[str] = []
    i = 0
    while i < len(args):
        token = str(args[i] or "").strip()
        if not token:
            raise HTTPException(status_code=400, detail="empty docker option")
        if token in allowed_noarg:
            normalized.append(token)
            i += 1
            continue
        flag = token
        value: str | None = None
        if "=" in token and token.startswith("--"):
            flag, value = token.split("=", 1)
        if flag not in allowed_with_arg:
            raise HTTPException(status_code=400, detail=f"unsupported docker option: {token}")
        if value is None:
            i += 1
            if i >= len(args):
                raise HTTPException(status_code=400, detail=f"missing value for docker option: {flag}")
            value = str(args[i] or "")
        normalized.extend([flag, value])
        i += 1
    return normalized


def _decode_stdin(stdin_b64: str | None) -> bytes | None:
    if not stdin_b64:
        return None
    try:
        return base64.b64decode(stdin_b64.encode("ascii"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid stdin payload") from exc


def _docker_binary() -> str:
    explicit = str(os.getenv("TAKYON_DOCKER_REAL_BINARY") or "").strip()
    if explicit and os.path.isfile(explicit) and os.access(explicit, os.X_OK):
        return explicit
    for candidate in (
        shutil.which("docker"),
        "/usr/bin/docker",
        "/usr/local/bin/docker",
        "/opt/homebrew/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
        shutil.which("podman"),
    ):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise HTTPException(status_code=503, detail="docker runtime unavailable")


def _pipe_stdin(proc: subprocess.Popen[str], stdin_bytes: bytes | None) -> None:
    if proc.stdin is None or stdin_bytes is None:
        return

    def _write() -> None:
        try:
            proc.stdin.buffer.write(stdin_bytes)
            proc.stdin.close()
        except Exception:
            pass

    threading.Thread(target=_write, daemon=True).start()


def _stream_process(cmd: list[str], stdin_bytes: bytes | None = None) -> Iterable[bytes]:
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _pipe_stdin(proc, stdin_bytes)
    event_queue: queue.Queue[dict[str, str | int | None]] = queue.Queue()

    def _reader(pipe, stream_name: str) -> None:
        if pipe is None:
            return
        try:
            for line in iter(pipe.readline, ""):
                event_queue.put({"stream": stream_name, "data": line})
        finally:
            try:
                pipe.close()
            except Exception:
                pass
            event_queue.put({"stream": f"{stream_name}_eof"})

    stdout_thread = threading.Thread(target=_reader, args=(proc.stdout, "stdout"), daemon=True)
    stderr_thread = threading.Thread(target=_reader, args=(proc.stderr, "stderr"), daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    eof_seen = set()
    while True:
        item = event_queue.get()
        stream_name = str(item.get("stream") or "")
        if stream_name.endswith("_eof"):
            eof_seen.add(stream_name)
            if proc.poll() is not None and {"stdout_eof", "stderr_eof"} <= eof_seen:
                break
            continue
        yield (json.dumps(item) + "\n").encode("utf-8")
        if proc.poll() is not None and {"stdout_eof", "stderr_eof"} <= eof_seen and event_queue.empty():
            break

    returncode = proc.wait()
    stdout_thread.join(timeout=1.0)
    stderr_thread.join(timeout=1.0)
    yield (json.dumps({"returncode": returncode}) + "\n").encode("utf-8")


def build_docker_broker_app() -> FastAPI:
    app = FastAPI(title="Takyon Docker Broker")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/version")
    def version(authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        result = subprocess.run(
            [_docker_binary(), "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    @app.post("/v1/info-driver")
    def info_driver(authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        result = subprocess.run(
            [_docker_binary(), "info", "--format", "{{.Driver}}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    @app.post("/v1/containers/create")
    def create_container(body: _DockerCommandBody, authorization: str | None = Header(default=None)) -> dict[str, str | int]:
        _require_internal_token(authorization)
        args = _validated_args(body.args, set(), _RUN_FLAG_WITH_ARG)
        cmd = [_docker_binary(), "create", *args, _validated_image(body.image), *body.command]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

    @app.post("/v1/containers/run-detached")
    def run_detached(body: _DockerCommandBody, authorization: str | None = Header(default=None)) -> dict[str, str | int]:
        _require_internal_token(authorization)
        args = _validated_args(body.args, _RUN_FLAG_NOARG, _RUN_FLAG_WITH_ARG)
        cmd = [_docker_binary(), "run", *args, _validated_image(body.image), *body.command]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

    @app.post("/v1/containers/run-attached")
    def run_attached(body: _DockerCommandBody, authorization: str | None = Header(default=None)) -> StreamingResponse:
        _require_internal_token(authorization)
        args = _validated_args(body.args, _RUN_FLAG_NOARG, _RUN_FLAG_WITH_ARG)
        cmd = [_docker_binary(), "run", *args, _validated_image(body.image), *body.command]
        return StreamingResponse(
            _stream_process(cmd, _decode_stdin(body.stdin_b64)),
            media_type="application/x-ndjson",
        )

    @app.post("/v1/containers/exec-attached")
    def exec_attached(body: _DockerExecBody, authorization: str | None = Header(default=None)) -> StreamingResponse:
        _require_internal_token(authorization)
        args = _validated_args(body.args, _EXEC_FLAG_NOARG, _EXEC_FLAG_WITH_ARG)
        cmd = [_docker_binary(), "exec", *args, _validated_container_id(body.container_id), *body.command]
        return StreamingResponse(
            _stream_process(cmd, _decode_stdin(body.stdin_b64)),
            media_type="application/x-ndjson",
        )

    @app.post("/v1/containers/stop")
    def stop_container(body: _ContainerIdBody, authorization: str | None = Header(default=None)) -> dict[str, str | int]:
        _require_internal_token(authorization)
        result = subprocess.run(
            [_docker_binary(), "stop", _validated_container_id(body.container_id)],
            capture_output=True,
            text=True,
            timeout=65,
            check=False,
        )
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

    @app.post("/v1/containers/remove")
    def remove_container(body: _RemoveBody, authorization: str | None = Header(default=None)) -> dict[str, str | int]:
        _require_internal_token(authorization)
        args = _validated_args(body.args, _REMOVE_FLAG_NOARG, set())
        result = subprocess.run(
            [_docker_binary(), "rm", *args, *_validated_container_ids(body.container_ids)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}

    return app


app = build_docker_broker_app()
