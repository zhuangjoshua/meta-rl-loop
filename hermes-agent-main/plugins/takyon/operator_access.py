"""Root-SSH-only Four Manifold product-profile access grants.

This module is deliberately not registered as a Takyon CLI/web command. The only tracked entrypoint
is the root-owned ``/usr/local/bin/takyon-op profile-access ...`` launcher on the operator VPS.
Database writes go through a dedicated least-privilege login and three bounded functions from
migration 0073; normal entitlement code remains Stripe-evidence-only.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import socket
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


_STAFF_EMAIL_RE = re.compile(r"^[^@\s]+@fourmanifold[.]com$", re.IGNORECASE)
_ACCESS_DATABASE_URL_FILE = "/root/.config/takyon/operator-access/database-url"
_ACCESS_DATABASE_ROLE = "takyon_operator_access"
_SAFE_DATABASE_ERRORS = {
    "verified_active_profile_required": "verified active profile required",
    "fresh_verified_supabase_login_required": "profile must sign in again before grant",
    "active_business_required": "active business required",
    "fourmanifold_email_required": "email must be exactly @fourmanifold.com",
    "active_monthly_plan_required": "paid monthly plan required",
    "access_already_granted_with_different_plan": "profile already has a different SSH grant",
    "operator_ssh_access_not_found": "profile access grant not found",
    "grant_request_id_scope_mismatch": "request id belongs to a different grant",
    "revoke_request_id_scope_mismatch": "request id belongs to a different revoke",
}
_GRANT_COLUMNS = (
    "grant_id",
    "entitlement_id",
    "app_user_id",
    "verified_email",
    "plan_key",
    "tier",
    "status",
    "changed",
)
_LIST_COLUMNS = (
    "grant_id",
    "business_slug",
    "app_user_id",
    "verified_email",
    "plan_key",
    "tier",
    "status",
    "entitlement_id",
    "grant_request_id",
    "granted_at",
    "granted_from",
    "granted_on_host",
    "revoke_request_id",
    "revoked_at",
    "revoked_from",
    "revoked_on_host",
    "revoked_reason",
    "usage_period_start",
    "used_microusd",
    "monthly_limit_microusd",
)


class OperatorAccessError(RuntimeError):
    """The SSH-only access command failed a security or input precondition."""


@dataclass(frozen=True)
class SSHOperatorContext:
    client_address: str
    operator_host: str


def normalize_staff_email(value: str) -> str:
    email = str(value or "").strip().lower()
    if not _STAFF_EMAIL_RE.fullmatch(email):
        raise OperatorAccessError("email must be exactly @fourmanifold.com")
    return email


def require_root_ssh_operator_context(
    *,
    environ: Mapping[str, str] | None = None,
    euid: int | None = None,
    hostname: str | None = None,
) -> SSHOperatorContext:
    """Prove the OS-side boundary before any DB connection is opened.

    ``SSH_CONNECTION`` is injected by sshd. It is not treated as authority by itself: euid 0 and
    the exact operator host role are independently required. The deployed launcher also verifies
    the operator systemd unit and is installed root:root mode 0750 only on the operator VPS.
    """
    env = os.environ if environ is None else environ
    actual_euid = os.geteuid() if euid is None else int(euid)
    if actual_euid != 0:
        raise OperatorAccessError("profile access grants require euid 0")
    if str(env.get("TAKYON_ENV") or "").strip().lower() != "prod":
        raise OperatorAccessError("profile access grants require TAKYON_ENV=prod")
    if str(env.get("TAKYON_HOST_ROLE") or "").strip().lower() != "operator":
        raise OperatorAccessError(
            "profile access grants require TAKYON_HOST_ROLE=operator"
        )

    parts = str(env.get("SSH_CONNECTION") or "").strip().split()
    if len(parts) != 4:
        raise OperatorAccessError("profile access grants require an active SSH session")
    client_address, client_port, server_address, server_port = parts
    try:
        ipaddress.ip_address(client_address)
        ipaddress.ip_address(server_address)
        for raw_port in (client_port, server_port):
            port = int(raw_port)
            if port < 1 or port > 65535:
                raise ValueError("port out of range")
    except ValueError as exc:
        raise OperatorAccessError("SSH_CONNECTION is malformed") from exc

    host = str(hostname if hostname is not None else socket.gethostname()).strip()
    if not host or len(host) > 255 or "\x00" in host:
        raise OperatorAccessError("operator hostname is unavailable")
    return SSHOperatorContext(
        client_address=str(ipaddress.ip_address(client_address)), operator_host=host
    )


def _read_access_database_url(path: str = _ACCESS_DATABASE_URL_FILE) -> str:
    """Read the narrow login from a root-owned, non-symlinked 0600 file.

    The operator runtime cannot read ``/root`` (OS permissions plus ``ProtectHome``). The file is
    never sourced as shell and its value is never added to the process environment.
    """
    parent = os.path.dirname(path)
    try:
        parent_info = os.lstat(parent)
        if (
            stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent_info.st_uid != 0
            or parent_info.st_gid != 0
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise OperatorAccessError(
                "operator-access credential directory must be root:root mode 0700"
            )
        info = os.lstat(path)
    except OperatorAccessError:
        raise
    except OSError as exc:
        raise OperatorAccessError(
            "operator-access database credential is unavailable"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OperatorAccessError(
            "operator-access database credential must be a regular file"
        )
    if info.st_uid != 0 or info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
        raise OperatorAccessError(
            "operator-access database credential must be root:root mode 0600"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise OperatorAccessError(
                    "operator-access database credential changed while opening"
                )
            value = os.read(fd, 16_384).decode("utf-8").strip()
            if os.read(fd, 1):
                raise OperatorAccessError(
                    "operator-access database credential is oversized"
                )
        finally:
            os.close(fd)
    except OperatorAccessError:
        raise
    except (OSError, UnicodeError) as exc:
        raise OperatorAccessError(
            "operator-access database credential is unreadable"
        ) from exc
    if (
        not value.startswith(("postgres://", "postgresql://"))
        or "\n" in value
        or "\x00" in value
    ):
        raise OperatorAccessError("operator-access database credential is malformed")
    return value


def _assert_access_database_role(conn) -> None:
    row = conn.execute("select current_user, session_user").fetchone()
    if (
        row is None
        or str(row[0]) != _ACCESS_DATABASE_ROLE
        or str(row[1]) != _ACCESS_DATABASE_ROLE
    ):
        raise OperatorAccessError(
            "operator-access database credential has the wrong role"
        )


def _request_uuid(value: str | uuid.UUID | None) -> uuid.UUID:
    if value is None or str(value).strip() == "":
        return uuid.uuid4()
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise OperatorAccessError("request id must be a UUID") from exc


def _required_text(value: str, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise OperatorAccessError(f"{field} is required")
    if "\x00" in text:
        raise OperatorAccessError(f"{field} is malformed")
    return text


def _row_value(row: Any, index: int, name: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(name)
    return row[index]


def _grant_result(row: Any, *, action: str, request_id: uuid.UUID) -> dict[str, Any]:
    if row is None:
        raise OperatorAccessError(f"{action} returned no database receipt")
    result = {
        name: _row_value(row, index, name) for index, name in enumerate(_GRANT_COLUMNS)
    }
    return {
        "success": True,
        "action": action,
        "request_id": str(request_id),
        **{
            key: (str(value) if isinstance(value, uuid.UUID) else value)
            for key, value in result.items()
        },
    }


def grant_profile_access(
    conn,
    context: SSHOperatorContext,
    *,
    business_slug: str,
    email: str,
    plan_key: str,
    request_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    business = _required_text(business_slug, "business")
    normalized_email = normalize_staff_email(email)
    plan = _required_text(plan_key, "plan")
    request = _request_uuid(request_id)
    row = conn.execute(
        "select * from operator_ssh_grant_app_access(%s, %s, %s, %s, %s::inet, %s)",
        (
            business,
            normalized_email,
            plan,
            request,
            context.client_address,
            context.operator_host,
        ),
    ).fetchone()
    return _grant_result(row, action="grant", request_id=request)


def revoke_profile_access(
    conn,
    context: SSHOperatorContext,
    *,
    business_slug: str,
    email: str,
    request_id: str | uuid.UUID | None = None,
) -> dict[str, Any]:
    business = _required_text(business_slug, "business")
    normalized_email = normalize_staff_email(email)
    request = _request_uuid(request_id)
    row = conn.execute(
        "select * from operator_ssh_revoke_app_access(%s, %s, %s, %s::inet, %s)",
        (
            business,
            normalized_email,
            request,
            context.client_address,
            context.operator_host,
        ),
    ).fetchone()
    return _grant_result(row, action="revoke", request_id=request)


def list_profile_access(
    conn,
    *,
    business_slug: str | None = None,
    email: str | None = None,
) -> dict[str, Any]:
    business = _required_text(business_slug, "business") if business_slug else None
    normalized_email = normalize_staff_email(email) if email else None
    rows = conn.execute(
        "select * from operator_ssh_list_app_access(%s, %s)",
        (business, normalized_email),
    ).fetchall()
    grants = []
    for row in rows:
        grants.append({
            name: _row_value(row, index, name)
            for index, name in enumerate(_LIST_COLUMNS)
        })
    return {"success": True, "action": "list", "grants": grants}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="takyon-op profile-access",
        description="Root-SSH-only Four Manifold product-profile access grants.",
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON receipt")
    subparsers = parser.add_subparsers(dest="action", required=True)

    grant = subparsers.add_parser(
        "grant", help="Grant one verified staff profile plan access"
    )
    grant.add_argument("business")
    grant.add_argument("email")
    grant.add_argument("--plan", required=True)
    grant.add_argument("--request-id", default="")

    revoke = subparsers.add_parser("revoke", help="Revoke one active SSH staff grant")
    revoke.add_argument("business")
    revoke.add_argument("email")
    revoke.add_argument("--request-id", default="")

    listing = subparsers.add_parser(
        "list", help="List private SSH grant audit receipts"
    )
    listing.add_argument("--business", default="")
    listing.add_argument("--email", default="")
    return parser


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _print_receipt(result: Mapping[str, Any], *, raw_json: bool) -> None:
    if raw_json:
        print(json.dumps(_json_ready(result), sort_keys=True))
        return
    action = str(result.get("action") or "")
    if action == "list":
        grants = result.get("grants") if isinstance(result.get("grants"), list) else []
        if not grants:
            print("No operator SSH profile-access grants.")
            return
        for grant in grants:
            print(
                f"{grant.get('status')} business:{grant.get('business_slug')} "
                f"{grant.get('verified_email')} plan={grant.get('plan_key')} "
                f"grant={grant.get('grant_id')}"
            )
        return
    changed = bool(result.get("changed"))
    verb = "granted" if action == "grant" else "revoked"
    if not changed:
        verb = f"already {str(result.get('status') or verb)}"
    print(
        f"Profile access {verb}: business:{result.get('business_slug') or ''} "
        f"{result.get('verified_email')} plan={result.get('plan_key')} "
        f"grant={result.get('grant_id')} request={result.get('request_id')}"
    )


def run(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    context = require_root_ssh_operator_context()

    # Imported only after the OS checks: a rejected web/non-root invocation never loads env files
    # or attempts a database connection.
    import psycopg

    dsn = _read_access_database_url()
    with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
        _assert_access_database_role(conn)
        if args.action == "grant":
            result = grant_profile_access(
                conn,
                context,
                business_slug=args.business,
                email=args.email,
                plan_key=args.plan,
                request_id=args.request_id,
            )
        elif args.action == "revoke":
            result = revoke_profile_access(
                conn,
                context,
                business_slug=args.business,
                email=args.email,
                request_id=args.request_id,
            )
        else:
            result = list_profile_access(
                conn,
                business_slug=args.business or None,
                email=args.email or None,
            )
    result["ssh"] = asdict(context)
    result["business_slug"] = result.get("business_slug") or getattr(
        args, "business", ""
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    raw_json = "--json" in raw_args
    try:
        result = run(raw_args)
        _print_receipt(result, raw_json=raw_json)
        return 0
    except OperatorAccessError as exc:
        print(f"profile-access error: {exc}", file=sys.stderr)
        return 2
    # Fail closed; database errors can embed connection details.
    except Exception as exc:
        raw = str(exc)
        safe = next(
            (
                message
                for token, message in _SAFE_DATABASE_ERRORS.items()
                if token in raw
            ),
            "operation failed",
        )
        print(f"profile-access error: {safe}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
