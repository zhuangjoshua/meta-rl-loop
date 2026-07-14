"""Durable, code-owned state machine for one fresh-business bootstrap job."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Sequence


BOOTSTRAP_PHASES = (
    "preflight",
    "brief",
    "surface",
    "landing_build_publish",
    "search",
    "logo",
    "final_workflow_build_publish",
    "mobile",
    "finalize",
)


PHASE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "preflight": frozenset(),
    "brief": frozenset(
        {
            "business_list_files",
            "business_read_business",
            "business_read_file",
            "business_write_file",
            "skill_read_resource",
        }
    ),
    "surface": frozenset(
        {
            "business_list_files",
            "business_read_business",
            "business_read_file",
            "business_upsert_app_plan",
            "business_upsert_app_surface_contract",
        }
    ),
    "landing_build_publish": frozenset(
        {
            "business_generate_site_image",
            "business_list_files",
            "business_patch_file",
            "business_read_business",
            "business_read_file",
            "business_refresh_product_surface",
            "business_write_file",
            "skill_read_resource",
        }
    ),
    "search": frozenset(
        {
            "business_patch_file",
            "business_register_search_console",
            "business_write_file",
        }
    ),
    "logo": frozenset(
        {
            "business_generate_logo",
            "business_list_files",
            "business_patch_file",
            "business_read_business",
            "business_read_file",
            "business_write_file",
            "skill_read_resource",
        }
    ),
    "final_workflow_build_publish": frozenset(
        {
            "business_check_runtime_capabilities",
            "business_list_files",
            "business_patch_file",
            "business_read_business",
            "business_read_file",
            "business_refresh_product_surface",
            "business_upsert_app_surface_contract",
            "business_write_file",
            "skill_read_resource",
        }
    ),
    "mobile": frozenset(
        {
            "business_list_files",
            "business_patch_file",
            "business_publish_mobile_release",
            "business_read_business",
            "business_read_file",
            "business_read_store_status",
            "business_write_file",
            "skill_read_resource",
        }
    ),
    "finalize": frozenset({"business_read_business"}),
}


PHASE_MAX_TURNS = {
    "brief": 12,
    "surface": 10,
    "landing_build_publish": 36,
    "search": 10,
    "logo": 16,
    "final_workflow_build_publish": 60,
    "mobile": 60,
    "finalize": 8,
}


PHASE_REQUIRED_SKILLS: dict[str, frozenset[str]] = {
    "brief": frozenset({"design-taste-frontend"}),
    "landing_build_publish": frozenset(
        {"design-taste-frontend", "takyon-product"}
    ),
    "logo": frozenset({"takyon-brand-logo"}),
    "final_workflow_build_publish": frozenset(
        {"design-taste-frontend", "takyon-app-runtime", "takyon-product"}
    ),
    "mobile": frozenset({"takyon-mobile-app"}),
}


class BootstrapPhaseError(RuntimeError):
    """The durable bootstrap phase contract was violated."""


@dataclass(frozen=True)
class AuthoritativePhaseEvidence:
    """Evidence produced by runtime validators, never by assistant prose."""

    source: str
    details: Mapping[str, Any]

    def as_json(self) -> dict[str, Any]:
        if not self.source or self.source.startswith("model"):
            raise BootstrapPhaseError("bootstrap evidence must be runtime-authoritative")
        return {
            "verified": True,
            "source": self.source,
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "details": _json_object(self.details, label="phase evidence"),
        }


@dataclass(frozen=True)
class BootstrapPhaseRun:
    job_id: str
    sdk_session_id: str
    owner_user_id: str
    business_slug: str
    immutable_inputs: Mapping[str, Any]
    phase_idempotency: Mapping[str, Any]
    current_phase: str | None
    completed_phases: tuple[str, ...]
    phase_evidence: Mapping[str, Any]
    phase_receipts: Mapping[str, Any]
    phase_attempts: Mapping[str, Any]
    status: str


def _json_object(value: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    try:
        encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise BootstrapPhaseError(f"{label} must be JSON-safe") from exc
    if not isinstance(decoded, dict):
        raise BootstrapPhaseError(f"{label} must be an object")
    return decoded


def _input_digest(inputs: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    normalized = _json_object(inputs, label="bootstrap immutable inputs")
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    return normalized, hashlib.sha256(encoded).hexdigest()


def bootstrap_phase_idempotency(job_id: str) -> dict[str, dict[str, str]]:
    prefix = f"bootstrap:{uuid.UUID(str(job_id))}"
    return {
        "preflight": {"checkpoint": f"{prefix}:preflight"},
        "brief": {
            "artifact": f"{prefix}:brief",
            "operator_update": f"{prefix}:brief-update",
        },
        "surface": {
            "contract": f"{prefix}:surface-contract",
            "plan": f"{prefix}:surface-plan",
            "operator_update": f"{prefix}:surface-update",
        },
        "landing_build_publish": {
            "publish": f"{prefix}:landing-publish",
            "operator_update": f"{prefix}:landing-update",
        },
        "search": {
            "register": f"{prefix}:search-console",
            "operator_update": f"{prefix}:search-update",
        },
        "logo": {
            "generate": f"{prefix}:logo",
            "operator_update": f"{prefix}:logo-update",
        },
        "final_workflow_build_publish": {
            "contract": f"{prefix}:final-contract",
            "publish": f"{prefix}:final-publish",
            "operator_update": f"{prefix}:final-product-update",
        },
        "mobile": {
            "release_1": f"{prefix}:mobile-release-1",
            "release_2": f"{prefix}:mobile-release-2",
            "release_3": f"{prefix}:mobile-release-3",
            "operator_update": f"{prefix}:mobile-update",
        },
        "finalize": {
            "operator_update": f"{prefix}:finalize",
            "operator_update_completed": f"{prefix}:finalize-completed",
        },
    }


class PostgresBootstrapPhaseStore:
    """Serialized phase checkpoints for one durable queue job."""

    def __init__(
        self,
        *,
        operator_user_id: str,
        business_slug: str,
        connection_factory: Callable[[], Any] | None = None,
    ) -> None:
        try:
            self.operator_user_id = str(uuid.UUID(str(operator_user_id)))
        except (TypeError, ValueError, AttributeError) as exc:
            raise BootstrapPhaseError(
                "bootstrap phase store requires an operator UUID"
            ) from exc
        self.business_slug = str(business_slug or "").strip()
        if not self.business_slug:
            raise BootstrapPhaseError(
                "bootstrap phase store requires a business slug"
            )
        self._connection_factory = connection_factory

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        if self._connection_factory is not None:
            candidate = self._connection_factory()
            if hasattr(candidate, "__enter__"):
                with candidate as conn:
                    yield conn
            else:
                yield candidate
            return
        from .core import TakyonStore

        store = TakyonStore(operator_user_id=self.operator_user_id)
        with store._connect() as conn:
            with store._leaf_conn(conn) as raw:
                yield raw

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        if row is None:
            raise BootstrapPhaseError("bootstrap phase row disappeared")
        if isinstance(row, Mapping):
            return dict(row)
        columns = (
            "job_id", "sdk_session_id", "owner_user_id", "business_slug",
            "immutable_inputs", "phase_idempotency", "current_phase",
            "completed_phases", "phase_evidence", "phase_receipts",
            "phase_attempts", "status",
        )
        return dict(zip(columns, row, strict=True))

    @staticmethod
    def _decode(value: Any, default: Any) -> Any:
        if value is None:
            return default
        return json.loads(value) if isinstance(value, str) else value

    @classmethod
    def _run(cls, row: Any) -> BootstrapPhaseRun:
        data = cls._row(row)
        completed = cls._decode(data.get("completed_phases"), [])
        return BootstrapPhaseRun(
            job_id=str(data.get("job_id") or ""),
            sdk_session_id=str(data.get("sdk_session_id") or ""),
            owner_user_id=str(data.get("owner_user_id") or ""),
            business_slug=str(data.get("business_slug") or ""),
            immutable_inputs=cls._decode(data.get("immutable_inputs"), {}),
            phase_idempotency=cls._decode(data.get("phase_idempotency"), {}),
            current_phase=str(data.get("current_phase") or "") or None,
            completed_phases=tuple(str(item) for item in completed),
            phase_evidence=cls._decode(data.get("phase_evidence"), {}),
            phase_receipts=cls._decode(data.get("phase_receipts"), {}),
            phase_attempts=cls._decode(data.get("phase_attempts"), {}),
            status=str(data.get("status") or "running"),
        )

    def _select(self, conn: Any, job_id: str, *, lock: bool) -> Any:
        suffix = " for update" if lock else ""
        return conn.execute(
            "select job_id, sdk_session_id, owner_user_id, business_slug, "
            "immutable_inputs, phase_idempotency, current_phase, completed_phases, "
            "phase_evidence, phase_receipts, phase_attempts, status "
            "from public.bootstrap_phase_runs where job_id = %s::uuid "
            "and owner_user_id = %s::uuid and business_slug = %s" + suffix,
            (job_id, self.operator_user_id, self.business_slug),
        ).fetchone()

    def _scoped_update(
        self, sql: str, params: Sequence[Any], job_id: str
    ) -> tuple[str, tuple[Any, ...]]:
        """Append the bound tenant predicate to one phase-row mutation."""

        return (
            sql + " and owner_user_id = %s::uuid and business_slug = %s",
            (*params, job_id, self.operator_user_id, self.business_slug),
        )

    def initialize_or_load(
        self,
        *,
        job_id: str,
        sdk_session_id: str,
        owner_user_id: str,
        business_slug: str,
        immutable_inputs: Mapping[str, Any],
        job_attempt: int,
    ) -> BootstrapPhaseRun:
        job_uuid = str(uuid.UUID(str(job_id)))
        session_uuid = str(uuid.UUID(str(sdk_session_id)))
        owner_uuid = str(uuid.UUID(str(owner_user_id)))
        if owner_uuid != self.operator_user_id:
            raise BootstrapPhaseError(
                "bootstrap phase owner does not match the bound operator"
            )
        if str(business_slug or "").strip() != self.business_slug:
            raise BootstrapPhaseError(
                "bootstrap phase business does not match the bound business"
            )
        attempt = max(1, int(job_attempt))
        normalized, digest = _input_digest(immutable_inputs)
        idempotency = bootstrap_phase_idempotency(job_uuid)
        with self._connection() as conn, conn.transaction():
            owned = conn.execute(
                "select 1 from public.businesses where slug = %s "
                "and owner_user_id = %s::uuid for share",
                (business_slug, owner_uuid),
            ).fetchone()
            if owned is None:
                raise BootstrapPhaseError(
                    "bootstrap phase business ownership check failed"
                )
            conn.execute(
                "insert into public.bootstrap_phase_runs "
                "(job_id, sdk_session_id, owner_user_id, business_slug, input_sha256, "
                "immutable_inputs, phase_plan, phase_idempotency, current_phase, "
                "first_job_attempt, last_job_attempt) "
                "values (%s::uuid, %s::uuid, %s::uuid, %s, %s, %s::jsonb, %s::jsonb, "
                "%s::jsonb, %s, %s, %s) on conflict (job_id) do nothing",
                (
                    job_uuid, session_uuid, owner_uuid, business_slug, digest,
                    json.dumps(normalized), json.dumps(BOOTSTRAP_PHASES),
                    json.dumps(idempotency), BOOTSTRAP_PHASES[0], attempt, attempt,
                ),
            )
            row = self._select(conn, job_uuid, lock=True)
            run = self._run(row)
            if (
                run.sdk_session_id != session_uuid
                or run.owner_user_id != owner_uuid
                or run.business_slug != business_slug
                or dict(run.immutable_inputs) != normalized
                or dict(run.phase_idempotency) != idempotency
            ):
                raise BootstrapPhaseError(
                    "bootstrap retry changed immutable job/session/business inputs"
                )
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set last_job_attempt = greatest(last_job_attempt, %s) "
                "where job_id = %s::uuid",
                (attempt,),
                job_uuid,
            )
            conn.execute(sql, params)
            return run

    def load(self, job_id: str) -> BootstrapPhaseRun:
        with self._connection() as conn, conn.transaction():
            return self._run(self._select(conn, str(uuid.UUID(str(job_id))), lock=True))

    def start_phase(self, job_id: str, phase: str, *, job_attempt: int) -> BootstrapPhaseRun:
        if phase not in BOOTSTRAP_PHASES:
            raise BootstrapPhaseError(f"unknown bootstrap phase {phase!r}")
        job_uuid = str(uuid.UUID(str(job_id)))
        with self._connection() as conn, conn.transaction():
            run = self._run(self._select(conn, job_uuid, lock=True))
            if run.current_phase != phase or phase in run.completed_phases:
                raise BootstrapPhaseError("bootstrap phase start is out of order")
            attempts = dict(run.phase_attempts)
            prior = attempts.get(phase) if isinstance(attempts.get(phase), Mapping) else {}
            attempts[phase] = {
                "calls": int(prior.get("calls") or 0) + 1,
                "last_job_attempt": max(1, int(job_attempt)),
                "last_started_at": datetime.now(timezone.utc).isoformat(),
            }
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set phase_attempts = %s::jsonb, "
                "last_job_attempt = greatest(last_job_attempt, %s) where job_id = %s::uuid",
                (json.dumps(attempts), max(1, int(job_attempt))),
                job_uuid,
            )
            conn.execute(sql, params)
            return run

    def record_tool_receipt(
        self,
        job_id: str,
        phase: str,
        *,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
    ) -> None:
        if tool_name not in PHASE_ALLOWED_TOOLS.get(phase, frozenset()):
            raise BootstrapPhaseError(
                f"tool {tool_name!r} is outside bootstrap phase {phase!r}"
            )
        job_uuid = str(uuid.UUID(str(job_id)))
        try:
            parsed = json.loads(str(result or ""))
        except json.JSONDecodeError:
            parsed = {"success": False, "error": str(result or "")[:1000]}
        if not isinstance(parsed, Mapping):
            parsed = {"success": False, "error": "tool returned a non-object receipt"}
        with self._connection() as conn, conn.transaction():
            run = self._run(self._select(conn, job_uuid, lock=True))
            if run.current_phase != phase:
                return
            allowed_keys = {
                str(value)
                for value in dict(run.phase_idempotency.get(phase) or {}).values()
            }
            observed_key = str(args.get("idempotency_key") or "")
            if observed_key and observed_key not in allowed_keys:
                raise BootstrapPhaseError("phase tool used an unbound idempotency key")
            safe = {
                "tool": tool_name,
                "idempotency_key": observed_key,
                "success": bool(parsed.get("success")),
                "status": str(parsed.get("status") or "")[:200],
                "error": str(parsed.get("error") or "")[:1000],
                "build_id": str(parsed.get("build_id") or "")[:200],
                "receipt": str(parsed.get("receipt") or "")[:500],
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "result_sha256": hashlib.sha256(str(result or "").encode()).hexdigest(),
            }
            receipts = dict(run.phase_receipts)
            phase_receipts = list(receipts.get(phase) or [])
            phase_receipts.append(safe)
            receipts[phase] = phase_receipts[-20:]
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set phase_receipts = %s::jsonb "
                "where job_id = %s::uuid",
                (json.dumps(receipts),),
                job_uuid,
            )
            conn.execute(sql, params)

    def record_operator_update_receipt(
        self,
        job_id: str,
        phase: str,
        *,
        args: Mapping[str, Any],
        result: str,
    ) -> None:
        """Record the deterministic runtime-owned customer milestone update."""

        job_uuid = str(uuid.UUID(str(job_id)))
        try:
            parsed = json.loads(str(result or ""))
        except json.JSONDecodeError as exc:
            raise BootstrapPhaseError("phase operator update returned invalid JSON") from exc
        if not isinstance(parsed, Mapping) or not bool(parsed.get("success")):
            raise BootstrapPhaseError(
                "phase operator update did not produce a successful durable receipt"
            )
        with self._connection() as conn, conn.transaction():
            run = self._run(self._select(conn, job_uuid, lock=True))
            if run.current_phase != phase and phase not in run.completed_phases:
                return
            observed = str(args.get("idempotency_key") or "")
            phase_keys = run.phase_idempotency.get(phase) or {}
            expected = {
                str(phase_keys.get("operator_update") or ""),
                str(phase_keys.get("operator_update_completed") or ""),
            } - {""}
            if observed not in expected:
                raise BootstrapPhaseError("phase operator update key is not runtime-bound")
            receipts = dict(run.phase_receipts)
            phase_receipts = list(receipts.get(phase) or [])
            phase_receipts.append(
                {
                    "tool": "business_post_operator_update",
                    "idempotency_key": observed,
                    "success": True,
                    "status": "completed",
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "result_sha256": hashlib.sha256(str(result).encode()).hexdigest(),
                }
            )
            receipts[phase] = phase_receipts[-20:]
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set phase_receipts = %s::jsonb "
                "where job_id = %s::uuid",
                (json.dumps(receipts),),
                job_uuid,
            )
            conn.execute(sql, params)

    def record_runtime_completion(
        self,
        job_id: str,
        phase: str,
        *,
        runtime_receipt: Mapping[str, Any] | None = None,
    ) -> None:
        """Persist a natural runtime completion, distinct from assistant text."""

        job_uuid = str(uuid.UUID(str(job_id)))
        raw = dict(runtime_receipt or {})
        skill_receipt = (
            raw.get("skill_receipt")
            if isinstance(raw.get("skill_receipt"), Mapping)
            else {}
        )

        def receipt_skills(field: str) -> list[str]:
            values = skill_receipt.get(field, [])
            if not isinstance(values, Sequence) or isinstance(
                values, (str, bytes, bytearray)
            ):
                return []
            return [
                str(value)[:200]
                for value in values
                if str(value or "").strip()
            ][:50]

        safe = {
            "tool": "__primary_agent_runtime__",
            "success": True,
            "status": "completed",
            "session_id": str(raw.get("session_id") or "")[:100],
            "invocation_id": str(raw.get("invocation_id") or "")[:100],
            "skills_attempted": receipt_skills("attempted"),
            "skills_invoked": receipt_skills("invoked"),
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._connection() as conn, conn.transaction():
            run = self._run(self._select(conn, job_uuid, lock=True))
            if run.current_phase != phase:
                return
            receipts = dict(run.phase_receipts)
            phase_receipts = list(receipts.get(phase) or [])
            phase_receipts.append(safe)
            receipts[phase] = phase_receipts[-20:]
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set phase_receipts = %s::jsonb "
                "where job_id = %s::uuid",
                (json.dumps(receipts),),
                job_uuid,
            )
            conn.execute(sql, params)

    def complete_phase(
        self, job_id: str, phase: str, evidence: AuthoritativePhaseEvidence
    ) -> BootstrapPhaseRun:
        payload = evidence.as_json()
        job_uuid = str(uuid.UUID(str(job_id)))
        with self._connection() as conn, conn.transaction():
            run = self._run(self._select(conn, job_uuid, lock=True))
            if phase in run.completed_phases:
                return run
            if run.current_phase != phase:
                raise BootstrapPhaseError("bootstrap phase completion is out of order")
            completed = [*run.completed_phases, phase]
            next_index = len(completed)
            next_phase = BOOTSTRAP_PHASES[next_index] if next_index < len(BOOTSTRAP_PHASES) else None
            phase_evidence = dict(run.phase_evidence)
            phase_evidence[phase] = payload
            status = "completed" if next_phase is None else "running"
            sql, params = self._scoped_update(
                "update public.bootstrap_phase_runs set completed_phases = %s::jsonb, "
                "phase_evidence = %s::jsonb, current_phase = %s, status = %s "
                "where job_id = %s::uuid",
                (json.dumps(completed), json.dumps(phase_evidence), next_phase, status),
                job_uuid,
            )
            conn.execute(sql, params)
            return self._run(self._select(conn, job_uuid, lock=False))

    def reconcile_first_incomplete(
        self,
        job_id: str,
        verifier: Callable[[BootstrapPhaseRun, str], AuthoritativePhaseEvidence | None],
    ) -> BootstrapPhaseRun:
        """Revalidate and checkpoint effects that committed before a worker crash."""

        while True:
            run = self.load(job_id)
            phase = run.current_phase
            if phase is None:
                return run
            evidence = verifier(run, phase)
            if evidence is None:
                return run
            self.complete_phase(job_id, phase, evidence)


def phase_prompt(
    run: BootstrapPhaseRun,
    phase: str,
    *,
    public_site_url: str,
    animations: bool,
) -> str:
    """One bounded phase directive; later phases are deliberately absent."""

    inputs = dict(run.immutable_inputs)
    goal = str(inputs.get("goal") or "")
    business_name = str(inputs.get("business_name") or run.business_slug)
    workflow = bool(inputs.get("workflow_requested"))
    mobile = str(inputs.get("archetype") or "").lower() == "mobile_app"
    keys = dict(run.phase_idempotency.get(phase) or {})
    header = [
        f"Continue the same fresh-business launch for business:{run.business_slug}.",
        f"Current code-owned phase: {phase}.",
        f"Canonical business name: {business_name}",
        f"Business goal: {goal}",
        f"Explicit product workflow requested: {'yes' if workflow else 'no'}.",
        "Execute only this phase, use only the exposed capabilities, and stop when its authoritative tool/artifact outcome exists.",
        "Do not redo earlier phases. Do not install or enumerate skills. Never fake a receipt, provider result, publication, auth, subscription, record, or customer workflow.",
        "Use every idempotency key below exactly; a retry must reattach to the same effect, never mint a replacement unless three mobile repair keys are explicitly supplied.",
        "The operator_update key is reserved for the runtime-owned customer milestone; never pass it to a tool.",
        "Customer milestone updates are runtime-owned and rate-limited; do not attempt to post extra updates.",
        "Phase idempotency: " + json.dumps(keys, sort_keys=True),
        "",
    ]
    body: dict[str, Sequence[str]] = {
        "brief": (
            "Invoke design-taste-frontend for product/landing judgment, without web research.",
            "Derive the display name, tagline, ICP, problem, offer, value proposition, tone, and positioning from the idea alone.",
            "Write a truthful non-empty research/strategy.md; do not fabricate statistics, testimonials, partners, awards, or sourced claims.",
        ),
        "surface": (
            "Create the product surface contract with source_path product/site, runtime_features auth/account/profile/checkout, and routes /, /app, /app/profile.",
            "Use the one human display name from research/strategy.md, never the routing slug.",
            "If monthly paid, upsert the canonical monthly plan with included_ai_budget_microusd; do not leave checkout planless.",
            "Do not set bootstrap_final_product_pass in this phase.",
        ),
        "landing_build_publish": (
            "Invoke takyon-product and design-taste-frontend. Inspect the seeded product/site source.",
            "Customize the polished truthful landing and brand tokens while preserving PublicSiteHeader, canonical routing, src/lib/takyon.ts, src/lib/hooks.ts, /app, /app/profile, and support.",
            "Do not customize app-layout.tsx, app-home.tsx, or profile.tsx yet.",
            "Use reduced-motion-safe continuous product-relevant animation." if animations else "Animation is optional and must be reduced-motion safe.",
            "Refresh once with the exact publish idempotency key and require structured publish.status published plus a real public_url; on a deterministic blocker, record the exact blocker in research/strategy.md and stop.",
        ),
        "search": (
            f"Register the already-live URL-prefix property at {public_site_url} with business_register_search_console and the exact register key.",
            "On any exact Search Console blocker, record that blocker in research/strategy.md and stop this phase; this optional integration does not block later product work.",
        ),
        "logo": (
            "Invoke takyon-brand-logo, read the established name/category/tone from research/strategy.md, and generate the real logo with republish false and the exact key.",
            "A created receipt completes this phase. Only an explicit insufficient-credits or unconfigured-provider blocker may be recorded and carried forward with the seeded monogram.",
        ),
        "final_workflow_build_publish": (
            "Invoke takyon-product, takyon-app-runtime, and design-taste-frontend. Reinspect the existing product/site source.",
            "First upsert the same surface contract with bootstrap_final_product_pass true and workflow_completion_required true only when the requested workflow is explicit.",
            "Customize app-home.tsx and profile.tsx while preserving app-layout.tsx, canonical account/entitlement/cancellation hooks, landing, auth, checkout, profile, and support rails.",
            "When a workflow is requested, implement real product/site/actions/*.ts generation, one normalized validated result schema, useDecodedActionRunner UI wiring, and records persistence/reopen; no SDK keys, fake output, fixtures, localStorage authority, orphan action, or unsupported route.",
            "Do not invoke the app action: signed-in subscriber execution remains post-bootstrap verification.",
            "Refresh with the exact publish key and require a different live build than the landing baseline; record and stop on the exact deterministic blocker.",
        ),
        "mobile": (
            "Invoke takyon-mobile-app and turn seeded product/app into the real business app with deterministic install, typecheck, and Expo config green.",
            "Publish lane preview with release_1. Reattach with the same key after a crash/detach.",
            "Only after an authoritative errored build may repair and use release_2, then release_3; never exceed three total builds.",
            "A real build_id completes the phase. An exact compliance, credit, or eas_builder_unconfigured gate is an allowed truthful terminal mobile outcome and must be recorded in research/strategy.md.",
        ) if mobile else ("This non-mobile business has no mobile work; stop immediately.",),
        "finalize": (
            "Read the final business truth and return a concise customer-facing status.",
            "Name what is live and any exact allowed optional blocker. Do not claim signed-in workflow execution; that browser verification follows bootstrap.",
        ),
    }
    if phase not in body:
        raise BootstrapPhaseError(f"phase {phase!r} has no model prompt")
    return "\n".join([*header, *body[phase]])


__all__ = [
    "AuthoritativePhaseEvidence",
    "BOOTSTRAP_PHASES",
    "BootstrapPhaseError",
    "BootstrapPhaseRun",
    "PHASE_ALLOWED_TOOLS",
    "PHASE_MAX_TURNS",
    "PHASE_REQUIRED_SKILLS",
    "PostgresBootstrapPhaseStore",
    "bootstrap_phase_idempotency",
    "phase_prompt",
]
