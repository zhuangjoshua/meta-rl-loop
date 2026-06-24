"""Regression tests for the google-site-verification META injection.

Contract for ``core._inject_search_console_meta_tag``:
  (a) Google's siteVerification getToken (META method) returns a FULL ``<meta ...>`` tag, and the
      scaffold already bakes one. The injector must EXTRACT the bare content value so it never
      produces a nested ``content="<meta ... />"`` — invalid HTML that fails the Vite build
      (parse5 missing-whitespace-between-attributes).
  (b) Idempotent against the already-baked tag: passing the full tag when the bare-token tag is
      already present must NOT add a duplicate.
  (c) A bare token still injects correctly.
"""

from plugins.takyon import core


def test_full_meta_tag_token_is_not_nested(tmp_path):
    (tmp_path / "index.html").write_text(
        "<html><head></head><body></body></html>", encoding="utf-8"
    )
    full_tag = '<meta name="google-site-verification" content="TOKEN123" />'
    assert core._inject_search_console_meta_tag(tmp_path, full_tag) is True
    out = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'content="TOKEN123"' in out  # bare value injected
    assert 'content="<meta' not in out  # never a tag inside an attribute
    assert out.count("google-site-verification") == 1


def test_idempotent_against_baked_clean_tag(tmp_path):
    # The scaffold already baked the clean tag; the runtime register tool passing the full tag
    # again must skip, not duplicate.
    (tmp_path / "index.html").write_text(
        '<html><head>\n    <meta name="google-site-verification" content="TOKEN123" />\n'
        "  </head><body></body></html>",
        encoding="utf-8",
    )
    full_tag = '<meta name="google-site-verification" content="TOKEN123" />'
    assert core._inject_search_console_meta_tag(tmp_path, full_tag) is True
    out = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert out.count("google-site-verification") == 1  # no duplicate


def test_bare_token_still_injects(tmp_path):
    (tmp_path / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
    assert core._inject_search_console_meta_tag(tmp_path, "BARETOKEN") is True
    out = (tmp_path / "index.html").read_text(encoding="utf-8")
    assert 'content="BARETOKEN"' in out
    assert 'content="<meta' not in out
