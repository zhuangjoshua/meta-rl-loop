"""RuntimeContext — the one Environment object (modularization plan Stage 3, §2.1 / UC3).

"Which environment am I" used to be reconstructed from ~213 scattered ``os.getenv`` reads,
four divergent ``_normalized_host_role`` copies, and hardcoded prod-IP fallbacks. This module
is the single source of truth:

- :class:`HostRole` — ONE place that knows every deployment-role spelling. The historical
  divergences between the runtime's normalizers are deliberate behavior (pinned by
  ``test_takyon_host_role_characterization.py``), so they are exposed as three explicit,
  named VIEWS of one truth table instead of four accidental copies:
    * :meth:`HostRole.canonical` — core/runtime_app behavior: alias map (dashboard→operator,
      app/product→subuser, all/default→combined, incl. ``safebox``), unknown → raw lowercase.
    * :meth:`HostRole.serving` — web_server behavior: same aliases EXCEPT ``safebox`` (the
      secret-authority host must never be treated as a dashboard-serving role) and ``worker``
      (a queue worker is not a dashboard-serving role either), and every unknown/empty value
      defaults to ``combined`` (an HTTP server must resolve to something servable).
    * :meth:`HostRole.bare` — app_actions/safebox behavior: no normalization at all; those
      modules gate on exact spellings and must keep seeing them.
- :class:`RuntimeContext` — a frozen value object naming the instance (prod/dev/hermetic)
  with the seven config slices the load-bearing reads resolve through. Built once at process
  boot via :meth:`RuntimeContext.from_env`; carried in a ContextVar that COMPOSES with
  ``gateway.session_context`` (it does not replace per-turn session vars).
- :func:`assert_not_prod_leakage` — the fail-loud boot assertion: a context whose name is not
  ``prod`` must not resolve any prod literal (control-plane host, VPS/safebox IPs).

Parsimony rule (plan §Stage 3): only load-bearing reads route through the context — DSN/plane
resolution, PG role gates, safebox authority mode, host targets, base domains, spend gates,
worker profile. Cosmetic env reads stay where they are.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping

# ── the one role truth table ─────────────────────────────────────────────────────────────

_CANONICAL_ALIASES: dict[str, str] = {
    "dashboard": "operator",
    "operator": "operator",
    "app": "subuser",
    "product": "subuser",
    "subuser": "subuser",
    "safebox": "safebox",
    "all": "combined",
    "default": "combined",
    "combined": "combined",
    "worker": "worker",
}

# web_server's view: no "safebox" or "worker" keys (neither is a dashboard-serving role; both
# fold to the combined default — pinned by the Stage-0 truth table rows for web_server),
# unknown/empty -> "combined".
_SERVING_ALIASES: dict[str, str] = {
    k: v for k, v in _CANONICAL_ALIASES.items() if k not in ("safebox", "worker")
}


class HostRole(str, Enum):
    """Deployment role of this process. str-valued so existing string comparisons keep working."""

    OPERATOR = "operator"
    SUBUSER = "subuser"
    SAFEBOX = "safebox"
    COMBINED = "combined"
    WORKER = "worker"
    UNSET = ""

    # -- the three views (see module docstring; each pinned by the Stage-0 truth table) ----

    @staticmethod
    def raw(environ: Mapping[str, str] | None = None) -> str:
        env = os.environ if environ is None else environ
        return str(env.get("TAKYON_HOST_ROLE") or "").strip().lower()

    @classmethod
    def canonical(cls, value: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
        """core/runtime_app behavior: alias-normalize; unknown values pass through raw."""
        raw = cls.raw(environ) if value is None else str(value or "").strip().lower()
        return _CANONICAL_ALIASES.get(raw, raw)

    @classmethod
    def serving(cls, value: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
        """web_server behavior: like canonical but WITHOUT the safebox/worker aliases, and
        everything unknown/empty resolves to 'combined' (an HTTP process must land on a
        servable role)."""
        raw = cls.raw(environ) if value is None else str(value or "").strip().lower()
        return _SERVING_ALIASES.get(raw, "combined")

    @classmethod
    def bare(cls, value: str | None = None, *, environ: Mapping[str, str] | None = None) -> str:
        """app_actions/safebox behavior: exact lowercase spelling, no aliasing."""
        return cls.raw(environ) if value is None else str(value or "").strip().lower()


# ── prod literals (the boot assertion's deny-list for non-prod contexts) ────────────────

PROD_LITERALS: tuple[str, ...] = (
    "137.184.75.57",   # operator VPS
    "134.209.123.8",   # subuser VPS
    "67.205.158.170",  # safebox host (public)
    "10.116.0.2",      # safebox VPC address
    "db.ddftvmjpfghfrdxhavvp.supabase.co",  # prod control plane
    "app.fourmanifold.com",
)


class ProdLeakage(RuntimeError):
    """A non-prod RuntimeContext resolved a prod literal — refusing to boot."""


# ── dev control-plane DSN aliases (UC3 runtime slice mapping) ────────────────────────────

# The Stage-3b provisioner (env_provisioner.py / environments/dev.yaml) deposits the dev twin
# DSNs in the dev store under these aliases. In a dev instance each DB authority plane resolves
# ONLY its dev alias — the prod alias is deliberately NOT a fallback: a dev process silently
# reading the prod DSN is exactly the leak the environment split exists to prevent, so absence
# fails closed upstream naming the dev alias. Non-dev instances never consult this table.
DEV_DATABASE_PLANE_ENV: dict[str, tuple[str, ...]] = {
    "operator": ("TAKYON_DEV_OPERATOR_DATABASE_URL",),
    # Dev twin of the takyon_app_runtime plane (the provisioner names it "runtime").
    "app": ("TAKYON_DEV_RUNTIME_DATABASE_URL",),
    "safebox": ("TAKYON_DEV_SAFEBOX_DATABASE_URL",),
    "migration": ("TAKYON_DEV_MIGRATION_DATABASE_URL",),
}


def env_name() -> str:
    """This process's instance name (prod|dev|hermetic): the bound context's name, or — before
    boot binds one — the same raw env read ``from_env`` performs (mirrors :func:`cache_scope`,
    which deliberately avoids forcing full context construction on hot pre-boot paths)."""
    ctx = _CURRENT.get()
    if ctx is not None:
        return ctx.name
    return str(os.getenv("TAKYON_ENV") or "prod").strip().lower() or "prod"


def database_plane_env_names(plane: str, default_names: tuple[str, ...]) -> tuple[str, ...]:
    """UC3 slice mapping: the env aliases one DB authority plane resolves in THIS environment.

    Non-dev instances keep their existing aliases byte-identically. A dev instance resolves only
    its ``TAKYON_DEV_*`` twin; an unknown plane raises KeyError loudly rather than falling back
    to a prod alias (callers validate the plane before resolving, so this is a belt-and-braces
    refusal, not a reachable path)."""
    if env_name() != "dev":
        return default_names
    return DEV_DATABASE_PLANE_ENV[str(plane or "").strip().lower()]


def assert_dsn_not_prod_literal(value: str, *, plane: str, alias: str) -> None:
    """Resolution-time arm of the prod-leakage gate (plan §3 UC3): a non-prod instance that
    RESOLVES a DSN containing a prod literal refuses before any connection is opened. This
    complements :func:`assert_not_prod_leakage` (which sweeps process env at boot) by also
    covering values resolved from the ``$TAKYON_HOME/.env`` store. The DSN value itself is a
    credential and is never echoed — only the literal hits and the alias are named."""
    if env_name() == "prod":
        return
    hits = sorted({lit for lit in PROD_LITERALS if lit and lit in str(value or "")})
    if hits:
        raise ProdLeakage(
            f"{env_name()} instance resolved a {plane} DSN (via {alias}) containing prod "
            f"literal(s) {hits} — a non-prod instance must point every slice at its own twins. "
            "Fix the environment (dev store / config), never bypass this gate."
        )


# ── the seven slices ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DatabaseProfile:
    """DSN resolution stays in runtime_app (safebox-backed); this slice carries the POLICY:
    which planes exist and whether non-VPS/remote access is allowed for this instance."""

    allow_postgres_outside_vps: bool
    require_role_gates: bool  # False only for the hermetic profile


@dataclass(frozen=True)
class SecretAuthority:
    mode: Literal["remote", "local", "stub"]
    url: str  # the safebox base URL this instance talks to ("" for local/stub)


@dataclass(frozen=True)
class SpendPolicy:
    mode: Literal["live", "test", "stub"]  # dev runs REAL gates with test-mode keys; stub = hermetic only


@dataclass(frozen=True)
class HostMap:
    operator: str
    subuser: str
    safebox: str


@dataclass(frozen=True)
class DomainProfile:
    company_base: str      # e.g. coscale.app
    dashboard_host: str    # e.g. app.fourmanifold.com


@dataclass(frozen=True)
class InfraPolicy:
    systemd: bool
    r2: bool
    auth0: bool
    dns: bool


@dataclass(frozen=True)
class WorkerProfile:
    concurrency: int
    poll_seconds: float
    stale_seconds: int


@dataclass(frozen=True)
class RuntimeContext:
    name: str  # 'prod' | 'dev' | 'hermetic'  (TAKYON_ENV)
    home: Path
    host_role: str  # canonical view at boot
    db: DatabaseProfile
    secrets: SecretAuthority
    providers: SpendPolicy
    hosts: HostMap
    domains: DomainProfile
    infra: InfraPolicy
    worker: WorkerProfile
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_prod(self) -> bool:
        return self.name == "prod"

    @classmethod
    def from_env(cls, overrides: Mapping[str, Any] | None = None) -> "RuntimeContext":
        """Build the context ONCE at process boot from the canonical sources (env + config).
        This is deliberately the only place these load-bearing values are assembled."""

        def _env(name: str, default: str = "") -> str:
            return str(os.getenv(name) or default).strip()

        def _env_int(name: str, default: int) -> int:
            raw = _env(name)
            try:
                return int(raw) if raw else default
            except ValueError:
                return default

        def _env_float(name: str, default: float) -> float:
            raw = _env(name)
            try:
                return float(raw) if raw else default
            except ValueError:
                return default

        name = _env("TAKYON_ENV", "prod").lower() or "prod"
        if name not in ("prod", "dev", "hermetic"):
            raise ValueError(f"TAKYON_ENV must be prod|dev|hermetic (got {name!r})")

        home_raw = _env("TAKYON_HOME")
        home = Path(home_raw) if home_raw else Path.home() / ".takyon"

        ctx = cls(
            name=name,
            home=home,
            host_role=HostRole.canonical(),
            db=DatabaseProfile(
                allow_postgres_outside_vps=_env("TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS") == "1",
                require_role_gates=(name != "hermetic"),
            ),
            secrets=SecretAuthority(
                mode="stub" if name == "hermetic" else ("local" if HostRole.canonical() == "safebox" else "remote"),
                url=_env("TAKYON_SAFEBOX_URL"),
            ),
            providers=SpendPolicy(
                mode="stub" if name == "hermetic" else ("test" if name == "dev" else "live")
            ),
            hosts=HostMap(
                operator=_env("TAKYON_OPERATOR_HOST", "137.184.75.57" if name == "prod" else ""),
                subuser=_env("TAKYON_SUBUSER_HOST", "134.209.123.8" if name == "prod" else ""),
                safebox=_env("TAKYON_SAFEBOX_HOST", "10.116.0.2" if name == "prod" else ""),
            ),
            domains=DomainProfile(
                company_base=_env("PUBLIC_COMPANY_BASE_DOMAIN", "coscale.app" if name == "prod" else "localtest.me"),
                dashboard_host=_env("TAKYON_DASHBOARD_PUBLIC_HOST", "app.fourmanifold.com" if name == "prod" else ""),
            ),
            infra=InfraPolicy(
                systemd=(name == "prod"),
                r2=(name != "hermetic"),
                auth0=(name != "hermetic"),
                dns=(name != "hermetic"),
            ),
            worker=WorkerProfile(
                concurrency=_env_int("TAKYON_WORKER_CONCURRENCY", 2),
                poll_seconds=_env_float("TAKYON_WORKER_POLL_SECONDS", 15.0),
                stale_seconds=_env_int("TAKYON_WORKER_STALE_SECONDS", 900),
            ),
            extras=dict(overrides or {}),
        )
        assert_not_prod_leakage(ctx)
        return ctx


_CURRENT: ContextVar[RuntimeContext | None] = ContextVar("takyon_runtime_context", default=None)


def current_context() -> RuntimeContext:
    """The process's RuntimeContext; built lazily on first use so existing entrypoints that
    never call :func:`bind_context` keep today's behavior exactly (prod defaults)."""
    ctx = _CURRENT.get()
    if ctx is None:
        ctx = RuntimeContext.from_env()
        _CURRENT.set(ctx)
    return ctx


def bind_context(ctx: RuntimeContext) -> None:
    _CURRENT.set(ctx)


def cache_scope() -> str:
    """The environment key process-global caches must be partitioned by (plan R3: a dev
    instance must never read prod's cached secret/DSN). Cheap enough for hot paths."""
    ctx = _CURRENT.get()
    if ctx is None:
        # Do not force full context construction on hot cache paths before boot binds one:
        # the scope is derived from the same env the context would read.
        return f"{str(os.getenv('TAKYON_ENV') or 'prod').strip().lower() or 'prod'}|{str(os.getenv('TAKYON_HOME') or '').strip()}"
    return f"{ctx.name}|{ctx.home}"


def assert_not_prod_leakage(ctx: RuntimeContext) -> None:
    """Fail-loud boot gate (plan §3 UC3): a non-prod context must not point at any prod
    literal. Checks the resolved slices, not the whole environment (parsimony)."""
    if ctx.is_prod:
        return
    resolved = " ".join(
        str(v)
        for v in (
            ctx.secrets.url,
            ctx.hosts.operator,
            ctx.hosts.subuser,
            ctx.hosts.safebox,
            ctx.domains.dashboard_host,
            os.getenv("TAKYON_OPERATOR_DATABASE_URL") or "",
            os.getenv("TAKYON_APP_DATABASE_URL") or "",
            os.getenv("DATABASE_URL") or "",
            # The dev slices this context would actually resolve (UC3): a dev instance whose
            # TAKYON_DEV_* twin points at prod must refuse at boot, not at first query.
            *(
                os.getenv(alias) or ""
                for aliases in DEV_DATABASE_PLANE_ENV.values()
                for alias in aliases
            ),
        )
    )
    hits = sorted({lit for lit in PROD_LITERALS if lit and lit in resolved})
    if hits:
        raise ProdLeakage(
            f"RuntimeContext(name={ctx.name!r}) resolves prod literal(s) {hits} — a non-prod "
            "instance must point every slice at its own twins. Fix the environment (dev env "
            "file / config), never bypass this gate."
        )
