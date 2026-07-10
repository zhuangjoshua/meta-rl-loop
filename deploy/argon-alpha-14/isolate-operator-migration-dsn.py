"""Move the production migration DSN out of service-readable operator env files.

This one-time/idempotent deploy step runs as root before environment validation. It writes the DSN
under ``/root`` first, then atomically removes every migration-DSN alias from the runtime files.
No secret is accepted on argv/stdin or emitted to output.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
from pathlib import Path


ROOT_DIR = Path("/root/.config/takyon/migration")
ROOT_FILE = ROOT_DIR / "database-url"
RUNTIME_ENV_FILES = (
    Path("/opt/takyon/.takyon/.env"),
    Path("/opt/takyon/secrets/.env"),
)
ALIASES = ("TAKYON_MIGRATION_DATABASE_URL", "MIGRATION_DATABASE_URL")
_LINE = re.compile(
    r"^(?P<prefix>\s*(?:export\s+)?)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$"
)


class IsolationError(RuntimeError):
    pass


def _replacement_metadata_matches(
    info: os.stat_result, *, uid: int, gid: int, mode: int
) -> bool:
    return (
        info.st_uid == uid
        and info.st_gid == gid
        and stat.S_IMODE(info.st_mode) == mode
    )


def _read_regular(path: Path) -> tuple[bytes, os.stat_result]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise IsolationError("credential source must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise IsolationError("credential source changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > 2_000_000:
                raise IsolationError("credential source is oversized")
        return b"".join(chunks), opened
    finally:
        os.close(fd)


def _decode_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    if not value.startswith(("postgres://", "postgresql://")):
        raise IsolationError("migration credential is not a postgres URL")
    if any(char in value for char in ("\x00", "\n", "\r")) or any(
        char.isspace() for char in value
    ):
        raise IsolationError("migration credential is malformed")
    return value


def _runtime_values_and_filtered(data: bytes) -> tuple[list[str], bytes]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IsolationError("operator env file is not UTF-8") from exc
    values: list[str] = []
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        match = _LINE.match(line.rstrip("\r\n"))
        if match and match.group("key") in ALIASES:
            values.append(_decode_value(match.group("value")))
        else:
            kept.append(line)
    return values, "".join(kept).encode("utf-8")


def _atomic_write(path: Path, data: bytes, *, uid: int, gid: int, mode: int) -> None:
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        remaining = memoryview(data)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise IsolationError("short write while isolating migration credential")
            remaining = remaining[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        persisted, info = _read_regular(path)
        if (
            persisted != data
            or not _replacement_metadata_matches(info, uid=uid, gid=gid, mode=mode)
        ):
            raise IsolationError("atomic credential replacement did not persist exactly")
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _read_root() -> str | None:
    try:
        ROOT_FILE.lstat()
    except FileNotFoundError:
        return None
    directory = ROOT_DIR.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or stat.S_ISLNK(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_gid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise IsolationError("root migration directory must be root:root mode 0700")
    data, info = _read_regular(ROOT_FILE)
    if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise IsolationError("root migration credential must be root:root mode 0600")
    return _decode_value(data.decode("utf-8").strip())


def isolate() -> str:
    if os.geteuid() != 0:
        raise IsolationError("root is required")

    root_value = _read_root()
    files: list[tuple[Path, bytes, os.stat_result]] = []
    discovered: list[str] = []
    for path in RUNTIME_ENV_FILES:
        try:
            data, info = _read_regular(path)
        except FileNotFoundError:
            continue
        values, filtered = _runtime_values_and_filtered(data)
        discovered.extend(values)
        files.append((path, filtered, info))

    unique = set(discovered)
    if len(unique) > 1:
        raise IsolationError("migration credential aliases disagree")
    discovered_value = next(iter(unique), None)
    if (
        root_value is not None
        and discovered_value is not None
        and root_value != discovered_value
    ):
        raise IsolationError("root and runtime migration credentials disagree")
    value = root_value or discovered_value
    if value is None:
        raise IsolationError("migration credential is unavailable")

    ROOT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(ROOT_DIR, 0, 0)
    os.chmod(ROOT_DIR, 0o700)
    if root_value is None:
        _atomic_write(ROOT_FILE, (value + "\n").encode(), uid=0, gid=0, mode=0o600)

    # Root copy is durable before any runtime file is rewritten. Preserve each runtime file's
    # original owner/group/mode while removing every broad migration alias.
    for path, filtered, info in files:
        _atomic_write(
            path,
            filtered,
            uid=info.st_uid,
            gid=info.st_gid,
            mode=stat.S_IMODE(info.st_mode),
        )

    for path in RUNTIME_ENV_FILES:
        try:
            data, _ = _read_regular(path)
        except FileNotFoundError:
            continue
        remaining, _ = _runtime_values_and_filtered(data)
        if remaining:
            raise IsolationError("runtime migration credential removal did not persist")
    return "isolated" if discovered else "validated"


def main() -> int:
    try:
        state = isolate()
    except Exception:
        print("operator migration credential isolation failed", file=sys.stderr)
        return 1
    print(f"operator migration credential {state} root-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
