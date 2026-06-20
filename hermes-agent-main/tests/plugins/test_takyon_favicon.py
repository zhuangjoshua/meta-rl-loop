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


# ── transient mirror-wipe recovery (the bootstrap-logo 502 root cause) ────────────────────────────
#
# The prod bug: logo-render published public/brand-logo.png into the LOCAL cache mirror, then the
# canonical commit re-read each source file UNGUARDED for CAS upload. A concurrent
# _business_root(sync=True) from another store re-materialized the mirror with delete_local=True and
# unlinked the not-yet-committed brand-logo.png mid-commit -> FileNotFoundError -> 502
# "No such file or directory: .../public/brand-logo.png" -> blocked_authority_runtime_unavailable;
# the logo never published and the tab favicon stayed the monogram on every fresh business.


def test_transient_mirror_wipe_is_recoverable_commit_conflict():
    # The exact prod exception (FileNotFoundError on the published brand logo) must be classified
    # recoverable so the commit retries; a real concurrent source edit (stale-base TakyonError) must
    # NOT be, so it still propagates.
    fnf = FileNotFoundError(
        2, "No such file or directory", "/x/product/site/public/brand-logo.png"
    )
    assert core._is_transient_mirror_wipe(fnf) is True
    assert core._is_recoverable_commit_conflict(fnf) is True
    assert (
        core._is_recoverable_commit_conflict(core.TakyonError("stale workspace base: ...")) is False
    )


def test_sync_remote_reasserts_files_and_retries_through_mirror_wipe(tmp_path, monkeypatch):
    """End-to-end shape of the fix: the before_attempt callback re-asserts the published files into
    the (re-materialized) mirror before EACH commit attempt, and a transient FileNotFoundError from a
    mid-commit mirror wipe is retried instead of escaping as a 502."""

    class _FakeStore(core.TakyonStore):
        def __init__(self, root):
            self.root = root
            self._workspace_sync_cache = set()
            self._workspace_revision_cache = {}
            self._operator_user_id = "op"
            self._attempts = 0

        # Backend gate: pretend the local backend is configured and allowed.
        def _workspace_storage_backend(self):
            return type("B", (), {"name": "local"})()

        def _business_root(self, slug, *, sync=True):
            r = self.root / core._slugify(slug)
            r.mkdir(parents=True, exist_ok=True)
            return r

        def _connect(self):
            import contextlib

            class _Conn:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

            @contextlib.contextmanager
            def _outer():
                yield _Conn()

            return _outer()

        def _canonical_workspace_revision(self, slug):
            return 0

        def _commit_business_workspace_revision(self, conn, slug, **_):
            # Simulate the unguarded CAS re-read: first attempt finds the file MISSING (a concurrent
            # delete_local wipe between before_attempt and here), and raises exactly like
            # storage.write_workspace_revision's _read_file_bytes. Second attempt sees it present.
            self._attempts += 1
            root = self._business_root(slug, sync=False)
            target = root / "product" / "site" / "public" / core._PUBLISHED_BRAND_LOGO_FILENAME
            if self._attempts == 1:
                target.unlink(missing_ok=True)  # the wipe race
                raise FileNotFoundError(2, "No such file or directory", str(target))
            assert target.is_file(), "before_attempt must re-publish the logo before the retry"
            return 1

    monkeypatch.setattr(core, "_remote_workspace_sync_allowed", lambda *_a, **_k: True)

    store = _FakeStore(tmp_path)
    site_root = store._business_root("acme", sync=False) / "product" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text(SCAFFOLD_INDEX, encoding="utf-8")

    calls = {"n": 0}

    def _reassert(workspace_root):
        calls["n"] += 1
        core._publish_brand_logo_to_site(workspace_root / "product" / "site", png_bytes=PNG)

    result = store._sync_business_workspace_remote("acme", before_attempt=_reassert)

    assert result == "synced"
    assert store._attempts == 2  # failed once on the wipe, succeeded on retry
    assert calls["n"] == 2  # re-asserted before BOTH attempts
    published = site_root / "public" / core._PUBLISHED_BRAND_LOGO_FILENAME
    assert published.read_bytes() == PNG  # durable after recovery
    assert not _svg_icon_link((site_root / "index.html").read_text())
