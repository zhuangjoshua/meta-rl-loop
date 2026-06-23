#!/usr/bin/env python3
"""One-shot operator cleanup: run the canonical ``business.delete`` handler against the
SUPERUSER DSN for every business owned by one Takyon operator.

This uses the SAME ``TakyonStore.commit(business.delete)`` path the ``takyon delete`` CLI and
the dashboard delete button use, so R2 / workspace / crons / DB rows all get cleaned. Only the
connection role and the operator identity differ (operator-authorized admin op).

Run it on the OPERATOR VPS as the ``takyon`` user with the superuser ``DATABASE_URL`` in env:

    DATABASE_URL=... TAKYON_FORCE_OPERATOR_ID=<owner-uuid> \
        .venv/bin/python scripts/force_delete_businesses.py dryrun     # preview owner's businesses
    DATABASE_URL=... TAKYON_FORCE_OPERATOR_ID=<owner-uuid> \
        .venv/bin/python scripts/force_delete_businesses.py confirm    # DELETE owner's businesses
    ... confirm slug-a slug-b                                          # only the named slugs

Default owner is the platform-owner account (every shell/dashboard-created business is owned by it).
"""
import os
import sys

import psycopg

import plugins.takyon.core as core
from plugins.takyon.core import TakyonStore
from plugins.takyon.cli import _scope_for_business, _idempotency_key

SUPERUSER_DSN = os.environ.get("DATABASE_URL", "")
# Default: the platform-owner account that owns every shell/dashboard-created business.
OPERATOR_ID = os.environ.get(
    "TAKYON_FORCE_OPERATOR_ID", "06ec6799-a579-4bb9-ba08-2913dc417cb9"
)

# --- route out / harden the cleanup steps that run BEFORE the DB-row delete ------------------------
# The canonical handler deletes the DB row LAST. Any step that raises before it strands the row while
# the assets are already gone. We make every pre-DB step non-raising so the DB delete always lands.

# 1) We are DELETING the workspace, so never sync it down from R2 first. sync=True would pull the whole
#    tree (slow, fragile) and, as root, leave root-owned files. Force no-sync.
_orig_business_root = TakyonStore._business_root


def _no_sync_business_root(self, slug, *, sync=True):  # noqa: ARG001 - drop sync, always False
    return _orig_business_root(self, slug, sync=False)


TakyonStore._business_root = _no_sync_business_root

# 2) Sub-user product-site cleanup SSHes operator->subuser with a key that is intentionally NOT on the
#    operator host (least-privilege). It RAISES when the key is missing, before the DB delete. Route it
#    out and record the target so a key holder can clean the sub-user dir afterwards.
SUBUSER_TARGETS: list[tuple] = []


def _skip_subuser_cleanup(slug: str) -> dict:
    try:
        summary = core._subuser_product_site_summary(slug)
        SUBUSER_TARGETS.append((slug, summary.get("target"), summary.get("path")))
        return {**summary, "skipped": True, "reason": "subuser cleanup routed to external key holder"}
    except Exception as exc:  # noqa: BLE001
        SUBUSER_TARGETS.append((slug, None, None))
        return {"skipped": True, "reason": f"subuser summary failed: {exc}"}


core._delete_subuser_product_site = _skip_subuser_cleanup

# 3) R2 public-edge + remote-workspace cleanup SHOULD work now (storage.public_site_object_prefix is
#    restored). Attempt the real cleanup, but never let a transient provider error abort the DB delete.
_orig_public_edge = core._delete_public_edge_product_site


def _safe_public_edge(slug: str) -> dict:
    try:
        return _orig_public_edge(slug)
    except Exception as exc:  # noqa: BLE001
        return {"provider": "cloudflare_r2", "skipped": True, "error": str(exc)[:200]}


core._delete_public_edge_product_site = _safe_public_edge

_orig_ws_remote = TakyonStore._delete_business_workspace_remote


def _safe_ws_remote(self, slug):
    try:
        return _orig_ws_remote(self, slug)
    except Exception as exc:  # noqa: BLE001
        print(f"  workspace-remote cleanup skipped {slug}: {str(exc)[:200]}")
        return None


TakyonStore._delete_business_workspace_remote = _safe_ws_remote
# --------------------------------------------------------------------------------------------------


def owner_slugs() -> list[str]:
    with psycopg.connect(SUPERUSER_DSN) as c, c.cursor() as cur:
        cur.execute(
            "select slug from businesses where owner_user_id = %s order by created_at",
            (OPERATOR_ID,),
        )
        return [r[0] for r in cur.fetchall()]


def main() -> int:
    if not SUPERUSER_DSN:
        print("FATAL: DATABASE_URL (superuser) not in env", file=sys.stderr)
        return 2
    mode = sys.argv[1] if len(sys.argv) > 1 else "dryrun"
    confirm = mode == "confirm"
    slugs = sys.argv[2:] or owner_slugs()
    print(f"role=superuser operator={OPERATOR_ID[:8]} confirm={confirm} target_count={len(slugs)}")
    print("slugs:", ", ".join(slugs) or "(none)")
    store = TakyonStore(database_url=SUPERUSER_DSN, operator_user_id=OPERATOR_ID)

    ok, failed = [], []
    for slug in slugs:
        op = {
            "action": "business.delete",
            "business": slug,
            "confirm": confirm,
            "delete_files": True,
            "delete_cron": True,
            "delete_domains": False,  # *.coscale.app wildcard routing — no per-business domain to delete
            "subdomains": [],
        }
        idem = _idempotency_key("operator-bulk-delete-v2", slug, confirm)
        try:
            res = store.commit(
                scope=_scope_for_business(slug),
                operations=[op],
                idempotency_key=idem,
                reason=("operator bulk delete" if confirm else "operator preview bulk delete"),
                actor="operator",
            )
            payload = res[0] if isinstance(res, list) and res else res
            db = (payload or {}).get("database", {})
            fs = (payload or {}).get("filesystem", {})
            pub = (payload or {}).get("public_edge_site", {})
            if confirm:
                deleted = db.get("deleted", {})
                n = sum(deleted.values()) if isinstance(deleted, dict) else deleted
                print(
                    f"  DELETED {slug}: db_rows={n} fs_removed={fs.get('removed')} "
                    f"public_edge={pub.get('removed', pub.get('skipped'))}"
                )
            else:
                cand = db.get("candidates", {})
                n = sum(cand.values()) if isinstance(cand, dict) else cand
                print(f"  PREVIEW {slug}: db_rows={n} fs_exists={fs.get('exists')}")
            ok.append(slug)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  ERROR {slug}: {type(exc).__name__}: {str(exc)[:300]}")
            failed.append(slug)

    print(f"\nSUMMARY: ok={len(ok)} failed={len(failed)}")
    if failed:
        print("failed slugs:", ", ".join(failed))
    if SUBUSER_TARGETS:
        print("SUBUSER_CLEANUP_PENDING (clean from a host that holds the subuser ssh key):")
        for slug, target, path in SUBUSER_TARGETS:
            print(f"  {slug}\t{target}\t{path}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
