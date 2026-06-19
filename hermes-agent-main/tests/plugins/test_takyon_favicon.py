"""Brand-favicon regression tests.

Pins the fix for the tab-favicon bug: a business's published logo must become the browser
TAB favicon, not just the landing brand mark. The original bug was that the scaffold seeds
``<link rel="icon" type="image/svg+xml" href="/favicon.svg">`` FIRST, and SVG-capable browsers
prefer a typed SVG icon over an appended PNG one — so merely appending a PNG ``<link>`` left the
monogram in the tab (observed live on splitease.fourmanifold.com's served index.html).

The fix (`core._set_index_favicon_links`) STRIPS every existing icon/apple-touch-icon link and
points both at the brand PNG, wired into both the logo path (`_publish_brand_logo_to_site`) and
the bootstrap/rebuild path (`_inject_favicon_links`). These are pure file transforms — no DB / no
network.
"""

import re

import pytest

from plugins.takyon import core

# The scaffold's seeded head: SVG monogram icon declared FIRST (the losing-PNG hazard).
SCAFFOLD_INDEX = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <!-- Free seeded brand mark; vite copies public/favicon.svg into dist. -->
    <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
    <link rel="apple-touch-icon" href="/favicon.svg" />
    <title>SplitEase</title>
  </head>
  <body><div id="root"></div></body>
</html>
"""

PNG = b"\x89PNG\r\n\x1a\nfake-logo-bytes"


def _seed(tmp_path):
    (tmp_path / "index.html").write_text(SCAFFOLD_INDEX, encoding="utf-8")
    return tmp_path


def _svg_icon_link(html: str) -> bool:
    """True iff a real <link ... href="/favicon.svg"> remains (ignore the HTML comment mention)."""
    return bool(re.search(r'<link\b[^>]*href="/favicon\.svg"', html))


def test_repoint_strips_svg_link_and_points_both_at_brand_png(tmp_path):
    root = _seed(tmp_path)
    core._set_index_favicon_links(root, href="/brand-logo.png", icon_type="image/png")
    html = (root / "index.html").read_text()
    # THE regression: the SVG icon link must be gone, so it can never win over the PNG again.
    assert not _svg_icon_link(html)
    assert 'type="image/svg+xml"' not in html
    assert '<link rel="icon" type="image/png" href="/brand-logo.png" />' in html
    assert '<link rel="apple-touch-icon" href="/brand-logo.png" />' in html
    # Exactly one of each — no duplicate/stale icon links accumulate.
    assert html.count('rel="icon"') == 1
    assert html.count('rel="apple-touch-icon"') == 1
    # Untouched surrounding markup.
    assert "<title>SplitEase</title>" in html and 'id="root"' in html


def test_repoint_is_idempotent(tmp_path):
    root = _seed(tmp_path)
    core._set_index_favicon_links(root, href="/brand-logo.png", icon_type="image/png")
    once = (root / "index.html").read_text()
    core._set_index_favicon_links(root, href="/brand-logo.png", icon_type="image/png")
    core._set_index_favicon_links(root, href="/brand-logo.png", icon_type="image/png")
    assert (root / "index.html").read_text() == once


def test_inject_favicon_links_repoints_when_logo_present(tmp_path):
    root = _seed(tmp_path)
    (root / "public").mkdir()
    (root / "public" / core._PUBLISHED_BRAND_LOGO_FILENAME).write_bytes(PNG)
    core._inject_favicon_links(root)
    html = (root / "index.html").read_text()
    assert not _svg_icon_link(html)
    assert core._PUBLISHED_BRAND_LOGO_URL in html


def test_inject_favicon_links_keeps_svg_when_no_logo(tmp_path):
    # No published logo -> the free SVG monogram is the correct tab favicon; leave it.
    root = _seed(tmp_path)
    core._inject_favicon_links(root)
    html = (root / "index.html").read_text()
    assert _svg_icon_link(html)
    assert "/brand-logo.png" not in html


def test_publish_brand_logo_writes_png_and_fixes_tab_favicon(tmp_path):
    root = _seed(tmp_path)
    assert core._publish_brand_logo_to_site(root, png_bytes=PNG) is True
    assert (root / "public" / core._PUBLISHED_BRAND_LOGO_FILENAME).read_bytes() == PNG
    html = (root / "index.html").read_text()
    assert not _svg_icon_link(html)
    assert '<link rel="icon" type="image/png" href="/brand-logo.png" />' in html


def test_rebuild_after_logo_publish_is_stable(tmp_path):
    # Existing-business backfill: a rebuild pass after the logo published must not flip state.
    root = _seed(tmp_path)
    core._publish_brand_logo_to_site(root, png_bytes=PNG)
    snap = (root / "index.html").read_text()
    core._inject_favicon_links(root)
    assert (root / "index.html").read_text() == snap


def test_publish_no_png_bytes_is_noop(tmp_path):
    root = _seed(tmp_path)
    assert core._publish_brand_logo_to_site(root, png_bytes=b"") is False
    # The seed SVG stays; nothing repointed.
    assert _svg_icon_link((root / "index.html").read_text())


def test_set_favicon_links_safe_without_head(tmp_path):
    root = tmp_path
    raw = "<html><body>no head element</body></html>"
    (root / "index.html").write_text(raw, encoding="utf-8")
    core._set_index_favicon_links(root, href="/brand-logo.png", icon_type="image/png")
    assert (root / "index.html").read_text() == raw
