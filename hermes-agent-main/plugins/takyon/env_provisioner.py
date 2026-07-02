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

    resource: str                       # 'database' | 'safebox' | 'auth0' | 'cloudflare' | 'stripe' | 'droplet' | 'config'
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
        receipts.append(self._append_receipt(self._create_droplet()))
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

        # === REAL DB SIDE EFFECT (gated behind the resolved, non-prod DSN) ===
        # Consumes the DB rail verbatim: topology.sql then run_migrations as takyon_migration.
        try:
            import psycopg
            from .db import runner as db_runner
        except Exception as exc:  # pragma: no cover - import guard
            return StepReceipt("database", STATUS_ERROR, "create", f"db rail unavailable: {exc}")

        applied: list[str] = []
        try:
            with psycopg.connect(dsn, autocommit=True, prepare_threshold=None) as conn:
                conn.execute("select set_config('statement_timeout', '0', false)")
                if cfg.get("apply_topology", True):
                    conn.execute(db_runner.topology_sql_path().read_text())
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
            f"applied topology + {len(applied)} migration(s) to dev control plane",
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

    # (c) Auth0 dev application via the Management API (token resolved via safebox; fail-closed if absent).
    def _create_auth0(self) -> StepReceipt:
        cfg = self.manifest.get("auth0") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("auth0", STATUS_DISABLED, "create", "auth0 twin disabled in manifest")

        mgmt_alias = str(cfg.get("mgmt_token_alias") or "TAKYON_AUTH0_MGMT_TOKEN").strip()
        domain_alias = str(cfg.get("domain_alias") or "AUTH0_DOMAIN").strip()
        token = self._resolve_alias(mgmt_alias)
        if not token:
            return StepReceipt(
                "auth0", STATUS_BLOCKED, "create",
                f"Auth0 Management API token not deposited (alias {mgmt_alias}); "
                "deposit TAKYON_AUTH0_MGMT_TOKEN once to enable autonomous dev-app creation",
                deposit=mgmt_alias,
            )
        domain = self._resolve_alias(domain_alias)
        if not domain:
            return StepReceipt(
                "auth0", STATUS_BLOCKED, "create",
                f"Auth0 tenant domain not deposited (alias {domain_alias})",
                deposit=domain_alias,
            )
        self._assert_not_prod(domain)

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

    # (f) DigitalOcean droplet — optional dev compute, gated by manifest.droplet.enabled.
    def _create_droplet(self) -> StepReceipt:
        cfg = self.manifest.get("droplet") or {}
        if not cfg.get("enabled", False):
            return StepReceipt("droplet", STATUS_DISABLED, "create", "droplet twin disabled in manifest")

        token_alias = str(cfg.get("token_alias") or "TAKYON_DO_API_TOKEN").strip()
        token = self._resolve_alias(token_alias)
        if not token:
            return StepReceipt(
                "droplet", STATUS_BLOCKED, "create",
                f"DigitalOcean API token not deposited (alias {token_alias}); "
                "deposit TAKYON_DO_API_TOKEN once to enable autonomous dev droplet creation",
                deposit=token_alias,
            )
        name = str(cfg.get("name") or f"takyon-{self.name}-1").strip()
        base = "https://api.digitalocean.com/v2"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # === REAL DigitalOcean API calls (gated) ===
        # Idempotent: look up an existing droplet by name before creating one.
        try:
            listed = self.http.request(
                "GET", f"{base}/droplets?" + urllib.parse.urlencode({"name": name}), headers=headers
            )
        except Exception as exc:
            return StepReceipt("droplet", STATUS_ERROR, "create", f"droplet list failed: {exc}")
        for d in (listed.get("droplets") or []) if isinstance(listed, dict) else []:
            if isinstance(d, dict) and str(d.get("name") or "") == name:
                return StepReceipt(
                    "droplet", STATUS_EXISTS, "create",
                    f"droplet {name!r} already exists",
                    data={"droplet_id": d.get("id")},
                )
        body = {
            "name": name,
            "region": str(cfg.get("region") or "nyc3"),
            "size": str(cfg.get("size") or "s-2vcpu-4gb"),
            "image": "ubuntu-24-04-x64",
            # cloud-init runs the node bootstrap with TAKYON_ENV=dev (plan §2.6): a dev droplet
            # registers itself into the dev pool registry like any other node.
            "user_data": "#cloud-config\nruncmd:\n  - echo 'TAKYON_ENV=dev' >> /etc/environment\n",
            "tags": [f"takyon-env-{self.name}"],
        }
        try:
            created = self.http.request("POST", f"{base}/droplets", headers=headers, body=body)
        except Exception as exc:
            return StepReceipt("droplet", STATUS_ERROR, "create", f"droplet create failed: {exc}")
        droplet = (created or {}).get("droplet") if isinstance(created, dict) else {}
        return StepReceipt(
            "droplet", STATUS_CREATED, "create",
            f"created dev droplet {name!r}",
            data={"droplet_id": (droplet or {}).get("id")},
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

        for resource, alias_key, default_alias in (
            ("auth0", "mgmt_token_alias", "TAKYON_AUTH0_MGMT_TOKEN"),
            ("cloudflare", "token_alias", "CLOUDFLARE_API_TOKEN"),
            ("droplet", "token_alias", "TAKYON_DO_API_TOKEN"),
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
        # DB + safebox + cloudflare + droplet destruction is intentionally NOT automated here: deleting
        # a Supabase project / R2 bucket / droplet is a high-blast-radius act better done deliberately.
        # We receipt them as skipped with the manual pointer rather than silently doing nothing.
        for resource, note in (
            ("database", "drop the dev Supabase project manually (high blast radius); receipts record what was applied"),
            ("cloudflare", "delete the dev R2 bucket / DNS records manually if created"),
            ("droplet", "destroy the dev droplet manually via the DO console/API if created"),
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
        mgmt_alias = str(cfg.get("mgmt_token_alias") or "TAKYON_AUTH0_MGMT_TOKEN").strip()
        domain_alias = str(cfg.get("domain_alias") or "AUTH0_DOMAIN").strip()
        token = self._resolve_alias(mgmt_alias)
        domain = self._resolve_alias(domain_alias)
        if not token or not domain:
            return StepReceipt("auth0", STATUS_BLOCKED, "destroy",
                               "auth0 mgmt token/domain absent; cannot remove dev application",
                               deposit=mgmt_alias if not token else domain_alias)
        self._assert_not_prod(domain)
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
