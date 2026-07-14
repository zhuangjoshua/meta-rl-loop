"""EnvironmentProvisioner — the backend half of UC3 (modularization plan §2.6 / Stage 3b).

``takyon env create|status|destroy <name>`` stands up a DEV environment that is prod-SHAPED
but fully ISOLATED. It is deterministic, idempotent, RECEIPTED infra-rail code — squarely inside
the "durable rails" exception (idempotent, receipted, destroy-safe).

Design invariants (all HARD):

- **Inert until invoked.** Importing this module touches nothing. Every provider call happens only
  inside :meth:`EnvironmentProvisioner.create` / :meth:`destroy`.
- **Never touches prod.** :func:`environment.assert_not_prod_leakage` (over ``PROD_LITERALS``) guards
  every resolved target — DSN, safebox URL, host, webhook URL. A manifest that resolves a prod literal
  refuses before any side effect. The CLI + provisioner also refuse ``name == 'prod'`` outright.
- **Fail closed on missing credentials.** Every step resolves its admin token via the safebox
  authority route (``safebox.first_env_backed_value(...)``), NEVER ``os.environ``. When a token is
  absent the step returns a ``blocked`` receipt naming the EXACT alias to deposit — it does not error
  opaquely and does not fabricate.
- **Idempotent.** Re-running ``create`` is a no-op on existing resources (topology/migrations are
  idempotent by construction; Auth0/Cloudflare/Stripe steps look up an existing resource by name
  before creating one).
- **Receipted.** Each step appends a structured receipt to ``<home>/environments/<name>/receipts.jsonl``
  and is returned in the result. Every deletion in ``destroy`` is receipted too.

The provisioner CONSUMES the DB rail (``db/topology.sql`` + ``db/runner.run_migrations`` +
``assert_migration_topology``) — it never re-implements them — and the RuntimeContext prod-literal
guard. It writes the resulting pointers into ``<home>/environments/<name>/config.yaml`` so a dev
``RuntimeContext.from_env`` (``TAKYON_ENV=dev``) reads what this stood up.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets as _secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from . import environment


# ── receipts + result shapes ──────────────────────────────────────────────────────────────

# A step is exactly one of these outcomes. `blocked` is the fail-closed state: a required credential
# is not deposited yet, and `deposit` names the exact alias the operator must provide.
STATUS_CREATED = "created"      # a new resource was provisioned this run
STATUS_EXISTS = "exists"        # the resource already existed — idempotent no-op
STATUS_BLOCKED = "blocked"      # fail-closed: a required credential is missing (see `deposit`)
STATUS_DISABLED = "disabled"    # the manifest turned this twin off
STATUS_SKIPPED = "skipped"      # not applicable this run (e.g. destroy with nothing to remove)
STATUS_ERROR = "error"          # the provider call failed (details in `detail`)
STATUS_DELETED = "deleted"      # destroy removed the resource


@dataclass(frozen=True)
class StepReceipt:
    """One structured, append-only receipt for a single provisioning step."""

    resource: str                       # 'database' | 'safebox' | 'auth0' | 'cloudflare' | 'stripe' | 'vpc' |
                                        # 'ssh_key' | 'droplets' | 'load_balancer' | 'firewall' | 'node_registry' | 'config'
    status: str                         # one of the STATUS_* constants above
    action: str                         # 'create' | 'status' | 'destroy'
    detail: str = ""                    # human-readable outcome
    deposit: str | None = None          # for STATUS_BLOCKED: the EXACT alias/env to deposit
    data: Mapping[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=lambda: time.time())

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "resource": self.resource,
            "status": self.status,
            "action": self.action,
            "ts": self.ts,
        }
        if self.detail:
            out["detail"] = self.detail
        if self.deposit:
            out["deposit"] = self.deposit
        if self.data:
            out["data"] = dict(self.data)
        return out


@dataclass(frozen=True)
class ProvisionResult:
    """The full outcome of a create/status/destroy run over one environment."""

    name: str
    action: str
    receipts: tuple[StepReceipt, ...]

    @property
    def ok(self) -> bool:
        """True when nothing is blocked/errored (disabled/skipped/exists/created/deleted are fine)."""
        return not any(r.status in (STATUS_BLOCKED, STATUS_ERROR) for r in self.receipts)

    @property
    def blocked(self) -> tuple[StepReceipt, ...]:
        return tuple(r for r in self.receipts if r.status == STATUS_BLOCKED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "action": self.action,
            "ok": self.ok,
            "receipts": [r.to_dict() for r in self.receipts],
        }


class _AdminDepositMissing(Exception):
    """Internal fail-closed signal: scoped-role DDL needs the admin DSN and it is not deposited."""

    def __init__(self, alias: str) -> None:
        super().__init__(alias)
        self.alias = alias


class EnvironmentProvisionError(RuntimeError):
    """A hard refusal (bad manifest, prod-literal leakage, name=prod) — distinct from a blocked
    credential (which is a receipt, not an exception)."""


# ── manifest loading ───────────────────────────────────────────────────────────────────────

# The manifests live next to the package root (hermes-agent-main/environments/*.yaml).
_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "environments"

# The outer workspace git repo — the tracked source of every environment's `code_revision`.
# hermes-agent-main/plugins/takyon/ -> hermes-agent-main -> <workspace root>.
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd: Path | None = None) -> str | None:
    """Run one git command in the workspace repo; return stripped stdout, or None on ANY failure
    (not a git checkout, git missing, bad ref, non-zero exit). Never raises — code_revision
    reporting/deploy is best-effort and must degrade cleanly on a deployed host that is not a
    checkout of the workspace repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd or _REPO_ROOT), *args],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    """True iff the git command exits 0 (for boolean predicates like `merge-base --is-ancestor`,
    where success prints nothing). Never raises."""
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd or _REPO_ROOT), *args],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0

_REQUIRED_TOP_KEYS = ("name", "domains", "database", "safebox")


def manifest_path(name: str) -> Path:
    safe = _safe_env_name(name)
    return _MANIFEST_DIR / f"{safe}.yaml"


def load_manifest(name: str) -> dict[str, Any]:
    """Parse ``environments/<name>.yaml`` and validate the required keys. Pure — no side effects."""
    import yaml  # PyYAML is already a runtime dep (config.yaml is YAML); no new dependency.

    path = manifest_path(name)
    if not path.exists():
        raise EnvironmentProvisionError(
            f"no environment manifest at {path} — create environments/{_safe_env_name(name)}.yaml first"
        )
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise EnvironmentProvisionError(f"manifest {path} must be a YAML mapping")
    missing = [k for k in _REQUIRED_TOP_KEYS if k not in data]
    if missing:
        raise EnvironmentProvisionError(f"manifest {path} missing required keys: {missing}")
    declared_name = str(data.get("name") or "").strip().lower()
    if declared_name != _safe_env_name(name):
        raise EnvironmentProvisionError(
            f"manifest name {declared_name!r} does not match requested env {name!r}"
        )
    return data


def _safe_env_name(name: str) -> str:
    safe = str(name or "").strip().lower()
    if not safe or not all(c.isalnum() or c in "-_" for c in safe):
        raise EnvironmentProvisionError(f"invalid environment name {name!r}")
    return safe


# ── the provisioner ─────────────────────────────────────────────────────────────────────────


class EnvironmentProvisioner:
    """Stands up / inspects / tears down the API-provisionable twins for one environment.

    Construct with the env name; ``home`` defaults to the current ``TAKYON_HOME`` (where receipts +
    the resolved config are written). ``safebox_mod`` and an ``http`` transport are injectable so the
    tests can drive create() with fakes and assert fail-closed behavior WITHOUT any network.
    """

    def __init__(
        self,
        name: str,
        *,
        home: Path | None = None,
        safebox_mod: Any | None = None,
        http: "HttpTransport | None" = None,
        manifest: Mapping[str, Any] | None = None,
        remote: "RemoteExec | None" = None,
        probe: "HttpProbe | None" = None,
        sleep: Any | None = None,
    ) -> None:
        self.name = _safe_env_name(name)
        if self.name == "prod":
            # Hard refusal: this rail exists to stand up ISOLATED twins, never to touch prod.
            raise EnvironmentProvisionError(
                "refusing to provision name='prod' — the provisioner only stands up isolated twins"
            )
        self.manifest = dict(manifest) if manifest is not None else load_manifest(self.name)
        # The git ref this env's hosts run (the code gate). Empty = env runs its host's own rev.
        self.code_revision = str(self.manifest.get("code_revision") or "").strip()
        home_raw = str(home) if home is not None else str(os.getenv("TAKYON_HOME") or "").strip()
        self.home = Path(home_raw) if home_raw else Path.home() / ".takyon"
        # Lazy import keeps this module inert and cheap to import; safebox is a leaf.
        if safebox_mod is None:
            from . import safebox as safebox_mod  # type: ignore[no-redef]
        self.safebox = safebox_mod
        self.http = http or UrllibTransport()
        # Rolling-restart transports (injectable so the drain tests run with zero network/SSH):
        # `remote` runs a script on a replica as root over SSH; `probe` is a plain HTTP GET that
        # ALSO returns response headers (the LB rejoin gate reads X-Takyon-Node); `sleep` is the
        # grace/poll wait (tests inject a no-op).
        self.remote = remote or SshRemoteExec()
        self.probe = probe or UrllibProbe()
        self._sleep = sleep or time.sleep
        # Per-run cache for a Management API token minted from client credentials, so one run's
        # create+destroy mints at most once. Never persisted.
        self._auth0_minted_token: str = ""
        # Per-run DigitalOcean state threaded between the DO steps (vpc -> droplets -> lb ->
        # firewall -> node registry). IDs only, never a credential. Reset per create()/destroy().
        self._do_state: dict[str, Any] = {}

    # -- receipts I/O ------------------------------------------------------------------------

    @property
    def env_dir(self) -> Path:
        return self.home / "environments" / self.name

    @property
    def receipts_path(self) -> Path:
        return self.env_dir / "receipts.jsonl"

    @property
    def config_path(self) -> Path:
        return self.env_dir / "config.yaml"

    def _append_receipt(self, receipt: StepReceipt) -> StepReceipt:
        try:
            self.env_dir.mkdir(parents=True, exist_ok=True)
            with self.receipts_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(receipt.to_dict(), separators=(",", ":")) + "\n")
        except OSError:
            # Receipts are best-effort durable; never let a receipt write failure mask the real outcome.
            pass
        return receipt

    # -- credential resolution (safebox authority route ONLY, never os.environ) --------------

    def _resolve_alias(self, *aliases: str) -> str:
        """Resolve the first non-empty value across safebox aliases via the operator-authority route.

        This is the ONLY credential-read path. On the operator host it reads the local dev-safebox
        env; from a runtime plane it is a remote authority call. Either way the raw value never lands
        in a manifest and is never read from ``os.environ`` directly."""
        names = [a for a in aliases if a]
        if not names:
            return ""
        try:
            return str(self.safebox.first_env_backed_value(*names) or "").strip()
        except Exception:
            # A safebox that cannot answer is treated as "absent" — the caller emits a blocked receipt
            # naming the alias, which is the correct fail-closed signal.
            return ""

    def _assert_not_prod(self, *values: str) -> None:
        """Guard a set of RESOLVED targets against the prod-literal deny-list. Reuses the exact same
        gate RuntimeContext boot uses (environment.PROD_LITERALS), so dev and boot agree."""
        blob = " ".join(str(v or "") for v in values)
        hits = sorted({lit for lit in environment.PROD_LITERALS if lit and lit in blob})
        if hits:
            raise EnvironmentProvisionError(
                f"environment {self.name!r} resolves prod literal(s) {hits} — a non-prod environment "
                "must point every twin at its own isolated resources. Fix the manifest / dev safebox; "
                "never bypass this gate."
            )

    # ── CREATE ──────────────────────────────────────────────────────────────────────────────

    def create(self) -> ProvisionResult:
        """Stand up every enabled twin. Idempotent, receipted, fail-closed. Returns all receipts;
        blocked steps name the exact alias to deposit."""
        receipts: list[StepReceipt] = []
        receipts.append(self._append_receipt(self._create_database()))
        receipts.append(self._append_receipt(self._create_safebox()))
        receipts.append(self._append_receipt(self._create_auth0()))
        receipts.append(self._append_receipt(self._create_cloudflare()))
        receipts.append(self._append_receipt(self._create_stripe()))
        # DigitalOcean dev-split twins (Stage 4b): order matters — the droplets join the VPC,
        # the LB fronts the droplets (by role tag), the firewall references the LB id, and the
        # node registry enrolls the created replicas.
        receipts.append(self._append_receipt(self._create_vpc()))
        receipts.append(self._append_receipt(self._create_ssh_key()))
        receipts.append(self._append_receipt(self._create_droplets()))
        receipts.append(self._append_receipt(self._create_load_balancer()))
        receipts.append(self._append_receipt(self._create_firewall()))
        receipts.append(self._append_receipt(self._bootstrap_missing_hosts()))
        receipts.append(self._append_receipt(self._register_replica_nodes()))
        receipts.append(self._append_receipt(self._enroll_replica_credentials()))
        receipts.append(self._append_receipt(self._write_config(receipts)))
        return ProvisionResult(name=self.name, action="create", receipts=tuple(receipts))

    # (a) DB — dev's own Supabase project: assert non-prod DSN, apply topology.sql + run_migrations.
    def _create_database(self) -> StepReceipt:
        cfg = self.manifest.get("database") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("database", STATUS_DISABLED, "create", "database twin disabled in manifest")

        dsn_alias = str(cfg.get("dsn_alias") or "").strip()
        if not dsn_alias:
            return StepReceipt(
                "database", STATUS_ERROR, "create",
                "manifest database.dsn_alias is required to resolve the dev migration DSN",
            )
        dsn = self._resolve_alias(dsn_alias)
        if not dsn:
            return StepReceipt(
                "database", STATUS_BLOCKED, "create",
                f"dev migration DSN not deposited (alias {dsn_alias})",
                deposit=dsn_alias,
            )
        # HARD: the dev DSN must not be a prod literal (reuses environment.PROD_LITERALS).
        self._assert_not_prod(dsn)

        # topology.sql is PRIVILEGED work (CREATEROLE + admin-option grants) — it must run as the
        # project's admin role (postgres), never as takyon_migration: Postgres refuses "ADMIN option
        # cannot be granted back to your own grantor". So topology resolves its OWN admin DSN alias.
        # When the admin DSN is absent, topology is skipped — safely, because run_migrations calls
        # assert_migration_topology first and fails loudly (naming the exact SQL) if the topology is
        # actually missing. Re-runs on a bootstrapped DB therefore need no admin credential at all.
        admin_dsn_alias = str(cfg.get("admin_dsn_alias") or "").strip()
        admin_dsn = self._resolve_alias(admin_dsn_alias) if admin_dsn_alias else ""
        if admin_dsn:
            self._assert_not_prod(admin_dsn)

        # === REAL DB SIDE EFFECT (gated behind the resolved, non-prod DSN) ===
        # Consumes the DB rail verbatim: topology.sql (admin DSN) then run_migrations (migration DSN).
        try:
            import psycopg
            from .db import runner as db_runner
        except Exception as exc:  # pragma: no cover - import guard
            return StepReceipt("database", STATUS_ERROR, "create", f"db rail unavailable: {exc}")

        applied: list[str] = []
        topology_note = "topology skipped (disabled in manifest)"
        try:
            if cfg.get("apply_topology", True):
                if admin_dsn:
                    with psycopg.connect(admin_dsn, autocommit=True, prepare_threshold=None) as admin_conn:
                        admin_conn.execute("select set_config('statement_timeout', '0', false)")
                        admin_conn.execute(db_runner.topology_sql_path().read_text())
                    topology_note = "topology applied (admin DSN)"
                else:
                    topology_note = (
                        f"topology not re-applied (admin DSN alias {admin_dsn_alias or 'unset'} absent); "
                        "run_migrations asserts the existing topology"
                    )
            with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
                conn.execute("select set_config('statement_timeout', '0', false)")
                if cfg.get("apply_migrations", True):
                    # run_migrations calls assert_migration_topology internally, then applies every
                    # db/migrations/*.sql idempotently. Re-running is a safe no-op.
                    applied = db_runner.run_migrations(conn)
        except Exception as exc:
            return StepReceipt("database", STATUS_ERROR, "create", f"dev DB provisioning failed: {exc}")

        return StepReceipt(
            "database",
            STATUS_CREATED if applied else STATUS_EXISTS,
            "create",
            f"{topology_note}; {len(applied)} migration(s) replayed on the dev control plane",
            data={"migrations_applied": len(applied), "last_migration": applied[-1] if applied else None},
        )

    # (b) dev safebox — verify/report the alias set the dev safebox must hold. Never seeds a value.
    def _create_safebox(self) -> StepReceipt:
        cfg = self.manifest.get("safebox") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("safebox", STATUS_DISABLED, "create", "safebox twin disabled in manifest")

        url_alias = str(cfg.get("url_alias") or "").strip()
        url = self._resolve_alias(url_alias) if url_alias else ""
        if url_alias and not url:
            return StepReceipt(
                "safebox", STATUS_BLOCKED, "create",
                f"dev safebox URL not deposited (alias {url_alias})",
                deposit=url_alias,
            )
        self._assert_not_prod(url)

        # Flatten the grouped required_aliases into a single list, then report which are present.
        groups = cfg.get("required_aliases") or {}
        wanted: list[str] = []
        for _group, aliases in (groups.items() if isinstance(groups, dict) else []):
            for alias in (aliases or []):
                a = str(alias or "").strip()
                if a and a not in wanted:
                    wanted.append(a)

        missing: list[str] = []
        present: list[str] = []
        for alias in wanted:
            (present if self._resolve_alias(alias) else missing).append(alias)

        if missing:
            # Fail-closed: name EACH missing alias to deposit into the dev safebox. Deposit field holds
            # the first; detail lists them all.
            return StepReceipt(
                "safebox", STATUS_BLOCKED, "create",
                f"dev safebox is missing {len(missing)} alias(es): {', '.join(missing)}",
                deposit=missing[0],
                data={"missing": missing, "present": present},
            )
        return StepReceipt(
            "safebox", STATUS_EXISTS, "create",
            f"dev safebox holds all {len(present)} required alias(es)",
            data={"present": present, "url_set": bool(url)},
        )

    # -- Auth0 Management API credential (token OR client-credentials mint) -------------------

    def _resolve_auth0_mgmt_token(self, cfg: Mapping[str, Any], domain: str) -> tuple[str, StepReceipt | None]:
        """Resolve a Management API bearer for the tenant, durable-first.

        Two accepted deposits, in order:
        1. ``mgmt_token_alias`` (default TAKYON_AUTH0_MGMT_TOKEN) — a raw token. Auth0 mgmt tokens
           expire in ~24h, so this path suits one-shot runs only.
        2. ``mgmt_client_id_alias``/``mgmt_client_secret_alias`` (default TAKYON_AUTH0_MGMT_CLIENT_ID/
           _SECRET) — an M2M application authorized for the Management API. A fresh token is minted
           per run via the client_credentials grant, so the deposit never goes stale.

        Returns ``(token, None)`` or ``("", blocked_receipt)``. The mint goes through ``self.http``
        so tests drive it with a fake and no network.
        """
        token_alias = str(cfg.get("mgmt_token_alias") or "TAKYON_AUTH0_MGMT_TOKEN").strip()
        token = self._resolve_alias(token_alias)
        if token:
            return token, None
        if self._auth0_minted_token:
            return self._auth0_minted_token, None

        id_alias = str(cfg.get("mgmt_client_id_alias") or "TAKYON_AUTH0_MGMT_CLIENT_ID").strip()
        secret_alias = str(cfg.get("mgmt_client_secret_alias") or "TAKYON_AUTH0_MGMT_CLIENT_SECRET").strip()
        client_id = self._resolve_alias(id_alias)
        client_secret = self._resolve_alias(secret_alias)
        if not client_id or not client_secret:
            return "", StepReceipt(
                "auth0", STATUS_BLOCKED, "create",
                f"Auth0 Management API credential not deposited: deposit {id_alias}+{secret_alias} "
                f"(an M2M app authorized for the Management API — durable, minted per run) or "
                f"{token_alias} (a raw ~24h token)",
                deposit=id_alias if not client_id else secret_alias,
            )
        try:
            minted = self.http.request(
                "POST",
                f"https://{domain.rstrip('/')}/oauth/token",
                headers={"Content-Type": "application/json"},
                body={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "audience": f"https://{domain.rstrip('/')}/api/v2/",
                },
            )
        except Exception as exc:
            return "", StepReceipt(
                "auth0", STATUS_ERROR, "create", f"auth0 mgmt token mint failed: {exc}"
            )
        token = str((minted or {}).get("access_token") or "").strip()
        if not token:
            return "", StepReceipt(
                "auth0", STATUS_ERROR, "create",
                "auth0 mgmt token mint returned no access_token",
            )
        self._auth0_minted_token = token
        return token, None

    # (c) Auth0 dev application via the Management API (credential resolved via safebox; fail-closed if absent).
    def _create_auth0(self) -> StepReceipt:
        cfg = self.manifest.get("auth0") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("auth0", STATUS_DISABLED, "create", "auth0 twin disabled in manifest")

        domain_alias = str(cfg.get("domain_alias") or "AUTH0_DOMAIN").strip()
        domain = self._resolve_alias(domain_alias)
        if not domain:
            return StepReceipt(
                "auth0", STATUS_BLOCKED, "create",
                f"Auth0 tenant domain not deposited (alias {domain_alias})",
                deposit=domain_alias,
            )
        self._assert_not_prod(domain)
        token, blocked = self._resolve_auth0_mgmt_token(cfg, domain)
        if blocked is not None:
            return blocked

        app_name = str(cfg.get("application_name") or f"Takyon {self.name.title()}").strip()
        base = f"https://{domain.rstrip('/')}/api/v2"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # === REAL Auth0 Management API call (gated behind the resolved token) ===
        # Idempotent and convergent: an existing application is patched to the manifest contract.
        try:
            existing = self.http.request(
                "GET",
                f"{base}/clients?"
                + urllib.parse.urlencode({"fields": "client_id,name", "include_fields": "true"}),
                headers=headers,
            )
        except Exception as exc:
            return StepReceipt("auth0", STATUS_ERROR, "create", f"auth0 clients list failed: {exc}")
        for client in (existing if isinstance(existing, list) else []):
            if isinstance(client, dict) and str(client.get("name") or "") == app_name:
                client_id = str(client.get("client_id") or "").strip()
                body = {
                    "name": app_name,
                    "app_type": "regular_web",
                    "callbacks": list(cfg.get("callback_urls") or []),
                    "allowed_logout_urls": list(cfg.get("logout_urls") or []),
                    "web_origins": list(cfg.get("web_origins") or []),
                }
                try:
                    current = self.http.request(
                        "GET", f"{base}/clients/{urllib.parse.quote(client_id)}", headers=headers,
                    )
                    if not isinstance(current, dict):
                        raise EnvironmentProvisionError("auth0 client read returned a non-object")
                    changed = any(current.get(key) != value for key, value in body.items())
                    if changed:
                        self.http.request(
                            "PATCH", f"{base}/clients/{urllib.parse.quote(client_id)}",
                            headers=headers, body=body,
                        )
                except Exception as exc:
                    return StepReceipt(
                        "auth0", STATUS_ERROR, "create", f"auth0 application convergence failed: {exc}",
                    )
                return StepReceipt(
                    "auth0", STATUS_CREATED if changed else STATUS_EXISTS, "create",
                    (f"updated auth0 application {app_name!r}" if changed
                     else f"auth0 application {app_name!r} is current"),
                    data={"client_id": client_id},
                )

        body = {
            "name": app_name,
            "app_type": "regular_web",
            "callbacks": list(cfg.get("callback_urls") or []),
            "allowed_logout_urls": list(cfg.get("logout_urls") or []),
            "web_origins": list(cfg.get("web_origins") or []),
        }
        try:
            created = self.http.request("POST", f"{base}/clients", headers=headers, body=body)
        except Exception as exc:
            return StepReceipt("auth0", STATUS_ERROR, "create", f"auth0 application create failed: {exc}")
        return StepReceipt(
            "auth0", STATUS_CREATED, "create",
            f"created auth0 application {app_name!r}",
            data={"client_id": (created or {}).get("client_id")},
        )

    # (d) Cloudflare dev DNS/R2 — optional, gated by manifest.cloudflare.enabled. Token already in safebox.
    def _create_cloudflare(self) -> StepReceipt:
        cfg = self.manifest.get("cloudflare") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("cloudflare", STATUS_DISABLED, "create", "cloudflare twin disabled in manifest")

        token_alias = str(cfg.get("token_alias") or "CLOUDFLARE_API_TOKEN").strip()
        token = self._resolve_alias(token_alias)
        if not token:
            return StepReceipt(
                "cloudflare", STATUS_BLOCKED, "create",
                f"Cloudflare API token not deposited (alias {token_alias})",
                deposit=token_alias,
            )
        zone = str(cfg.get("zone_name") or "").strip()
        self._assert_not_prod(zone)
        bucket = str(cfg.get("r2_bucket") or "").strip()
        base = "https://api.cloudflare.com/client/v4"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # === REAL Cloudflare API calls (gated behind the resolved token) ===
        # Idempotent: resolve the account, look up the R2 bucket before creating it.
        try:
            accounts = self.http.request("GET", f"{base}/accounts", headers=headers)
        except Exception as exc:
            return StepReceipt("cloudflare", STATUS_ERROR, "create", f"cloudflare accounts list failed: {exc}")
        result = accounts.get("result") if isinstance(accounts, dict) else None
        account_id = (result[0].get("id") if result else None) if isinstance(result, list) else None
        if not account_id:
            return StepReceipt("cloudflare", STATUS_ERROR, "create", "cloudflare account not resolvable")

        created_bucket = False
        if bucket:
            try:
                existing = self.http.request(
                    "GET", f"{base}/accounts/{account_id}/r2/buckets", headers=headers
                )
                names = {
                    b.get("name")
                    for b in ((existing.get("result") or {}).get("buckets") or [])
                    if isinstance(b, dict)
                } if isinstance(existing, dict) else set()
                if bucket not in names:
                    self.http.request(
                        "POST", f"{base}/accounts/{account_id}/r2/buckets",
                        headers=headers, body={"name": bucket},
                    )
                    created_bucket = True
            except Exception as exc:
                return StepReceipt("cloudflare", STATUS_ERROR, "create", f"cloudflare r2 bucket failed: {exc}")

        return StepReceipt(
            "cloudflare",
            STATUS_CREATED if created_bucket else STATUS_EXISTS,
            "create",
            f"cloudflare dev twin ready (bucket {bucket or 'n/a'})",
            data={"account_id": account_id, "r2_bucket": bucket, "created_bucket": created_bucket},
        )

    # (e) Stripe TEST webhook registration (safebox holds STRIPE_SECRET_KEY; assert test-mode key).
    def _create_stripe(self) -> StepReceipt:
        cfg = self.manifest.get("stripe") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("stripe", STATUS_DISABLED, "create", "stripe twin disabled in manifest")

        # The Stripe secret key is an infra secret held by the safebox under the 'stripe' alias set.
        secret = self._resolve_alias("STRIPE_SECRET_KEY")
        if not secret:
            return StepReceipt(
                "stripe", STATUS_BLOCKED, "create",
                "Stripe secret key not deposited (alias STRIPE_SECRET_KEY) in the dev safebox",
                deposit="STRIPE_SECRET_KEY",
            )
        # HARD: dev must use a TEST-mode key. A live key here would register a webhook on the real
        # Stripe account — refuse it.
        if not secret.startswith(("sk_test_", "rk_test_")):
            return StepReceipt(
                "stripe", STATUS_ERROR, "create",
                "dev Stripe key is not a TEST-mode key (expected sk_test_/rk_test_); "
                "deposit a Stripe TEST secret key in the dev safebox",
            )

        webhook_url = str(cfg.get("webhook_url") or "").strip()
        if not webhook_url:
            return StepReceipt("stripe", STATUS_ERROR, "create", "manifest stripe.webhook_url is required")
        self._assert_not_prod(webhook_url)
        # Stripe refuses loopback endpoint URLs outright ("URL must be publicly accessible"; its 400
        # points at the Stripe CLI). The REAL local-dev webhook rail is CLI forwarding, which mints
        # its own signing secret — so a loopback webhook_url skips registration with that guidance
        # instead of erroring every run. A public dev twin sets a public webhook_url and registers.
        webhook_host = (urllib.parse.urlsplit(webhook_url).hostname or "").lower()
        if webhook_host in {"localhost", "127.0.0.1", "::1"}:
            return StepReceipt(
                "stripe", STATUS_SKIPPED, "create",
                f"webhook_url {webhook_url} is loopback — Stripe cannot deliver there; for local dev "
                "run `stripe listen --forward-to " + webhook_url.split("://", 1)[-1] + "` (the CLI "
                "prints the whsec_… to deposit as STRIPE_WEBHOOK_SECRET)",
            )
        events = list(cfg.get("enabled_events") or [])

        base = "https://api.stripe.com/v1"
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        # === REAL Stripe API calls against the TEST key (gated) ===
        # Idempotent: list webhook endpoints and reuse one already pointed at webhook_url.
        try:
            listed = self.http.request("GET", f"{base}/webhook_endpoints?limit=100", headers=headers)
        except Exception as exc:
            return StepReceipt("stripe", STATUS_ERROR, "create", f"stripe webhook list failed: {exc}")
        for ep in (listed.get("data") or []) if isinstance(listed, dict) else []:
            if isinstance(ep, dict) and str(ep.get("url") or "") == webhook_url:
                endpoint_id = str(ep.get("id") or "").strip()
                desired_events = {str(event) for event in events}
                current_events = {
                    str(event) for event in (ep.get("enabled_events") or [])
                }
                if endpoint_id and current_events != desired_events:
                    form = urllib.parse.urlencode(
                        [("enabled_events[]", event) for event in sorted(desired_events)]
                    )
                    try:
                        self.http.request(
                            "POST",
                            f"{base}/webhook_endpoints/{endpoint_id}",
                            headers=headers,
                            form=form,
                        )
                    except Exception as exc:
                        return StepReceipt(
                            "stripe",
                            STATUS_ERROR,
                            "create",
                            f"stripe webhook event update failed: {exc}",
                        )
                    return StepReceipt(
                        "stripe",
                        STATUS_CREATED,
                        "create",
                        f"updated stripe TEST webhook events for {webhook_url}",
                        data={"webhook_endpoint_id": endpoint_id, "events": len(events)},
                    )
                return StepReceipt(
                    "stripe", STATUS_EXISTS, "create",
                    f"stripe test webhook already registered for {webhook_url}",
                    data={"webhook_endpoint_id": endpoint_id},
                )

        # Stripe form-encodes list params as enabled_events[].
        form: list[tuple[str, str]] = [("url", webhook_url)]
        for ev in events:
            form.append(("enabled_events[]", str(ev)))
        try:
            created = self.http.request(
                "POST", f"{base}/webhook_endpoints", headers=headers,
                form=urllib.parse.urlencode(form),
            )
        except Exception as exc:
            return StepReceipt("stripe", STATUS_ERROR, "create", f"stripe webhook create failed: {exc}")
        return StepReceipt(
            "stripe", STATUS_CREATED, "create",
            f"registered stripe TEST webhook for {webhook_url}",
            data={"webhook_endpoint_id": (created or {}).get("id"), "events": len(events)},
        )

    # ── (f) DigitalOcean dev split (Stage 4b): VPC + ssh key + N replicas + LB + firewall ──────
    #
    # Every resource is idempotent (looked up by name/tag before create), receipted, and tagged
    # `takyon-env-<name>` so destroy() can sweep EXACTLY what this rail created. The subuser
    # security model is identical to prod: replicas never hold operator/safebox DSNs, and the
    # firewall admits :9119/:80 only from the LB (plus the env's own tagged droplets) and :22
    # only from the operator's deposited CIDR.

    _DO_BASE = "https://api.digitalocean.com/v2"

    @property
    def env_tag(self) -> str:
        """The tag on every DO resource this environment owns; destroy sweeps this tag."""
        return f"takyon-env-{self.name}"

    def role_tag(self, role: str) -> str:
        return f"{self.env_tag}-{str(role or '').strip().lower()}"

    def _do_token_or_blocked(self, resource: str, action: str = "create") -> tuple[str, StepReceipt | None]:
        """Resolve the DigitalOcean API token for a DO step, fail-closed. The alias chain is the
        block's own ``token_alias``, then the droplets block's, then TAKYON_DO_API_TOKEN."""
        cfg = self.manifest.get(resource) or {}
        droplets_cfg = self.manifest.get("droplets") or {}
        alias = str(
            cfg.get("token_alias") or droplets_cfg.get("token_alias") or "TAKYON_DO_API_TOKEN"
        ).strip()
        token = self._resolve_alias(alias)
        if not token:
            return "", StepReceipt(
                resource, STATUS_BLOCKED, action,
                f"DigitalOcean API token not deposited (alias {alias}); deposit it once to enable "
                "autonomous dev compute provisioning",
                deposit=alias,
            )
        return token, None

    def _do_headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _guard_do_block(self, cfg: Mapping[str, Any]) -> None:
        """Prod-literal guard over a whole DO manifest block (names, ranges, urls)."""
        self._assert_not_prod(json.dumps(dict(cfg), sort_keys=True, default=str))

    # (f1) VPC — dev droplets live in their OWN network, never the prod VPC (10.116.0.0/20).
    def _create_vpc(self) -> StepReceipt:
        cfg = self.manifest.get("vpc") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("vpc", STATUS_DISABLED, "create", "vpc twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("vpc")
        if blocked is not None:
            return blocked
        self._guard_do_block(cfg)
        name = str(cfg.get("name") or f"takyon-{self.name}-vpc").strip()
        region = str(cfg.get("region") or "nyc3").strip()
        headers = self._do_headers(token)
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/vpcs?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("vpc", STATUS_ERROR, "create", f"vpc list failed: {exc}")
        for v in (listed.get("vpcs") or []) if isinstance(listed, dict) else []:
            if isinstance(v, dict) and str(v.get("name") or "") == name:
                self._do_state["vpc_id"] = v.get("id")
                return StepReceipt(
                    "vpc", STATUS_EXISTS, "create", f"vpc {name!r} already exists",
                    data={"vpc_id": v.get("id"), "ip_range": v.get("ip_range")},
                )
        body = {"name": name, "region": region}
        ip_range = str(cfg.get("ip_range") or "").strip()
        if ip_range:
            body["ip_range"] = ip_range
        try:
            created = self.http.request("POST", f"{self._DO_BASE}/vpcs", headers=headers, body=body)
        except Exception as exc:
            return StepReceipt("vpc", STATUS_ERROR, "create", f"vpc create failed: {exc}")
        vpc = (created or {}).get("vpc") if isinstance(created, dict) else {}
        self._do_state["vpc_id"] = (vpc or {}).get("id")
        return StepReceipt(
            "vpc", STATUS_CREATED, "create", f"created dev vpc {name!r}",
            data={"vpc_id": (vpc or {}).get("id"), "ip_range": (vpc or {}).get("ip_range") or ip_range},
        )

    # (f2) dedicated ssh key — the operator generates the keypair locally; the manifest points at
    # the PUBLIC half (never a secret). Registered by name, idempotent.
    def _create_ssh_key(self) -> StepReceipt:
        cfg = self.manifest.get("ssh_key") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("ssh_key", STATUS_DISABLED, "create", "ssh_key twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("ssh_key")
        if blocked is not None:
            return blocked
        name = str(cfg.get("name") or f"takyon-{self.name}-split").strip()
        headers = self._do_headers(token)
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/account/keys?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("ssh_key", STATUS_ERROR, "create", f"ssh key list failed: {exc}")
        for k in (listed.get("ssh_keys") or []) if isinstance(listed, dict) else []:
            if isinstance(k, dict) and str(k.get("name") or "") == name:
                self._do_state["ssh_key_id"] = k.get("id")
                return StepReceipt(
                    "ssh_key", STATUS_EXISTS, "create", f"ssh key {name!r} already registered",
                    data={"ssh_key_id": k.get("id"), "fingerprint": k.get("fingerprint")},
                )
        pub_path = Path(str(cfg.get("public_key_path") or "")).expanduser()
        if not str(cfg.get("public_key_path") or "").strip() or not pub_path.exists():
            return StepReceipt(
                "ssh_key", STATUS_BLOCKED, "create",
                f"public key not found at ssh_key.public_key_path ({pub_path}); generate the dev "
                "split keypair first (ssh-keygen -t ed25519 -N '' -f ~/.ssh/takyon_dev_split)",
            )
        try:
            created = self.http.request(
                "POST", f"{self._DO_BASE}/account/keys", headers=headers,
                body={"name": name, "public_key": pub_path.read_text().strip()},
            )
        except Exception as exc:
            return StepReceipt("ssh_key", STATUS_ERROR, "create", f"ssh key create failed: {exc}")
        key = (created or {}).get("ssh_key") if isinstance(created, dict) else {}
        self._do_state["ssh_key_id"] = (key or {}).get("id")
        return StepReceipt(
            "ssh_key", STATUS_CREATED, "create", f"registered dev ssh key {name!r}",
            data={"ssh_key_id": (key or {}).get("id"), "fingerprint": (key or {}).get("fingerprint")},
        )

    # (f3) droplets — the replicated subuser split plus the singleton Safebox and operator hosts.
    # This is the same three-role compute topology as production; only the isolated environment
    # slices and replica count are manifest data.
    def _create_droplets(self) -> StepReceipt:
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("droplets", STATUS_DISABLED, "create", "droplets twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("droplets")
        if blocked is not None:
            return blocked
        self._guard_do_block(cfg)

        role = str(cfg.get("role") or "subuser").strip().lower()
        count = max(1, int(cfg.get("count") or 1))
        prefix = str(cfg.get("name_prefix") or f"takyon-{self.name}-{role}").strip()
        region = str(cfg.get("region") or "nyc3").strip()
        size = str(cfg.get("size") or "s-1vcpu-2gb").strip()
        headers = self._do_headers(token)

        # Desired set: the N subuser replicas plus the singleton authority/compute hosts.
        desired: list[dict[str, Any]] = [
            {"name": f"{prefix}-{i}", "role": role, "size": size} for i in range(1, count + 1)
        ]
        sb_host = cfg.get("safebox_host") or {}
        if sb_host.get("enabled", False):
            desired.append({
                "name": str(sb_host.get("name") or f"takyon-{self.name}-safebox").strip(),
                "role": "safebox",
                "size": str(sb_host.get("size") or size).strip(),
            })
        operator_host = cfg.get("operator_host") or {}
        if operator_host.get("enabled", False):
            desired.append({
                "name": str(
                    operator_host.get("name") or f"takyon-{self.name}-operator"
                ).strip(),
                "role": "operator",
                "size": str(operator_host.get("size") or size).strip(),
            })

        # Idempotent: list the account's droplets and match the manifest-derived EXACT names.
        # (Name-exact matching works on tokens without tag scope too; the env tag is still
        # attempted on create, and the tags a droplet actually carries are recorded.)
        try:
            listed = self.http.request(
                "GET", f"{self._DO_BASE}/droplets?per_page=200", headers=headers
            )
        except Exception as exc:
            return StepReceipt("droplets", STATUS_ERROR, "create", f"droplet list failed: {exc}")
        existing = {
            str(d.get("name") or ""): d
            for d in ((listed.get("droplets") or []) if isinstance(listed, dict) else [])
            if isinstance(d, dict)
        }

        results: list[dict[str, Any]] = []
        created_any = False
        for spec in desired:
            name = spec["name"]
            if name in existing:
                d = existing[name]
                results.append({
                    "name": name, "role": spec["role"], "droplet_id": d.get("id"),
                    "created": False, "tagged": self.env_tag in (d.get("tags") or []),
                })
                continue
            body: dict[str, Any] = {
                "name": name,
                "region": region,
                "size": spec["size"],
                "image": str(cfg.get("image") or "ubuntu-24-04-x64"),
                # cloud-init marks the node's environment; the real runtime bootstrap (rsync +
                # venv + unit) is the tracked deploy rail, mirroring how prod hosts deploy.
                "user_data": f"#cloud-config\nruncmd:\n  - echo 'TAKYON_ENV={self.name}' >> /etc/environment\n",
                "tags": [self.env_tag, self.role_tag(spec["role"])],
            }
            if self._do_state.get("vpc_id"):
                body["vpc_uuid"] = self._do_state["vpc_id"]
            if self._do_state.get("ssh_key_id"):
                body["ssh_keys"] = [self._do_state["ssh_key_id"]]
            tagged = True
            try:
                created = self.http.request("POST", f"{self._DO_BASE}/droplets", headers=headers, body=body)
            except Exception as exc:
                # Scope fallback: a token WITHOUT tag:create (the dev token deliberately holds
                # tag read-only) cannot mint the env tag at droplet create. Fall back to an
                # untagged create — idempotency + destroy remain safe because both also match
                # the manifest-derived exact names — and record tagged=false in the receipt.
                if "tag" not in str(exc).lower():
                    return StepReceipt(
                        "droplets", STATUS_ERROR, "create", f"droplet create failed for {name!r}: {exc}",
                        data={"droplets": results},
                    )
                body.pop("tags", None)
                tagged = False
                try:
                    created = self.http.request("POST", f"{self._DO_BASE}/droplets", headers=headers, body=body)
                except Exception as exc2:
                    return StepReceipt(
                        "droplets", STATUS_ERROR, "create", f"droplet create failed for {name!r}: {exc2}",
                        data={"droplets": results},
                    )
            droplet = (created or {}).get("droplet") if isinstance(created, dict) else {}
            results.append({
                "name": name, "role": spec["role"], "droplet_id": (droplet or {}).get("id"),
                "created": True, "tagged": tagged,
            })
            created_any = True

        self._do_state["droplets"] = results
        replica_names = [r["name"] for r in results if r["role"] == role]
        return StepReceipt(
            "droplets",
            STATUS_CREATED if created_any else STATUS_EXISTS,
            "create",
            f"{len(replica_names)} {role} replica(s) "
            + (f"+ safebox host " if sb_host.get("enabled", False) else "")
            + (f"+ operator host " if operator_host.get("enabled", False) else "")
            + ("provisioned" if created_any else "already present"),
            data={"droplets": results, "tag": self.env_tag},
        )

    def _bootstrap_missing_hosts(self) -> StepReceipt:
        """Turn freshly-created droplets into the declared production-shaped systemd roles.

        Existing healthy hosts are never restarted here; revision activation belongs to
        ``takyon env deploy`` (and the subuser drain rail). This step exists so a clean
        ``takyon env create dev`` does not stop at empty Ubuntu droplets.
        """
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False) or not cfg.get("bootstrap_hosts", False):
            return StepReceipt(
                "host_bootstrap", STATUS_DISABLED, "create",
                "automatic host bootstrap disabled in manifest",
            )
        token, blocked = self._do_token_or_blocked("droplets")
        if blocked is not None:
            return StepReceipt(
                "host_bootstrap", STATUS_BLOCKED, "create", blocked.detail,
                deposit=blocked.deposit,
            )
        key_path = self._split_key_path()
        store_path = self.home / ".env"
        bootstrap = _REPO_ROOT / "deploy" / "takyon-dev-split" / "bootstrap-dev-droplet.sh"
        if not key_path.is_file():
            return StepReceipt(
                "host_bootstrap", STATUS_BLOCKED, "create",
                f"dev split private key not found at {key_path}",
            )
        if not store_path.is_file():
            return StepReceipt(
                "host_bootstrap", STATUS_BLOCKED, "create",
                f"dev authority store not found at {store_path}",
            )
        if not bootstrap.is_file():
            return StepReceipt(
                "host_bootstrap", STATUS_ERROR, "create",
                f"tracked host bootstrap not found at {bootstrap}",
            )
        _git("fetch", "origin", "main")
        head = _git("rev-parse", "HEAD")
        published = _git("rev-parse", "origin/main")
        dirty = _git(
            "status", "--porcelain", "--untracked-files=all", "--",
            "hermes-agent-main", "deploy", "scripts",
        )
        if not head or head != published or dirty:
            return StepReceipt(
                "host_bootstrap", STATUS_BLOCKED, "create",
                "host bootstrap requires a clean checkout at the published origin/main revision; "
                "commit/push first so fresh hosts cannot receive an untracked source tree",
            )

        headers = self._do_headers(token)
        expected_count = max(1, int(cfg.get("count") or 1))
        safebox_host = operator_host = None
        replicas: list[dict[str, Any]] = []
        why = "droplet addresses unavailable"
        for attempt in range(37):
            safebox_host, why = self._resolve_safebox_host(headers, cfg)
            operator_host, operator_why = self._resolve_singleton_host(headers, cfg, "operator")
            replicas, err = self._resolve_replicas(headers, cfg)
            if (safebox_host is not None and operator_host is not None and err is None
                    and len(replicas) == expected_count):
                break
            why = operator_why if operator_host is None else (err.detail if err is not None else why)
            if attempt < 36:
                self.sleep(5)
        if safebox_host is None or operator_host is None or len(replicas) != expected_count:
            return StepReceipt(
                "host_bootstrap", STATUS_ERROR, "create",
                f"droplet IP assignment timed out: {why}; resolved "
                f"{len(replicas)}/{expected_count} subuser replicas",
            )

        subuser_hosts = ",".join(str(rep["public_ip"]) for rep in replicas)
        common_env = {
            **os.environ,
            "TAKYON_DEV_STORE": str(store_path),
            "TAKYON_DEV_KEY": str(key_path),
            "TAKYON_DEV_SUBUSER_HOSTS": subuser_hosts,
            "TAKYON_DEV_OPERATOR_NODE": str(operator_host["name"]),
            "TAKYON_DEV_OPERATOR_VPC_IP": str(operator_host["private_ip"]),
        }
        specs = [
            (safebox_host, "safebox", str(safebox_host["private_ip"])),
            *((rep, "subuser", str(safebox_host["private_ip"])) for rep in replicas),
            (operator_host, "operator", str(safebox_host["private_ip"])),
        ]
        bootstrapped: list[str] = []
        healthy: list[str] = []
        for host, role, vpc_ip in specs:
            public_ip = str(host["public_ip"])
            role_services = {
                "safebox": "takyon-safebox.service",
                "subuser": "takyon-subuser.service caddy.service",
                "operator": (
                    "takyon-dashboard.service takyon-worker.service "
                    "takyon-docker-broker.service"
                ),
            }[role]
            probe = (
                f"systemctl is-active --quiet {role_services}; "
                + (
                    f"curl -fsS http://{vpc_ip}:8000/healthz >/dev/null"
                    if role == "safebox"
                    else "curl -fsS http://127.0.0.1:9119/healthz >/dev/null"
                )
            )
            try:
                rc, _out = self.remote.run(
                    public_ip, probe, key_path=str(key_path), timeout=20.0,
                )
            except Exception:
                rc = 1
            if rc == 0:
                healthy.append(str(host["name"]))
                continue
            result = subprocess.run(
                [str(bootstrap), public_ip, role, str(host["name"]), vpc_ip],
                capture_output=True,
                text=True,
                timeout=1800,
                env=common_env,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "").strip()[-400:]
                return StepReceipt(
                    "host_bootstrap", STATUS_ERROR, "create",
                    f"{host['name']} bootstrap failed: {detail}",
                    data={"healthy": healthy, "bootstrapped": bootstrapped},
                )
            bootstrapped.append(str(host["name"]))

        return StepReceipt(
            "host_bootstrap",
            STATUS_CREATED if bootstrapped else STATUS_EXISTS,
            "create",
            f"all {len(specs)} dev hosts are systemd-managed and healthy"
            + (f"; bootstrapped {', '.join(bootstrapped)}" if bootstrapped else ""),
            data={"healthy": healthy, "bootstrapped": bootstrapped},
        )

    # (f4) load balancer — fronts the replicas by ROLE TAG (a future replica joins by tag, no LB
    # edit), health-checked on the app plane's /healthz.
    def _create_load_balancer(self) -> StepReceipt:
        cfg = self.manifest.get("load_balancer") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("load_balancer", STATUS_DISABLED, "create", "load_balancer twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("load_balancer")
        if blocked is not None:
            return blocked
        self._guard_do_block(cfg)

        droplets_cfg = self.manifest.get("droplets") or {}
        role = str(droplets_cfg.get("role") or "subuser").strip().lower()
        name = str(cfg.get("name") or f"takyon-{self.name}-{role}-lb").strip()
        region = str(cfg.get("region") or droplets_cfg.get("region") or "nyc3").strip()
        headers = self._do_headers(token)

        # Backend membership: tag-membership when the replicas actually carry the env tags (a
        # future replica then joins by tag, no LB edit); explicit droplet_ids when the token
        # lacks tag scope and the replicas are untagged (the recorded scope fallback above).
        replicas = [
            d for d in (self._do_state.get("droplets") or [])
            if d.get("role") == role and d.get("droplet_id")
        ]
        replica_ids = [d["droplet_id"] for d in replicas]
        tagged_mode = bool(replicas) and all(d.get("tagged") for d in replicas)

        try:
            listed = self.http.request(
                "GET", f"{self._DO_BASE}/load_balancers?per_page=200", headers=headers
            )
        except Exception as exc:
            return StepReceipt("load_balancer", STATUS_ERROR, "create", f"load balancer list failed: {exc}")

        rules = list(cfg.get("forwarding_rules") or []) or [
            {"entry_port": 80, "entry_protocol": "http", "target_port": 9119, "target_protocol": "http"},
        ]
        forwarding = [
            {
                "entry_protocol": str(r.get("entry_protocol") or "http"),
                "entry_port": int(r.get("entry_port") or 80),
                "target_protocol": str(r.get("target_protocol") or "http"),
                "target_port": int(r.get("target_port") or 9119),
            }
            for r in rules if isinstance(r, dict)
        ]
        hc = cfg.get("health_check") or {}
        body: dict[str, Any] = {
            "name": name,
            "region": region,
            "forwarding_rules": forwarding,
            "health_check": {
                "protocol": str(hc.get("protocol") or "http"),
                "port": int(hc.get("port") or 9119),
                "path": str(hc.get("path") or "/healthz"),
                "check_interval_seconds": int(hc.get("check_interval_seconds") or 10),
                "response_timeout_seconds": int(hc.get("response_timeout_seconds") or 5),
                "healthy_threshold": int(hc.get("healthy_threshold") or 3),
                "unhealthy_threshold": int(hc.get("unhealthy_threshold") or 3),
            },
        }
        if tagged_mode or not replica_ids:
            body["tag"] = self.role_tag(role)
        else:
            body["droplet_ids"] = replica_ids
        if self._do_state.get("vpc_id"):
            body["vpc_uuid"] = self._do_state["vpc_id"]

        for lb in (listed.get("load_balancers") or []) if isinstance(listed, dict) else []:
            if not (isinstance(lb, dict) and str(lb.get("name") or "") == name):
                continue
            self._do_state["lb_id"] = lb.get("id")
            # Idempotent reuse — but converge (full PUT of the same desired spec, which keeps
            # the LB id + IP) when the live LB drifted from the manifest: missing replica ids
            # (e.g. first created against a tag a scope-limited token cannot mint), or a
            # health-check/forwarding change (e.g. tightened eviction thresholds).
            have = set(lb.get("droplet_ids") or [])
            members_stale = bool(replica_ids) and not tagged_mode and not set(replica_ids) <= have
            live_hc = lb.get("health_check") or {}
            hc_stale = any(live_hc.get(k) != v for k, v in body["health_check"].items())
            live_rules = [
                {k: r.get(k) for k in ("entry_protocol", "entry_port", "target_protocol", "target_port")}
                for r in (lb.get("forwarding_rules") or []) if isinstance(r, dict)
            ]
            rules_stale = live_rules != forwarding
            if members_stale or hc_stale or rules_stale:
                try:
                    self.http.request(
                        "PUT", f"{self._DO_BASE}/load_balancers/{urllib.parse.quote(str(lb.get('id')))}",
                        headers=headers, body=body,
                    )
                except Exception as exc:
                    return StepReceipt("load_balancer", STATUS_ERROR, "create",
                                       f"load balancer converge failed: {exc}")
                drift = ", ".join(
                    part for part, stale in (
                        ("membership", members_stale), ("health check", hc_stale),
                        ("forwarding rules", rules_stale),
                    ) if stale
                )
                return StepReceipt(
                    "load_balancer", STATUS_EXISTS, "create",
                    f"load balancer {name!r} reused; converged {drift}",
                    data={"lb_id": lb.get("id"), "ip": lb.get("ip"), "droplet_ids": replica_ids},
                )
            return StepReceipt(
                "load_balancer", STATUS_EXISTS, "create", f"load balancer {name!r} already exists",
                data={"lb_id": lb.get("id"), "ip": lb.get("ip"), "tag": lb.get("tag"),
                      "droplet_ids": sorted(have)},
            )

        try:
            created = self.http.request("POST", f"{self._DO_BASE}/load_balancers", headers=headers, body=body)
        except Exception as exc:
            return StepReceipt("load_balancer", STATUS_ERROR, "create", f"load balancer create failed: {exc}")
        lb = (created or {}).get("load_balancer") if isinstance(created, dict) else {}
        self._do_state["lb_id"] = (lb or {}).get("id")
        backend = f"tag {self.role_tag(role)!r}" if "tag" in body else f"{len(replica_ids)} replica id(s)"
        return StepReceipt(
            "load_balancer", STATUS_CREATED, "create",
            f"created load balancer {name!r} fronting {backend}",
            data={"lb_id": (lb or {}).get("id"), "forwarding_rules": forwarding},
        )

    # (f5) firewall — applied to the env's droplets by tag: :22 from the operator's deposited
    # CIDR only, :80/:9119 from the LB (+ the env's own droplets), :8000 (dev safebox) from the
    # env's own droplets only. Everything else inbound is denied by DO's default-deny.
    def _create_firewall(self) -> StepReceipt:
        cfg = self.manifest.get("firewall") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("firewall", STATUS_DISABLED, "create", "firewall twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("firewall")
        if blocked is not None:
            return blocked
        self._guard_do_block(cfg)

        name = str(cfg.get("name") or f"takyon-{self.name}-fw").strip()
        headers = self._do_headers(token)

        # Membership: env tag when the droplets carry it; explicit droplet ids in the recorded
        # scope fallback (token without tag:create → untagged droplets).
        env_droplets = [d for d in (self._do_state.get("droplets") or []) if d.get("droplet_id")]
        env_ids = [d["droplet_id"] for d in env_droplets]
        tagged_mode = bool(env_droplets) and all(d.get("tagged") for d in env_droplets)

        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/firewalls?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("firewall", STATUS_ERROR, "create", f"firewall list failed: {exc}")
        lb_uid = str(self._do_state.get("lb_id") or "")
        for fw in (listed.get("firewalls") or []) if isinstance(listed, dict) else []:
            if isinstance(fw, dict) and str(fw.get("name") or "") == name:
                self._do_state["firewall_id"] = fw.get("id")
                have = set(fw.get("droplet_ids") or [])
                members_stale = bool(env_ids) and not tagged_mode and not set(env_ids) <= have
                # A recreated LB gets a NEW uid; rules referencing the old one silently block
                # its health checks — converge whenever the current LB uid is absent.
                referenced_uids = {
                    uid
                    for rule in (fw.get("inbound_rules") or []) if isinstance(rule, dict)
                    for uid in ((rule.get("sources") or {}).get("load_balancer_uids") or [])
                }
                rules_stale = bool(lb_uid) and lb_uid not in referenced_uids
                if not members_stale and not rules_stale:
                    return StepReceipt(
                        "firewall", STATUS_EXISTS, "create", f"firewall {name!r} already exists",
                        data={"firewall_id": fw.get("id")},
                    )
                converge = self._firewall_body(cfg, name, env_ids, tagged_mode)
                if isinstance(converge, StepReceipt):
                    return converge
                try:
                    self.http.request(
                        "PUT", f"{self._DO_BASE}/firewalls/{urllib.parse.quote(str(fw.get('id')))}",
                        headers=headers, body=converge,
                    )
                except Exception as exc:
                    return StepReceipt("firewall", STATUS_ERROR, "create",
                                       f"firewall converge failed: {exc}")
                return StepReceipt(
                    "firewall", STATUS_EXISTS, "create",
                    f"firewall {name!r} reused; converged "
                    + ("membership" if members_stale else "")
                    + (" and " if members_stale and rules_stale else "")
                    + ("LB rule sources" if rules_stale else ""),
                    data={"firewall_id": fw.get("id"), "droplet_ids": env_ids},
                )

        body = self._firewall_body(cfg, name, env_ids, tagged_mode)
        if isinstance(body, StepReceipt):
            return body
        ssh_cidrs = body["inbound_rules"][0]["sources"]["addresses"]
        lb_uids = [lb_uid] if lb_uid else []
        try:
            created = self.http.request("POST", f"{self._DO_BASE}/firewalls", headers=headers, body=body)
        except Exception as exc:
            return StepReceipt("firewall", STATUS_ERROR, "create", f"firewall create failed: {exc}")
        fw = (created or {}).get("firewall") if isinstance(created, dict) else {}
        self._do_state["firewall_id"] = (fw or {}).get("id")
        target = f"tag {self.env_tag!r}" if "tags" in body else f"{len(env_ids)} droplet id(s)"
        return StepReceipt(
            "firewall", STATUS_CREATED, "create",
            f"created firewall {name!r} over {target} (ssh from {len(ssh_cidrs)} cidr(s), "
            f"app ports from {'the LB' if lb_uids else 'env droplets only'})",
            data={"firewall_id": (fw or {}).get("id")},
        )

    def _firewall_body(self, cfg: Mapping[str, Any], name: str, env_ids: list,
                       tagged_mode: bool) -> "dict[str, Any] | StepReceipt":
        """The DESIRED firewall spec (shared by create + converge). Returns a blocked receipt
        when the operator SSH CIDR is not deposited — an absent deposit must not widen :22."""
        ssh_alias = str(cfg.get("ssh_allow_alias") or "TAKYON_DEV_SSH_ALLOW_CIDR").strip()
        ssh_raw = self._resolve_alias(ssh_alias)
        if not ssh_raw:
            return StepReceipt(
                "firewall", STATUS_BLOCKED, "create",
                f"operator SSH CIDR not deposited (alias {ssh_alias}); deposit e.g. <your-ip>/32 "
                "so :22 stays closed to everyone else",
                deposit=ssh_alias,
            )
        ssh_cidrs = [c.strip() for c in ssh_raw.split(",") if c.strip()]
        lb_uids = [self._do_state["lb_id"]] if self._do_state.get("lb_id") else []
        own = {"tags": [self.env_tag]} if (tagged_mode or not env_ids) else {"droplet_ids": env_ids}
        inbound: list[dict[str, Any]] = [
            {"protocol": "tcp", "ports": "22", "sources": {"addresses": ssh_cidrs}},
            {"protocol": "tcp", "ports": "9119",
             "sources": {"load_balancer_uids": lb_uids, **own} if lb_uids else dict(own)},
            {"protocol": "tcp", "ports": "80",
             "sources": {"load_balancer_uids": lb_uids, **own} if lb_uids else dict(own)},
            # dev safebox (:8000) is VPC-internal only: reachable from the env's own droplets.
            {"protocol": "tcp", "ports": "8000", "sources": dict(own)},
        ]
        outbound = [
            {"protocol": "tcp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
            {"protocol": "udp", "ports": "all", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
            {"protocol": "icmp", "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}},
        ]
        body: dict[str, Any] = {"name": name, "inbound_rules": inbound, "outbound_rules": outbound}
        if tagged_mode or not env_ids:
            body["tags"] = [self.env_tag]
        else:
            body["droplet_ids"] = env_ids
        return body

    # (f6) node registry — enroll each replica in the environment's worker_pools registry (the
    # Stage-2 pool registry doubling as the Stage-4 replica/node registry, plan §A.5). The
    # subuser role CANNOT write worker_pools (migration 0059 revokes it — deliberately), so
    # enrollment is done here by the provisioning authority over the migration DSN. Per-replica
    # credential enrollment (plan Stage 4b security bullet) is the NEXT create() step, (f7): it
    # stamps each node's credential IDS (never values) onto these registry rows.
    def _register_replica_nodes(self) -> StepReceipt:
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("node_registry", STATUS_SKIPPED, "create", "no droplets twin — nothing to enroll")
        role = str(cfg.get("role") or "subuser").strip().lower()
        replicas = [d for d in (self._do_state.get("droplets") or []) if d.get("role") == role]
        if not replicas:
            return StepReceipt("node_registry", STATUS_SKIPPED, "create", "no replicas provisioned this run")

        db_cfg = self.manifest.get("database") or {}
        dsn_alias = str(db_cfg.get("dsn_alias") or "").strip()
        dsn = self._resolve_alias(dsn_alias) if dsn_alias else ""
        if not dsn:
            return StepReceipt(
                "node_registry", STATUS_BLOCKED, "create",
                f"cannot enroll replicas: dev control-plane DSN not deposited (alias {dsn_alias or 'unset'})",
                deposit=dsn_alias or None,
            )
        self._assert_not_prod(dsn)
        lease = float(cfg.get("node_lease_seconds") or 7 * 86400)
        try:
            import psycopg
            from . import claim_scope
            with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
                for rep in replicas:
                    claim_scope.register_pool(
                        conn,
                        pool_id=str(rep["name"]),
                        hostname=str(rep["name"]),
                        exclusive=False,
                        concurrency=1,
                        capabilities={
                            "env": self.name,
                            "role": str(rep.get("role") or ""),
                            "replica": True,
                            "droplet_id": rep.get("droplet_id"),
                        },
                        lease_seconds=lease,
                    )
        except Exception as exc:
            return StepReceipt("node_registry", STATUS_ERROR, "create", f"replica enrollment failed: {exc}")
        names = [str(r["name"]) for r in replicas]
        return StepReceipt(
            "node_registry", STATUS_CREATED, "create",
            f"enrolled {len(names)} replica node(s) in worker_pools: {', '.join(names)}",
            data={"registered": names, "lease_seconds": lease},
        )

    # ── (f7) per-replica credentials — the Stage 4b hardening bullet ───────────────────────────
    #
    # Today's split shipped with every replica sharing (a) one app-plane DB login and (b) one
    # safebox transport token, so compromising one replica yielded credentials that outlive it.
    # This step makes both PER-REPLICA, SCOPED and REVOCABLE:
    #
    #   * DB: each replica gets its own login role `takyon_app_runtime__<node>` — a plain INHERIT
    #     member of the ONE canonical takyon_app_runtime role (grants/RLS stay on the canonical
    #     role; runtime_app.assert_takyon_pg_role + takyon_rls_bound_app_user_id accept scoped
    #     members via live pg_has_role membership, never by name alone). Revocation = DROP ROLE.
    #   * Safebox transport: each replica gets its own token; the safebox host stores only the
    #     token's sha256 in $TAKYON_HOME/safebox/node_tokens.json (safebox_app re-reads it on
    #     mtime change — revocation needs no restart). The shared token stays valid so non-split
    #     hosts keep working unchanged.
    #
    # Fail-closed and idempotent: a fully-enrolled replica is a no-op; missing deposits are named;
    # an unreachable/un-bootstrapped replica blocks rather than half-enrolling. Credential VALUES
    # ride only over SSH stdin to exactly the box that owns them — receipts, logs and the node
    # registry carry credential IDs (role name, sha256 prefix), never values. Activation is the
    # drain rail (`takyon env restart <env>`), so enrolling loses zero requests.

    _APP_PLANE_BASE_ROLE = "takyon_app_runtime"

    def _cred_aliases(self) -> tuple[str, str]:
        """(shared runtime-DSN alias, shared safebox-token alias) the scoped per-replica
        credentials derive from / replace on each replica."""
        cfg = self.manifest.get("droplets") or {}
        upper = re.sub(r"[^A-Z0-9_]+", "_", self.name.upper())
        dsn_alias = str(cfg.get("runtime_dsn_alias") or f"TAKYON_{upper}_RUNTIME_DATABASE_URL").strip()
        token_alias = str(cfg.get("safebox_token_alias") or f"TAKYON_{upper}_SAFEBOX_TOKEN").strip()
        return dsn_alias, token_alias

    def _replica_env_file(self) -> str:
        return str((self.manifest.get("droplets") or {}).get("env_file") or "/opt/takyon/.takyon/.env").strip()

    def _node_tokens_file(self) -> str:
        return str(
            (self.manifest.get("droplets") or {}).get("node_tokens_file")
            or "/opt/takyon/.takyon/safebox/node_tokens.json"
        ).strip()

    def _split_key_path(self) -> Path:
        """The split's own deploy key (manifest ssh_key.public_key_path minus .pub, overridable
        via rolling_restart.private_key_path) — the same resolution the drain rail uses."""
        cfg = self.manifest.get("rolling_restart") or {}
        return Path(str(
            cfg.get("private_key_path")
            or str((self.manifest.get("ssh_key") or {}).get("public_key_path") or "").removesuffix(".pub")
            or ""
        )).expanduser()

    def _scoped_role_name(self, node_name: str) -> str:
        try:
            from .runtime_app import scoped_plane_role_name
        except ImportError:  # pragma: no cover - alternate load path
            from plugins.takyon.runtime_app import scoped_plane_role_name
        return scoped_plane_role_name(self._APP_PLANE_BASE_ROLE, node_name)

    @staticmethod
    def _scoped_login_dsn(shared_dsn: str, role: str, password: str) -> str:
        """Swap the login on the SHARED app-plane DSN URL for the scoped role, preserving a
        Supabase-pooler tenant suffix (user ``takyon_app_runtime.<ref>`` keeps ``.<ref>``)."""
        m = re.match(
            r"^(?P<scheme>postgres(?:ql)?://)(?:(?P<user>[^:@/]*)(?::(?P<pw>[^@/]*))?@)?(?P<rest>.+)$",
            str(shared_dsn or "").strip(),
        )
        if not m:
            raise EnvironmentProvisionError("shared runtime DSN is not a postgres:// URL")
        base_user = urllib.parse.unquote(m.group("user") or "")
        suffix = "." + base_user.split(".", 1)[1] if "." in base_user else ""
        user = urllib.parse.quote(role, safe="") + suffix
        return f"{m.group('scheme')}{user}:{urllib.parse.quote(password, safe='')}@{m.group('rest')}"

    @staticmethod
    def _assert_sql_safe_credential(role: str, password: str) -> None:
        """Role DDL cannot be parameterized; keep both sides in provably-quotable charsets.
        Roles are provisioner-minted ([a-z0-9_]); passwords are token_urlsafe ([A-Za-z0-9_-])."""
        if not re.fullmatch(r"[a-z0-9_]{1,63}", role):
            raise EnvironmentProvisionError(f"scoped role name {role!r} is not sql-safe")
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", password):
            raise EnvironmentProvisionError("minted password contains unexpected characters")

    def _resolve_singleton_host(
        self,
        headers: Mapping[str, str],
        droplets_cfg: Mapping[str, Any],
        role: str,
    ) -> "tuple[dict[str, Any] | None, str]":
        """Resolve one manifest singleton to its public/private VPC addresses."""
        role = str(role or "").strip().lower()
        block = droplets_cfg.get(f"{role}_host") or {}
        if not block.get("enabled", False):
            return None, f"no {role} host declared in the manifest"
        name = str(block.get("name") or f"takyon-{self.name}-{role}").strip()
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/droplets?per_page=200", headers=dict(headers))
        except Exception as exc:
            return None, f"droplet list failed: {exc}"
        for d in (listed.get("droplets") or []) if isinstance(listed, dict) else []:
            if not isinstance(d, dict) or str(d.get("name") or "") != name:
                continue
            public_ip = next(
                (str(n.get("ip_address") or "") for n in ((d.get("networks") or {}).get("v4") or [])
                 if isinstance(n, dict) and n.get("type") == "public"),
                "",
            )
            if not public_ip:
                return None, f"{role} host {name!r} has no public IPv4"
            private_ip = next(
                (str(n.get("ip_address") or "") for n in ((d.get("networks") or {}).get("v4") or [])
                 if isinstance(n, dict) and n.get("type") == "private"),
                "",
            )
            if not private_ip:
                return None, f"{role} host {name!r} has no private IPv4"
            return {
                "name": name,
                "droplet_id": d.get("id"),
                "public_ip": public_ip,
                "private_ip": private_ip,
            }, ""
        return None, f"{role} host {name!r} not found"

    def _resolve_safebox_host(
        self, headers: Mapping[str, str], droplets_cfg: Mapping[str, Any]
    ) -> "tuple[dict[str, Any] | None, str]":
        return self._resolve_singleton_host(headers, droplets_cfg, "safebox")

    def _read_replica_cred_state(
        self, rep: Mapping[str, Any], key_path: Path, env_file: str, dsn_alias: str, role: str
    ) -> "dict[str, Any] | None":
        """Value-free remote read of one replica's enrollment state: whether its runtime-DSN line
        already names the scoped role, and the sha256 of its current transport token. Only a
        boolean and a hash ever cross the wire back."""
        script = (
            "set -u\n"
            f"f={env_file!r}\n"
            f"dsn_line=$(grep -m1 '^{dsn_alias}=' \"$f\" 2>/dev/null || true)\n"
            f"case \"$dsn_line\" in *{role}*) echo DSN_SCOPED=yes;; *) echo DSN_SCOPED=no;; esac\n"
            "tok=$(grep -m1 '^TAKYON_SAFEBOX_TOKEN=' \"$f\" 2>/dev/null | cut -d= -f2- | tr -d '\\n')\n"
            "if [ -n \"$tok\" ]; then printf 'TOKEN_SHA=%s\\n' \"$(printf '%s' \"$tok\" | sha256sum | cut -d' ' -f1)\";"
            " else echo TOKEN_SHA=; fi\n"
        )
        try:
            rc, out = self.remote.run(str(rep["public_ip"]), script, key_path=str(key_path), timeout=30.0)
        except Exception:
            return None
        if rc != 0 or "DSN_SCOPED=" not in out:
            return None
        state: dict[str, Any] = {"dsn_scoped": "DSN_SCOPED=yes" in out, "token_sha": ""}
        for line in out.splitlines():
            if line.startswith("TOKEN_SHA="):
                state["token_sha"] = line[len("TOKEN_SHA="):].strip().lower()
        return state

    def _read_node_tokens(self, sb_host: Mapping[str, Any], key_path: Path) -> "dict[str, str] | None":
        """Enrolled node -> token_sha256 map from the safebox host (hashes only; never values)."""
        tokens_file = self._node_tokens_file()
        script = f"cat {tokens_file!r} 2>/dev/null || echo '{{}}'"
        try:
            rc, out = self.remote.run(str(sb_host["public_ip"]), script, key_path=str(key_path), timeout=30.0)
        except Exception:
            return None
        if rc != 0:
            return None
        try:
            data = json.loads(out.strip() or "{}")
        except Exception:
            return {}
        nodes = data.get("nodes") if isinstance(data, dict) else {}
        return {
            str(name): str((entry or {}).get("token_sha256") or "").strip().lower()
            for name, entry in (nodes or {}).items()
            if isinstance(entry, dict)
        }

    def _update_node_tokens(
        self,
        sb_host: Mapping[str, Any],
        key_path: Path,
        *,
        enroll: Mapping[str, Mapping[str, Any]] | None = None,
        revoke: tuple[str, ...] = (),
    ) -> str:
        """Merge/prune the safebox host's node-token digest file (atomic replace; 0600; owned by
        the service user). The payload carries HASHES only, so it may ride the script itself.
        Returns '' on success or a failure detail."""
        tokens_file = self._node_tokens_file()
        tokens_dir = os.path.dirname(tokens_file)
        payload = json.dumps({"nodes": dict(enroll or {}), "revoke": list(revoke)}, sort_keys=True)
        if "TAKYON_NODE_TOKENS_EOF" in payload:
            return "refusing: payload contains the heredoc sentinel"
        script = (
            "set -euo pipefail\n"
            "umask 077\n"
            "python3 - <<'TAKYON_NODE_TOKENS_EOF'\n"
            "import json, os\n"
            f"path = {tokens_file!r}\n"
            f"incoming = json.loads({payload!r})\n"
            'data = {"version": 1, "nodes": {}}\n'
            "try:\n"
            "    with open(path) as fh:\n"
            "        loaded = json.load(fh)\n"
            '    if isinstance(loaded, dict) and isinstance(loaded.get("nodes"), dict):\n'
            "        data = loaded\n"
            "except Exception:\n"
            "    pass\n"
            'nodes = data.setdefault("nodes", {})\n'
            'nodes.update(incoming.get("nodes") or {})\n'
            'for name in incoming.get("revoke") or []:\n'
            "    nodes.pop(name, None)\n"
            "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
            'tmp = path + ".tmp"\n'
            'with open(tmp, "w") as fh:\n'
            "    json.dump(data, fh, indent=1, sort_keys=True)\n"
            "os.chmod(tmp, 0o600)\n"
            "os.replace(tmp, path)\n"
            'print("NODE_TOKENS_UPDATED", ",".join(sorted(nodes)) or "<empty>")\n'
            "TAKYON_NODE_TOKENS_EOF\n"
            f"chown takyon:takyon {tokens_dir!r} {tokens_file!r} 2>/dev/null || true\n"
        )
        try:
            rc, out = self.remote.run(str(sb_host["public_ip"]), script, key_path=str(key_path), timeout=60.0)
        except Exception as exc:
            return f"node-token update on {sb_host.get('name')!r} failed: {exc}"
        if rc != 0 or "NODE_TOKENS_UPDATED" not in out:
            return f"node-token update on {sb_host.get('name')!r} failed (rc={rc}): {out[-300:]}"
        return ""

    def _write_replica_env(
        self, rep: Mapping[str, Any], key_path: Path, env_file: str, dsn_alias: str, env_lines: str,
        *, replace_token: bool = True,
    ) -> str:
        """Replace the replica's shared-credential lines with its scoped ones. The secret VALUES
        ride stdin (never a remote command line); the file stays 0600 and service-user owned.
        Returns '' on success or a failure detail."""
        strip = f"{dsn_alias}|TAKYON_SAFEBOX_TOKEN" if replace_token else dsn_alias
        script = (
            "set -euo pipefail\n"
            "umask 077\n"
            f"f={env_file!r}\n"
            'tmp="$f.repcreds.$$"\n'
            f"grep -vE '^({strip})=' \"$f\" > \"$tmp\" 2>/dev/null || true\n"
            'cat >> "$tmp"\n'
            'chown takyon:takyon "$tmp" 2>/dev/null || true\n'
            'chmod 600 "$tmp"\n'
            'mv "$tmp" "$f"\n'
            "echo ENV_WRITTEN\n"
        )
        try:
            rc, out = self.remote.run(
                str(rep["public_ip"]), script, key_path=str(key_path), timeout=60.0, stdin=env_lines
            )
        except Exception as exc:
            return f"env write on {rep['name']} failed: {exc}"
        if rc != 0 or "ENV_WRITTEN" not in out:
            return f"env write on {rep['name']} failed (rc={rc}): {out[-300:]}"
        return ""

    def _admin_dsn_or_none(self) -> "tuple[str, str]":
        """(admin DSN, alias name). Role DDL (CREATE/DROP ROLE) is privileged work the migration
        role deliberately cannot do — same rule as topology.sql."""
        db_cfg = self.manifest.get("database") or {}
        alias = str(db_cfg.get("admin_dsn_alias") or "").strip()
        dsn = self._resolve_alias(alias) if alias else ""
        if dsn:
            self._assert_not_prod(dsn)
        return dsn, alias

    def _mint_scoped_db_role(self, admin_conn, role: str, password: str) -> str:
        """CREATE (or rotate) one scoped login role and grant it INHERIT membership of the ONE
        canonical app-plane role. SET FALSE: a replica never role-switches. Returns created|rotated."""
        self._assert_sql_safe_credential(role, password)
        exists = admin_conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
        attrs = "login inherit nosuperuser nobypassrls nocreatedb nocreaterole"
        if exists:
            admin_conn.execute(f"alter role {role} with {attrs} password '{password}'")
        else:
            admin_conn.execute(f"create role {role} with {attrs} password '{password}'")
        admin_conn.execute(
            f"grant {self._APP_PLANE_BASE_ROLE} to {role} with inherit true, set false"
        )
        return "rotated" if exists else "created"

    def _drop_scoped_db_role(self, admin_conn, role: str) -> bool:
        """Revoke the role's membership, terminate its live backends, and DROP it — DB access is
        refused instantly. No ``DROP OWNED``: the scoped role owns nothing by construction (it
        never DDLs), and Supabase's admin login holds ADMIN on the role WITHOUT its privileges,
        so DROP OWNED refuses there (verified live 2026-07-03). If a stray owned object ever
        exists, DROP ROLE fails LOUDLY naming it — fail closed, never a silent shrug. Returns
        True when the role existed."""
        self._assert_sql_safe_credential(role, "x")
        exists = admin_conn.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
        if not exists:
            return False
        admin_conn.execute(f"revoke {self._APP_PLANE_BASE_ROLE} from {role}")
        admin_conn.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity where usename = %s", (role,)
        )
        admin_conn.execute(f"drop role if exists {role}")
        return True

    def _stamp_node_credentials(self, mig_conn, node_name: str, credentials: Mapping[str, Any]) -> None:
        """Record WHICH credential ids (never values) belong to a node on its registry row."""
        mig_conn.execute(
            "update worker_pools set capabilities = capabilities || %s::jsonb, updated_at = now()"
            " where pool_id = %s",
            (json.dumps({"credentials": dict(credentials)}), node_name),
        )

    def _enroll_replica_credentials(self) -> StepReceipt:
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("replica_credentials", STATUS_SKIPPED, "create",
                               "no droplets twin — nothing to enroll")
        if not (self.manifest.get("database") or {}).get("enabled", False):
            return StepReceipt("replica_credentials", STATUS_SKIPPED, "create",
                               "no database twin — scoped per-replica logins need the env's own "
                               "control plane")
        role = str(cfg.get("role") or "subuser").strip().lower()
        registered = [d for d in (self._do_state.get("droplets") or []) if d.get("role") == role]
        if not registered:
            return StepReceipt("replica_credentials", STATUS_SKIPPED, "create",
                               "no replicas provisioned this run")
        token, blocked = self._do_token_or_blocked("droplets")
        if blocked is not None:
            return StepReceipt("replica_credentials", STATUS_BLOCKED, "create",
                               blocked.detail, deposit=blocked.deposit)
        headers = self._do_headers(token)
        replicas, err = self._resolve_replicas(headers, cfg)
        if err is not None:
            return StepReceipt("replica_credentials", STATUS_ERROR, "create", err.detail)
        sb_host, sb_why = self._resolve_safebox_host(headers, cfg)
        key_path = self._split_key_path()
        if not str(key_path) or str(key_path) == "." or not key_path.exists():
            return StepReceipt(
                "replica_credentials", STATUS_ERROR, "create",
                f"replica ssh key not found at {key_path} — enrollment writes each replica's env "
                "over SSH with the split's deploy key",
            )
        dsn_alias, _token_alias = self._cred_aliases()
        shared_dsn = self._resolve_alias(dsn_alias)
        if not shared_dsn:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "create",
                f"shared app-plane runtime DSN not deposited (alias {dsn_alias}) — the scoped "
                "per-replica DSNs are derived from it",
                deposit=dsn_alias,
            )
        self._assert_not_prod(shared_dsn)
        db_cfg = self.manifest.get("database") or {}
        mig_alias = str(db_cfg.get("dsn_alias") or "").strip()
        mig_dsn = self._resolve_alias(mig_alias) if mig_alias else ""
        if not mig_dsn:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "create",
                f"control-plane DSN not deposited (alias {mig_alias or 'unset'})",
                deposit=mig_alias or None,
            )
        self._assert_not_prod(mig_dsn)
        env_file = self._replica_env_file()
        node_tokens = self._read_node_tokens(sb_host, key_path) if sb_host else None

        results: list[dict[str, Any]] = []
        pending: list[str] = []
        minted = 0
        admin_conn_holder: list[Any] = []

        try:
            import psycopg

            def _admin_conn():
                if admin_conn_holder:
                    return admin_conn_holder[0]
                admin_dsn, admin_alias = self._admin_dsn_or_none()
                if not admin_dsn:
                    raise _AdminDepositMissing(admin_alias or "TAKYON_DEV_ADMIN_DATABASE_URL")
                conn = psycopg.connect(admin_dsn, autocommit=True, prepare_threshold=None)
                admin_conn_holder.append(conn)
                return conn

            with psycopg.connect(mig_dsn, autocommit=True, prepare_threshold=None) as mig:
                for rep in replicas:
                    node = str(rep["name"])
                    role = self._scoped_role_name(node)
                    role_exists = bool(
                        mig.execute("select 1 from pg_roles where rolname = %s", (role,)).fetchone()
                    )
                    state = self._read_replica_cred_state(rep, key_path, env_file, dsn_alias, role)
                    if state is None:
                        pending.append(node)
                        results.append({"node": node, "db_role": role, "status": "unreachable"})
                        continue
                    token_enrolled = bool(
                        sb_host
                        and node_tokens is not None
                        and state["token_sha"]
                        and node_tokens.get(node) == state["token_sha"]
                    )
                    if role_exists and state["dsn_scoped"] and token_enrolled:
                        # Re-stamp even on the no-op path: the (f6) node_registry upsert replaces
                        # capabilities wholesale on every create() re-run, so the credential ids
                        # must be re-asserted or the registry forgets which creds a node holds.
                        self._stamp_node_credentials(mig, node, {
                            "db_role": role,
                            "safebox_token_id": state["token_sha"][:12],
                            "stamped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        })
                        results.append({
                            "node": node, "db_role": role, "status": "exists",
                            "safebox_token_id": state["token_sha"][:12],
                        })
                        continue

                    # Mint path: fresh secrets for BOTH credentials (a partial enrollment is
                    # converged by rotating, never by trusting half-written state).
                    password = _secrets.token_urlsafe(24)
                    node_token = _secrets.token_urlsafe(32)
                    digest = hashlib.sha256(node_token.encode()).hexdigest()
                    mint_kind = self._mint_scoped_db_role(_admin_conn(), role, password)
                    token_id = ""
                    if sb_host:
                        detail = self._update_node_tokens(
                            sb_host, key_path,
                            enroll={node: {
                                "token_sha256": digest,
                                "env": self.name,
                                "db_role": role,
                                "enrolled_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            }},
                        )
                        if detail:
                            return self._enroll_error(results, node, detail)
                        token_id = digest[:12]
                    scoped_dsn = self._scoped_login_dsn(shared_dsn, role, password)
                    env_lines = f"{dsn_alias}={scoped_dsn}\n"
                    if sb_host:
                        env_lines += f"TAKYON_SAFEBOX_TOKEN={node_token}\n"
                    detail = self._write_replica_env(
                        rep, key_path, env_file, dsn_alias, env_lines,
                        replace_token=bool(sb_host),
                    )
                    if detail:
                        return self._enroll_error(results, node, detail)
                    credentials: dict[str, Any] = {"db_role": role}
                    if token_id:
                        credentials["safebox_token_id"] = token_id
                    credentials["enrolled_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._stamp_node_credentials(mig, node, credentials)
                    minted += 1
                    results.append({
                        "node": node, "db_role": role, "status": mint_kind,
                        **({"safebox_token_id": token_id} if token_id else {}),
                    })
        except _AdminDepositMissing as exc:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "create",
                f"scoped role mint needs the admin DSN (alias {exc.alias}) — CREATE ROLE is "
                "privileged work the migration role deliberately cannot do",
                deposit=exc.alias, data={"nodes": results},
            )
        except Exception as exc:
            return StepReceipt("replica_credentials", STATUS_ERROR, "create",
                               f"replica credential enrollment failed: {exc}", data={"nodes": results})
        finally:
            for conn in admin_conn_holder:
                try:
                    conn.close()
                except Exception:
                    pass

        note = "" if sb_host else f" (safebox transport tokens skipped: {sb_why})"
        if pending:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "create",
                f"replica(s) not reachable for enrollment yet: {', '.join(pending)} — bootstrap "
                "them (deploy/takyon-dev-split/bootstrap-dev-droplet.sh), then re-run "
                f"`takyon env create {self.name}`" + note,
                data={"nodes": results},
            )
        if minted:
            return StepReceipt(
                "replica_credentials", STATUS_CREATED, "create",
                f"enrolled per-replica scoped credentials on {minted} replica(s); activate with "
                f"`takyon env restart {self.name}` (drain rail — zero requests lost)" + note,
                data={"nodes": results},
            )
        return StepReceipt(
            "replica_credentials", STATUS_EXISTS, "create",
            f"all {len(results)} replica(s) already hold their scoped credentials" + note,
            data={"nodes": results},
        )

    def _enroll_error(self, results: list, node: str, detail: str) -> StepReceipt:
        results.append({"node": node, "status": "error"})
        return StepReceipt(
            "replica_credentials", STATUS_ERROR, "create",
            f"{detail} — re-run `takyon env create {self.name}` to converge (partial enrollments "
            "are rotated, never trusted)",
            data={"nodes": results},
        )

    def revoke_node_credentials(self, node_name: str) -> ProvisionResult:
        """Targeted revocation of ONE replica's scoped credentials (`takyon env revoke-node`):
        DROP its DB role (terminating live backends) and prune its transport-token digest from the
        safebox host — the old DSN and token are refused everywhere within one request. The node's
        registry row keeps its history with credentials marked revoked. Fail-closed: an
        unprovable revocation is an error, never a shrug."""
        receipts = [self._append_receipt(self._revoke_one_node(str(node_name or "").strip()))]
        return ProvisionResult(name=self.name, action="revoke-node", receipts=tuple(receipts))

    def _revoke_one_node(self, node: str) -> StepReceipt:
        if not node:
            return StepReceipt("replica_credentials", STATUS_ERROR, "revoke-node", "node name required")
        try:
            role = self._scoped_role_name(node)
        except Exception as exc:
            return StepReceipt("replica_credentials", STATUS_ERROR, "revoke-node", str(exc))
        admin_dsn, admin_alias = self._admin_dsn_or_none()
        if not admin_dsn:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "revoke-node",
                f"DROP ROLE needs the admin DSN (alias {admin_alias or 'unset'})",
                deposit=admin_alias or None,
            )
        dropped = False
        try:
            import psycopg
            with psycopg.connect(admin_dsn, autocommit=True, prepare_threshold=None) as admin:
                dropped = self._drop_scoped_db_role(admin, role)
        except Exception as exc:
            return StepReceipt("replica_credentials", STATUS_ERROR, "revoke-node",
                               f"db role drop failed for {role!r}: {exc}")

        token_note = ""
        cfg = self.manifest.get("droplets") or {}
        do_token, blocked = self._do_token_or_blocked("droplets", "revoke-node")
        if blocked is not None:
            return StepReceipt(
                "replica_credentials", STATUS_ERROR, "revoke-node",
                f"db role {role!r} dropped, but the transport token could not be revoked: "
                f"{blocked.detail}",
                data={"node": node, "db_role_dropped": dropped},
            )
        headers = self._do_headers(do_token)
        sb_host, sb_why = self._resolve_safebox_host(headers, cfg)
        key_path = self._split_key_path()
        if sb_host is None:
            token_note = f"; transport token skipped: {sb_why}"
        elif not key_path.exists():
            return StepReceipt(
                "replica_credentials", STATUS_ERROR, "revoke-node",
                f"db role {role!r} dropped, but the transport token could not be revoked: ssh key "
                f"not found at {key_path}",
                data={"node": node, "db_role_dropped": dropped},
            )
        else:
            detail = self._update_node_tokens(sb_host, key_path, revoke=(node,))
            if detail:
                return StepReceipt(
                    "replica_credentials", STATUS_ERROR, "revoke-node",
                    f"db role {role!r} dropped, but the transport token could not be revoked: {detail}",
                    data={"node": node, "db_role_dropped": dropped},
                )

        # Best-effort registry stamp (the row may already be decommissioned/absent).
        db_cfg = self.manifest.get("database") or {}
        mig_dsn = self._resolve_alias(str(db_cfg.get("dsn_alias") or ""))
        if mig_dsn:
            try:
                self._assert_not_prod(mig_dsn)
                import psycopg
                with psycopg.connect(mig_dsn, autocommit=True, prepare_threshold=None) as mig:
                    self._stamp_node_credentials(mig, node, {
                        "db_role": role, "revoked": True,
                        "revoked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    })
            except Exception:
                pass

        return StepReceipt(
            "replica_credentials", STATUS_DELETED, "revoke-node",
            f"revoked {node}: db role {role!r} "
            + ("dropped (live backends terminated)" if dropped else "was already absent")
            + ", transport token digest pruned" + token_note
            + f"; re-enroll with `takyon env create {self.name}`",
            data={"node": node, "db_role": role, "db_role_dropped": dropped},
        )

    def _revoke_replica_credentials(self) -> StepReceipt:
        """destroy(): drop EVERY manifest-derived scoped role (the dev control plane outlives the
        droplets, so the roles must not). Token digests die with the safebox droplet; prune them
        anyway while the host still answers."""
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("replica_credentials", STATUS_DISABLED, "destroy",
                               "droplets twin disabled in manifest")
        if not (self.manifest.get("database") or {}).get("enabled", False):
            return StepReceipt("replica_credentials", STATUS_SKIPPED, "destroy",
                               "no database twin — no scoped roles to drop")
        role_tag = str(cfg.get("role") or "subuser").strip().lower()
        prefix = str(cfg.get("name_prefix") or f"takyon-{self.name}-{role_tag}").strip()
        count = max(1, int(cfg.get("count") or 1))
        nodes = [f"{prefix}-{i}" for i in range(1, count + 1)]
        admin_dsn, admin_alias = self._admin_dsn_or_none()
        if not admin_dsn:
            return StepReceipt(
                "replica_credentials", STATUS_BLOCKED, "destroy",
                f"cannot drop scoped replica roles: admin DSN not deposited (alias "
                f"{admin_alias or 'unset'})",
                deposit=admin_alias or None,
            )
        dropped: list[str] = []
        try:
            import psycopg
            with psycopg.connect(admin_dsn, autocommit=True, prepare_threshold=None) as admin:
                for node in nodes:
                    role = self._scoped_role_name(node)
                    if self._drop_scoped_db_role(admin, role):
                        dropped.append(role)
        except Exception as exc:
            return StepReceipt("replica_credentials", STATUS_ERROR, "destroy",
                               f"scoped role drop failed: {exc}", data={"dropped": dropped})

        # Best-effort token prune while the safebox droplet still exists (it is deleted next).
        do_token, blocked = self._do_token_or_blocked("droplets", "destroy")
        if blocked is None:
            sb_host, _why = self._resolve_safebox_host(self._do_headers(do_token), cfg)
            key_path = self._split_key_path()
            if sb_host is not None and key_path.exists():
                self._update_node_tokens(sb_host, key_path, revoke=tuple(nodes))
        if not dropped:
            return StepReceipt("replica_credentials", STATUS_SKIPPED, "destroy",
                               "no scoped replica roles to drop")
        return StepReceipt(
            "replica_credentials", STATUS_DELETED, "destroy",
            f"dropped {len(dropped)} scoped replica role(s): {', '.join(dropped)}",
            data={"dropped": dropped},
        )

    # Write the resolved pointers a dev RuntimeContext.from_env reads. Never writes a secret VALUE —
    # only the alias names + non-secret domains/plans. FAIL if the resolved config would leak a prod literal.
    def _write_config(self, prior: list[StepReceipt]) -> StepReceipt:
        domains = self.manifest.get("domains") or {}
        plans = self.manifest.get("plans") or {}
        company_base = str(domains.get("company_base") or "").strip()
        dashboard_host = str(domains.get("dashboard_host") or "").strip()
        self._assert_not_prod(company_base, dashboard_host)

        resolved = {
            "name": self.name,
            "domains": {"company_base": company_base, "dashboard_host": dashboard_host},
            "plans": dict(plans),
            "safebox_url_alias": str((self.manifest.get("safebox") or {}).get("url_alias") or ""),
            "database_dsn_alias": str((self.manifest.get("database") or {}).get("dsn_alias") or ""),
            "provisioned_at": time.time(),
            "steps": {r.resource: r.status for r in prior},
        }
        dev_split = self._resolved_dev_split_config()
        if dev_split:
            resolved["dev_split"] = dev_split
        try:
            import yaml
            self.env_dir.mkdir(parents=True, exist_ok=True)
            self.config_path.write_text(yaml.safe_dump(resolved, sort_keys=True))
        except Exception as exc:
            return StepReceipt("config", STATUS_ERROR, "create", f"config write failed: {exc}")
        return StepReceipt(
            "config", STATUS_CREATED, "create",
            f"wrote resolved dev pointers to {self.config_path}",
            data={"path": str(self.config_path)},
        )

    def _resolved_dev_split_config(self) -> dict[str, Any]:
        """Best-effort non-secret metadata for remote dev use.

        This is written so ordinary dev shells can discover the dev split topology without
        depending on a local authority env file. Secrets stay on the authority host; this section
        carries only hostnames/IPs/key-paths needed to reach the split.
        """
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return {}
        try:
            token, blocked = self._do_token_or_blocked("droplets")
            if blocked is not None or not token:
                return {}
            headers = self._do_headers(token)
            safebox_host, _why = self._resolve_safebox_host(headers, cfg)
            operator_host, _why = self._resolve_singleton_host(headers, cfg, "operator")
            replicas, _err = self._resolve_replicas(headers, cfg)
        except Exception:
            return {}
        out: dict[str, Any] = {}
        if safebox_host:
            out["safebox"] = {
                "name": str(safebox_host.get("name") or ""),
                "public_ip": str(safebox_host.get("public_ip") or ""),
                "private_ip": str(safebox_host.get("private_ip") or ""),
            }
        if operator_host:
            out["operator"] = {
                "name": str(operator_host.get("name") or ""),
                "public_ip": str(operator_host.get("public_ip") or ""),
                "private_ip": str(operator_host.get("private_ip") or ""),
            }
        key_path = self._split_key_path()
        if str(key_path):
            out["ssh_key_path"] = str(key_path)
        if replicas:
            out["replicas"] = [
                {
                    "name": str(rep.get("name") or ""),
                    "public_ip": str(rep.get("public_ip") or ""),
                    "private_ip": str(rep.get("private_ip") or ""),
                }
                for rep in replicas
            ]
        return out

    # ── ROLLING RESTART — the full-4b graceful-drain rail ──────────────────────────────────────
    #
    # ``takyon env restart <name>`` is the tracked deploy-ACTIVATION rail for the replica split:
    # after new code/config is rsynced to the replicas (deploy/takyon-dev-split/
    # bootstrap-dev-droplet.sh), this drains and restarts them ONE AT A TIME so a planned
    # restart/deploy loses ZERO requests (vs the ~4.5s LB health-check black-hole on a hard kill).
    #
    # Checked against the live DO API (REGIONAL lb-small): v2 load balancers expose NO draining
    # state and NO per-member health — so the rail implements the drain itself:
    #
    #   per replica (fail-closed at every gate, receipted at every step):
    #     0. refuse unless EVERY OTHER replica is an LB member and serving 200s locally
    #        (:9119 app + :80 caddy front — the exact path the LB health-checks);
    #     1. remove the replica from the LB (DELETE /v2/load_balancers/<id>/droplets) and poll
    #        membership until the LB confirms it is out — new connections now only go elsewhere;
    #     2. grace-wait for in-flight requests to complete (no LB drain signal exists to poll);
    #     3. converge the caddy front from the tracked template (deploy/takyon-dev-split/
    #        Caddyfile.dev — carries the X-Takyon-Node identity header the rejoin gate reads)
    #        and restart the runtime unit; poll local healthz until 200. An unhealthy replica is
    #        LEFT OUT of the LB and the run aborts — never re-add a node that is not serving;
    #     4. re-add to the LB and poll membership until present;
    #     5. rejoin gate: poll THROUGH the LB until X-Takyon-Node shows this node serving again
    #        (positive proof the LB health check re-admitted it). Any non-200 during the gate
    #        fails the run. Only then move to the next replica.
    #
    # The LB is droplet_ids-managed (the dev token lacks tag scope). A tag-managed LB is refused:
    # de-tagging a droplet to drain it would also rip it out of the tag-anchored firewall.

    def rolling_restart(
        self,
        *,
        grace_seconds: float | None = None,
        rejoin_timeout: float | None = None,
    ) -> ProvisionResult:
        """Drain-aware rolling restart across the environment's replicas. Zero-request-loss for
        PLANNED restarts/deploys; fail-closed and receipted at every step."""
        receipts: list[StepReceipt] = []

        def _fail(receipt: StepReceipt) -> ProvisionResult:
            receipts.append(self._append_receipt(receipt))
            return ProvisionResult(name=self.name, action="restart", receipts=tuple(receipts))

        cfg = self.manifest.get("rolling_restart") or {}
        droplets_cfg = self.manifest.get("droplets") or {}
        if not droplets_cfg.get("enabled", False) or not (self.manifest.get("load_balancer") or {}).get("enabled", False):
            return _fail(StepReceipt(
                "rolling_restart", STATUS_SKIPPED, "restart",
                "no droplets+load_balancer twins in the manifest — nothing to roll",
            ))
        token, blocked = self._do_token_or_blocked("load_balancer", "restart")
        if blocked is not None:
            return _fail(blocked)
        headers = self._do_headers(token)

        role = str(droplets_cfg.get("role") or "subuser").strip().lower()
        grace = float(grace_seconds if grace_seconds is not None else cfg.get("grace_seconds", 8.0))
        rejoin_deadline = float(rejoin_timeout if rejoin_timeout is not None else cfg.get("rejoin_timeout_seconds", 120.0))
        service = str(cfg.get("service") or f"takyon-{role}.service").strip()

        # SSH key: the split's own deploy key (manifest ssh_key.public_key_path minus .pub),
        # overridable via rolling_restart.private_key_path. Fail closed if absent.
        key_path = self._split_key_path()
        if not str(key_path) or str(key_path) == "." or not key_path.exists():
            return _fail(StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"replica ssh key not found at {key_path} — the drain rail restarts replicas over "
                "SSH with the split's deploy key (manifest ssh_key.public_key_path minus .pub)",
            ))

        # Tracked caddy front template (carries the X-Takyon-Node identity header the rejoin gate
        # reads). Lives in the workspace deploy rail for the split; fail closed when missing.
        template = self._caddy_template_path(cfg)
        if not template.exists():
            return _fail(StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"tracked caddy front template not found at {template} — run from the workspace "
                "checkout (deploy/takyon-dev-split/Caddyfile.dev is part of the split's deploy rail)",
            ))
        caddy_template = template.read_text()

        # Resolve the replicas (manifest-derived exact names -> droplet id + public ip).
        replicas, err = self._resolve_replicas(headers, droplets_cfg)
        if err is not None:
            return _fail(err)
        if len(replicas) < 2:
            return _fail(StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"graceful drain needs >=2 replicas so one keeps serving; found {len(replicas)} — "
                "refusing (a single-replica restart is an outage, not a drain)",
            ))

        # Resolve the LB (by manifest name) and refuse tag-managed membership.
        lb, err = self._resolve_lb(headers)
        if err is not None:
            return _fail(err)
        if str(lb.get("tag") or ""):
            return _fail(StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"load balancer {lb.get('name')!r} is tag-managed ({lb.get('tag')!r}) — draining by "
                "de-tagging would also rip the droplet out of the tag-anchored firewall; refusing",
            ))
        lb_id = str(lb.get("id") or "")
        lb_ip = str(lb.get("ip") or "")
        if not lb_id or not lb_ip:
            return _fail(StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"load balancer {lb.get('name')!r} has no id/ip yet (status {lb.get('status')!r})",
            ))

        for rep in replicas:
            others = [r for r in replicas if r["name"] != rep["name"]]

            # (0) fail-closed gate: every OTHER replica must be an LB member and healthy.
            gate = self._other_replicas_healthy_gate(headers, lb_id, rep, others, key_path, lb_ip)
            if gate is not None:
                return _fail(gate)

            # (1) drain: remove from the LB, poll membership until out, grace-wait for in-flight.
            drain, was_member = self._drain_from_lb(headers, lb_id, rep, grace)
            if drain.status == STATUS_ERROR:
                return _fail(drain)
            receipts.append(self._append_receipt(drain))

            # (2+3) converge front + restart unit + local health verify (:9119 and :80).
            restart = self._restart_replica(rep, key_path, service, caddy_template)
            if restart.status == STATUS_ERROR:
                # The replica is deliberately LEFT OUT of the LB: never re-add a node that is not
                # provably serving. The other replica keeps taking traffic.
                receipts.append(self._append_receipt(restart))
                return _fail(StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"aborted: {rep['name']} did not come back healthy after restart and was left "
                    f"OUT of the LB (the surviving replica(s) keep serving); fix the node, then "
                    f"re-run `takyon env restart {self.name}`",
                ))
            receipts.append(self._append_receipt(restart))

            # (4) re-add to the LB and poll membership until present.
            readd = self._readd_to_lb(headers, lb_id, rep, was_member)
            if readd.status == STATUS_ERROR:
                return _fail(readd)
            receipts.append(self._append_receipt(readd))

            # (5) rejoin gate: poll THROUGH the LB until X-Takyon-Node shows this node serving.
            rejoin = self._await_lb_routes_to(rep, lb_ip, rejoin_deadline)
            if rejoin.status == STATUS_ERROR:
                receipts.append(self._append_receipt(rejoin))
                return ProvisionResult(name=self.name, action="restart", receipts=tuple(receipts))
            receipts.append(self._append_receipt(rejoin))

        receipts.append(self._append_receipt(StepReceipt(
            "rolling_restart", STATUS_CREATED, "restart",
            f"drain-aware rolling restart complete across {len(replicas)} replica(s) of "
            f"{service}; every replica was drained from the LB before restart and proven back in "
            "rotation before the next began",
            data={"replicas": [r["name"] for r in replicas], "grace_seconds": grace},
        )))
        return ProvisionResult(name=self.name, action="restart", receipts=tuple(receipts))

    def _caddy_template_path(self, cfg: Mapping[str, Any]) -> Path:
        """The tracked replica-front template. Default: the workspace deploy rail for the split
        (this file lives at <workspace>/hermes-agent-main/plugins/takyon/env_provisioner.py)."""
        raw = str(cfg.get("caddy_template") or "").strip()
        if raw:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = Path(__file__).resolve().parents[3] / p
            return p
        return Path(__file__).resolve().parents[3] / "deploy" / "takyon-dev-split" / "Caddyfile.dev"

    def _resolve_replicas(
        self, headers: Mapping[str, str], droplets_cfg: Mapping[str, Any]
    ) -> tuple[list[dict[str, Any]], StepReceipt | None]:
        """Manifest-derived replica names -> [{name, droplet_id, public_ip}], sorted by name."""
        role = str(droplets_cfg.get("role") or "subuser").strip().lower()
        prefix = str(droplets_cfg.get("name_prefix") or f"takyon-{self.name}-{role}").strip()
        count = max(1, int(droplets_cfg.get("count") or 1))
        wanted = {f"{prefix}-{i}" for i in range(1, count + 1)}
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/droplets?per_page=200", headers=dict(headers))
        except Exception as exc:
            return [], StepReceipt("rolling_restart", STATUS_ERROR, "restart", f"droplet list failed: {exc}")
        out: list[dict[str, Any]] = []
        for d in (listed.get("droplets") or []) if isinstance(listed, dict) else []:
            if not isinstance(d, dict) or str(d.get("name") or "") not in wanted:
                continue
            public_ip = next(
                (str(n.get("ip_address") or "") for n in ((d.get("networks") or {}).get("v4") or [])
                 if isinstance(n, dict) and n.get("type") == "public"),
                "",
            )
            if not public_ip:
                return [], StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"replica {d.get('name')!r} has no public IPv4 — cannot reach it over SSH to restart",
                )
            private_ip = next(
                (str(n.get("ip_address") or "") for n in ((d.get("networks") or {}).get("v4") or [])
                 if isinstance(n, dict) and n.get("type") == "private"),
                "",
            )
            out.append({
                "name": str(d.get("name")),
                "droplet_id": d.get("id"),
                "public_ip": public_ip,
                "private_ip": private_ip,
            })
        return sorted(out, key=lambda r: r["name"]), None

    def _resolve_lb(self, headers: Mapping[str, str]) -> tuple[dict[str, Any], StepReceipt | None]:
        cfg = self.manifest.get("load_balancer") or {}
        droplets_cfg = self.manifest.get("droplets") or {}
        role = str(droplets_cfg.get("role") or "subuser").strip().lower()
        name = str(cfg.get("name") or f"takyon-{self.name}-{role}-lb").strip()
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/load_balancers?per_page=200", headers=dict(headers))
        except Exception as exc:
            return {}, StepReceipt("rolling_restart", STATUS_ERROR, "restart", f"load balancer list failed: {exc}")
        for lb in (listed.get("load_balancers") or []) if isinstance(listed, dict) else []:
            if isinstance(lb, dict) and str(lb.get("name") or "") == name:
                return lb, None
        return {}, StepReceipt(
            "rolling_restart", STATUS_ERROR, "restart",
            f"no load balancer named {name!r} — is the split provisioned (`takyon env create {self.name}`)?",
        )

    def _lb_member_ids(self, headers: Mapping[str, str], lb_id: str) -> set:
        got = self.http.request(
            "GET", f"{self._DO_BASE}/load_balancers/{urllib.parse.quote(lb_id)}", headers=dict(headers)
        )
        lb = (got or {}).get("load_balancer") if isinstance(got, dict) else {}
        return set((lb or {}).get("droplet_ids") or [])

    # The local health probe every gate uses: the app plane directly (:9119) AND the caddy front
    # (:80) — the latter is byte-for-byte the path the DO LB health-checks over the VPC.
    _REPLICA_HEALTH_SCRIPT = (
        "set -u; "
        "a=$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/healthz || echo 000); "
        "b=$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1/healthz || echo 000); "
        "echo \"HEALTH app=$a front=$b\""
    )

    def _replica_locally_healthy(self, rep: Mapping[str, Any], key_path: Path) -> bool:
        try:
            rc, out = self.remote.run(
                str(rep["public_ip"]), self._REPLICA_HEALTH_SCRIPT, key_path=str(key_path), timeout=30.0
            )
        except Exception:
            return False
        return rc == 0 and "app=200" in out and "front=200" in out

    def _other_replicas_healthy_gate(
        self,
        headers: Mapping[str, str],
        lb_id: str,
        rep: Mapping[str, Any],
        others: list[dict[str, Any]],
        key_path: Path,
        lb_ip: str,
    ) -> StepReceipt | None:
        """Fail-closed: refuse to start draining ``rep`` unless every OTHER replica is an LB member
        and locally healthy, and the LB front itself answers 200."""
        try:
            members = self._lb_member_ids(headers, lb_id)
        except Exception as exc:
            return StepReceipt("rolling_restart", STATUS_ERROR, "restart", f"LB membership read failed: {exc}")
        for other in others:
            if other["droplet_id"] not in members:
                return StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"refusing to drain {rep['name']}: {other['name']} is not an LB member — it "
                    "would leave zero healthy backends",
                )
            if not self._replica_locally_healthy(other, key_path):
                return StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"refusing to drain {rep['name']}: {other['name']} is not serving healthz 200 "
                    "locally (:9119/:80) — it could not carry the traffic alone",
                )
        status, _ = self.probe.probe(f"http://{lb_ip}/healthz", timeout=8.0)
        if status != 200:
            return StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"refusing to drain {rep['name']}: the LB front {lb_ip} is not answering 200 "
                f"(got {status})",
            )
        return None

    def _drain_from_lb(
        self, headers: Mapping[str, str], lb_id: str, rep: Mapping[str, Any], grace: float
    ) -> tuple[StepReceipt, bool]:
        """Remove ``rep`` from the LB, poll membership until the LB confirms it is out, then
        grace-wait for in-flight requests (the v2 API exposes no drain state to poll)."""
        droplet_id = rep["droplet_id"]
        try:
            members = self._lb_member_ids(headers, lb_id)
        except Exception as exc:
            return StepReceipt("rolling_restart", STATUS_ERROR, "restart", f"LB membership read failed: {exc}"), False
        was_member = droplet_id in members
        if was_member:
            try:
                self.http.request(
                    "DELETE",
                    f"{self._DO_BASE}/load_balancers/{urllib.parse.quote(lb_id)}/droplets",
                    headers=dict(headers),
                    body={"droplet_ids": [droplet_id]},
                )
            except Exception as exc:
                return StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"LB removal of {rep['name']} failed: {exc}",
                ), True
            for _ in range(30):
                try:
                    if droplet_id not in self._lb_member_ids(headers, lb_id):
                        break
                except Exception:
                    pass
                self._sleep(2.0)
            else:
                return StepReceipt(
                    "rolling_restart", STATUS_ERROR, "restart",
                    f"LB never confirmed {rep['name']} out of the member set",
                ), True
        self._sleep(grace)
        return StepReceipt(
            "drain", STATUS_CREATED, "restart",
            (f"removed {rep['name']} (droplet {droplet_id}) from the LB; membership converged; "
             f"in-flight grace {grace:g}s")
            if was_member else
            f"{rep['name']} (droplet {droplet_id}) was already out of the LB (resuming an aborted "
            f"roll); in-flight grace {grace:g}s",
            data={"droplet_id": droplet_id, "was_member": was_member, "grace_seconds": grace},
        ), was_member

    def _restart_replica(
        self, rep: Mapping[str, Any], key_path: Path, service: str, caddy_template: str
    ) -> StepReceipt:
        """While drained: converge the caddy front from the tracked template (rendered with this
        node's name), restart the runtime unit, and poll local healthz until 200."""
        rendered = caddy_template.replace("__NODE_NAME__", str(rep["name"]))
        if "TAKYON_CADDY_EOF" in rendered:
            return StepReceipt(
                "restart", STATUS_ERROR, "restart",
                "caddy template contains the heredoc sentinel TAKYON_CADDY_EOF — refusing",
            )
        script = (
            "set -euo pipefail\n"
            "cat > /etc/caddy/Caddyfile.staged <<'TAKYON_CADDY_EOF'\n"
            f"{rendered}\n"
            "TAKYON_CADDY_EOF\n"
            "caddy validate --adapter caddyfile --config /etc/caddy/Caddyfile.staged >/dev/null 2>&1\n"
            "mv /etc/caddy/Caddyfile.staged /etc/caddy/Caddyfile\n"
            "systemctl reload caddy\n"
            "systemctl daemon-reload\n"
            f"systemctl restart {service}\n"
            "code=000\n"
            "for _ in $(seq 1 60); do\n"
            "  code=$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:9119/healthz || echo 000)\n"
            "  [ \"$code\" = \"200\" ] && break\n"
            "  sleep 2\n"
            "done\n"
            "[ \"$code\" = \"200\" ]\n"
            "front=$(curl -fsS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1/healthz || echo 000)\n"
            "[ \"$front\" = \"200\" ]\n"
            f"systemctl is-active --quiet {service}\n"
            "echo \"RESTART_OK app=$code front=$front\"\n"
        )
        try:
            rc, out = self.remote.run(str(rep["public_ip"]), script, key_path=str(key_path), timeout=240.0)
        except Exception as exc:
            return StepReceipt("restart", STATUS_ERROR, "restart", f"{rep['name']} restart failed: {exc}")
        if rc != 0 or "RESTART_OK" not in out:
            return StepReceipt(
                "restart", STATUS_ERROR, "restart",
                f"{rep['name']} did not come back healthy after restart (rc={rc}): {out[-400:]}",
            )
        return StepReceipt(
            "restart", STATUS_CREATED, "restart",
            f"{rep['name']}: caddy front converged from the tracked template + {service} restarted; "
            "local healthz 200 on :9119 and :80",
            data={"droplet_id": rep["droplet_id"], "service": service},
        )

    def _readd_to_lb(
        self, headers: Mapping[str, str], lb_id: str, rep: Mapping[str, Any], was_member: bool
    ) -> StepReceipt:
        droplet_id = rep["droplet_id"]
        try:
            self.http.request(
                "POST",
                f"{self._DO_BASE}/load_balancers/{urllib.parse.quote(lb_id)}/droplets",
                headers=dict(headers),
                body={"droplet_ids": [droplet_id]},
            )
        except Exception as exc:
            return StepReceipt(
                "rolling_restart", STATUS_ERROR, "restart",
                f"re-adding {rep['name']} to the LB failed: {exc} — the node is healthy but out of "
                f"rotation; re-run `takyon env restart {self.name}` (or `create`) to converge",
            )
        for _ in range(30):
            try:
                if droplet_id in self._lb_member_ids(headers, lb_id):
                    return StepReceipt(
                        "rejoin", STATUS_CREATED, "restart",
                        f"re-added {rep['name']} to the LB member set",
                        data={"droplet_id": droplet_id, "was_member": was_member},
                    )
            except Exception:
                pass
            self._sleep(2.0)
        return StepReceipt(
            "rolling_restart", STATUS_ERROR, "restart",
            f"LB never confirmed {rep['name']} back in the member set",
        )

    def _await_lb_routes_to(
        self, rep: Mapping[str, Any], lb_ip: str, rejoin_deadline: float
    ) -> StepReceipt:
        """Positive rejoin proof: poll THROUGH the LB until X-Takyon-Node (set by the tracked caddy
        front) names this node — i.e. the LB health check re-admitted it and it is serving real
        traffic again. Any non-200 during the gate fails the run (zero-loss is the contract)."""
        seen: set[str] = set()
        attempts = max(1, int(rejoin_deadline))
        for attempt in range(attempts):
            status, headers = self.probe.probe(f"http://{lb_ip}/healthz", timeout=8.0)
            if status != 200:
                return StepReceipt(
                    "rejoin", STATUS_ERROR, "restart",
                    f"LB returned {status} during the {rep['name']} rejoin gate (probe {attempt + 1}) "
                    "— zero-loss contract violated; aborting the roll",
                    data={"droplet_id": rep["droplet_id"], "probes": attempt + 1},
                )
            node = str((headers or {}).get("x-takyon-node") or "")
            if node:
                seen.add(node)
            if node == rep["name"]:
                return StepReceipt(
                    "rejoin", STATUS_CREATED, "restart",
                    f"{rep['name']} PROVEN back in rotation: the LB routed a request to it after "
                    f"{attempt + 1} probe(s), all 200",
                    data={"droplet_id": rep["droplet_id"], "probes": attempt + 1},
                )
            self._sleep(1.0)
        return StepReceipt(
            "rejoin", STATUS_ERROR, "restart",
            f"{rep['name']} rejoined the member set but the LB never routed to it within "
            f"{rejoin_deadline:g}s (nodes seen: {sorted(seen) or ['<no X-Takyon-Node header>']}); "
            "aborting before draining the next replica",
            data={"droplet_id": rep["droplet_id"], "nodes_seen": sorted(seen)},
        )

    # ── STATUS ────────────────────────────────────────────────────────────────────────────────

    # ── code revision (THE code gate) ──────────────────────────────────────────────────────
    #
    # Dev and prod pin the same published main revision. Status/deploy prove that parity without
    # reading the dirty worktree; the former dev->prod promotion rail is intentionally inactive.

    def _resolve_rev_info(self) -> dict[str, Any]:
        """Compare this env's pinned code_revision against published prod (origin/main). Returns
        {pinned_ref, is_git, pinned_sha, prod_sha, ahead, behind, diverged}. Never raises."""
        ref = self.code_revision
        info: dict[str, Any] = {
            "pinned_ref": ref, "is_git": False, "pinned_sha": "", "prod_sha": "",
            "ahead": 0, "behind": 0, "diverged": False,
        }
        if not ref:
            return info
        pinned = _git("rev-parse", "--short", ref)
        prod = _git("rev-parse", "--short", "origin/main")
        if pinned is None or prod is None:
            return info
        info.update({"is_git": True, "pinned_sha": pinned, "prod_sha": prod})
        # `--left-right --count main...ref` -> "<behind> <ahead>": left = commits main has that ref
        # lacks (dev behind), right = commits ref has that main lacks (dev ahead).
        counts = _git("rev-list", "--left-right", "--count", f"origin/main...{ref}")
        if counts:
            parts = counts.split()
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
                behind, ahead = int(parts[0]), int(parts[1])
                info.update({"behind": behind, "ahead": ahead, "diverged": behind > 0})
        return info

    def deploy(self, rev: str | None = None) -> ProvisionResult:
        """Deploy the exact published production revision to every dev role from ``git archive``.

        A caller may pass ``--rev`` only to pin the current ``origin/main`` SHA explicitly; any
        different revision is refused because dev/prod source parity is the contract.
        """
        import tempfile

        receipts: list[StepReceipt] = []
        target_ref = str(rev or self.code_revision or "").strip()
        if not target_ref:
            receipts.append(StepReceipt(
                "code_revision", STATUS_BLOCKED, "deploy",
                "no --rev given and no code_revision pinned in the manifest",
                deposit=f"environments/{self.name}.yaml: code_revision"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        # Prefer published refs over possibly-stale local branches. Best-effort fetch first.
        if "/" not in target_ref and target_ref != "HEAD":
            _git("fetch", "origin", target_ref)
            sha = _git("rev-parse", f"origin/{target_ref}") or _git("rev-parse", target_ref)
        else:
            sha = _git("rev-parse", target_ref)
        if sha is None:
            receipts.append(StepReceipt(
                "code_revision", STATUS_BLOCKED, "deploy",
                f"{target_ref!r} not resolvable — run deploy from the workspace git checkout"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        _git("fetch", "origin", "main")
        published_main = _git("rev-parse", "origin/main")
        if published_main is None or sha != published_main:
            receipts.append(StepReceipt(
                "code_revision", STATUS_BLOCKED, "deploy",
                f"dev must run the published production revision origin/main; requested "
                f"{sha[:12]}, published {str(published_main or 'unresolved')[:12]}",
            ))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        short = sha[:12]
        tree_sha = _git("rev-parse", f"{sha}^{{tree}}") or ""
        # Materialize the pinned SHA into a staging tarball — ships THAT revision, not the working
        # tree. The git tree-hash is the deterministic deploy fingerprint (identical per SHA).
        staged = Path(tempfile.mkdtemp(prefix=f"takyon-deploy-{self.name}-"))
        tar_path = staged / "src.tar"
        archived = _git("archive", "--format=tar", "-o", str(tar_path), sha)
        if archived is None or not tar_path.exists() or tar_path.stat().st_size == 0:
            receipts.append(StepReceipt(
                "code_revision", STATUS_ERROR, "deploy", f"git archive of {short} failed"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        receipts.append(StepReceipt(
            "code_revision", STATUS_CREATED, "deploy",
            f"staged rev {short} (tree {tree_sha[:12]}) — the pinned revision, not the working tree",
            data={"sha": sha, "ref": target_ref, "tree": tree_sha, "staging": str(staged)}))
        # Host activation leg — revision-aware, CODE-ONLY. It rsyncs the STAGED runtime tree and
        # restarts services; it NEVER touches a host's .env / Doppler config (re-running the full
        # bootstrap would clobber the safebox's managed-secret setup). Host topology (public IPs +
        # the dev ssh key) comes from the env config's dev_split block.
        import subprocess

        tree_root = staged / "tree"
        try:
            tree_root.mkdir(exist_ok=True)
            subprocess.run(["tar", "-xf", str(tar_path), "-C", str(tree_root)],
                           check=True, capture_output=True, timeout=180)
        except Exception as exc:
            receipts.append(StepReceipt("hosts", STATUS_ERROR, "deploy",
                                        f"failed to extract staged rev: {exc}"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        src_tree = tree_root / "hermes-agent-main"
        if not (src_tree / "plugins").is_dir():
            receipts.append(StepReceipt("hosts", STATUS_ERROR, "deploy",
                                        "staged rev has no hermes-agent-main/ runtime tree"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))

        # Build the dashboard from the same archived revision before any host is staged. The
        # deployed services use --skip-build, so preserving an older web_dist would be source drift.
        web_dir = src_tree / "web"
        try:
            subprocess.run(
                ["npm", "ci"], cwd=web_dir, check=True, capture_output=True, timeout=600,
            )
            subprocess.run(
                ["npm", "run", "build"], cwd=web_dir, check=True, capture_output=True, timeout=600,
            )
        except Exception as exc:
            receipts.append(StepReceipt(
                "web_dist", STATUS_ERROR, "deploy",
                f"dashboard build failed for staged rev {short}: {exc}",
            ))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        receipts.append(StepReceipt(
            "web_dist", STATUS_CREATED, "deploy",
            f"built dashboard bundle from staged rev {short}",
        ))

        # Deployed hosts are archive trees, not Git checkouts. Seal the staged runtime with the
        # same release identity contract consumed by claim_scope.runtime_release_sha() before any
        # service restarts; otherwise a correctly archived worker cannot prove which revision it
        # is executing and must fail closed. The web bundle is host-preserved on this code-only
        # rail, so this manifest intentionally records only the immutable source/tree identity.
        runtime_tree_sha = _git("rev-parse", f"{sha}:hermes-agent-main") or ""
        if not runtime_tree_sha:
            receipts.append(StepReceipt(
                "code_revision", STATUS_ERROR, "deploy",
                f"could not resolve hermes-agent-main tree for staged rev {short}",
            ))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        (src_tree / ".takyon-deploy-artifact.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "source_revision": sha,
                    "repository_tree": tree_sha,
                    "runtime_path": "hermes-agent-main",
                    "runtime_tree": runtime_tree_sha,
                    "prepared_at_unix": int(time.time()),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )

        ds: dict[str, Any] = {}
        try:
            import yaml
            if self.config_path.exists():
                ds = (yaml.safe_load(self.config_path.read_text()) or {}).get("dev_split") or {}
        except Exception:
            ds = {}
        key_path = str(ds.get("ssh_key_path") or "").strip()
        if not key_path:
            receipts.append(StepReceipt(
                "hosts", STATUS_BLOCKED, "deploy",
                "no dev_split in env config (host topology unknown) — provision the dev split first",
                deposit="dev_split.ssh_key_path"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        ssh_base = ["-i", key_path, "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=accept-new"]

        def _push_code(ip: str) -> "tuple[bool, str]":
            # --delete gives every host the exact archived source tree. Exclusions preserve only
            # host-owned runtime state and dependencies.
            rsync = ["rsync", "-rt", "--no-perms", "--no-owner", "--no-group", "--checksum",
                     "--delete", "--delete-delay",
                     "--exclude=.git", "--exclude=.venv", "--exclude=venv", "--exclude=node_modules",
                     "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=._*", "--exclude=.DS_Store",
                     "--exclude=.env", "--exclude=secrets", "--exclude=logs", "--exclude=tmp",
                     "-e", "ssh " + " ".join(ssh_base),
                     f"{src_tree}/", f"root@{ip}:/opt/takyon/hermes-agent-main/"]
            r = subprocess.run(rsync, capture_output=True, text=True, timeout=900,
                               env={**os.environ, "COPYFILE_DISABLE": "1"})
            return (r.returncode == 0), ("synced" if r.returncode == 0
                                         else f"rsync failed: {(r.stderr or '').strip()[:160]}")

        def _push_service_units(
            ip: str, role: str, block: Mapping[str, Any]
        ) -> "tuple[bool, str]":
            """Install every tracked unit owned by a role from the staged revision.

            This is code/config deployment, not bootstrap: it never touches host env or managed
            secrets. Rendering uses only the already-resolved non-secret dev topology.
            """
            unit_specs = {
                "operator": (
                    ("takyon-dashboard-dev.service.tmpl", "takyon-dashboard.service"),
                    ("takyon-worker-dev.service.tmpl", "takyon-worker.service"),
                    ("takyon-docker-broker-dev.service.tmpl", "takyon-docker-broker.service"),
                ),
                "safebox": (("takyon-safebox-dev.service.tmpl", "takyon-safebox.service"),),
                "subuser": (("takyon-subuser-dev.service.tmpl", "takyon-subuser.service"),),
            }.get(role)
            if not unit_specs:
                return False, f"unknown public service role {role!r}"
            safebox_ip = str((ds.get("safebox") or {}).get("private_ip") or "").strip()
            operator = ds.get("operator") or {}
            operator_name = str(operator.get("name") or "takyon-dev-operator").strip()
            operator_private_ip = str(operator.get("private_ip") or "").strip()
            uid = ""
            for template_name, service_name in unit_specs:
                template_path = tree_root / "deploy" / "takyon-dev-split" / template_name
                if not template_path.is_file():
                    return False, f"staged revision is missing {template_path.name}"
                rendered = template_path.read_text(encoding="utf-8")
                rendered = rendered.replace("__NODE_NAME__", str(block.get("name") or role))
                rendered = rendered.replace("__SAFEBOX_VPC_IP__", safebox_ip)
                rendered = rendered.replace("__OPERATOR_NODE__", operator_name)
                rendered = rendered.replace("__OPERATOR_VPC_IP__", operator_private_ip)
                rendered = rendered.replace("__BIND_IP__", str(block.get("private_ip") or "").strip())
                if "__TAKYON_UID__" in rendered:
                    if not uid:
                        uid_read = subprocess.run(
                            ["ssh", *ssh_base, f"root@{ip}", "id -u takyon"],
                            capture_output=True, text=True, timeout=30,
                        )
                        uid = str(uid_read.stdout or "").strip()
                        if uid_read.returncode != 0 or not uid.isdigit():
                            return False, "could not resolve remote takyon uid for unit rendering"
                    rendered = rendered.replace("__TAKYON_UID__", uid)
                if re.search(r"__[A-Z0-9_]+__", rendered):
                    return False, "staged unit contains an unresolved deployment placeholder"
                staged_unit = staged / f"{role}-{service_name}"
                staged_unit.write_text(rendered, encoding="utf-8")
                copied = subprocess.run(
                    ["scp", *ssh_base, str(staged_unit), f"root@{ip}:/etc/systemd/system/{service_name}"],
                    capture_output=True, text=True, timeout=60,
                )
                if copied.returncode != 0:
                    return False, f"unit sync failed: {(copied.stderr or '').strip()[:160]}"
            return True, f"{len(unit_specs)} unit(s) synced"

        def _build_operator_worker_image(ip: str) -> "tuple[bool, str]":
            """Build and verify the revision-pinned coding image without browser requirements."""
            dockerfile = tree_root / "deploy" / "argon-alpha-14" / "takyon-claude-worker.Dockerfile"
            if not dockerfile.is_file():
                return False, "staged revision is missing takyon-claude-worker.Dockerfile"
            built = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{ip}",
                    "docker build --tag takyon/claude-worker:node20-chromium-v1 -",
                ],
                input=dockerfile.read_bytes(),
                capture_output=True,
                timeout=900,
            )
            if built.returncode != 0:
                detail = (built.stderr or built.stdout or b"").decode("utf-8", "replace")
                return False, f"worker image build failed: {detail.strip()[-240:]}"
            verified = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{ip}",
                    "docker image inspect takyon/claude-worker:node20-chromium-v1 >/dev/null && "
                    "docker run --rm --entrypoint node "
                    "takyon/claude-worker:node20-chromium-v1 --version >/dev/null && "
                    "docker run --rm --entrypoint node "
                    "--mount type=bind,src=/opt/takyon/hermes-agent-main,dst=/takyon-runtime,readonly "
                    "--workdir /takyon-runtime takyon/claude-worker:node20-chromium-v1 "
                    "--input-type=module -e 'import fs from \"node:fs\"; "
                    "const pkg = JSON.parse(fs.readFileSync(\"package.json\", \"utf8\")); "
                    "const lock = JSON.parse(fs.readFileSync(\"package-lock.json\", \"utf8\")); "
                    "const sdk = \"@anthropic-ai/claude-agent-sdk\"; "
                    "if (!pkg.dependencies?.[sdk] || !lock.packages?.[`node_modules/${sdk}`]) "
                    "throw new Error(\"Agent SDK dependency is not pinned\"); "
                    "await import(\"./scripts/takyon-claude-primary-runtime.mjs\");' >/dev/null",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if verified.returncode != 0:
                return False, (
                    "worker image verification failed: "
                    f"{(verified.stderr or verified.stdout or '').strip()[-240:]}"
                )
            chromium = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{ip}",
                    "docker run --rm --entrypoint /bin/sh "
                    "takyon/claude-worker:node20-chromium-v1 -lc "
                    "'test -x /usr/bin/chromium && /usr/bin/chromium --version >/dev/null'",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            renderer = "available" if chromium.returncode == 0 else "unavailable (optional)"
            return True, f"worker image, Node, Agent SDK, and native skill verified; Chromium {renderer}"

        def _prepare_remote_runtime(ip: str, role: str) -> "tuple[bool, str]":
            """Converge the role's locked dependencies before activation."""
            if role == "safebox":
                builder = tree_root / "deploy" / "takyon-safebox" / "rebuild-venv.sh"
                if not builder.is_file():
                    return False, "staged revision is missing the Safebox venv builder"
                result = subprocess.run(
                    [str(builder)],
                    capture_output=True,
                    text=True,
                    timeout=1200,
                    env={
                        **os.environ,
                        "TAKYON_VPS_HOST": f"root@{ip}",
                        "TAKYON_VPS_KEY": key_path,
                        "TAKYON_REMOTE_RUNTIME": "/opt/takyon/hermes-agent-main",
                        "TAKYON_SAFEBOX_VENV_ACTIVATE": "1",
                        "TAKYON_SAFEBOX_VENV_REPAIR_ID": short,
                    },
                )
                if result.returncode != 0:
                    return False, f"Safebox dependencies failed: {(result.stderr or result.stdout or '').strip()[-240:]}"
                return True, "Safebox locked dependencies ready"

            # Production code deploys preserve the host-owned venv and preflight it; dependency
            # mutation belongs to bootstrap (or the Safebox's separately locked venv rail).
            remote = (
                "set -euo pipefail; cd /opt/takyon/hermes-agent-main; "
                "test -x .venv/bin/python; "
                ".venv/bin/python -c 'import fastapi, psycopg, uvicorn; "
                "from plugins.takyon import core'"
            )
            result = subprocess.run(
                ["ssh", *ssh_base, f"root@{ip}", remote],
                capture_output=True, text=True, timeout=1200,
            )
            if result.returncode != 0:
                return False, f"runtime dependency preflight failed: {(result.stderr or result.stdout or '').strip()[-240:]}"
            if role != "operator":
                return True, "host-owned runtime dependencies ready"

            # Existing droplets converge too: the Agent SDK runtime and worker image need Node 20+.
            node_ready = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{ip}",
                    "set -euo pipefail; "
                    "major=$(node --version 2>/dev/null | sed -E 's/^v([0-9]+).*/\\1/' || true); "
                    "if [ -z \"$major\" ] || [ \"$major\" -lt 20 ]; then "
                    "apt-get update -y >/dev/null; apt-get install -y ca-certificates curl gnupg >/dev/null; "
                    "install -m 0755 -d /etc/apt/keyrings; "
                    "curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key "
                    "| gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg; "
                    "printf 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] "
                    "https://deb.nodesource.com/node_20.x nodistro main\\n' "
                    "> /etc/apt/sources.list.d/nodesource.list; "
                    "apt-get update -y >/dev/null; apt-get install -y nodejs >/dev/null; fi; "
                    "test \"$(node --version | sed -E 's/^v([0-9]+).*/\\1/')\" -ge 20",
                ],
                capture_output=True, text=True, timeout=600,
            )
            if node_ready.returncode != 0:
                return False, (
                    "Node 20 convergence failed: "
                    f"{(node_ready.stderr or node_ready.stdout or '').strip()[-240:]}"
                )

            helper = tree_root / "scripts" / "prepare-claude-agent-sdk-runtime.sh"
            if not helper.is_file():
                return False, "staged revision is missing the Agent SDK runtime helper"
            copied = subprocess.run(
                ["scp", *ssh_base, str(helper), f"root@{ip}:/opt/takyon/prepare-claude-agent-sdk-runtime.sh"],
                capture_output=True, text=True, timeout=60,
            )
            if copied.returncode != 0:
                return False, f"Agent SDK helper sync failed: {(copied.stderr or '').strip()[-240:]}"
            prepared = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{ip}",
                    "chown root:root /opt/takyon/prepare-claude-agent-sdk-runtime.sh; "
                    "chmod 0755 /opt/takyon/prepare-claude-agent-sdk-runtime.sh; "
                    "runuser -u takyon -- env "
                    "TAKYON_PYTHON=/opt/takyon/hermes-agent-main/.venv/bin/python "
                    "/opt/takyon/prepare-claude-agent-sdk-runtime.sh "
                    "/opt/takyon /opt/takyon/.takyon >/dev/null",
                ],
                capture_output=True, text=True, timeout=1200,
            )
            if prepared.returncode != 0:
                return False, f"Agent SDK runtime failed: {(prepared.stderr or prepared.stdout or '').strip()[-240:]}"
            return True, "host-owned runtime dependencies and Agent SDK ready"

        def _ensure_subuser_publish_rail(
            operator_ip: str, replica_blocks: list[Mapping[str, Any]]
        ) -> "tuple[bool, str]":
            """Give the operator a role-scoped key that reaches only dev subuser replicas."""
            sync_key = Path(str(
                (self.manifest.get("droplets") or {}).get("subuser_sync_private_key_path")
                or "~/.ssh/takyon_dev_subuser_sync"
            )).expanduser()
            sync_key.parent.mkdir(parents=True, exist_ok=True)
            if not sync_key.is_file() or not Path(str(sync_key) + ".pub").is_file():
                generated = subprocess.run(
                    [
                        "ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                        "-C", "takyon-dev-operator-to-subuser", "-f", str(sync_key),
                    ],
                    capture_output=True, text=True, timeout=30,
                )
                if generated.returncode != 0:
                    return False, f"subuser publish key generation failed: {(generated.stderr or '').strip()[-200:]}"
            public_key_path = Path(str(sync_key) + ".pub")
            for rep in replica_blocks:
                rep_ip = str(rep.get("public_ip") or "").strip()
                if not rep_ip:
                    return False, "subuser replica is missing a public IP"
                copied = subprocess.run(
                    ["scp", *ssh_base, str(public_key_path), f"root@{rep_ip}:/root/takyon-dev-subuser-sync.pub"],
                    capture_output=True, text=True, timeout=60,
                )
                if copied.returncode != 0:
                    return False, f"publish public-key sync failed for {rep_ip}: {(copied.stderr or '').strip()[-200:]}"
                installed = subprocess.run(
                    [
                        "ssh", *ssh_base, f"root@{rep_ip}",
                        "set -euo pipefail; install -d -m 0700 /root/.ssh; "
                        "touch /root/.ssh/authorized_keys; chmod 0600 /root/.ssh/authorized_keys; "
                        "key=$(cat /root/takyon-dev-subuser-sync.pub); "
                        "grep -qxF \"$key\" /root/.ssh/authorized_keys || "
                        "printf '%s\\n' \"$key\" >> /root/.ssh/authorized_keys; "
                        "rm -f /root/takyon-dev-subuser-sync.pub",
                    ],
                    capture_output=True, text=True, timeout=60,
                )
                if installed.returncode != 0:
                    return False, f"publish public-key install failed for {rep_ip}: {(installed.stderr or '').strip()[-200:]}"

            copied_private = subprocess.run(
                [
                    "scp", *ssh_base, str(sync_key),
                    f"root@{operator_ip}:/opt/takyon/takyon-subuser-sync.key",
                ],
                capture_output=True, text=True, timeout=60,
            )
            if copied_private.returncode != 0:
                return False, f"operator publish-key sync failed: {(copied_private.stderr or '').strip()[-200:]}"
            hosts = ",".join(str(rep.get("public_ip") or "").strip() for rep in replica_blocks)
            configured = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{operator_ip}",
                    "python3 - /opt/takyon/.takyon/.env "
                    f"{hosts!r} /opt/takyon/secrets/takyon-subuser-sync.key",
                ],
                input=(
                    "from pathlib import Path\n"
                    "import sys\n"
                    "path=Path(sys.argv[1]); hosts=sys.argv[2]; key_path=sys.argv[3]\n"
                    "updates={'TAKYON_SUBUSER_VPS_HOSTS':hosts,'TAKYON_SUBUSER_VPS_USER':'root',"
                    "'TAKYON_SUBUSER_VPS_SSH_KEY':key_path}\n"
                    "lines=path.read_text().splitlines() if path.exists() else []\n"
                    "kept=[line for line in lines if line.split('=',1)[0] not in updates]\n"
                    "kept.extend(f'{key}={value}' for key,value in updates.items())\n"
                    "path.write_text('\\n'.join(kept)+'\\n')\n"
                ),
                capture_output=True, text=True, timeout=60,
            )
            if configured.returncode != 0:
                return False, f"operator publish env failed: {(configured.stderr or '').strip()[-200:]}"
            secured = subprocess.run(
                [
                    "ssh", *ssh_base, f"root@{operator_ip}",
                    "set -euo pipefail; install -d -o takyon -g takyon -m 0700 /opt/takyon/secrets; "
                    "mv /opt/takyon/takyon-subuser-sync.key /opt/takyon/secrets/takyon-subuser-sync.key; "
                    "chown takyon:takyon /opt/takyon/secrets/takyon-subuser-sync.key /opt/takyon/.takyon/.env; "
                    "chmod 0600 /opt/takyon/secrets/takyon-subuser-sync.key /opt/takyon/.takyon/.env; "
                    "runuser -u takyon -- ssh -i /opt/takyon/secrets/takyon-subuser-sync.key "
                    "-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new "
                    f"root@{str(replica_blocks[0].get('public_ip') or '').strip()} true",
                ],
                capture_output=True, text=True, timeout=60,
            )
            if secured.returncode != 0:
                return False, f"operator-to-subuser publish proof failed: {(secured.stderr or '').strip()[-200:]}"
            return True, f"operator publish key reaches {len(replica_blocks)} subuser replica(s)"

        def _restart(ip: str, services: "list[str]") -> "tuple[bool, str]":
            remote = (
                "find /opt/takyon/hermes-agent-main -name '._*' -delete 2>/dev/null; "
                "runuser -u takyon -- /opt/takyon/hermes-agent-main/.venv/bin/python -m compileall -q "
                "/opt/takyon/hermes-agent-main/plugins/takyon >/dev/null 2>&1; systemctl daemon-reload; "
                + "; ".join(f"systemctl restart {s}" for s in services) + "; sleep 2; "
                + "systemctl is-active " + " ".join(services) + "; "
                + "grep -q '" + sha + "' "
                  "/opt/takyon/hermes-agent-main/.takyon-deploy-artifact.json"
            )
            r = subprocess.run(["ssh", *ssh_base, f"root@{ip}", remote],
                               capture_output=True, text=True, timeout=180)
            return (r.returncode == 0), (r.stdout or r.stderr or "").strip().replace("\n", " ")[:160]

        def _migrate_on_operator(ip: str) -> "tuple[bool, str]":
            remote = (
                "find /opt/takyon/hermes-agent-main -name '._*' -delete 2>/dev/null; "
                "runuser -u takyon -- env TAKYON_ENV=dev TAKYON_HOST_ROLE=operator "
                "TAKYON_HOME=/opt/takyon/.takyon HOME=/opt/takyon "
                "/opt/takyon/hermes-agent-main/takyon migrate"
            )
            result = subprocess.run(
                ["ssh", *ssh_base, f"root@{ip}", remote],
                capture_output=True, text=True, timeout=600,
            )
            detail = (result.stdout or result.stderr or "").strip().replace("\n", " ")[-240:]
            return result.returncode == 0, detail or "migrations current"

        # Stage singleton code first. Nothing restarts until the operator has applied the staged
        # migrations, so Safebox/operator/subuser code can never observe an older schema. Install
        # the staged revision's public unit at the same time; env/secrets remain untouched.
        singleton_specs = (
            ("safebox", ds.get("safebox") or {}, ["takyon-safebox.service"]),
            ("operator", ds.get("operator") or {},
             ["takyon-docker-broker.service", "takyon-worker.service", "takyon-dashboard.service"]),
        )
        staged_singletons: list[tuple[str, str, list[str]]] = []
        staging_failed = False
        for role, block, services in singleton_specs:
            ip = str((block or {}).get("public_ip") or "").strip()
            if not ip:
                receipts.append(StepReceipt(role, STATUS_SKIPPED, "deploy", f"no {role} host in dev_split"))
                continue
            ok, why = _push_code(ip)
            if ok:
                ok, why = _push_service_units(ip, role, block)
            if ok:
                ok, why = _prepare_remote_runtime(ip, role)
            if ok and role == "operator":
                ok, why = _build_operator_worker_image(ip)
            if not ok:
                receipts.append(StepReceipt(role, STATUS_ERROR, "deploy", f"{ip}: {why}"))
                staging_failed = True
                continue
            staged_singletons.append((role, ip, services))

        # Stage every replica before changing schema or restarting anything. A partial sync aborts
        # the activation phase, preserving the N-replica same-revision contract.
        replicas = [
            r for r in (ds.get("replicas") or [])
            if str((r or {}).get("public_ip") or "").strip()
        ]
        synced: list[dict[str, Any]] = []
        for rep in replicas:
            ip = str(rep.get("public_ip")).strip()
            ok, why = _push_code(ip)
            if ok:
                ok, why = _push_service_units(ip, "subuser", rep)
            if ok:
                ok, why = _prepare_remote_runtime(ip, "subuser")
            receipts.append(StepReceipt(
                "subuser", STATUS_EXISTS if ok else STATUS_ERROR, "deploy",
                f"{rep.get('name') or ip}: code {'staged' if ok else 'sync FAILED — ' + why}"))
            if ok:
                synced.append(rep)
            else:
                staging_failed = True
        if staging_failed:
            receipts.append(StepReceipt(
                "hosts", STATUS_ERROR, "deploy",
                "revision did not stage on every declared host; refusing migration and restarts"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))

        declared_operator_ip = str(((ds.get("operator") or {}).get("public_ip") or "")).strip()
        publish_ready, publish_why = _ensure_subuser_publish_rail(
            declared_operator_ip, replicas,
        ) if declared_operator_ip and replicas else (False, "operator or replicas missing")
        receipts.append(StepReceipt(
            "subuser_publish",
            STATUS_EXISTS if publish_ready else STATUS_ERROR,
            "deploy",
            publish_why,
        ))
        if not publish_ready:
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        operator_ip = next((ip for role, ip, _ in staged_singletons if role == "operator"), "")
        if not declared_operator_ip:
            receipts.append(StepReceipt(
                "database", STATUS_BLOCKED, "deploy",
                "no dev operator host is declared; refusing every restart because migrations "
                "cannot run"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        if not operator_ip:
            receipts.append(StepReceipt(
                "database", STATUS_ERROR, "deploy",
                "operator code did not stage; refusing every restart because migrations cannot run"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))
        migrated, why = _migrate_on_operator(operator_ip)
        receipts.append(StepReceipt(
            "database", STATUS_CREATED if migrated else STATUS_ERROR, "deploy",
            f"{operator_ip}: staged rev {short} "
            f"{'migrated before restart' if migrated else 'migration FAILED — ' + why}"))
        if not migrated:
            return ProvisionResult(self.name, "deploy", tuple(receipts))

        # Singleton services are not behind an LB; activate them only after schema convergence.
        singleton_restart_failed = False
        for role, ip, services in staged_singletons:
            ok, why = _restart(ip, services)
            receipts.append(StepReceipt(
                role, STATUS_CREATED if ok else STATUS_ERROR, "deploy",
                f"{ip}: rev {short} {'active (' + why + ')' if ok else 'restart FAILED — ' + why}"))
            if not ok:
                singleton_restart_failed = True
        if singleton_restart_failed:
            receipts.append(StepReceipt(
                "hosts", STATUS_ERROR, "deploy",
                "singleton activation failed; refusing replica activation"))
            return ProvisionResult(self.name, "deploy", tuple(receipts))

        # Replicas activate drain-aware only after schema and singleton convergence.
        if synced:
            restarted = self.rolling_restart()
            receipts.extend(restarted.receipts)
            if not restarted.ok:
                return ProvisionResult(self.name, "deploy", tuple(receipts))
        try:
            import yaml
            self.env_dir.mkdir(parents=True, exist_ok=True)
            cfg = yaml.safe_load(self.config_path.read_text()) if self.config_path.exists() else {}
            if not isinstance(cfg, dict):
                cfg = {}
            cfg["deployed_code_revision"] = {"ref": target_ref, "sha": sha, "tree": tree_sha}
            self.config_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        except Exception:
            pass
        return ProvisionResult(self.name, "deploy", tuple(receipts))

    def status(self) -> ProvisionResult:
        """Report the environment's current state WITHOUT any side effect. A nonexistent env is a clean
        report (every twin 'blocked'/'disabled'), never a crash."""
        receipts: list[StepReceipt] = []

        # code_revision — THE code gate, reported first. Dev and prod must resolve to main;
        # this surfaces the pin + any drift with no side effect. Git-only; degrades to a literal
        # report off a checkout (e.g. a deployed host that is not the workspace repo).
        rev = self._resolve_rev_info()
        if not rev["pinned_ref"]:
            receipts.append(StepReceipt(
                "code_revision", STATUS_DISABLED, "status",
                "no code_revision pinned — env runs its deploy host's revision"))
        elif not rev["is_git"]:
            receipts.append(StepReceipt(
                "code_revision", STATUS_EXISTS, "status",
                f"pinned to {rev['pinned_ref']!r} (git comparison unavailable here)",
                data={"pinned_ref": rev["pinned_ref"]}))
        else:
            detail = (f"pinned {rev['pinned_ref']} @ {rev['pinned_sha']}; prod main @ {rev['prod_sha']}; "
                      f"drift: {rev['ahead']} ahead, {rev['behind']} behind")
            if rev["diverged"]:
                detail += " — dev is not on the published production revision"
            receipts.append(StepReceipt("code_revision", STATUS_EXISTS, "status", detail, data=rev))

        # The dedicated Safebox is the authority store in the prod-shaped dev topology. Read only
        # secret NAMES from it so status does not falsely report a managed provider secret missing
        # merely because it is intentionally absent from the local operator store.
        dev_split: dict[str, Any] = {}
        remote_aliases: set[str] = set()
        try:
            import yaml
            config = yaml.safe_load(self.config_path.read_text()) if self.config_path.exists() else {}
            dev_split = (config or {}).get("dev_split") or {}
            key_path = str(dev_split.get("ssh_key_path") or "").strip()
            safebox_ip = str(((dev_split.get("safebox") or {}).get("public_ip") or "")).strip()
            if key_path and safebox_ip:
                names = subprocess.run(
                    [
                        "ssh", "-i", key_path, "-o", "IdentitiesOnly=yes",
                        "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                        f"root@{safebox_ip}",
                        "set -e; env=/opt/takyon/.takyon/.env; "
                        "awk -F= '/^[A-Z0-9_]+=/ {print $1}' \"$env\"; "
                        "sed -n 's/^TAKYON_MANAGED_SECRET_KEYS=//p' \"$env\" "
                        "| tr ' ,' '\\n' | tr -d '\"'",
                    ],
                    capture_output=True, text=True, timeout=15,
                )
                if names.returncode == 0:
                    remote_aliases = {
                        line.strip() for line in names.stdout.splitlines()
                        if re.fullmatch(r"[A-Z][A-Z0-9_]*", line.strip())
                    }
        except Exception:
            dev_split = {}

        db_cfg = self.manifest.get("database") or {}
        if not db_cfg.get("enabled", False):
            receipts.append(StepReceipt("database", STATUS_DISABLED, "status", "disabled in manifest"))
        else:
            dsn = self._resolve_alias(str(db_cfg.get("dsn_alias") or ""))
            receipts.append(
                StepReceipt("database", STATUS_EXISTS if dsn else STATUS_BLOCKED, "status",
                            "dev DSN present" if dsn else "dev DSN not deposited",
                            deposit=None if dsn else str(db_cfg.get("dsn_alias") or ""))
            )

        sb_cfg = self.manifest.get("safebox") or {}
        if not sb_cfg.get("enabled", False):
            receipts.append(StepReceipt("safebox", STATUS_DISABLED, "status", "disabled in manifest"))
        else:
            groups = sb_cfg.get("required_aliases") or {}
            wanted = [
                str(a).strip()
                for aliases in (groups.values() if isinstance(groups, dict) else [])
                for a in (aliases or []) if str(a).strip()
            ]
            missing = [a for a in wanted if not self._resolve_alias(a) and a not in remote_aliases]
            receipts.append(
                StepReceipt("safebox", STATUS_BLOCKED if missing else STATUS_EXISTS, "status",
                            f"{len(missing)} alias(es) missing" if missing else "all aliases present",
                            deposit=missing[0] if missing else None,
                            data={"missing": missing})
            )

        a0_cfg = self.manifest.get("auth0") or {}
        if not a0_cfg.get("enabled", False):
            receipts.append(StepReceipt("auth0", STATUS_DISABLED, "status", "disabled in manifest"))
        else:
            # Either deposit shape counts as present: a raw token, or the durable M2M client pair
            # (minted per run). Status never mints — presence of the pair is the signal.
            token_alias = str(a0_cfg.get("mgmt_token_alias") or "TAKYON_AUTH0_MGMT_TOKEN").strip()
            id_alias = str(a0_cfg.get("mgmt_client_id_alias") or "TAKYON_AUTH0_MGMT_CLIENT_ID").strip()
            secret_alias = str(a0_cfg.get("mgmt_client_secret_alias") or "TAKYON_AUTH0_MGMT_CLIENT_SECRET").strip()
            has_token = bool(self._resolve_alias(token_alias))
            has_pair = bool(self._resolve_alias(id_alias)) and bool(self._resolve_alias(secret_alias))
            has = has_token or has_pair
            receipts.append(
                StepReceipt("auth0", STATUS_EXISTS if has else STATUS_BLOCKED, "status",
                            ("mgmt client credentials present (minted per run)" if has_pair
                             else "mgmt token present") if has
                            else f"mgmt credential not deposited (alias {id_alias}+{secret_alias} or {token_alias})",
                            deposit=None if has else id_alias)
            )

        droplets_alias = str(
            (self.manifest.get("droplets") or {}).get("token_alias") or "TAKYON_DO_API_TOKEN"
        ).strip()
        for resource, alias_key, default_alias in (
            ("cloudflare", "token_alias", "CLOUDFLARE_API_TOKEN"),
            ("vpc", "token_alias", droplets_alias),
            ("ssh_key", "token_alias", droplets_alias),
            ("droplets", "token_alias", droplets_alias),
            ("load_balancer", "token_alias", droplets_alias),
            ("firewall", "token_alias", droplets_alias),
        ):
            cfg = self.manifest.get(resource) or {}
            if not cfg.get("enabled", False):
                receipts.append(StepReceipt(resource, STATUS_DISABLED, "status", "disabled in manifest"))
                continue
            alias = str(cfg.get(alias_key) or default_alias).strip()
            has = bool(self._resolve_alias(alias))
            receipts.append(
                StepReceipt(resource, STATUS_EXISTS if has else STATUS_BLOCKED, "status",
                            "token present" if has else f"token not deposited (alias {alias})",
                            deposit=None if has else alias)
            )

        st_cfg = self.manifest.get("stripe") or {}
        if not st_cfg.get("enabled", False):
            receipts.append(StepReceipt("stripe", STATUS_DISABLED, "status", "disabled in manifest"))
        else:
            secret = self._resolve_alias("STRIPE_SECRET_KEY")
            receipts.append(
                StepReceipt("stripe", STATUS_EXISTS if secret else STATUS_BLOCKED, "status",
                            "stripe key present" if secret else "stripe key not deposited",
                            deposit=None if secret else "STRIPE_SECRET_KEY")
            )

        receipts.append(
            StepReceipt("config", STATUS_EXISTS if self.config_path.exists() else STATUS_SKIPPED, "status",
                        f"config at {self.config_path}" if self.config_path.exists() else "not yet provisioned")
        )

        # Prove each declared host is healthy and executing the published production revision.
        key_path = str(dev_split.get("ssh_key_path") or "").strip()
        target_sha = _git("rev-parse", "origin/main") or ""
        host_specs = [
            ("safebox", dev_split.get("safebox") or {}, ["takyon-safebox.service"],
             lambda block: f"http://{block.get('private_ip')}:8000/healthz"),
            ("operator", dev_split.get("operator") or {},
             ["takyon-dashboard.service", "takyon-worker.service", "takyon-docker-broker.service"],
             lambda _block: "http://127.0.0.1:9119/healthz"),
        ]
        host_specs.extend(
            ("subuser", block or {}, ["takyon-subuser.service", "caddy.service"],
             lambda _block: "http://127.0.0.1/healthz")
            for block in (dev_split.get("replicas") or [])
        )
        for role, block, services, health_url in host_specs:
            ip = str(block.get("public_ip") or "").strip()
            name = str(block.get("name") or role).strip()
            if not key_path or not ip:
                continue
            command = (
                "set -euo pipefail; systemctl is-active --quiet " + " ".join(services)
                + "; curl -fsS --max-time 5 " + health_url(block) + " >/dev/null; "
                "python3 -c \"import json; print(json.load(open("
                "'/opt/takyon/hermes-agent-main/.takyon-deploy-artifact.json'))"
                "['source_revision'])\""
            )
            checked = subprocess.run(
                ["ssh", "-i", key_path, "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
                 "-o", "ConnectTimeout=5", f"root@{ip}", command],
                capture_output=True, text=True, timeout=20,
            )
            live_sha = str(checked.stdout or "").strip().splitlines()[-1:] or [""]
            live_sha = live_sha[0]
            current = checked.returncode == 0 and bool(target_sha) and live_sha == target_sha
            receipts.append(StepReceipt(
                f"host:{name}", STATUS_EXISTS if current else STATUS_ERROR, "status",
                (f"healthy at production revision {live_sha[:12]}" if current else
                 f"unhealthy or revision drift (live {live_sha[:12] or 'unknown'}, "
                 f"expected {target_sha[:12] or 'unknown'})"),
                data={"role": role, "source_revision": live_sha, "expected_revision": target_sha},
            ))
        return ProvisionResult(name=self.name, action="status", receipts=tuple(receipts))

    # ── DESTROY ───────────────────────────────────────────────────────────────────────────────

    def destroy(self, force: bool = False) -> ProvisionResult:
        """Tear down the environment's twins. CONSERVATIVE: refuses while the environment has live
        nodes/pools registered or non-empty ledgers, UNLESS ``force``. Receipts every deletion."""
        receipts: list[StepReceipt] = []

        # Live-state guard (unless forced): a dev control plane with registered pools or non-empty
        # ledgers is in-use; refuse to tear it down.
        if not force:
            live = self._live_state_summary()
            if live is not None and (live.get("pools", 0) > 0 or live.get("ledger_rows", 0) > 0):
                receipts.append(self._append_receipt(StepReceipt(
                    "database", STATUS_BLOCKED, "destroy",
                    f"refusing destroy: environment has {live.get('pools', 0)} live pool(s) and "
                    f"{live.get('ledger_rows', 0)} ledger row(s); pass force=True to override",
                    data=live,
                )))
                return ProvisionResult(name=self.name, action="destroy", receipts=tuple(receipts))

        # Auth0 application removal (idempotent).
        receipts.append(self._append_receipt(self._destroy_auth0()))
        # Stripe test webhook removal (idempotent).
        receipts.append(self._append_receipt(self._destroy_stripe()))
        # DigitalOcean dev-split teardown IS automated (Stage 4b): every droplet/LB/firewall this
        # rail created carries the env tag, so the sweep deletes EXACTLY the tagged set (plus the
        # manifest-named ssh key + vpc, which DO cannot tag). Reverse creation order so the VPC
        # is empty by the time it is removed.
        receipts.append(self._append_receipt(self._destroy_firewall()))
        receipts.append(self._append_receipt(self._destroy_load_balancer()))
        # Scoped per-replica credentials are revoked BEFORE the droplets go away: the dev control
        # plane outlives the droplets, so the scoped roles must be dropped, and the safebox host
        # must still answer for the token-digest prune.
        receipts.append(self._append_receipt(self._revoke_replica_credentials()))
        receipts.append(self._append_receipt(self._destroy_droplets()))
        receipts.append(self._append_receipt(self._decommission_replica_nodes()))
        receipts.append(self._append_receipt(self._destroy_ssh_key()))
        receipts.append(self._append_receipt(self._destroy_vpc()))
        # DB + safebox + cloudflare destruction is intentionally NOT automated here: deleting a
        # Supabase project / R2 bucket is a high-blast-radius act better done deliberately.
        # We receipt them as skipped with the manual pointer rather than silently doing nothing.
        for resource, note in (
            ("database", "drop the dev Supabase project manually (high blast radius); receipts record what was applied"),
            ("cloudflare", "delete the dev R2 bucket / DNS records manually if created"),
        ):
            if (self.manifest.get(resource) or {}).get("enabled", False):
                receipts.append(self._append_receipt(StepReceipt(resource, STATUS_SKIPPED, "destroy", note)))

        # Remove the local resolved config/receipts pointer dir.
        receipts.append(self._append_receipt(self._destroy_local_state()))
        return ProvisionResult(name=self.name, action="destroy", receipts=tuple(receipts))

    def _live_state_summary(self) -> dict[str, int] | None:
        """Best-effort read of live pools + ledger rows against the dev control plane. Returns None if
        the DB is unreachable/not provisioned (treated as 'nothing live' — the config-dir removal still
        proceeds; a truly live DB with a resolvable DSN is what the guard protects)."""
        db_cfg = self.manifest.get("database") or {}
        dsn = self._resolve_alias(str(db_cfg.get("dsn_alias") or ""))
        if not dsn:
            return None
        try:
            self._assert_not_prod(dsn)
            import psycopg
            with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
                pools = self._count_if_exists(conn, "worker_pools", "status <> 'decommissioned'")
                ledger = self._count_if_exists(conn, "billing_entries", None)
                return {"pools": pools, "ledger_rows": ledger}
        except Exception:
            return None

    @staticmethod
    def _count_if_exists(conn, table: str, where: str | None) -> int:
        row = conn.execute(
            "select to_regclass(%s) is not null", (f"public.{table}",)
        ).fetchone()
        exists = bool(row and (list(row.values())[0] if isinstance(row, Mapping) else row[0]))
        if not exists:
            return 0
        clause = f" where {where}" if where else ""
        r = conn.execute(f"select count(*) from public.{table}{clause}").fetchone()
        return int(list(r.values())[0] if isinstance(r, Mapping) else r[0]) if r else 0

    def _destroy_auth0(self) -> StepReceipt:
        cfg = self.manifest.get("auth0") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("auth0", STATUS_DISABLED, "destroy", "auth0 twin disabled in manifest")
        domain_alias = str(cfg.get("domain_alias") or "AUTH0_DOMAIN").strip()
        domain = self._resolve_alias(domain_alias)
        if not domain:
            return StepReceipt("auth0", STATUS_BLOCKED, "destroy",
                               "auth0 tenant domain absent; cannot remove dev application",
                               deposit=domain_alias)
        self._assert_not_prod(domain)
        token, blocked = self._resolve_auth0_mgmt_token(cfg, domain)
        if blocked is not None:
            return StepReceipt("auth0", blocked.status, "destroy", blocked.detail, deposit=blocked.deposit)
        app_name = str(cfg.get("application_name") or f"Takyon {self.name.title()}").strip()
        base = f"https://{domain.rstrip('/')}/api/v2"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            existing = self.http.request(
                "GET", f"{base}/clients?" + urllib.parse.urlencode({"fields": "client_id,name"}),
                headers=headers,
            )
        except Exception as exc:
            return StepReceipt("auth0", STATUS_ERROR, "destroy", f"auth0 clients list failed: {exc}")
        client_id = next(
            (c.get("client_id") for c in (existing if isinstance(existing, list) else [])
             if isinstance(c, dict) and str(c.get("name") or "") == app_name),
            None,
        )
        if not client_id:
            return StepReceipt("auth0", STATUS_SKIPPED, "destroy", f"auth0 application {app_name!r} not found")
        try:
            self.http.request("DELETE", f"{base}/clients/{urllib.parse.quote(str(client_id))}", headers=headers)
        except Exception as exc:
            return StepReceipt("auth0", STATUS_ERROR, "destroy", f"auth0 application delete failed: {exc}")
        return StepReceipt("auth0", STATUS_DELETED, "destroy", f"deleted auth0 application {app_name!r}",
                           data={"client_id": client_id})

    def _destroy_stripe(self) -> StepReceipt:
        cfg = self.manifest.get("stripe") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("stripe", STATUS_DISABLED, "destroy", "stripe twin disabled in manifest")
        secret = self._resolve_alias("STRIPE_SECRET_KEY")
        if not secret:
            return StepReceipt("stripe", STATUS_BLOCKED, "destroy",
                               "stripe key absent; cannot remove dev webhook", deposit="STRIPE_SECRET_KEY")
        if not secret.startswith(("sk_test_", "rk_test_")):
            return StepReceipt("stripe", STATUS_ERROR, "destroy",
                               "dev stripe key is not test-mode; refusing to touch live webhooks")
        webhook_url = str(cfg.get("webhook_url") or "").strip()
        base = "https://api.stripe.com/v1"
        headers = {"Authorization": f"Bearer {secret}"}
        try:
            listed = self.http.request("GET", f"{base}/webhook_endpoints?limit=100", headers=headers)
        except Exception as exc:
            return StepReceipt("stripe", STATUS_ERROR, "destroy", f"stripe webhook list failed: {exc}")
        ep_id = next(
            (ep.get("id") for ep in (listed.get("data") or []) if isinstance(listed, dict)
             and isinstance(ep, dict) and str(ep.get("url") or "") == webhook_url),
            None,
        )
        if not ep_id:
            return StepReceipt("stripe", STATUS_SKIPPED, "destroy", f"no stripe webhook for {webhook_url}")
        try:
            self.http.request("DELETE", f"{base}/webhook_endpoints/{urllib.parse.quote(str(ep_id))}", headers=headers)
        except Exception as exc:
            return StepReceipt("stripe", STATUS_ERROR, "destroy", f"stripe webhook delete failed: {exc}")
        return StepReceipt("stripe", STATUS_DELETED, "destroy", f"deleted stripe test webhook for {webhook_url}",
                           data={"webhook_endpoint_id": ep_id})

    # ── DigitalOcean teardown (tag-anchored: only the env's own resources are touched) ───────

    def _destroy_firewall(self) -> StepReceipt:
        cfg = self.manifest.get("firewall") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("firewall", STATUS_DISABLED, "destroy", "firewall twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("firewall", "destroy")
        if blocked is not None:
            return blocked
        headers = self._do_headers(token)
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/firewalls?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("firewall", STATUS_ERROR, "destroy", f"firewall list failed: {exc}")
        name = str(cfg.get("name") or f"takyon-{self.name}-fw").strip()
        deleted: list[str] = []
        for fw in (listed.get("firewalls") or []) if isinstance(listed, dict) else []:
            if not isinstance(fw, dict):
                continue
            # Ours = targets the env tag, or carries the manifest name. Never touch anything else.
            if self.env_tag in (fw.get("tags") or []) or str(fw.get("name") or "") == name:
                try:
                    self.http.request(
                        "DELETE", f"{self._DO_BASE}/firewalls/{urllib.parse.quote(str(fw.get('id')))}",
                        headers=headers,
                    )
                except Exception as exc:
                    return StepReceipt("firewall", STATUS_ERROR, "destroy", f"firewall delete failed: {exc}")
                deleted.append(str(fw.get("id")))
        if not deleted:
            return StepReceipt("firewall", STATUS_SKIPPED, "destroy", "no env-tagged firewall found")
        return StepReceipt("firewall", STATUS_DELETED, "destroy",
                           f"deleted {len(deleted)} firewall(s)", data={"firewall_ids": deleted})

    def _destroy_load_balancer(self) -> StepReceipt:
        cfg = self.manifest.get("load_balancer") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("load_balancer", STATUS_DISABLED, "destroy", "load_balancer twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("load_balancer", "destroy")
        if blocked is not None:
            return blocked
        headers = self._do_headers(token)
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/load_balancers?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("load_balancer", STATUS_ERROR, "destroy", f"load balancer list failed: {exc}")
        droplets_cfg = self.manifest.get("droplets") or {}
        role = str(droplets_cfg.get("role") or "subuser").strip().lower()
        name = str(cfg.get("name") or f"takyon-{self.name}-{role}-lb").strip()
        deleted: list[str] = []
        for lb in (listed.get("load_balancers") or []) if isinstance(listed, dict) else []:
            if not isinstance(lb, dict):
                continue
            # Ours = fronts one of the env's role tags, or carries the manifest name.
            backend_tag = str(lb.get("tag") or "")
            if backend_tag.startswith(f"{self.env_tag}-") or backend_tag == self.env_tag \
                    or str(lb.get("name") or "") == name:
                try:
                    self.http.request(
                        "DELETE", f"{self._DO_BASE}/load_balancers/{urllib.parse.quote(str(lb.get('id')))}",
                        headers=headers,
                    )
                except Exception as exc:
                    return StepReceipt("load_balancer", STATUS_ERROR, "destroy", f"load balancer delete failed: {exc}")
                deleted.append(str(lb.get("id")))
        if not deleted:
            return StepReceipt("load_balancer", STATUS_SKIPPED, "destroy", "no env load balancer found")
        return StepReceipt("load_balancer", STATUS_DELETED, "destroy",
                           f"deleted {len(deleted)} load balancer(s)", data={"lb_ids": deleted})

    def _destroy_droplets(self) -> StepReceipt:
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("droplets", STATUS_DISABLED, "destroy", "droplets twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("droplets", "destroy")
        if blocked is not None:
            return blocked
        headers = self._do_headers(token)
        # Deletable set = the env TAG set ∪ the manifest-derived EXACT names (covers droplets a
        # tag-scope-limited token had to create untagged). Both selectors are provably ours;
        # nothing else in the account is ever touched.
        role = str(cfg.get("role") or "subuser").strip().lower()
        prefix = str(cfg.get("name_prefix") or f"takyon-{self.name}-{role}").strip()
        count = max(1, int(cfg.get("count") or 1))
        owned_names = {f"{prefix}-{i}" for i in range(1, count + 1)}
        sb_host = cfg.get("safebox_host") or {}
        if sb_host.get("enabled", False):
            owned_names.add(str(sb_host.get("name") or f"takyon-{self.name}-safebox").strip())
        operator_host = cfg.get("operator_host") or {}
        if operator_host.get("enabled", False):
            owned_names.add(
                str(operator_host.get("name") or f"takyon-{self.name}-operator").strip()
            )
        try:
            listed = self.http.request(
                "GET", f"{self._DO_BASE}/droplets?per_page=200", headers=headers
            )
        except Exception as exc:
            return StepReceipt("droplets", STATUS_ERROR, "destroy", f"droplet list failed: {exc}")
        deleted: list[dict[str, Any]] = []
        for d in (listed.get("droplets") or []) if isinstance(listed, dict) else []:
            if not isinstance(d, dict):
                continue
            if self.env_tag not in (d.get("tags") or []) and str(d.get("name") or "") not in owned_names:
                continue
            try:
                self.http.request(
                    "DELETE", f"{self._DO_BASE}/droplets/{urllib.parse.quote(str(d.get('id')))}",
                    headers=headers,
                )
            except Exception as exc:
                return StepReceipt("droplets", STATUS_ERROR, "destroy",
                                   f"droplet delete failed for {d.get('name')!r}: {exc}",
                                   data={"deleted": deleted})
            deleted.append({"name": d.get("name"), "droplet_id": d.get("id")})
        if not deleted:
            return StepReceipt("droplets", STATUS_SKIPPED, "destroy",
                               f"no droplets tagged {self.env_tag!r} or named {sorted(owned_names)}")
        return StepReceipt("droplets", STATUS_DELETED, "destroy",
                           f"deleted {len(deleted)} env droplet(s)",
                           data={"deleted": deleted, "tag": self.env_tag})

    def _decommission_replica_nodes(self) -> StepReceipt:
        """Best-effort: flip the env's replica rows in worker_pools to 'decommissioned' so the dev
        control plane (which destroy deliberately leaves standing) does not show ghost nodes."""
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("node_registry", STATUS_DISABLED, "destroy", "droplets twin disabled in manifest")
        db_cfg = self.manifest.get("database") or {}
        dsn = self._resolve_alias(str(db_cfg.get("dsn_alias") or ""))
        if not dsn:
            return StepReceipt("node_registry", STATUS_SKIPPED, "destroy", "dev DSN absent; nothing to decommission")
        try:
            self._assert_not_prod(dsn)
            import psycopg
            with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
                row = conn.execute(
                    "update worker_pools set status = 'decommissioned', updated_at = now()"
                    " where capabilities->>'env' = %s and status <> 'decommissioned'"
                    " returning pool_id",
                    (self.name,),
                ).fetchall()
        except Exception as exc:
            return StepReceipt("node_registry", STATUS_ERROR, "destroy", f"node decommission failed: {exc}")
        if not row:
            return StepReceipt("node_registry", STATUS_SKIPPED, "destroy", "no live env nodes registered")
        return StepReceipt("node_registry", STATUS_DELETED, "destroy",
                           f"decommissioned {len(row)} node row(s)")

    def _destroy_ssh_key(self) -> StepReceipt:
        cfg = self.manifest.get("ssh_key") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("ssh_key", STATUS_DISABLED, "destroy", "ssh_key twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("ssh_key", "destroy")
        if blocked is not None:
            return blocked
        headers = self._do_headers(token)
        name = str(cfg.get("name") or f"takyon-{self.name}-split").strip()
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/account/keys?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("ssh_key", STATUS_ERROR, "destroy", f"ssh key list failed: {exc}")
        key_id = next(
            (k.get("id") for k in ((listed.get("ssh_keys") or []) if isinstance(listed, dict) else [])
             if isinstance(k, dict) and str(k.get("name") or "") == name),
            None,
        )
        if key_id is None:
            return StepReceipt("ssh_key", STATUS_SKIPPED, "destroy", f"no ssh key named {name!r}")
        try:
            self.http.request("DELETE", f"{self._DO_BASE}/account/keys/{urllib.parse.quote(str(key_id))}",
                              headers=headers)
        except Exception as exc:
            return StepReceipt("ssh_key", STATUS_ERROR, "destroy", f"ssh key delete failed: {exc}")
        return StepReceipt("ssh_key", STATUS_DELETED, "destroy", f"deleted ssh key {name!r}",
                           data={"ssh_key_id": key_id})

    def _destroy_vpc(self) -> StepReceipt:
        cfg = self.manifest.get("vpc") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("vpc", STATUS_DISABLED, "destroy", "vpc twin disabled in manifest")
        token, blocked = self._do_token_or_blocked("vpc", "destroy")
        if blocked is not None:
            return blocked
        headers = self._do_headers(token)
        name = str(cfg.get("name") or f"takyon-{self.name}-vpc").strip()
        try:
            listed = self.http.request("GET", f"{self._DO_BASE}/vpcs?per_page=200", headers=headers)
        except Exception as exc:
            return StepReceipt("vpc", STATUS_ERROR, "destroy", f"vpc list failed: {exc}")
        vpc_id = next(
            (v.get("id") for v in ((listed.get("vpcs") or []) if isinstance(listed, dict) else [])
             if isinstance(v, dict) and str(v.get("name") or "") == name),
            None,
        )
        if vpc_id is None:
            return StepReceipt("vpc", STATUS_SKIPPED, "destroy", f"no vpc named {name!r}")
        try:
            self.http.request("DELETE", f"{self._DO_BASE}/vpcs/{urllib.parse.quote(str(vpc_id))}",
                              headers=headers)
        except Exception as exc:
            # DO refuses to delete a VPC while members are still tearing down; that is a re-run,
            # not a leak — the droplets above were already deleted.
            return StepReceipt("vpc", STATUS_ERROR, "destroy",
                               f"vpc delete failed (droplet teardown may still be in flight; re-run destroy): {exc}")
        return StepReceipt("vpc", STATUS_DELETED, "destroy", f"deleted vpc {name!r}", data={"vpc_id": vpc_id})

    def _destroy_local_state(self) -> StepReceipt:
        if not self.env_dir.exists():
            return StepReceipt("config", STATUS_SKIPPED, "destroy", "no local env state to remove")
        try:
            import shutil
            shutil.rmtree(self.env_dir)
        except OSError as exc:
            return StepReceipt("config", STATUS_ERROR, "destroy", f"local state removal failed: {exc}")
        return StepReceipt("config", STATUS_DELETED, "destroy", f"removed local env state {self.env_dir}")


# ── HTTP transport (injectable so tests never touch the network) ────────────────────────────


class HttpTransport:
    """Minimal JSON/form HTTP transport interface. Injectable so tests drive create()/destroy() with a
    fake and assert fail-closed behavior with zero network."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        form: str | None = None,
    ) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


class RemoteExec:
    """Runs a script on a replica as root over SSH. Injectable so the rolling-restart tests drive
    the drain flow with a fake and zero SSH. ``stdin`` carries secret payloads (per-replica env
    lines) over the SSH channel so a credential value never appears on a remote command line."""

    def run(
        self, host: str, script: str, *, key_path: str, timeout: float = 120.0, stdin: str | None = None
    ) -> tuple[int, str]:  # pragma: no cover - interface
        raise NotImplementedError


class SshRemoteExec(RemoteExec):
    """subprocess ssh with the split's deploy key. BatchMode: never prompts (fail-closed when the
    key is not authorized)."""

    def run(
        self, host: str, script: str, *, key_path: str, timeout: float = 120.0, stdin: str | None = None
    ) -> tuple[int, str]:
        import subprocess

        proc = subprocess.run(
            [
                "ssh", "-i", key_path,
                "-o", "IdentitiesOnly=yes",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                f"root@{host}",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            input=stdin,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


class HttpProbe:
    """A plain GET that returns (status_code, lowercased response headers). Distinct from
    HttpTransport because the LB rejoin gate reads a response HEADER (X-Takyon-Node), which the
    JSON transport deliberately does not expose."""

    def probe(
        self, url: str, *, host_header: str | None = None, timeout: float = 8.0
    ) -> tuple[int, Mapping[str, str]]:  # pragma: no cover - interface
        raise NotImplementedError


class UrllibProbe(HttpProbe):
    def probe(
        self, url: str, *, host_header: str | None = None, timeout: float = 8.0
    ) -> tuple[int, Mapping[str, str]]:
        headers = {"Host": host_header} if host_header else {}
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return int(resp.status), {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            return int(exc.code), {str(k).lower(): str(v) for k, v in (exc.headers or {}).items()}
        except Exception:
            return 0, {}


class UrllibTransport(HttpTransport):
    """stdlib-only transport (mirrors the urllib pattern used across safebox.py). No new dependency."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        form: str | None = None,
    ) -> Any:
        data: bytes | None = None
        if form is not None:
            data = form.encode("utf-8")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw) if raw.strip() else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise EnvironmentProvisionError(f"http {method} {url} -> {exc.code}: {detail[:400]}") from exc
