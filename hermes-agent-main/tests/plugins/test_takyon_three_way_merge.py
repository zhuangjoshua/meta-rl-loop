"""Conservative 3-way text merge used by the workspace stale-base rebase.

Two tools editing different regions of the same file (the build worker's body + the logo/GSC
step's <head>) must merge instead of wedging the commit; the SAME lines edited on both sides, or
binary inputs, must fall back (None) so the merge can never silently corrupt a file.
"""
from __future__ import annotations

from plugins.takyon.core import _three_way_text_merge as merge


def _b(s: str) -> bytes:
    return s.encode("utf-8")


def test_non_overlapping_edits_merge():
    base = _b("a\nb\nc\nd\ne\n")
    ours = _b("A\nb\nc\nd\ne\n")   # changed line 1
    theirs = _b("a\nb\nc\nd\nE\n")  # changed line 5
    assert merge(base, ours, theirs) == _b("A\nb\nc\nd\nE\n")


def test_index_html_body_vs_head_merge():
    # The exact prod scenario: build worker edits the body; logo step inserts a <head> tag.
    base = _b("<head>\n<title>T</title>\n</head>\n<body>\n<p>old</p>\n</body>\n")
    ours = _b("<head>\n<title>T</title>\n</head>\n<body>\n<p>new</p>\n</body>\n")  # body
    theirs = _b("<head>\n<title>T</title>\n<link rel=icon>\n</head>\n<body>\n<p>old</p>\n</body>\n")  # head
    merged = merge(base, ours, theirs)
    assert merged is not None
    text = merged.decode()
    assert "<link rel=icon>" in text  # logo's head edit survived
    assert "<p>new</p>" in text       # build's body edit survived
    assert "<p>old</p>" not in text


def test_only_ours_changed_returns_ours():
    base = _b("x\ny\nz\n")
    assert merge(base, _b("x\nY\nz\n"), base) == _b("x\nY\nz\n")


def test_only_theirs_changed_returns_theirs():
    base = _b("x\ny\nz\n")
    assert merge(base, base, _b("x\nY\nz\n")) == _b("x\nY\nz\n")


def test_identical_edits_return_ours():
    base = _b("x\ny\nz\n")
    same = _b("x\nQ\nz\n")
    assert merge(base, same, same) == same


def test_same_line_edited_both_sides_is_conflict():
    base = _b("a\nb\nc\n")
    assert merge(base, _b("a\nX\nc\n"), _b("a\nY\nc\n")) is None


def test_overlapping_multiline_edits_conflict():
    base = _b("l1\nl2\nl3\nl4\n")
    ours = _b("l1\nOO\nOO\nl4\n")    # replaces lines 2-3
    theirs = _b("l1\nl2\nTT\nl4\n")  # replaces line 3 -> overlaps
    assert merge(base, ours, theirs) is None


def test_same_point_insertion_both_sides_conflict():
    base = _b("a\nb\n")
    ours = _b("a\nINS_O\nb\n")    # insert after line 1
    theirs = _b("a\nINS_T\nb\n")  # insert at the same point
    assert merge(base, ours, theirs) is None


def test_binary_input_returns_none():
    base = b"\x00\x01\x02"
    assert merge(base, b"\x00\x09\x02", b"\x00\x01\x09") is None


def test_oversize_input_returns_none():
    big = _b("x\n" * 3_000_000)  # > 4MB
    assert merge(big, big + _b("a\n"), big + _b("b\n")) is None


def test_disjoint_inserts_at_different_points_merge():
    base = _b("a\nb\nc\n")
    ours = _b("a\nINS\nb\nc\n")     # insert after line 1
    theirs = _b("a\nb\nc\nTAIL\n")  # append at end
    merged = merge(base, ours, theirs)
    assert merged == _b("a\nINS\nb\nc\nTAIL\n")
