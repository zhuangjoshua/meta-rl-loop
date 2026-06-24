"""One-shot operator-wallet reup (test money). Run on the safebox authority host.

Tops up the operator (business owner) weekly AI allowance via the canonical
safebox grant so CEO turns can run. Sets included and resets used->0.
"""
import sys
import uuid

sys.path.insert(0, "/opt/takyon/hermes-agent-main")

import psycopg  # noqa: E402
from plugins.takyon import safebox  # noqa: E402
from plugins.takyon.runtime_app import resolve_database_url  # noqa: E402

SLUG = sys.argv[1] if len(sys.argv) > 1 else "petpal-an"
INC_CENTS = int(sys.argv[2]) if len(sys.argv) > 2 else 5000  # $50 test allowance

conn = psycopg.connect(resolve_database_url(), autocommit=True)
try:
    try:
        print("remote_safebox_enabled:", safebox._remote_safebox_enabled())
    except Exception as e:  # noqa: BLE001
        print("remote check err:", e)

    row = conn.execute(
        "select owner_user_id from businesses where slug=%s", (SLUG,)
    ).fetchone()
    if not row or not row[0]:
        print("NO_BUSINESS_OR_OWNER", SLUG)
        sys.exit(1)
    user_id = str(row[0]).strip()
    print("slug:", SLUG, "operator_user_id:", user_id)

    before = conn.execute(
        "select allowance_included_cents, allowance_used_cents "
        "from billing_accounts where user_id=%s",
        (user_id,),
    ).fetchone()
    print("before included/used:", before)

    key = "manual-test-reup-" + uuid.uuid4().hex
    granted = safebox._local_grant_allowance(conn, user_id, INC_CENTS, key)
    print("granted included_cents:", granted)

    after = conn.execute(
        "select allowance_included_cents, allowance_used_cents "
        "from billing_accounts where user_id=%s",
        (user_id,),
    ).fetchone()
    print("after included/used:", after)
    if after:
        print("remaining_cents:", int(after[0] or 0) - int(after[1] or 0))
finally:
    conn.close()
