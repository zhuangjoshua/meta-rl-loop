"""The real EAS build invoker — the PROVEN store-lane recipe as a leaf (readmodular §2.2/§7).

This is the local-worker-rail implementation of the thunk ``store_build.run_build`` invokes: the
exact chain live-proven on 2026-07-04 against real Apple/Expo (project ``@coscale/storesmoke1``,
signed IPA verified with ``codesign --verify --deep --strict``):

    copy product/app → temp build dir → npm install → git init → eas init --force --non-interactive
    → ASC API: ensure bundle id + capabilities (eas-cli's "synced capabilities" does NOT persist
      under API-key auth — we set them ourselves) + App Store provisioning profile bound to the
      TEAM distribution certificate (reused, never re-minted: Apple caps distribution certs)
    → credentials.json (p12 exported with ``openssl pkcs12 -legacy`` — OpenSSL 3's default PBES2
      format fails Apple's keychain import) + credentialsSource=local
    → ``eas build -p ios -e production --non-interactive --no-wait`` → build id = the TRIGGER.

Custody (GOAL_RULES §7, same contract as ``asc.py``): every secret is read from EXPLICIT file
paths under one secrets directory — never from ``os.environ`` — and injected only into the
eas-cli CHILD process env, never the runtime's. The directory pointer
(``TAKYON_STORE_BUILDER_SECRETS_DIR``) is a path, not a secret, same class as
``TAKYON_GREENLIGHT_BIN``. On this operator Mac the directory is ``~/.takyon-secrets`` (the same
operator-rail custody the ASC/Expo keys already live in); when the safebox builder route lands it
resolves the same material server-side and passes it in — this leaf does not change.

Fail-closed: missing/incomplete custody raises ``StoreBuilderUnconfigured`` from
``resolve_local_store_credentials`` BEFORE any credit reserve; every pre-trigger failure inside
the thunk raises, which ``run_build`` turns into a released (refunded) reservation. Only a
returned build id — Expo accepted the upload, money spent — settles.

Both lanes sign with the App Store distribution profile: ``preview`` (TestFlight-internal) and
``production`` uploads are store-signed; ad-hoc signing (device UDIDs) is deliberately not
implemented. The lane is carried in the receipt; the submit destination is a later rail.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from .store_build import BuildResult, EasBuilderUnconfigured

ASC_BASE_URL = "https://api.appstoreconnect.apple.com"
DEFAULT_SECRETS_DIR = "~/.takyon-secrets"
SECRETS_DIR_ENV = "TAKYON_STORE_BUILDER_SECRETS_DIR"  # a PATH pointer, never a secret
# Pinned so `npx eas-cli@<v>` always runs the registry package — a business-tree dependency can
# never shadow the CLI through node_modules/.bin. The version live-proven by the store-lane smoke.
EAS_CLI_VERSION = "20.5.1"
# Capability mapping from app.json ios config → ASC bundleIdCapabilities capabilityType.
# IN_APP_PURCHASE exists on every bundle id by default; only additive ones are listed.
_CAPABILITY_MAP = (
    ("associatedDomains", "ASSOCIATED_DOMAINS"),
    ("usesApplePay", "APPLE_PAY"),
    ("usesIcloudStorage", "ICLOUD"),
)
_BUILD_TIMEOUT_SECONDS = 900  # npm install + eas upload; the REMOTE build is not waited on


class StoreBuilderUnconfigured(EasBuilderUnconfigured):
    """Local store-builder custody is absent/incomplete. Subclasses the store_build gate error so
    run_build/tool callers treat it identically (raised BEFORE reserve — nothing charged)."""


@dataclass(frozen=True, repr=False)
class StoreBuilderCreds:
    key_id: str
    issuer_id: str
    team_id: str
    private_key_pem: str
    expo_token: str
    expo_owner: str
    dist_cert_id: str
    dist_p12_path: str
    dist_p12_password: str

    def __repr__(self) -> str:  # never leak key material into tracebacks/logs
        return (
            f"StoreBuilderCreds(key_id={self.key_id!r}, issuer_id={self.issuer_id!r}, "
            f"team_id={self.team_id!r}, expo_owner={self.expo_owner!r}, "
            f"dist_cert_id={self.dist_cert_id!r}, secrets='<redacted>')"
        )


def _secrets_dir(explicit: str | None = None) -> Path:
    import os

    raw = explicit or os.environ.get(SECRETS_DIR_ENV) or DEFAULT_SECRETS_DIR
    return Path(raw).expanduser()


def resolve_local_store_credentials(secrets_dir: str | None = None) -> StoreBuilderCreds:
    """Resolve the full builder custody from explicit files, fail-closed.

    Layout (operator-rail custody, all chmod 600/700):
      asc/takyon-ci.meta.json   — key_id / issuer_id / team_id / key_file (non-secret identifiers)
      asc/<key_file>            — the ASC .p8 private key
      expo/expo_token           — the EAS robot token
      expo/eas-ci.meta.json     — account name (expo owner), non-secret
      dist/cert_id.txt          — the TEAM distribution certificate's ASC resource id (reused)
      dist/dist.p12             — the distribution identity (exported -legacy)
      dist/p12pw.txt            — its password
    """
    root = _secrets_dir(secrets_dir)

    def _read(rel: str, what: str) -> str:
        p = root / rel
        try:
            text = p.read_text().strip()
        except OSError as exc:
            raise StoreBuilderUnconfigured(
                f"store_builder_unconfigured: missing {what} at {p} ({exc.__class__.__name__}). "
                "Deposit the store-builder custody or point "
                f"{SECRETS_DIR_ENV} at the directory that holds it. No credits were reserved."
            ) from exc
        if not text:
            raise StoreBuilderUnconfigured(
                f"store_builder_unconfigured: {what} at {p} is empty. No credits were reserved."
            )
        return text

    try:
        meta = json.loads(_read("asc/takyon-ci.meta.json", "ASC key metadata"))
    except ValueError as exc:
        raise StoreBuilderUnconfigured(
            f"store_builder_unconfigured: ASC key metadata is not valid JSON ({exc}). "
            "No credits were reserved."
        ) from exc
    key_file = str(meta.get("key_file") or "").strip()
    if not (meta.get("key_id") and meta.get("issuer_id") and meta.get("team_id") and key_file):
        raise StoreBuilderUnconfigured(
            "store_builder_unconfigured: ASC key metadata lacks key_id/issuer_id/team_id/key_file. "
            "No credits were reserved."
        )
    expo_owner = "coscale"
    try:
        expo_meta = json.loads((root / "expo/eas-ci.meta.json").read_text())
        expo_owner = str(expo_meta.get("account") or expo_owner).strip() or expo_owner
    except OSError:
        pass  # owner is a convenience; the token is the credential
    return StoreBuilderCreds(
        key_id=str(meta["key_id"]),
        issuer_id=str(meta["issuer_id"]),
        team_id=str(meta["team_id"]),
        private_key_pem=_read(f"asc/{key_file}", "ASC private key (.p8)"),
        expo_token=_read("expo/expo_token", "Expo robot token"),
        expo_owner=expo_owner,
        dist_cert_id=_read("dist/cert_id.txt", "team distribution certificate id"),
        dist_p12_path=str(root / "dist/dist.p12"),
        dist_p12_password=_read("dist/p12pw.txt", "distribution p12 password"),
    )


def is_configured(secrets_dir: str | None = None) -> bool:
    try:
        resolve_local_store_credentials(secrets_dir)
        return True
    except StoreBuilderUnconfigured:
        return False


# ── ASC provisioning (bundle id / capabilities / profile) ─────────────────────────────────


def _asc_headers(creds: StoreBuilderCreds) -> dict[str, str]:
    from . import asc

    token = asc.mint_asc_jwt(creds.key_id, creds.issuer_id, creds.private_key_pem)
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _asc_request(method: str, path: str, creds: StoreBuilderCreds, body: dict | None = None) -> dict:
    import httpx

    resp = httpx.request(
        method,
        ASC_BASE_URL + path,
        headers=_asc_headers(creds),
        content=json.dumps(body).encode() if body is not None else None,
        timeout=30.0,
    )
    if resp.status_code == 204:
        return {}
    data = resp.json() if resp.content else {}
    if resp.status_code >= 400:
        detail = json.dumps(data.get("errors") or data)[:400]
        raise RuntimeError(f"asc {method} {path} -> {resp.status_code}: {detail}")
    return data


def capabilities_from_app_config(app_config: dict) -> list[str]:
    """Map the Expo app config's ios section to the ASC capabilityTypes the profile must carry.
    Pure — unit-tested. eas-cli's own capability sync does NOT persist under API-key auth
    (live-proven), so the builder sets these itself before minting the profile."""
    ios = (app_config.get("expo") or app_config).get("ios") or {}
    wanted: list[str] = []
    if ios.get("associatedDomains"):
        wanted.append("ASSOCIATED_DOMAINS")
    entitlements = ios.get("entitlements") or {}
    if "aps-environment" in entitlements:
        wanted.append("PUSH_NOTIFICATIONS")
    return wanted


def _ensure_bundle_id(creds: StoreBuilderCreds, identifier: str, name: str) -> str:
    found = _asc_request(
        "GET", f"/v1/bundleIds?filter[identifier]={identifier}&limit=5", creds
    )
    for row in found.get("data") or []:
        if str(row.get("attributes", {}).get("identifier")) == identifier:
            return str(row["id"])
    created = _asc_request(
        "POST",
        "/v1/bundleIds",
        creds,
        {"data": {"type": "bundleIds", "attributes": {"identifier": identifier, "name": name, "platform": "IOS"}}},
    )
    return str(created["data"]["id"])


def _ensure_capabilities(creds: StoreBuilderCreds, bundle_resource_id: str, wanted: list[str]) -> None:
    existing = {
        str(row.get("attributes", {}).get("capabilityType"))
        for row in (
            _asc_request("GET", f"/v1/bundleIds/{bundle_resource_id}/bundleIdCapabilities", creds).get("data") or []
        )
    }
    for cap in wanted:
        if cap in existing:
            continue
        _asc_request(
            "POST",
            "/v1/bundleIdCapabilities",
            creds,
            {
                "data": {
                    "type": "bundleIdCapabilities",
                    "attributes": {"capabilityType": cap},
                    "relationships": {"bundleId": {"data": {"type": "bundleIds", "id": bundle_resource_id}}},
                }
            },
        )


def _ensure_store_profile(
    creds: StoreBuilderCreds, *, bundle_resource_id: str, profile_name: str
) -> bytes:
    """Mint (or re-mint) the App Store provisioning profile bound to the TEAM distribution cert.
    Profiles are immutable snapshots of the bundle id's capabilities — a same-name profile is
    deleted and recreated so capability changes always take effect (live-proven gotcha)."""
    import base64

    listing = _asc_request("GET", f"/v1/profiles?filter[name]={profile_name}&limit=5", creds)
    for row in listing.get("data") or []:
        if str(row.get("attributes", {}).get("name")) == profile_name:
            _asc_request("DELETE", f"/v1/profiles/{row['id']}", creds)
    created = _asc_request(
        "POST",
        "/v1/profiles",
        creds,
        {
            "data": {
                "type": "profiles",
                "attributes": {"name": profile_name, "profileType": "IOS_APP_STORE"},
                "relationships": {
                    "bundleId": {"data": {"type": "bundleIds", "id": bundle_resource_id}},
                    "certificates": {"data": [{"type": "certificates", "id": creds.dist_cert_id}]},
                },
            }
        },
    )
    return base64.b64decode(created["data"]["attributes"]["profileContent"])


# ── the invoker ────────────────────────────────────────────────────────────────────────────


def expected_bundle_identifier(business_slug: str) -> str:
    """The ONLY bundle identifier a business may provision/sign on the shared Apple team — its own
    derived identity. Deterministic business-isolation rail (matches the seeder's shape)."""
    normalized = re.sub(r"[^a-z0-9-]+", "", str(business_slug or "").strip().lower())
    return f"com.coscale.{normalized}"


def _reconcile_triggered_build(build_dir: Path, child_env: dict[str, str]) -> str:
    """Trigger-ambiguity reconciler: after an eas-build CLI failure/timeout/unparseable output, ask
    the EAS API whether a build was actually created for THIS project just now. Returns the build id
    (→ the caller settles) or "" (→ the caller raises and the reservation releases). $0 read."""
    try:
        proc = subprocess.run(
            ["npx", "--yes", f"eas-cli@{EAS_CLI_VERSION}", "build:list", "--platform", "ios",
             "--limit", "1", "--non-interactive", "--json"],
            cwd=str(build_dir), env=child_env, capture_output=True, text=True, timeout=120,
        )
        if proc.returncode != 0:
            return ""
        rows = json.loads(proc.stdout)
        row = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(row, dict):
            return ""
        status = str(row.get("status") or "").upper()
        if status in ("NEW", "IN_QUEUE", "IN_PROGRESS", "FINISHED"):
            return str(row.get("id") or "").strip()
    except Exception:
        return ""
    return ""


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str], what: str, timeout: int = _BUILD_TIMEOUT_SECONDS) -> str:
    proc = subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-800:]
        raise RuntimeError(f"{what} failed (exit {proc.returncode}): {tail}")
    return proc.stdout


def local_eas_invoker(
    *,
    business_slug: str,
    lane: str,
    source_dir: str,
    creds: StoreBuilderCreds,
    keep_build_dir: bool = False,
) -> Callable[[], BuildResult]:
    """Thunk for store_build.run_build: runs the proven recipe, returns at the TRIGGER (build id).

    Secrets go ONLY into the eas-cli child env. The build happens in a temp copy of the business's
    ``product/app`` source — the business workspace is never mutated (no node_modules/.git/creds
    pollution). Every failure before ``eas build`` returns a build id raises → run_build releases
    the reservation (no spend happened)."""
    import os

    src = Path(source_dir)

    def _thunk() -> BuildResult:
        if not (src / "app.json").is_file():
            raise RuntimeError(f"mobile app source at {src} has no app.json — seed/build product/app first")
        build_root = Path(tempfile.mkdtemp(prefix=f"takyon-store-build-{business_slug}-"))
        build_dir = build_root / "app"
        try:
            shutil.copytree(
                src, build_dir, ignore=shutil.ignore_patterns("node_modules", ".git", "credentials.json")
            )
            app_cfg = json.loads((build_dir / "app.json").read_text())
            expo_cfg = app_cfg.get("expo") or {}
            expo_cfg["owner"] = creds.expo_owner
            bundle_identifier = str(((expo_cfg.get("ios") or {}).get("bundleIdentifier")) or "").strip()
            # HARD business-isolation rail: the bundle id IS the app's identity on the SHARED Apple
            # team. A business may only ever provision/sign its own derived identity — anything else
            # is a cross-tenant impersonation/squat and is refused deterministically (prompt prose
            # is not a security rail).
            expected_bundle_id = expected_bundle_identifier(business_slug)
            if bundle_identifier != expected_bundle_id:
                raise RuntimeError(
                    f"bundle_identifier_mismatch: app.json declares {bundle_identifier!r} but this "
                    f"business may only publish {expected_bundle_id!r}"
                )
            app_cfg["expo"] = expo_cfg
            (build_dir / "app.json").write_text(json.dumps(app_cfg, indent=2) + "\n")

            # The business tree is DELEGATED-WORKER-AUTHORED (untrusted). Strip EAS build hooks so
            # the tree cannot run its own script phases alongside the credentials on the build VM.
            pkg_path = build_dir / "package.json"
            if pkg_path.is_file():
                pkg = json.loads(pkg_path.read_text())
                scripts = pkg.get("scripts") or {}
                stripped = [name for name in list(scripts) if name.startswith("eas-build-")]
                for name in stripped:
                    scripts.pop(name, None)
                pkg["scripts"] = scripts
                pkg_path.write_text(json.dumps(pkg, indent=2) + "\n")

            # Child env: node tooling + the Expo/ASC identifiers. Never the runtime's environ, and
            # HOME is a scratch dir — business-tree code must not see the operator's real $HOME.
            scratch_home = build_root / "home"
            scratch_home.mkdir(mode=0o700)
            child_env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
                "HOME": str(scratch_home),
                "EXPO_TOKEN": creds.expo_token,
                "EXPO_APPLE_TEAM_ID": creds.team_id,
                "EXPO_APPLE_TEAM_TYPE": "INDIVIDUAL",
                "EXPO_NO_TELEMETRY": "1",
                "CI": "1",
            }

            # --ignore-scripts: the tree's npm lifecycle scripts NEVER execute on this host (same
            # rule as the web publish lane). The remote EAS builder runs its own sandboxed install.
            _run(
                ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=build_dir, env=child_env, what="npm install",
            )
            _run(["git", "init", "-q"], cwd=build_dir, env=child_env, what="git init")
            # Credentials must be readable by eas-cli locally but NEVER enter git or the uploaded
            # project archive (eas archives untracked-but-not-ignored files too).
            gitignore = build_dir / ".gitignore"
            ignore_text = gitignore.read_text() if gitignore.is_file() else ""
            for entry in ("credentials.json", "_store_credentials/"):
                if entry not in ignore_text:
                    ignore_text = ignore_text.rstrip() + f"\n{entry}\n"
            gitignore.write_text(ignore_text)
            _run(["git", "add", "-A"], cwd=build_dir, env=child_env, what="git add")
            _run(
                ["git", "-c", "user.email=builder@coscale.app", "-c", "user.name=takyon-builder",
                 "commit", "-qm", "takyon store build"],
                cwd=build_dir, env=child_env, what="git commit",
            )
            # eas-cli is version-PINNED so npx always runs the registry package — a dependency in
            # the business tree cannot shadow the binary via node_modules/.bin.
            _run(
                ["npx", "--yes", f"eas-cli@{EAS_CLI_VERSION}", "init", "--force", "--non-interactive"],
                cwd=build_dir, env=child_env, what="eas init",
            )

            # ASC provisioning: bundle id + capabilities + store profile bound to the team cert.
            bundle_resource = _ensure_bundle_id(creds, bundle_identifier, f"takyon {business_slug}")
            _ensure_capabilities(creds, bundle_resource, capabilities_from_app_config(app_cfg))
            profile_bytes = _ensure_store_profile(
                creds, bundle_resource_id=bundle_resource, profile_name=f"takyon {business_slug} appstore"
            )
            cred_dir = build_dir / "_store_credentials"
            cred_dir.mkdir(mode=0o700)
            profile_path = cred_dir / "store.mobileprovision"
            profile_path.write_bytes(profile_bytes)
            (build_dir / "credentials.json").write_text(
                json.dumps(
                    {
                        "ios": {
                            "provisioningProfilePath": str(profile_path),
                            "distributionCertificate": {
                                "path": creds.dist_p12_path,
                                "password": creds.dist_p12_password,
                            },
                        }
                    },
                    indent=2,
                )
                + "\n"
            )
            eas_cfg = json.loads((build_dir / "eas.json").read_text())
            eas_cfg.setdefault("build", {}).setdefault("production", {})["credentialsSource"] = "local"
            (build_dir / "eas.json").write_text(json.dumps(eas_cfg, indent=2) + "\n")

            # Both lanes are store-signed; the eas profile is always `production` (see module doc).
            # Everything from here on is TRIGGER-AMBIGUOUS: the upload may have been accepted even
            # if the CLI times out or its output is unparseable — so those paths RECONCILE against
            # the EAS API before deciding, and only raise (→ release) when no build was created.
            try:
                out = _run(
                    ["npx", "--yes", f"eas-cli@{EAS_CLI_VERSION}", "build", "--platform", "ios",
                     "--profile", "production", "--non-interactive", "--no-wait", "--json"],
                    cwd=build_dir, env=child_env, what="eas build trigger",
                )
            except (subprocess.TimeoutExpired, RuntimeError) as exc:
                reconciled = _reconcile_triggered_build(build_dir, child_env)
                if reconciled:
                    return BuildResult(build_id=reconciled, lane=lane, detail="trigger reconciled after CLI failure")
                raise RuntimeError(f"eas build did not trigger: {exc}") from exc
            build_id = ""
            try:
                payload = json.loads(out)
                row = payload[0] if isinstance(payload, list) else payload
                build_id = str(row.get("id") or "").strip()
            except (ValueError, AttributeError, IndexError, TypeError):
                match = re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", out or "")
                build_id = match.group(0) if match else ""
            if not build_id:
                reconciled = _reconcile_triggered_build(build_dir, child_env)
                if reconciled:
                    return BuildResult(build_id=reconciled, lane=lane, detail="trigger reconciled from unparseable output")
                raise RuntimeError(f"eas build returned no build id: {(out or '')[:300]}")
            # The TRIGGER: Expo accepted the upload — money is spent; run_build settles on return.
            return BuildResult(build_id=build_id, lane=lane)
        finally:
            if not keep_build_dir:
                shutil.rmtree(build_root, ignore_errors=True)

    return _thunk


def poll_build(build_id: str, creds: StoreBuilderCreds, *, cwd: str | None = None) -> dict[str, Any]:
    """$0 status read for a triggered build (never touches the reservation). Returns
    {status, artifact_url, error}."""
    import os

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "EXPO_TOKEN": creds.expo_token,
        "EXPO_NO_TELEMETRY": "1",
    }
    proc = subprocess.run(
        ["npx", "--yes", f"eas-cli@{EAS_CLI_VERSION}", "build:view", build_id, "--json"],
        cwd=cwd or str(Path.home()), env=env, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        return {"status": "unknown", "artifact_url": "", "error": (proc.stderr or "")[-300:]}
    try:
        row = json.loads(proc.stdout)
    except ValueError:
        return {"status": "unknown", "artifact_url": "", "error": "unparseable build:view output"}
    return {
        "status": str(row.get("status") or ""),
        "artifact_url": str((row.get("artifacts") or {}).get("buildUrl") or ""),
        "error": str((row.get("error") or {}).get("message") or ""),
    }
