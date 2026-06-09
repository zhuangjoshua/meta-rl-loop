#!/usr/bin/env python3
"""CLI shim that preserves the existing docker shell seam while routing
authority through the local Takyon Docker broker service."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugins.takyon.docker_broker import broker_headers, broker_url

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


def _fail(message: str, code: int = 1) -> int:
    sys.stderr.write(f"{message}\n")
    return code


def _request(path: str, payload: dict, *, stream: bool = False):
    if not broker_url():
        raise RuntimeError("TAKYON_DOCKER_BROKER_URL is not configured")
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{broker_url()}{path}",
        data=data,
        headers=broker_headers(),
        method="POST",
    )
    return urllib.request.urlopen(req, timeout=600)


def _stdin_b64(expect_stdin: bool) -> str | None:
    if not expect_stdin:
        return None
    raw = sys.stdin.buffer.read()
    if not raw:
        return None
    return base64.b64encode(raw).decode("ascii")


def _parse_options(argv: list[str], allowed_noarg: set[str], allowed_with_arg: set[str]) -> tuple[list[str], list[str]]:
    options: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            return options, argv[i + 1 :]
        if not token.startswith("-"):
            return options, argv[i:]
        if token in allowed_noarg:
            options.append(token)
            i += 1
            continue
        flag = token
        value: str | None = None
        if "=" in token and token.startswith("--"):
            flag, value = token.split("=", 1)
        if flag not in allowed_with_arg:
            return options, argv[i:]
        options.append(flag)
        if value is None:
            i += 1
            if i >= len(argv):
                raise ValueError(f"missing value for docker option: {flag}")
            value = argv[i]
        options.append(value)
        i += 1
    return options, []


def _stream_response(response) -> int:
    returncode = 1
    try:
        for raw_line in response:
            if not raw_line:
                continue
            event = json.loads(raw_line.decode("utf-8"))
            if "returncode" in event:
                returncode = int(event.get("returncode") or 0)
                continue
            data = str(event.get("data") or "")
            if event.get("stream") == "stderr":
                sys.stderr.write(data)
                sys.stderr.flush()
            else:
                sys.stdout.write(data)
                sys.stdout.flush()
    finally:
        response.close()
    return returncode


def _handle_version() -> int:
    with _request("/v1/version", {}) as response:
        data = json.loads(response.read().decode("utf-8"))
    sys.stdout.write(str(data.get("stdout") or ""))
    sys.stderr.write(str(data.get("stderr") or ""))
    return int(data.get("returncode") or 0)


def _handle_info(args: list[str]) -> int:
    if args[:2] == ["--format", "{{.Driver}}"]:
        with _request("/v1/info-driver", {}) as response:
            data = json.loads(response.read().decode("utf-8"))
        sys.stdout.write(str(data.get("stdout") or ""))
        sys.stderr.write(str(data.get("stderr") or ""))
        return int(data.get("returncode") or 0)
    return _fail("unsupported docker info invocation for broker shim")


def _handle_create(args: list[str]) -> int:
    options, rest = _parse_options(args, set(), _RUN_FLAG_WITH_ARG)
    if not rest:
        return _fail("docker create requires an image")
    image, command = rest[0], rest[1:]
    with _request("/v1/containers/create", {"args": options, "image": image, "command": command}) as response:
        data = json.loads(response.read().decode("utf-8"))
    sys.stdout.write(str(data.get("stdout") or ""))
    sys.stderr.write(str(data.get("stderr") or ""))
    return int(data.get("returncode") or 0)


def _handle_run(args: list[str]) -> int:
    options, rest = _parse_options(args, _RUN_FLAG_NOARG, _RUN_FLAG_WITH_ARG)
    if not rest:
        return _fail("docker run requires an image")
    image, command = rest[0], rest[1:]
    payload = {
        "args": options,
        "image": image,
        "command": command,
        "stdin_b64": _stdin_b64("-i" in options),
    }
    detached = "-d" in options
    path = "/v1/containers/run-detached" if detached else "/v1/containers/run-attached"
    if detached:
        with _request(path, payload) as response:
            data = json.loads(response.read().decode("utf-8"))
        sys.stdout.write(str(data.get("stdout") or ""))
        sys.stderr.write(str(data.get("stderr") or ""))
        return int(data.get("returncode") or 0)
    response = _request(path, payload, stream=True)
    return _stream_response(response)


def _handle_exec(args: list[str]) -> int:
    options, rest = _parse_options(args, _EXEC_FLAG_NOARG, _EXEC_FLAG_WITH_ARG)
    if len(rest) < 2:
        return _fail("docker exec requires a container id and command")
    container_id, command = rest[0], rest[1:]
    payload = {
        "args": options,
        "container_id": container_id,
        "command": command,
        "stdin_b64": _stdin_b64("-i" in options),
    }
    response = _request("/v1/containers/exec-attached", payload, stream=True)
    return _stream_response(response)


def _handle_stop(args: list[str]) -> int:
    if len(args) != 1:
        return _fail("docker stop expects exactly one container id")
    with _request("/v1/containers/stop", {"container_id": args[0]}) as response:
        data = json.loads(response.read().decode("utf-8"))
    sys.stdout.write(str(data.get("stdout") or ""))
    sys.stderr.write(str(data.get("stderr") or ""))
    return int(data.get("returncode") or 0)


def _handle_rm(args: list[str]) -> int:
    force = []
    container_ids: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "-f":
            force.append(token)
        else:
            container_ids.append(token)
        i += 1
    if not container_ids:
        return _fail("docker rm requires at least one container id")
    with _request("/v1/containers/remove", {"args": force, "container_ids": container_ids}) as response:
        data = json.loads(response.read().decode("utf-8"))
    sys.stdout.write(str(data.get("stdout") or ""))
    sys.stderr.write(str(data.get("stderr") or ""))
    return int(data.get("returncode") or 0)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _fail("docker broker shim requires a docker subcommand")
    cmd, rest = args[0], args[1:]
    try:
        if cmd == "version":
            return _handle_version()
        if cmd == "info":
            return _handle_info(rest)
        if cmd == "create":
            return _handle_create(rest)
        if cmd == "run":
            return _handle_run(rest)
        if cmd == "exec":
            return _handle_exec(rest)
        if cmd == "stop":
            return _handle_stop(rest)
        if cmd == "rm":
            return _handle_rm(rest)
        return _fail(f"unsupported docker subcommand for broker shim: {cmd}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return _fail(f"docker broker http {exc.code}: {detail.strip() or exc.reason}", code=125)
    except urllib.error.URLError as exc:
        return _fail(f"docker broker unavailable: {exc.reason}", code=125)
    except Exception as exc:
        return _fail(str(exc), code=125)


if __name__ == "__main__":
    raise SystemExit(main())
