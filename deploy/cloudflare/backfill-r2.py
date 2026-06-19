#!/usr/bin/env python3
"""Backfill the public Cloudflare R2 product-site bucket from existing live builds.

Run this ONCE on the VPS before cutting <slug>.fourmanifold.com over to the R2 edge, so every
already-published business is present in R2 ahead of the switch. After cutover the normal publish
path (`core._publish_product_surface_path` -> `storage.write_public_site_to_r2`) keeps R2 current;
this script is only the one-time catch-up for builds published before the mirror existed.

What it does, per business that has a live build:
  1. read the canonical live `build_id` from the control plane (`app_surface_contracts.live_build_id`);
  2. materialize that exact build's dist from the Supabase object store (the source of truth) into a
     throwaway temp dir — NOT from VPS local disk, so the backfill is faithful even on a fresh host;
  3. mirror it to R2 via `storage.write_public_site_to_r2(slug, build_id, dist)` — uploading
     `<slug>/<build_id>/<rel>` then flipping the `<slug>/current` pointer.

Properties:
  * READ-ONLY w.r.t. the source of truth — it only READS Postgres + the Supabase store, and only
    WRITES the public R2 bucket. It never mutates a business, a build artifact, or the control plane.
  * IDEMPOTENT — keys are deterministic (`<slug>/<build_id>/<rel>`) and puts are digest-tagged, so a
    re-run re-uploads the same bytes to the same keys and re-points `current` at the same build_id.
  * FAIL-SOFT per business — one business's failure is logged and skipped; the run continues and
    exits non-zero only if any business failed, so a partial backfill is visible.

Usage (on the VPS, as the takyon user):
    R2_S3_ENDPOINT=... R2_BUCKET=product-sites \\
    R2_S3_ACCESS_KEY_ID=... R2_S3_SECRET_ACCESS_KEY=... \\
    python3 deploy/cloudflare/backfill-r2.py [--dry-run] [--slug acme,beta] [--runtime-dir DIR]

R2 creds resolve through the same env-backed / safebox seam as the runtime
(`storage._sensitive_config_value`); they are no more exposed than the existing `SUPABASE_S3_*`
keys. If R2 is unconfigured the script refuses up front rather than silently no-op'ing.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s backfill-r2 %(message)s",
)
log = logging.getLogger("backfill-r2")


def _bootstrap_runtime(explicit_dir: str | None) -> Path:
    """Put the active hermes-agent-main runtime on sys.path so `plugins.takyon.*` imports resolve.

    Resolution order: explicit ``--runtime-dir`` / ``TAKYON_RUNTIME_DIR``; the workspace-relative
    ``../../hermes-agent-main`` next to this script; the VPS install ``/opt/takyon/hermes-agent-main``.
    """
    candidates: list[Path] = []
    if explicit_dir:
        candidates.append(Path(explicit_dir).expanduser())
    env_dir = os.getenv("TAKYON_RUNTIME_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    here = Path(__file__).resolve()
    candidates.append(here.parent.parent.parent / "hermes-agent-main")
    candidates.append(Path("/opt/takyon/hermes-agent-main"))
    for cand in candidates:
        if (cand / "plugins" / "takyon" / "storage.py").is_file():
            sys.path.insert(0, str(cand))
            return cand
    raise SystemExit(
        "could not locate the hermes-agent-main runtime; pass --runtime-dir or set "
        "TAKYON_RUNTIME_DIR to the dir containing plugins/takyon/storage.py"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill existing live builds into the public R2 bucket.")
    parser.add_argument(
        "--slug",
        default="",
        help="comma-separated slugs to limit the backfill (default: every business with a live build)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list what WOULD be mirrored (build_id per slug) without writing to R2",
    )
    parser.add_argument(
        "--runtime-dir",
        default="",
        help="path to hermes-agent-main (else TAKYON_RUNTIME_DIR, workspace-relative, or /opt/takyon)",
    )
    args = parser.parse_args(argv)

    runtime_dir = _bootstrap_runtime(args.runtime_dir or None)
    log.info("runtime: %s", runtime_dir)

    # Imports happen AFTER sys.path is set so the active runtime's modules resolve.
    from plugins.takyon import storage  # noqa: PLC0415
    from plugins.takyon.core import TakyonStore, _slugify  # noqa: PLC0415
    from takyon_constants import get_takyon_home  # noqa: PLC0415

    if not args.dry_run and not storage.r2_configured():
        log.error(
            "R2 is not configured (need R2_S3_ENDPOINT / R2_BUCKET / R2_S3_ACCESS_KEY_ID / "
            "R2_S3_SECRET_ACCESS_KEY). Refusing to run a no-op backfill."
        )
        return 2

    only_slugs = {
        _slugify(s) for s in (args.slug or "").split(",") if s.strip()
    }

    store = TakyonStore(get_takyon_home(), system_plane="product-serving")
    with store._connect() as conn:
        rows = conn.execute(
            "SELECT business_slug, live_build_id FROM app_surface_contracts "
            "WHERE live_build_id IS NOT NULL AND live_build_id != '' "
            "ORDER BY business_slug"
        ).fetchall()

    # Read the SUPABASE object store (source of truth for build bytes) — NOT VPS local disk.
    source_backend = storage.get_storage_backend()
    r2_backend = None if args.dry_run else storage.R2StorageBackend()

    total = mirrored = skipped = failed = 0
    for row in rows:
        slug = _slugify(str(row.get("business_slug") or ""))
        build_id = str(row.get("live_build_id") or "").strip().lower()
        if not slug or not build_id:
            continue
        if only_slugs and slug not in only_slugs:
            continue
        total += 1

        if args.dry_run:
            log.info("[dry-run] would mirror slug=%s build_id=%s", slug, build_id)
            mirrored += 1
            continue

        with tempfile.TemporaryDirectory(prefix=f"r2-backfill-{slug}-") as tmp:
            dest = Path(tmp) / "dist"
            try:
                report = storage.materialize_build_artifact(
                    source_backend, slug, build_id, dest, delete_local=True
                )
            except storage.ObjectNotFound:
                log.warning(
                    "skip slug=%s build_id=%s: build artifact not in object store", slug, build_id
                )
                skipped += 1
                continue
            except Exception as exc:  # noqa: BLE001 — fail-soft per business
                log.error("FAILED materialize slug=%s build_id=%s err=%s", slug, build_id, exc)
                failed += 1
                continue

            n_files = len(report.downloaded) + len(report.skipped)
            if n_files == 0:
                log.warning("skip slug=%s build_id=%s: empty build (0 files)", slug, build_id)
                skipped += 1
                continue

            try:
                result = storage.write_public_site_to_r2(
                    slug, build_id, dest, backend=r2_backend
                )
            except Exception as exc:  # noqa: BLE001 — fail-soft per business
                log.error("FAILED r2 mirror slug=%s build_id=%s err=%s", slug, build_id, exc)
                failed += 1
                continue

            log.info(
                "mirrored slug=%s build_id=%s files=%d -> %s",
                slug,
                build_id,
                len(result.get("files") or {}),
                result.get("pointer_key"),
            )
            mirrored += 1

    log.info(
        "done: candidates=%d mirrored=%d skipped=%d failed=%d", total, mirrored, skipped, failed
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
