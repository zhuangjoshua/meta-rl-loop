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
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

# ── limits + policy (spec §6-§10) ───────────────────────────────────────────────────────────
_UPSTREAM_TIMEOUT_S = 30
_MAX_REQUEST_BYTES = 256 * 1024      # must-fix #9/#14: cap forwarded request body
_MAX_RESPONSE_BYTES = 1024 * 1024    # must-fix #9: cap response body
_INTERNAL_HOST_SUFFIXES = (".localhost", ".internal", ".local", ".cluster.local")

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


# ── connection resolution (spec §safebox_route step 2) ───────────────────────────────────────

def resolve_active_connection(conn, business_slug: str, connection_slug: str) -> ProviderConnection:
    """Resolve the ACTIVE connection row by the AUTHORITATIVE (signed-scope) business_slug + slug.
    Cross-tenant is impossible: business_slug is the signed capability scope, never a caller value.
    Fails closed 404 on any miss. Runs on the safebox DB conn (only role that reads this table)."""
    row = conn.execute(
        "select id, business_slug, connection_slug, provider_kind, allowed_host, "
        "allowed_path_prefix, allowed_methods, placement, scope, status "
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


def _safe_relative_url(path: str, query: dict | None, allowed_path_prefix: str | None) -> str:
    """must-fix #2,#4: build a safe relative URL. NEVER concatenate into authority: require a
    leading '/', reject '@', leading '//', backslashes, and any CR/LF/NUL/control char in path and
    query keys/values. Enforce allowed_path_prefix when set. The absolute URL is re-parsed and
    re-asserted in call_egress() before any request."""
    p = str(path or "")
    if not p.startswith("/"):
        raise EgressError(400, "bad_path", "path must start with '/'")
    if p.startswith("//") or "@" in p or "\\" in p:
        raise EgressError(400, "bad_path", "path contains a forbidden character")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in p):
        raise EgressError(400, "bad_path", "path contains a control character")
    if allowed_path_prefix and not p.startswith(allowed_path_prefix):
        raise EgressError(403, "path_not_allowed", "path is outside the connection's allowed prefix")
    if query:
        parts = []
        for k, v in query.items():
            ks, vs = str(k), str(v)
            for s in (ks, vs):
                if any(ord(c) < 0x20 or ord(c) == 0x7F for c in s):
                    raise EgressError(400, "bad_query", "query contains a control character")
            parts.append((ks, vs))
        from urllib.parse import urlencode
        sep = "&" if "?" in p else "?"
        p = p + sep + urlencode(parts)
    return p


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
    m = str(method or "GET").strip().upper()
    if m not in connection.allowed_methods:
        raise EgressError(405, "method_not_allowed", "method is not allowed for this connection")

    denied = host_denied_for_egress(connection.allowed_host)
    if denied:
        raise EgressError(403, denied, "connection host is not permitted for egress")

    rel = _safe_relative_url(path, query, connection.allowed_path_prefix)
    url = "https://" + connection.allowed_host + rel

    # must-fix #2: re-parse the FINAL assembled URL and re-assert authority — no userinfo, exact
    # host, https, expected/absent port. This catches any assembly slip before we attach the key.
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise EgressError(400, "bad_scheme", "egress is https-only")
    if (parts.hostname or "").lower() != connection.allowed_host:
        raise EgressError(400, "host_mismatch", "assembled host does not equal the connection host")
    if parts.username or parts.password:
        raise EgressError(400, "userinfo_forbidden", "credentials in the URL are forbidden")
    if parts.port not in (None, 443):
        raise EgressError(400, "port_forbidden", "only the default https port is permitted")

    pinned_ip = _resolve_pinned_ip(connection.allowed_host)

    fwd = _clean_forward_headers(headers, connection.placement)
    _, final_headers = _attach_credential(url, fwd, url, connection.placement, secret)
    # Force Host to the allowed host regardless of the pinned IP connection.
    final_headers["host"] = connection.allowed_host

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

    # must-fix #1: REAL transport IP-pin. Connect to the exact vetted public IP with TLS
    # server_hostname=allowed_host and verification ON — httpx never re-resolves the hostname, so
    # the IP validated is the IP connected (closes the DNS-rebind check-vs-connect TOCTOU).
    transport = httpx.HTTPTransport(local_address=None)
    # httpx resolves the URL host itself; to pin, we point the request at the IP and carry the SNI
    # host via the Host header + a TLS context whose check_hostname targets allowed_host. httpx
    # supports this via `extensions={"sni_hostname": ...}` on the request URL when host is an IP.
    pinned_url = url.replace("https://" + connection.allowed_host, "https://" + pinned_ip, 1)

    # must-fix #9: never follow redirects (a 3xx could carry the credential to a new host); refuse 3xx.
    try:
        with httpx.Client(timeout=_UPSTREAM_TIMEOUT_S, follow_redirects=False, transport=transport,
                          verify=True) as client:
            req = client.build_request(
                m, pinned_url, headers=final_headers, content=body_bytes or None,
                extensions={"sni_hostname": connection.allowed_host},
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
