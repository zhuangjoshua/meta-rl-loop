"""Reproduce the dashboard operator-principal resolution to pin the exact failure."""
import sys
import traceback

sys.path.insert(0, "/opt/takyon/hermes-agent-main")
import psycopg  # noqa: E402

# Use the SAME DSN resolver the dashboard principal-resolution uses.
try:
    from takyon_cli.web_server import _resolve_runtime_database_url
    url = _resolve_runtime_database_url()
    print("dsn_source: web_server._resolve_runtime_database_url OK")
except Exception:
    print("web_server resolver failed:")
    traceback.print_exc()
    from plugins.takyon.runtime_app import resolve_database_url
    url = resolve_database_url()
    print("dsn_source: runtime_app.resolve_database_url fallback")

from plugins.takyon.control_plane import (  # noqa: E402
    resolve_auth0_principal,
    resolve_user_principal,
)

conn = psycopg.connect(url, autocommit=True)
try:
    role = conn.execute("select current_user, session_user").fetchone()
    print("db_role (current_user, session_user):", role)

    b = conn.execute(
        "select owner_user_id from businesses where slug=%s", ("petpal-an",)
    ).fetchone()
    print("petpal-an owner read:", b)
    uid = str(b[0]) if b else ""

    try:
        u = conn.execute(
            "select auth0_sub, (email is not null) as has_email, status "
            "from users where id=%s",
            (uid,),
        ).fetchone()
        print("users read OK -> sub_present:", bool(u and u[0]), "has_email:", u[1] if u else None, "status:", u[2] if u else None)
        sub = u[0] if u else ""
    except Exception:
        print("users read FAILED:")
        traceback.print_exc()
        sub = ""

    print("--- resolve_user_principal (read-only) ---")
    try:
        p = resolve_user_principal(conn, uid)
        print("read-only principal OK:", p is not None, "slugs:", getattr(p, "business_slugs", None))
    except Exception:
        traceback.print_exc()

    print("--- resolve_auth0_principal (current provisioning path) ---")
    try:
        p = resolve_auth0_principal(conn, sub, None)
        print("auth0 principal OK:", p is not None)
    except Exception:
        traceback.print_exc()
finally:
    conn.close()
