#!/usr/bin/env python3
"""Compile frozen Stripe Checkout branding for current product builds."""

from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request

import psycopg

from plugins.takyon import core
from plugins.takyon.operator_access import OperatorAccessError, _read_access_database_url
from plugins.takyon.runtime_app import resolve_database_url


class _StylesheetLinks(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag.lower() == "link" and "stylesheet" in values.get("rel", "").lower().split():
            if values.get("href"):
                self.hrefs.append(values["href"])


def _public_asset(url: str, *, host: str, max_bytes: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": "TakyonCheckoutBranding/1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if (
            final.scheme != "https"
            or str(final.hostname or "").lower() != host
            or final.username
            or final.password
            or final.port not in {None, 443}
        ):
            raise ValueError("public surface redirected outside its canonical host")
        declared = response.headers.get("Content-Length")
        if declared and int(declared) > max_bytes:
            raise ValueError("public surface asset is oversized")
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("public surface asset is oversized")
        return body, str(response.headers.get_content_type() or "")


def _materialize_public_surface(public_url: str, destination: Path) -> bool:
    parsed = urllib.parse.urlsplit(public_url)
    host = str(parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.path not in {"", "/"}:
        return False
    try:
        html, content_type = _public_asset(public_url, host=host, max_bytes=1024 * 1024)
        if content_type != "text/html":
            return False
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "index.html").write_bytes(html)

        parser = _StylesheetLinks()
        parser.feed(html.decode("utf-8", "replace"))
        assets = destination / "assets"
        css_total = 0
        for index, href in enumerate(parser.hrefs[:8]):
            css_url = urllib.parse.urljoin(public_url, href)
            try:
                css, css_type = _public_asset(css_url, host=host, max_bytes=1024 * 1024)
            except (OSError, ValueError):
                continue
            if css_type != "text/css" or css_total + len(css) > 1024 * 1024:
                continue
            assets.mkdir(exist_ok=True)
            (assets / f"published-{index}.css").write_bytes(css)
            css_total += len(css)

        try:
            logo, logo_type = _public_asset(
                urllib.parse.urljoin(public_url, "brand-logo.png"),
                host=host,
                max_bytes=512 * 1024,
            )
            if logo_type == "image/png" and logo[:8] == b"\x89PNG\r\n\x1a\n":
                (destination / "brand-logo.png").write_bytes(logo)
        except (OSError, ValueError):
            pass
        return True
    except (OSError, UnicodeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--company-base-domain")
    args = parser.parse_args()
    core.load_takyon_env()
    if args.company_base_domain:
        os.environ["PUBLIC_COMPANY_BASE_DOMAIN"] = core._company_base_domain(
            args.company_base_domain
        )
    product_root = core._product_publish_root()
    if product_root is None:
        raise SystemExit("product site root is unavailable")

    try:
        database_url = _read_access_database_url()
    except OperatorAccessError:
        if str(os.getenv("TAKYON_ENV") or "").strip().lower() != "dev":
            raise
        database_url = resolve_database_url(plane="migration")

    counts = {
        "eligible": 0,
        "compiled": 0,
        "remote_compiled": 0,
        "missing_product_build": 0,
        "missing_build": 0,
        "unchanged": 0,
    }
    with psycopg.connect(database_url, autocommit=True) as conn:
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
            public_url = core._product_publish_target(str(business))
            build_root = Path(product_root) / str(business) / "builds" / str(build_id)
            with tempfile.TemporaryDirectory(prefix="takyon-checkout-branding-") as temp_dir:
                source_root = build_root
                remote = False
                if not (source_root / "index.html").is_file():
                    source_root = Path(temp_dir)
                    remote = _materialize_public_surface(public_url, source_root)
                    if not remote:
                        counts["missing_build"] += 1
                        continue
                snapshot = core._compile_stripe_checkout_branding(
                    business=str(business),
                    display_name=str(name),
                    source_root=source_root,
                    public_url=public_url,
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
            if remote:
                counts["remote_compiled"] += 1
    print(json.dumps({"dry_run": bool(args.dry_run), **counts}, sort_keys=True))
    return 1 if counts["missing_product_build"] or counts["missing_build"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
