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

import json
import os
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


class EnvironmentProvisionError(RuntimeError):
    """A hard refusal (bad manifest, prod-literal leakage, name=prod) — distinct from a blocked
    credential (which is a receipt, not an exception)."""


# ── manifest loading ───────────────────────────────────────────────────────────────────────

# The manifests live next to the package root (hermes-agent-main/environments/*.yaml).
_MANIFEST_DIR = Path(__file__).resolve().parents[2] / "environments"

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
    ) -> None:
        self.name = _safe_env_name(name)
        if self.name == "prod":
            # Hard refusal: this rail exists to stand up ISOLATED twins, never to touch prod.
            raise EnvironmentProvisionError(
                "refusing to provision name='prod' — the provisioner only stands up isolated twins"
            )
        self.manifest = dict(manifest) if manifest is not None else load_manifest(self.name)
        home_raw = str(home) if home is not None else str(os.getenv("TAKYON_HOME") or "").strip()
        self.home = Path(home_raw) if home_raw else Path.home() / ".takyon"
        # Lazy import keeps this module inert and cheap to import; safebox is a leaf.
        if safebox_mod is None:
            from . import safebox as safebox_mod  # type: ignore[no-redef]
        self.safebox = safebox_mod
        self.http = http or UrllibTransport()
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
        receipts.append(self._append_receipt(self._register_replica_nodes()))
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
        # Idempotent: look up an existing application by name before creating one.
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
                return StepReceipt(
                    "auth0", STATUS_EXISTS, "create",
                    f"auth0 application {app_name!r} already exists",
                    data={"client_id": client.get("client_id")},
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
                return StepReceipt(
                    "stripe", STATUS_EXISTS, "create",
                    f"stripe test webhook already registered for {webhook_url}",
                    data={"webhook_endpoint_id": ep.get("id")},
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

    # (f3) droplets — the REPLICATED subuser split (count × name_prefix-N) plus the optional
    # singleton dev safebox host (the secret authority is deliberately NOT replicated).
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

        # Desired set: the N replicas, plus the singleton safebox host when declared.
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
            + ("provisioned" if created_any else "already present"),
            data={"droplets": results, "tag": self.env_tag},
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
            if d.get("role") != "safebox" and d.get("droplet_id")
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
    # credential enrollment (plan Stage 4b security bullet) supersedes this when it lands.
    def _register_replica_nodes(self) -> StepReceipt:
        cfg = self.manifest.get("droplets") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("node_registry", STATUS_SKIPPED, "create", "no droplets twin — nothing to enroll")
        replicas = [d for d in (self._do_state.get("droplets") or []) if d.get("role") != "safebox"]
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

    # ── STATUS ────────────────────────────────────────────────────────────────────────────────

    def status(self) -> ProvisionResult:
        """Report the environment's current state WITHOUT any side effect. A nonexistent env is a clean
        report (every twin 'blocked'/'disabled'), never a crash."""
        receipts: list[StepReceipt] = []

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
            missing = [a for a in wanted if not self._resolve_alias(a)]
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
