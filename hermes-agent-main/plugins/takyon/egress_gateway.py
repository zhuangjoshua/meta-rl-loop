"""Credentialed egress gateway — the security core of the "any integration" rail (delta 6).

Runs ONLY on the safebox host. An agent-written Deno action calls ctx.egress; the safebox
resolves an operator-approved provider_connections row by the HMAC-SIGNED capability scope's
business_slug, unseals the credential (AEAD, safebox-only key), attaches it on a SINGLE outbound
request to the connection's OWN host, and returns a KEY-FREE, redacted, bounded response. The
business runtime never sees the credential.

Build contract + threat model: egress-rail-build-spec.md. This module implements the 15
hostile-subuser must-fixes from the red-team; each is called out inline. Subusers are EVIL: they
author the action code and choose method/path/headers/body/query, but NOT the host, credential,
or placement (those come from the approved row).

House style: pure-ish leaf, imports httpx/psycopg lazily is unnecessary (safebox already has
them), raises typed EgressError the route maps to clean HTTP.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import posixpath
import re
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx

# ── limits + policy (spec §6-§10) ───────────────────────────────────────────────────────────
_UPSTREAM_TIMEOUT_S = 30
_MAX_REQUEST_BYTES = 256 * 1024      # must-fix #9/#14: cap forwarded request body
_MAX_RESPONSE_BYTES = 1024 * 1024    # must-fix #9: cap response body
_INTERNAL_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".cluster.local")
_HTTP_METHOD_RE = re.compile(r"^[A-Z][A-Z0-9_-]{0,31}$")
_ALLOWED_EGRESS_METHODS = frozenset({"DELETE", "GET", "HEAD", "PATCH", "POST", "PUT"})
_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_MALFORMED_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_PATH_DELIMITER_RE = re.compile(r"%(?:2f|5c)", re.IGNORECASE)
_ENCODED_DOT_RE = re.compile(r"%2e", re.IGNORECASE)
_FORBIDDEN_CREDENTIAL_HEADERS = frozenset({
    "connection", "content-length", "cookie", "host", "proxy-authorization", "set-cookie",
    "te", "trailer", "transfer-encoding", "upgrade",
})

# must-fix #7: platform-self egress denylist — _is_internal_host does NOT block the platform's own
# PUBLIC IPs/hosts, so a misconfigured/attacker-approved connection could otherwise turn
# credentialed egress into an authenticated call against Takyon's own control surfaces.
_PLATFORM_SELF_HOSTS = frozenset({
    "137.184.75.57",     # operator VPS
    "134.209.123.8",     # subuser VPS
    "206.81.10.173",     # subuser replica
    "67.205.158.170",    # safebox VPS (public)
    "10.116.0.2",        # safebox private VPC
    "app.fourmanifold.com",
})
_PLATFORM_SELF_SUFFIXES = (".coscale.app", ".fourmanifold.com")

# must-fix #9: providers already metered per-token/request through their dedicated priced brokers
# must NOT be reachable through the flat-fee egress rail (metering bypass). Refused at BOTH
# connection creation and call.
_METERED_PROVIDER_HOST_SUFFIXES = (
    "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
    "googleapis.com", "api.tavily.com", "fal.run", "fal.ai", "queue.fal.run",
)

# must-fix #5: forward only a strict allowlist of request headers to the upstream (never a
# denylist). Everything else — Authorization, Host, Cookie, Proxy-Authorization, X-Forwarded-*,
# hop-by-hop, and the placement header — is dropped; the connection credential is forced last.
_FORWARDABLE_REQUEST_HEADERS = frozenset({
    "accept", "accept-language", "content-type", "idempotency-key",
    "x-idempotency-key", "user-agent",
})
# must-fix #3: return only a strict allowlist of response headers (never echo auth/set-cookie/etc).
_RETURNABLE_RESPONSE_HEADERS = frozenset({
    "content-type", "content-length", "retry-after", "x-request-id",
})


class EgressError(Exception):
    """Base for egress refusals. ``status`` is the HTTP code the route surfaces; ``code`` a
    stable machine label. NEVER carries the credential or the raw upstream body beyond a truncation."""

    def __init__(self, status: int, code: str, detail: str = "") -> None:
        self.status = int(status)
        self.code = str(code)
        self.detail = str(detail)
        super().__init__(f"{status} {code}: {detail}")


@dataclass(frozen=True)
class ProviderConnection:
    id: str
    business_slug: str
    connection_slug: str
    provider_kind: str
    allowed_host: str
    allowed_path_prefix: str | None
    allowed_methods: tuple[str, ...]
    placement: dict
    scope: str
    status: str
    approved_scope_digest: str | None = None
    secret_ciphertext: bytes | None = None
    secret_nonce: bytes | None = None
    secret_fingerprint: str | None = None


# ── AEAD sealing (spec §migration; seal key is safebox-only, non-egress) ─────────────────────

def _seal_key() -> bytes:
    """The 32-byte AEAD key, derived from the safebox-only TAKYON_CONNECTION_SEAL_KEY (a member of
    core._SAFEBOX_SELF_AUTHORITY_SECRETS — categorically non-egress over /v1/env). Resolved via the
    safebox authority route, never os.environ in business code. HKDF-lite (sha256) so any key length
    yields a stable 32 bytes."""
    from . import safebox

    raw = ""
    try:
        raw = str(safebox.read_env_backed_value("TAKYON_CONNECTION_SEAL_KEY") or "").strip()
    except Exception:  # noqa: BLE001 — treat any resolution failure as unconfigured (fail closed)
        raw = ""
    if not raw:
        raise EgressError(503, "egress_seal_unconfigured", "TAKYON_CONNECTION_SEAL_KEY is not set")
    return hashlib.sha256(raw.encode("utf-8")).digest()


def seal_secret(plaintext: str) -> tuple[bytes, bytes, str]:
    """AEAD-seal a credential. Returns (ciphertext, nonce, sha256_fingerprint). The fingerprint is
    of the plaintext for rotation/audit and is NEVER the secret. Called only by the deposit route."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import os as _os

    if not plaintext:
        raise EgressError(400, "empty_secret", "credential is empty")
    key = _seal_key()
    nonce = _os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), b"takyon-connection")
    fp = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return ct, nonce, fp


def _unseal_secret(ciphertext: bytes, nonce: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not ciphertext or not nonce:
        raise EgressError(503, "connection_unsealed", "connection has no sealed credential")
    key = _seal_key()
    try:
        pt = AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), b"takyon-connection")
    except Exception as exc:  # noqa: BLE001 — bad key/tamper -> fail closed
        raise EgressError(503, "connection_unseal_failed", "credential could not be unsealed") from exc
    return pt.decode("utf-8")


def verify_sealed_secret(
    ciphertext: bytes,
    nonce: bytes,
    fingerprint: str,
) -> None:
    """Verify an existing Safebox-sealed credential without returning its plaintext.

    This is the narrow migration/reapproval seam for scope binding: after an operator grants the
    exact canonical connection scope, the Safebox may prove that the already-stored ciphertext is
    intact and reactivate it without asking the operator to expose the credential again. The
    plaintext remains process-local to the Safebox and is never returned to a caller.
    """
    plaintext = _unseal_secret(ciphertext, nonce)
    actual = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    expected = str(fingerprint or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected) or not hmac.compare_digest(actual, expected):
        raise EgressError(
            503,
            "connection_fingerprint_mismatch",
            "sealed credential fingerprint verification failed",
        )


# ── connection resolution (spec §safebox_route step 2) ───────────────────────────────────────

def resolve_active_connection(conn, business_slug: str, connection_slug: str) -> ProviderConnection:
    """Resolve the ACTIVE connection row (INCLUDING the sealed secret) by the AUTHORITATIVE
    (signed-scope) business_slug + slug, in ONE statement. Cross-tenant is impossible: business_slug
    is the signed capability scope, never a caller value. Fails closed 404 on any miss.

    Single-statement by design: on the Supabase transaction pooler (:6543) each autocommit statement
    can land on a different backend with the RLS-bypass GUC not carried over (the documented probe
    gotcha), so the resolve + secret read MUST be one statement (or one transaction). Fetching the
    ciphertext here lets the caller avoid a second query entirely."""
    row = conn.execute(
        "select id, business_slug, connection_slug, provider_kind, allowed_host, "
        "allowed_path_prefix, allowed_methods, placement, scope, status, "
        "approved_scope_digest, secret_ciphertext, secret_nonce, secret_fingerprint "
        "from provider_connections "
        "where business_slug = %s and connection_slug = %s and status = 'active'",
        (business_slug, connection_slug),
    ).fetchone()
    if row is None:
        raise EgressError(404, "connection_unknown", "no active connection for this business")
    placement = row[7] if isinstance(row[7], dict) else {}
    scope = str(row[8] or "business")
    # must-fix #11: v1 has no per-app-user credential vault — a 'per_customer' row would share the
    # single sealed secret across all customers. Refuse it at the call path too (belt with the
    # creation-time refusal).
    if scope != "business":
        raise EgressError(400, "per_customer_unsupported", "per-customer connections are not enabled")
    return ProviderConnection(
        id=str(row[0]), business_slug=str(row[1]), connection_slug=str(row[2]),
        provider_kind=str(row[3]), allowed_host=str(row[4] or "").strip().lower().rstrip("."),
        allowed_path_prefix=(None if row[5] is None else str(row[5])),
        allowed_methods=tuple(str(m).upper() for m in (row[6] or ())),
        placement=placement, scope=scope, status=str(row[9]),
        approved_scope_digest=(None if row[10] is None else str(row[10])),
        secret_ciphertext=(None if row[11] is None else bytes(row[11])),
        secret_nonce=(None if row[12] is None else bytes(row[12])),
        secret_fingerprint=(None if row[13] is None else str(row[13])),
    )


# ── host / SSRF policy (spec §safebox_route step 3,4,6; must-fix #7,#8,#9,#12,#13) ───────────

def _blocked_ip(addr: str) -> bool:
    """True if an IP literal is internal. must-fix #8: normalize IPv4-mapped/6to4 IPv6 explicitly so
    the guard is not silently Python-version-dependent (::ffff:127.0.0.1 must be caught)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None) is not None:
        ip = ip.ipv4_mapped
    sixto4 = getattr(ip, "sixtofour", None)
    if sixto4 is not None:
        ip = sixto4
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_unspecified or ip.is_multicast
    )


def host_denied_for_egress(host: str) -> str | None:
    """Return a refusal code if this host must never receive credentialed egress, else None.
    Checked at BOTH connection creation and call (must-fix #7,#9,#12)."""
    h = str(host or "").strip().lower().rstrip(".")
    if not h or h in {"localhost", "0.0.0.0"} or h.endswith(_INTERNAL_HOST_SUFFIXES):
        return "internal_host"
    if h in _PLATFORM_SELF_HOSTS or h.endswith(_PLATFORM_SELF_SUFFIXES):
        return "platform_self_host"
    if any(h == s or h.endswith("." + s) for s in _METERED_PROVIDER_HOST_SUFFIXES):
        return "metered_provider_host"  # must-fix #9: use the dedicated priced broker, not egress
    return None


def _resolve_pinned_ip(host: str) -> str:
    """must-fix #1,#13: resolve the host EXACTLY ONCE, reject if ANY A/AAAA is internal (DNS-rebind
    defense), and return the FIRST vetted public IP to PIN the socket to — so the IP validated is
    the IP connected (no re-resolution TOCTOU). Fails closed on DNS error."""
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise EgressError(502, "dns_unresolvable", "connection host did not resolve") from exc
    if not infos:
        raise EgressError(502, "dns_unresolvable", "connection host did not resolve")
    addrs = [str(info[4][0]) for info in infos]
    for addr in addrs:
        if _blocked_ip(addr):
            raise EgressError(403, "internal_address", "connection host resolves to an internal address")
    return addrs[0]


def _canonical_host(host: str) -> str:
    """Return one DNS authority with no scheme, port, userinfo, or Unicode ambiguity."""
    raw = str(host or "").strip().rstrip(".")
    if not raw or any(ord(c) < 0x21 or ord(c) == 0x7F for c in raw):
        raise EgressError(400, "bad_host", "connection host is empty or contains control characters")
    if any(token in raw for token in ("://", "/", "\\", "@", "?", "#", ":")):
        raise EgressError(400, "bad_host", "connection host must be a bare DNS name without a port")
    try:
        value = raw.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise EgressError(400, "bad_host", "connection host is not valid IDNA") from exc
    if len(value) > 253:
        raise EgressError(400, "bad_host", "connection host is too long")
    labels = value.split(".")
    if any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        raise EgressError(400, "bad_host", "connection host is not a valid DNS name")
    return value


def _canonical_path(path: str, *, field: str) -> str:
    """Decode and normalize exactly once, rejecting every ambiguous path representation first."""
    raw = str(path or "")
    if not raw.startswith("/") or raw.startswith("//"):
        raise EgressError(400, "bad_path", f"{field} must be an absolute-path reference")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        raise EgressError(400, "bad_path", f"{field} contains a control character")
    if "\\" in raw or "?" in raw or "#" in raw:
        raise EgressError(400, "bad_path", f"{field} must not contain backslashes, query, or fragment")
    if _MALFORMED_PERCENT_RE.search(raw):
        raise EgressError(400, "bad_path", f"{field} contains malformed percent encoding")
    if _ENCODED_PATH_DELIMITER_RE.search(raw) or _ENCODED_DOT_RE.search(raw):
        raise EgressError(400, "bad_path", f"{field} contains encoded path delimiters or dots")
    try:
        decoded = unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise EgressError(400, "bad_path", f"{field} contains invalid UTF-8 encoding") from exc
    if "\\" in decoded or "%" in decoded:
        raise EgressError(400, "bad_path", f"{field} contains a double-encoded path component")
    segments = decoded.split("/")
    if any(segment in {".", ".."} for segment in segments):
        raise EgressError(400, "bad_path", f"{field} contains dot traversal")
    normalized = posixpath.normpath(decoded)
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if decoded.endswith("/") and normalized != "/":
        normalized += "/"
    # Build the wire path from the normalized Unicode path, never from the raw caller string.
    return quote(normalized, safe="/-._~!$&'()*+,;=:@")


def _path_within_prefix(path: str, prefix: str) -> bool:
    if prefix.endswith("/"):
        return path.startswith(prefix)
    root = prefix.rstrip("/") or "/"
    if root == "/":
        return True
    return path == root or path.startswith(root + "/")


def normalize_connection_scope(
    *, provider_kind: str, allowed_host: str, allowed_path_prefix: str | None,
    allowed_methods: Any, placement: dict | None, scope: str,
) -> dict[str, Any]:
    """Canonical authority snapshot used at request, approval, deposit, and call time."""
    provider = str(provider_kind or "").strip().lower()
    if not provider or len(provider) > 96 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", provider):
        raise EgressError(400, "bad_provider_kind", "provider_kind is not a safe identifier")
    host = _canonical_host(allowed_host)
    denied = host_denied_for_egress(host)
    if denied:
        raise EgressError(403, denied, "connection host is not permitted for egress")
    prefix = None if allowed_path_prefix in {None, ""} else _canonical_path(
        str(allowed_path_prefix), field="allowed_path_prefix"
    )
    if isinstance(allowed_methods, str) or not isinstance(allowed_methods, (list, tuple, set)):
        raise EgressError(400, "bad_methods", "allowed_methods must be a non-empty list")
    methods = sorted({str(method or "").strip().upper() for method in allowed_methods})
    if (
        not methods
        or any(not _HTTP_METHOD_RE.fullmatch(method) for method in methods)
        or any(method not in _ALLOWED_EGRESS_METHODS for method in methods)
    ):
        raise EgressError(400, "bad_methods", "allowed_methods contains an invalid HTTP method")
    placement_value = dict(placement or {})
    placement_type = str(placement_value.get("type") or "header").strip().lower()
    header_name = str(placement_value.get("name") or "").strip().lower()
    if (
        placement_type != "header" or not _HEADER_NAME_RE.fullmatch(header_name)
        or header_name in _FORBIDDEN_CREDENTIAL_HEADERS
    ):
        raise EgressError(400, "unsupported_placement", "credential placement must be a safe header")
    scope_value = str(scope or "business").strip().lower()
    if scope_value != "business":
        raise EgressError(400, "per_customer_unsupported", "per-customer connections are not enabled")
    return {
        "provider_kind": provider,
        "allowed_host": host,
        "allowed_path_prefix": prefix,
        "allowed_methods": methods,
        "placement": {"type": "header", "name": header_name},
        "scope": scope_value,
    }


def connection_scope_digest(scope_snapshot: dict[str, Any]) -> str:
    canonical = json.dumps(scope_snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def connection_approval_payload(connection_slug: str, scope_snapshot: dict[str, Any]) -> dict[str, Any]:
    slug = str(connection_slug or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", slug):
        raise EgressError(400, "bad_connection_slug", "connection_slug is not a safe identifier")
    return {"connection_slug": slug, **scope_snapshot}


def connection_scope_from_connection(connection: ProviderConnection) -> dict[str, Any]:
    return normalize_connection_scope(
        provider_kind=connection.provider_kind,
        allowed_host=connection.allowed_host,
        allowed_path_prefix=connection.allowed_path_prefix,
        allowed_methods=connection.allowed_methods,
        placement=connection.placement,
        scope=connection.scope,
    )


def _safe_relative_url(path: str, query: dict | None, allowed_path_prefix: str | None) -> str:
    """Build the outbound target only from validated, normalized path/query components."""
    canonical_path = _canonical_path(path, field="path")
    canonical_prefix = (
        None if allowed_path_prefix in {None, ""}
        else _canonical_path(str(allowed_path_prefix), field="allowed_path_prefix")
    )
    if canonical_prefix and not _path_within_prefix(canonical_path, canonical_prefix):
        raise EgressError(403, "path_not_allowed", "path is outside the connection's allowed prefix")
    if not query:
        return canonical_path
    if not isinstance(query, dict):
        raise EgressError(400, "bad_query", "query must be an object")
    parts: list[tuple[str, str]] = []
    for key, value in query.items():
        values = value if isinstance(value, (list, tuple)) else (value,)
        for item in values:
            key_text, value_text = str(key), str(item)
            if any(
                ord(char) < 0x20 or ord(char) == 0x7F
                for char in key_text + value_text
            ):
                raise EgressError(400, "bad_query", "query contains a control character")
            parts.append((key_text, value_text))
    return canonical_path + "?" + urlencode(parts, doseq=False)


def _clean_forward_headers(headers: dict | None, placement: dict) -> dict:
    """must-fix #4,#5,#15: forward only allowlisted request headers with no control chars; drop the
    placement-name header + Authorization/Host/Cookie/etc so the caller cannot smuggle auth. The
    connection credential is attached AFTER this, canonically."""
    out: dict[str, str] = {}
    placement_name = str((placement or {}).get("name") or "").strip().lower()
    for k, v in (headers or {}).items():
        name = str(k).strip().lower()
        val = str(v)
        if name not in _FORWARDABLE_REQUEST_HEADERS:
            continue
        if name == placement_name:
            continue
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name + val):
            raise EgressError(400, "bad_header", "header contains a control character")
        out[name] = val
    return out


def _attach_credential(url: str, headers: dict, query_url: str, placement: dict, secret: str):
    """Attach the connection credential canonically per placement (header|query|basic). must-fix
    #15: strip any caller-supplied slot for that placement first (done in _clean_forward_headers for
    header; here for query/basic). Returns (final_url, final_headers). For secrets we FORBID query
    and basic placement in v1 (must-fix #3: always reflectable) — only header placement is allowed."""
    ptype = str((placement or {}).get("type") or "header").strip().lower()
    name = str((placement or {}).get("name") or "").strip()
    if ptype != "header" or not name:
        # query/basic secret placement is refused: those encodings are commonly reflected in
        # provider error/validation bodies, an exfil channel. Only header placement ships in v1.
        raise EgressError(400, "unsupported_placement", "only header credential placement is supported")
    final_headers = dict(headers)
    final_headers[name] = secret
    return query_url, final_headers


def _redact(text: str, secret: str, fingerprint: str) -> str:
    """must-fix #3: scrub the exact secret (raw + url-encoded + base64) and the fingerprint from any
    returned text so a reflecting upstream cannot hand the credential back to the action."""
    if not text:
        return text
    from urllib.parse import quote
    out = text
    for needle in filter(None, {
        secret, quote(secret, safe=""),
        base64.b64encode(secret.encode("utf-8")).decode("ascii"),
        fingerprint,
    }):
        out = out.replace(needle, "[redacted]")
    return out


# ── the call (spec §safebox_route steps 5-10) ────────────────────────────────────────────────

def build_request(
    connection: ProviderConnection, *, method: str, path: str, query: dict | None, headers: dict | None,
    body: Any, secret: str,
):
    """Assemble + fully validate the outbound request BEFORE any socket. Returns
    (method, url, pinned_ip, headers, body_bytes). Raises EgressError on any policy violation."""
    approved_scope = connection_scope_from_connection(connection)
    live_digest = connection_scope_digest(approved_scope)
    if not connection.approved_scope_digest or connection.approved_scope_digest != live_digest:
        raise EgressError(
            403, "connection_scope_not_approved",
            "the active credential is not bound to the connection's current authority scope",
        )

    m = str(method or "GET").strip().upper()
    if m not in approved_scope["allowed_methods"]:
        raise EgressError(405, "method_not_allowed", "method is not allowed for this connection")

    host = str(approved_scope["allowed_host"])
    rel = _safe_relative_url(path, query, approved_scope["allowed_path_prefix"])
    url = "https://" + host + rel

    # must-fix #2: re-parse the FINAL assembled URL and re-assert authority — no userinfo, exact
    # host, https, expected/absent port. This catches any assembly slip before we attach the key.
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise EgressError(400, "bad_scheme", "egress is https-only")
    if (parts.hostname or "").lower() != host:
        raise EgressError(400, "host_mismatch", "assembled host does not equal the connection host")
    if parts.username or parts.password:
        raise EgressError(400, "userinfo_forbidden", "credentials in the URL are forbidden")
    if parts.port not in (None, 443):
        raise EgressError(400, "port_forbidden", "only the default https port is permitted")

    pinned_ip = _resolve_pinned_ip(host)

    placement = dict(approved_scope["placement"])
    fwd = _clean_forward_headers(headers, placement)
    _, final_headers = _attach_credential(url, fwd, url, placement, secret)
    # Force Host to the allowed host regardless of the pinned IP connection.
    final_headers["host"] = host

    body_bytes = b""
    if body is not None:
        import json as _json
        body_bytes = body.encode("utf-8") if isinstance(body, str) else _json.dumps(body).encode("utf-8")
        if len(body_bytes) > _MAX_REQUEST_BYTES:
            raise EgressError(413, "request_too_large", "egress request body exceeds the cap")
        final_headers.setdefault("content-type", "application/json")
    return m, url, pinned_ip, final_headers, body_bytes


def call_egress(
    connection: ProviderConnection, *, method: str, path: str, query: dict | None, headers: dict | None,
    body: Any, secret: str, fingerprint: str,
) -> dict:
    """Perform the single credentialed outbound request, IP-pinned, redirects OFF, redacted +
    bounded + key-free response. Raises EgressError on any failure so the broker RELEASES the hold."""
    m, url, pinned_ip, final_headers, body_bytes = build_request(
        connection, method=method, path=path, query=query, headers=headers, body=body, secret=secret,
    )
    approved_host = str(urlsplit(url).hostname or "")

    # must-fix #1: REAL transport IP-pin. Connect to the exact vetted public IP with TLS
    # server_hostname=allowed_host and verification ON — httpx never re-resolves the hostname, so
    # the IP validated is the IP connected (closes the DNS-rebind check-vs-connect TOCTOU).
    transport = httpx.HTTPTransport(local_address=None)
    # httpx resolves the URL host itself; to pin, we point the request at the IP and carry the SNI
    # host via the Host header + a TLS context whose check_hostname targets allowed_host. httpx
    # supports this via `extensions={"sni_hostname": ...}` on the request URL when host is an IP.
    pinned_url = url.replace("https://" + approved_host, "https://" + pinned_ip, 1)

    # must-fix #9: never follow redirects (a 3xx could carry the credential to a new host); refuse 3xx.
    try:
        with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S, follow_redirects=False, transport=transport,
                          verify=True) as client:
            req = client.build_request(
                m, pinned_url, headers=final_headers, content=body_bytes or None,
                extensions={"sni_hostname": approved_host},
            )
            resp = client.send(req, stream=True)
            try:
                if 300 <= resp.status_code < 400:
                    raise EgressError(502, "redirect_refused", "upstream redirect is not permitted")
                # must-fix #9: bounded response read.
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_RESPONSE_BYTES:
                        raise EgressError(502, "response_too_large", "upstream response exceeds the cap")
                    chunks.append(chunk)
                raw = b"".join(chunks)
            finally:
                resp.close()
    except EgressError:
        raise
    except httpx.HTTPError as exc:
        raise EgressError(502, "provider_unreachable", "upstream request failed") from exc

    text = raw.decode("utf-8", errors="replace")
    text = _redact(text, secret, fingerprint)  # must-fix #3

    # must-fix #3: response headers via a strict allowlist, each redacted.
    safe_headers = {
        k: _redact(v, secret, fingerprint)
        for k, v in resp.headers.items()
        if k.lower() in _RETURNABLE_RESPONSE_HEADERS
    }
    return {"status": int(resp.status_code), "headers": safe_headers, "body": text[:_MAX_RESPONSE_BYTES]}
