#!/usr/bin/env python3
"""Compile frozen Stripe Checkout branding for current product builds."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import psycopg

from plugins.takyon import core
from plugins.takyon.operator_access import _read_access_database_url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--company-base-domain")
    args = parser.parse_args()
    if args.company_base_domain:
        os.environ["PUBLIC_COMPANY_BASE_DOMAIN"] = core._company_base_domain(
            args.company_base_domain
        )
    product_root = core._product_publish_root()
    if product_root is None:
        raise SystemExit("product site root is unavailable")

    counts = {
        "eligible": 0,
        "compiled": 0,
        "missing_product_build": 0,
        "missing_build": 0,
        "unchanged": 0,
    }
    with psycopg.connect(_read_access_database_url(), autocommit=True) as conn:
        current_user, session_user = conn.execute(
            "select current_user, session_user"
        ).fetchone()
        if current_user != "takyon_migration" or session_user != "takyon_migration":
            raise SystemExit("checkout branding backfill requires the takyon_migration role")
        rows = conn.execute(
            """
            select s.business_slug, b.name, s.live_build_id,
                   pb.build_id, coalesce(pb.checkout_branding_params_json, '{}')
              from app_surface_contracts s
              join businesses b on b.slug = s.business_slug
              left join product_builds pb
                on pb.business_slug = s.business_slug and pb.build_id = s.live_build_id
             where nullif(s.live_build_id, '') is not null
             order by s.business_slug
            """
        ).fetchall()
        for business, name, build_id, product_build_id, existing_json in rows:
            counts["eligible"] += 1
            if product_build_id is None:
                counts["missing_product_build"] += 1
                continue
            existing = str(existing_json or "").strip()
            if existing not in {"", "{}"}:
                counts["unchanged"] += 1
                continue
            build_root = Path(product_root) / str(business) / "builds" / str(build_id)
            if not (build_root / "index.html").is_file():
                counts["missing_build"] += 1
                continue
            snapshot = core._compile_stripe_checkout_branding(
                business=str(business),
                display_name=str(name),
                source_root=build_root,
                public_url=core._product_publish_target(str(business)),
                live_build_id=str(build_id),
            )
            if not snapshot:
                counts["missing_build"] += 1
                continue
            if not args.dry_run:
                updated = conn.execute(
                    """
                    update product_builds
                       set checkout_branding_params_json = %s
                     where business_slug = %s
                       and build_id = %s
                       and coalesce(nullif(trim(checkout_branding_params_json), ''), '{}') = '{}'
                    returning 1
                    """,
                    (json.dumps(snapshot, sort_keys=True, separators=(",", ":")), business, build_id),
                ).fetchone()
                if updated is None:
                    counts["unchanged"] += 1
                    continue
            counts["compiled"] += 1
    print(json.dumps({"dry_run": bool(args.dry_run), **counts}, sort_keys=True))
    return 1 if counts["missing_product_build"] or counts["missing_build"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
