#!/usr/bin/env python3
"""Remove Stripe authority assignments from non-Safebox service env files."""

from __future__ import annotations

import errno
import fcntl
import os
from pathlib import PurePosixPath
import re
import secrets
import stat
import sys


STRIPE_AUTHORITY_KEYS = (
    b"STRIPE_SECRET_KEY",
    b"STRIPE_SANDBOX_SECRET_KEY",
    b"STRIPE_WEBHOOK_SECRET",
    b"STRIPE_BILLING_WEBHOOK_SECRET",
)
_ASSIGNMENT = re.compile(
    rb"^[ \t]*(?:export[ \t]+)?(?:"
    + rb"|".join(re.escape(key) for key in STRIPE_AUTHORITY_KEYS)
    + rb")[ \t]*="
)
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW


class UnsafeEnvPath(RuntimeError):
    pass


def _scrub_assignments(contents: bytes) -> tuple[bytes, int]:
    kept: list[bytes] = []
    removed = 0
    for line in contents.splitlines(keepends=True):
        if _ASSIGNMENT.match(line):
            removed += 1
        else:
            kept.append(line)
    return b"".join(kept), removed


def _open_parent(path: str) -> tuple[int, str]:
    parsed = PurePosixPath(path)
    if not parsed.is_absolute() or not parsed.name:
        raise UnsafeEnvPath("env paths must be absolute files")
    if any(part in {".", ".."} for part in parsed.parts[1:]):
        raise UnsafeEnvPath("env paths may not contain traversal components")

    directory_fd = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in parsed.parts[1:-1]:
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd, parsed.name
    except BaseException:
        os.close(directory_fd)
        raise


def _snapshot(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_gid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _read_locked(file_fd: int) -> tuple[bytes, os.stat_result]:
    try:
        fcntl.flock(file_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise UnsafeEnvPath("env target is locked by another writer") from exc
    before = os.fstat(file_fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise UnsafeEnvPath("env target must be a regular, singly linked file")

    chunks: list[bytes] = []
    while True:
        chunk = os.read(file_fd, 1024 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
    after = os.fstat(file_fd)
    if _snapshot(before) != _snapshot(after):
        raise UnsafeEnvPath("env target changed while being read")
    return b"".join(chunks), after


def _create_temp(directory_fd: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    for _ in range(32):
        name = f".stripe-authority-cleanup.{os.getpid()}.{secrets.token_hex(8)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
    raise UnsafeEnvPath("could not allocate an atomic cleanup file")


def _write_all(file_fd: int, contents: bytes) -> None:
    view = memoryview(contents)
    while view:
        written = os.write(file_fd, view)
        if written <= 0:
            raise OSError("short write while cleaning env file")
        view = view[written:]


def _assert_parent_still_bound(path: str, directory_fd: int) -> None:
    try:
        current_fd, _ = _open_parent(path)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise UnsafeEnvPath("env path contains a symlink or non-directory") from exc
        raise
    try:
        expected = os.fstat(directory_fd)
        current = os.fstat(current_fd)
        if (expected.st_dev, expected.st_ino) != (current.st_dev, current.st_ino):
            raise UnsafeEnvPath("env parent changed during atomic cleanup")
    finally:
        os.close(current_fd)


def clean_env_file(path: str) -> bool:
    """Clean one file, returning whether an atomic replacement was made."""

    try:
        directory_fd, name = _open_parent(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise UnsafeEnvPath("env path contains a symlink or non-directory") from exc
        raise

    file_fd = -1
    temp_fd = -1
    temp_name: str | None = None
    try:
        try:
            file_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise UnsafeEnvPath("env target may not be a symlink") from exc
            raise

        contents, original = _read_locked(file_fd)
        cleaned, removed = _scrub_assignments(contents)
        if not removed:
            return False

        temp_fd, temp_name = _create_temp(directory_fd)
        os.fchown(temp_fd, original.st_uid, original.st_gid)
        _write_all(temp_fd, cleaned)
        os.fchmod(temp_fd, stat.S_IMODE(original.st_mode))
        os.fsync(temp_fd)

        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if _snapshot(current) != _snapshot(original) or not stat.S_ISREG(current.st_mode):
            raise UnsafeEnvPath("env target changed before atomic replacement")
        _assert_parent_still_bound(path, directory_fd)

        os.replace(temp_name, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temp_name = None
        os.fsync(directory_fd)
        _assert_parent_still_bound(path, directory_fd)

        verified_fd = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
        try:
            verified = os.fstat(verified_fd)
            if (
                verified.st_uid != original.st_uid
                or verified.st_gid != original.st_gid
                or stat.S_IMODE(verified.st_mode) != stat.S_IMODE(original.st_mode)
                or os.read(verified_fd, len(cleaned) + 1) != cleaned
            ):
                raise UnsafeEnvPath("atomic cleanup verification failed")
        finally:
            os.close(verified_fd)
        return True
    finally:
        if temp_fd >= 0:
            os.close(temp_fd)
        if temp_name is not None:
            try:
                os.unlink(temp_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        if file_fd >= 0:
            os.close(file_fd)
        os.close(directory_fd)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: remove-stripe-authority-env.py /absolute/env-file [...]", file=sys.stderr)
        return 2

    changed = 0
    try:
        for path in argv[1:]:
            changed += int(clean_env_file(path))
    except (OSError, UnsafeEnvPath) as exc:
        # Error classes and paths are safe to report; file contents and values never are.
        print(f"Stripe authority env cleanup refused: {exc}", file=sys.stderr)
        return 1

    print(f"Stripe authority env cleanup complete; files changed: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
