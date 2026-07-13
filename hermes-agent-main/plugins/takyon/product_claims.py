"""Bounded advisory inventory for customer-visible product promises.

These regex-based findings help operators compare marketing copy with source-level implementation
evidence. They are deliberately advisory and never decide whether a product surface may publish.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


_CUSTOMER_SOURCE_PREFIXES = (
    "src/screens/",
    "src/components/",
    "src/pages/",
    "pages/",
)
_CUSTOMER_SOURCE_FILES = {"index.html", "public/llms.txt"}
_SKIP_PARTS = {"node_modules", "dist", "build", ".next", "_takyon", "__fixtures__"}

_FEATURE_CLAIMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pdf_export", re.compile(r"\b(?:export|download|save)(?:\s+\w+){0,3}\s+(?:as\s+)?pdf\b|\bpdf\s+export\b", re.I)),
    ("template_library", re.compile(r"\btemplate\s+librar(?:y|ies)\b|\bsave\s+templates?\b|\breuse\s+(?:winning\s+)?(?:formats?|templates?)\b", re.I)),
    ("markdown_export", re.compile(r"\b(?:export|download|save)(?:\s+\w+){0,3}\s+(?:as\s+)?markdown\b", re.I)),
)

_IMPLEMENTATION_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "pdf_export": (
        re.compile(r"\b(?:jsPDF|PDFDocument|pdfMake|react-pdf)\b"),
        re.compile(r"application/pdf", re.I),
        re.compile(r"\bwindow\.print\s*\("),
        re.compile(r"\bdownload\b[^\n]{0,160}\.pdf\b|\.pdf\b[^\n]{0,160}\bdownload\b", re.I),
    ),
    "markdown_export": (
        re.compile(r"text/markdown", re.I),
        re.compile(r"\bdownload\b[^\n]{0,160}\.md\b|\.md\b[^\n]{0,160}\bdownload\b", re.I),
    ),
}
_DURABLE_TEMPLATE_RAIL = re.compile(
    r"(?:"
    r"\b(?:saveRecord|listRecords|readRecord|getRecord|invokeAction|useRecords)\s*\([^)]{0,240}\btemplates?\b"
    r"|\btemplates?\b[^\n]{0,240}\b(?:saveRecord|listRecords|readRecord|getRecord|invokeAction|useRecords)\s*\("
    r")",
    re.I,
)


def _customer_source(rel: str) -> bool:
    return rel in _CUSTOMER_SOURCE_FILES or rel.startswith(_CUSTOMER_SOURCE_PREFIXES)


def _looks_like_comment_or_import(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or stripped.startswith(("//", "/*", "*", "<!--", "import ", "export type "))
    )


def customer_feature_claims(root: Path, *, limit: int = 40) -> list[dict[str, Any]]:
    """Return explicit customer-facing feature promises from bounded authored source."""
    claims: list[dict[str, Any]] = []
    if not root.is_dir():
        return claims
    for path in sorted(root.rglob("*")):
        if len(claims) >= limit:
            break
        if not path.is_file() or _SKIP_PARTS.intersection(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if not _customer_source(rel) or path.suffix.lower() not in {".html", ".js", ".jsx", ".ts", ".tsx", ".txt"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            if _looks_like_comment_or_import(line):
                continue
            kinds = [kind for kind, pattern in _FEATURE_CLAIMS if pattern.search(line)]
            if not kinds:
                continue
            claims.append(
                {
                    "path": rel,
                    "line": number,
                    "snippet": line.strip()[:300],
                    "kinds": kinds,
                }
            )
            if len(claims) >= limit:
                break
    return claims


def implemented_claim_capabilities(root: Path) -> set[str]:
    """Infer implementation evidence, deliberately excluding marketing-only source files."""
    capabilities: set[str] = set()
    functional_chunks: list[str] = []
    if not root.is_dir():
        return capabilities
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _SKIP_PARTS.intersection(path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in {"src/screens/landing.tsx", "src/screens/landing.jsx", "public/llms.txt", "index.html"}:
            continue
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx"} and path.name != "package.json":
            continue
        try:
            functional_chunks.append(path.read_text(encoding="utf-8")[:128_000])
        except (OSError, UnicodeDecodeError):
            continue
    text = "\n".join(functional_chunks)
    for kind, patterns in _IMPLEMENTATION_PATTERNS.items():
        if any(pattern.search(text) for pattern in patterns):
            capabilities.add(kind)
    # A records call somewhere in the app is not evidence of a template library. Bind the
    # template record/action name to the durable call itself so ordinary proposal persistence
    # cannot accidentally legalize an unrelated marketing promise.
    if _DURABLE_TEMPLATE_RAIL.search(text):
        capabilities.add("template_library")
    return capabilities


def unsupported_feature_claims(root: Path, *, limit: int = 8) -> list[dict[str, Any]]:
    """Return customer promises for which no functional source evidence exists."""
    implemented = implemented_claim_capabilities(root)
    findings: list[dict[str, Any]] = []
    for claim in customer_feature_claims(root):
        for kind in claim["kinds"]:
            if kind in implemented:
                continue
            findings.append({**claim, "kind": kind})
            if len(findings) >= limit:
                return findings
    return findings


def feature_claim_warning(finding: dict[str, Any]) -> str:
    labels = {
        "pdf_export": "PDF export",
        "template_library": "a reusable template library",
        "markdown_export": "Markdown export",
    }
    kind = str(finding.get("kind") or "feature")
    label = labels.get(kind, kind.replace("_", " "))
    return (
        f"advisory copy scan: customer-facing source mentions {label} at "
        f"{finding.get('path')}:{finding.get('line')}, but the bounded source scan found no "
        "implementation evidence for that capability; verify the customer workflow"
    )
