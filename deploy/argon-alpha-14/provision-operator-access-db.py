"""Provision the root-only, least-privilege operator profile-access database login.

Executed on the operator host as root by the tracked deploy script after migration 0073. Secrets
are generated in memory, never accepted on argv/stdin, never printed, and persisted only beneath
``/root`` as root:root 0600.
"""

from __future__ import annotations

import os
import base64
import hashlib
import hmac
import re
import secrets
import stat
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path


ROLE = "takyon_operator_access"
CREDENTIAL_DIR = Path("/root/.config/takyon/operator-access")
CREDENTIAL_FILE = CREDENTIAL_DIR / "database-url"
MIGRATION_CREDENTIAL_FILE = Path("/root/.config/takyon/migration/database-url")


class ProvisionError(RuntimeError):
    pass


def _assert_root() -> None:
    if os.geteuid() != 0:
        raise ProvisionError("root is required")
    if (
        os.environ.get("TAKYON_ENV") != "prod"
        or os.environ.get("TAKYON_HOST_ROLE") != "operator"
    ):
        raise ProvisionError("the production operator host is required")


def _scoped_login_dsn(migration_dsn: str, password: str) -> str:
    """Replace only login/password while preserving a Supabase pooler tenant suffix."""
    match = re.match(
        r"^(?P<scheme>postgres(?:ql)?://)(?:(?P<user>[^:@/]*)(?::(?P<pw>[^@/]*))?@)?(?P<rest>.+)$",
        str(migration_dsn or "").strip(),
    )
    if not match:
        raise ProvisionError("migration database URL is not a postgres URL")
    base_user = urllib.parse.unquote(match.group("user") or "")
    suffix = "." + base_user.split(".", 1)[1] if "." in base_user else ""
    user = urllib.parse.quote(ROLE, safe="") + suffix
    return (
        f"{match.group('scheme')}{user}:{urllib.parse.quote(password, safe='')}@"
        f"{match.group('rest')}"
    )


def _scram_verifier(
    password: str, *, salt: bytes | None = None, iterations: int = 4096
) -> str:
    """Build PostgreSQL's SCRAM verifier so plaintext never enters SQL/server statement logs."""
    actual_salt = secrets.token_bytes(16) if salt is None else bytes(salt)
    salted = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), actual_salt, iterations
    )
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()

    def encoded(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return (
        f"SCRAM-SHA-256${iterations}:{encoded(actual_salt)}$"
        f"{encoded(stored_key)}:{encoded(server_key)}"
    )


def _assert_secure_path(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ProvisionError("credential path must be a regular file")
    if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise ProvisionError("credential path must be root:root mode 0600")
    return info


def _read_existing(path: Path = CREDENTIAL_FILE) -> str | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    directory = path.parent.lstat()
    if (
        not stat.S_ISDIR(directory.st_mode)
        or stat.S_ISLNK(directory.st_mode)
        or directory.st_uid != 0
        or directory.st_gid != 0
        or stat.S_IMODE(directory.st_mode) != 0o700
    ):
        raise ProvisionError("credential directory must be root:root mode 0700")
    before = _assert_secure_path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ProvisionError("credential changed while opening")
        value = os.read(fd, 16_384).decode("utf-8").strip()
        if os.read(fd, 1):
            raise ProvisionError("credential is oversized")
    finally:
        os.close(fd)
    if (
        not value.startswith(("postgres://", "postgresql://"))
        or "\n" in value
        or "\x00" in value
    ):
        raise ProvisionError("credential is malformed")
    return value


def _validate_access_connection(psycopg, dsn: str) -> None:
    with psycopg.connect(
        dsn, autocommit=True, prepare_threshold=None, connect_timeout=5
    ) as conn:
        role = conn.execute("select current_user, session_user").fetchone()
        if role != (ROLE, ROLE):
            raise ProvisionError("credential resolved to the wrong database role")
        attrs = conn.execute(
            "select rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolbypassrls, rolconnlimit "
            "from pg_roles where rolname = current_user"
        ).fetchone()
        if attrs != (False, False, False, False, False, 2):
            raise ProvisionError(
                "operator-access role attributes are not least privilege"
            )
        owns_objects = conn.execute(
            "select exists(select 1 from pg_class where relowner = (select oid from pg_roles "
            "where rolname = current_user))"
        ).fetchone()[0]
        memberships = conn.execute(
            "select exists(select 1 from pg_auth_members where "
            "member = (select oid from pg_roles where rolname = current_user) "
            "or roleid = (select oid from pg_roles where rolname = current_user))"
        ).fetchone()[0]
        if owns_objects or memberships:
            raise ProvisionError(
                "operator-access role owns objects or has role memberships"
            )
        for signature in (
            "operator_ssh_grant_app_access(text,text,text,uuid,inet,text)",
            "operator_ssh_revoke_app_access(text,text,uuid,inet,text)",
            "operator_ssh_list_app_access(text,text)",
        ):
            allowed = conn.execute(
                "select has_function_privilege(current_user, %s, 'execute')",
                (signature,),
            ).fetchone()[0]
            if not allowed:
                raise ProvisionError("operator-access function grant is missing")
        executable_app_functions = {
            str(row[0])
            for row in conn.execute(
                "select p.oid::regprocedure::text "
                "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                "where n.nspname = 'public' "
                "and has_function_privilege(current_user, p.oid, 'execute') "
                "and not exists (select 1 from pg_depend d "
                "where d.classid = 'pg_proc'::regclass and d.objid = p.oid "
                "and d.refclassid = 'pg_extension'::regclass and d.deptype = 'e')"
            ).fetchall()
        }
        if executable_app_functions != {
            "operator_ssh_grant_app_access(text,text,text,uuid,inet,text)",
            "operator_ssh_revoke_app_access(text,text,uuid,inet,text)",
            "operator_ssh_list_app_access(text,text)",
        }:
            raise ProvisionError(
                "operator-access role has unexpected function authority"
            )
        forbidden = conn.execute(
            "select has_table_privilege(current_user, 'app_operator_access_grants', 'select') "
            "or has_table_privilege(current_user, 'app_entitlements', 'insert') "
            "or has_function_privilege(current_user, "
            "'operator_ssh_revoke_stale_access(text,uuid,text)', 'execute')"
        ).fetchone()[0]
        if forbidden:
            raise ProvisionError(
                "operator-access role has authority outside its three ports"
            )


def _write_credential(
    value: str, *, directory: Path = CREDENTIAL_DIR, path: Path = CREDENTIAL_FILE
) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chown(directory, 0, 0)
    os.chmod(directory, 0o700)
    fd, raw_temp = tempfile.mkstemp(prefix=".database-url.", dir=directory)
    temp_path = Path(raw_temp)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, 0, 0)
        os.write(fd, (value + "\n").encode("utf-8"))
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temp_path, path)
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def provision() -> str:
    _assert_root()

    import psycopg
    from psycopg import sql

    existing = _read_existing()
    if existing:
        try:
            _validate_access_connection(psycopg, existing)
            return "validated"
        except Exception:
            # The migration credential below remains the recovery authority. Rotate the narrow
            # login without ever exposing either URL.
            pass

    migration_dsn = _read_existing(MIGRATION_CREDENTIAL_FILE)
    if migration_dsn is None:
        raise ProvisionError("root-only migration credential is unavailable")
    password = secrets.token_urlsafe(48)
    candidate = _scoped_login_dsn(migration_dsn, password)
    verifier = _scram_verifier(password)
    with psycopg.connect(
        migration_dsn, autocommit=True, prepare_threshold=None, connect_timeout=5
    ) as conn:
        role = conn.execute("select current_user, session_user").fetchone()
        if role != ("takyon_migration", "takyon_migration"):
            raise ProvisionError("root-only migration credential has the wrong role")
        conn.execute(
            sql.SQL("alter role {} with login password {}").format(
                sql.Identifier(ROLE), sql.Literal(verifier)
            )
        )

    # Supabase pooler auth propagation can lag briefly. Never print the candidate on failure.
    last_error: Exception | None = None
    for _ in range(20):
        try:
            _validate_access_connection(psycopg, candidate)
            last_error = None
            break
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    if last_error is not None:
        raise ProvisionError("new operator-access credential did not validate")

    _write_credential(candidate)
    return "provisioned"


def main() -> int:
    try:
        state = provision()
    except Exception:
        # Intentionally omit exception text: libpq errors may embed a DSN.
        print(
            "operator-access database credential provisioning failed",
            file=sys.stderr,
        )
        return 1
    print(f"operator-access database credential {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
