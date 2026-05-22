"""Local tool job queue for Mac-local browser, artifact, and Codex tools."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from hermes_constants import get_hermes_home


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
RETURNABLE_STATUSES = TERMINAL_STATUSES | {"blocked"}


def _now() -> float:
    return time.time()


def _clean_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_payload(v) for v in value[:1000]]
    if isinstance(value, tuple):
        return [_clean_payload(v) for v in value[:1000]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 1_000_000:
            return value[:1_000_000]
        return value
    return str(value)


def _state_path() -> Path:
    explicit = os.getenv("ARGON_LOCAL_TOOL_JOBS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return get_hermes_home() / "local_tool_jobs.json"


def _terminal_retention_seconds() -> float:
    return max(60.0, float(os.getenv("ARGON_LOCAL_TOOL_JOB_TERMINAL_TTL_SECONDS", "1800") or 1800))


def _blocked_ttl_seconds() -> float:
    return max(300.0, float(os.getenv("ARGON_LOCAL_TOOL_JOB_BLOCKED_TTL_SECONDS", str(24 * 60 * 60)) or 24 * 60 * 60))


def _capability_key(item: Dict[str, Any]) -> tuple[str, str]:
    return (str(item.get("capability") or "").strip(), str(item.get("target") or "").strip())


class LocalToolJobStore:
    def __init__(self, state_path: Optional[Path] = None) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._capabilities: Dict[tuple[str, str, str], str] = {}
        self._state_path = state_path or _state_path()
        self._load()

    def create(
        self,
        *,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        device_id: str = "local-mac",
        timeout_seconds: float = 90.0,
        created_by: str = "agent",
        max_retries: int = 1,
    ) -> Dict[str, Any]:
        now = _now()
        timeout = max(5.0, min(float(timeout_seconds or 90.0), 1800.0))
        job_id = f"local_tool_job_{uuid.uuid4().hex[:24]}"
        job = {
            "job_id": job_id,
            "device_id": (device_id or "local-mac").strip() or "local-mac",
            "action": action,
            "payload": _clean_payload(payload or {}),
            "status": "queued",
            "created_by": created_by,
            "created_at": now,
            "updated_at": now,
            "claimed_at": None,
            "completed_at": None,
            "timeout_seconds": timeout,
            "deadline_at": now + timeout,
            "result": None,
            "error": None,
            "blocked_on": [],
            "blocked_at": None,
            "blocked_expires_at": None,
            "retry_count": 0,
            "max_retries": max(0, min(int(max_retries), 5)),
        }
        with self._lock:
            self._sweep_locked(now)
            self._jobs[job_id] = job
            self._persist_locked()
            return deepcopy(job)

    def claim(
        self,
        *,
        device_id: str,
        capabilities: Optional[Iterable[str]] = None,
        capability_status: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        now = _now()
        wanted_device = (device_id or "local-mac").strip() or "local-mac"
        allowed = {str(item) for item in capabilities or [] if str(item)}
        with self._lock:
            changed = self._sweep_locked(now)
            if capability_status:
                changed = self._record_capabilities_locked(wanted_device, capability_status, now) or changed
            for job in self._jobs.values():
                if job.get("status") != "queued":
                    continue
                job_device = str(job.get("device_id") or "local-mac")
                if job_device not in {wanted_device, "*"}:
                    continue
                if allowed and str(job.get("action") or "") not in allowed:
                    continue
                job["status"] = "claimed"
                job["claimed_at"] = now
                job["updated_at"] = now
                job["deadline_at"] = now + float(job.get("timeout_seconds") or 90.0)
                self._persist_locked()
                return deepcopy(job)
            if changed:
                self._persist_locked()
        return None

    def complete(
        self,
        *,
        job_id: str,
        status: str,
        result: Any = None,
        error: Any = None,
        blocked_on: Any = None,
        device_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        now = _now()
        normalized = status if status in RETURNABLE_STATUSES else "failed"
        with self._lock:
            self._sweep_locked(now)
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.get("status") in TERMINAL_STATUSES:
                return deepcopy(job)
            if device_id:
                job["completed_by_device_id"] = device_id
            if normalized == "blocked":
                requirements = self._extract_blocked_on(blocked_on, result, error)
                if not requirements:
                    normalized = "failed"
                else:
                    job["status"] = "blocked"
                    job["result"] = _clean_payload(result)
                    job["error"] = _clean_payload(error)
                    job["blocked_on"] = requirements
                    job["blocked_at"] = now
                    job["blocked_expires_at"] = now + _blocked_ttl_seconds()
                    job["claimed_at"] = None
                    job["completed_at"] = None
                    job["updated_at"] = now
                    self._persist_locked()
                    return deepcopy(job)
            job["status"] = normalized
            job["result"] = _clean_payload(result)
            job["error"] = _clean_payload(error)
            job["completed_at"] = now
            job["updated_at"] = now
            self._persist_locked()
            return deepcopy(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._sweep_locked(_now()):
                self._persist_locked()
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def wait(self, job_id: str, timeout_seconds: float) -> Optional[Dict[str, Any]]:
        deadline = _now() + max(1.0, min(float(timeout_seconds or 90.0), 1810.0))
        while _now() < deadline:
            job = self.get(job_id)
            if not job:
                return None
            if job.get("status") in RETURNABLE_STATUSES:
                return job
            time.sleep(0.35)
        self.mark_timed_out(job_id)
        return self.get(job_id)

    def mark_timed_out(self, job_id: str) -> None:
        now = _now()
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.get("status") not in TERMINAL_STATUSES:
                job["status"] = "timed_out"
                job["error"] = {"message": "Local tool job timed out"}
                job["updated_at"] = now
                job["completed_at"] = now
                self._persist_locked()

    def blocked_requirements(self, *, device_id: str) -> list[Dict[str, Any]]:
        wanted_device = (device_id or "local-mac").strip() or "local-mac"
        with self._lock:
            if self._sweep_locked(_now()):
                self._persist_locked()
            seen: set[str] = set()
            requirements: list[Dict[str, Any]] = []
            for job in self._jobs.values():
                if job.get("status") != "blocked":
                    continue
                job_device = str(job.get("device_id") or "local-mac")
                if job_device not in {wanted_device, "*"}:
                    continue
                for requirement in job.get("blocked_on") or []:
                    if not isinstance(requirement, dict):
                        continue
                    key = json.dumps(_clean_payload(requirement), sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    requirements.append(deepcopy(requirement))
            return requirements

    def snapshot_rows(self, *, device_id: str = "local-mac") -> list[Dict[str, Any]]:
        wanted_device = (device_id or "local-mac").strip() or "local-mac"
        with self._lock:
            if self._sweep_locked(_now()):
                self._persist_locked()
            rows: list[Dict[str, Any]] = []
            for job in self._jobs.values():
                job_device = str(job.get("device_id") or "local-mac")
                if job_device not in {wanted_device, "*"}:
                    continue
                rows.append(deepcopy(job))
            rows.sort(key=lambda row: float(row.get("updated_at") or row.get("created_at") or 0.0), reverse=True)
            return rows[:100]

    def _record_capabilities_locked(
        self,
        device_id: str,
        capability_status: Iterable[Dict[str, Any]],
        now: float,
    ) -> bool:
        changed = False
        for item in capability_status:
            if not isinstance(item, dict):
                continue
            capability, target = _capability_key(item)
            if not capability:
                continue
            status = str(item.get("status") or "").strip().lower()
            if status not in {"granted", "missing", "unknown"}:
                status = "unknown"
            self._capabilities[(device_id, capability, target)] = status
        for job in self._jobs.values():
            if job.get("status") != "blocked":
                continue
            job_device = str(job.get("device_id") or "local-mac")
            if job_device not in {device_id, "*"}:
                continue
            if int(job.get("retry_count") or 0) >= int(job.get("max_retries") or 1):
                continue
            requirements = [r for r in (job.get("blocked_on") or []) if isinstance(r, dict)]
            if not requirements:
                continue
            if all(self._requirement_satisfied_locked(device_id, requirement) for requirement in requirements):
                job["status"] = "queued"
                job["claimed_at"] = None
                job["completed_at"] = None
                job["result"] = None
                job["error"] = None
                job["retry_count"] = int(job.get("retry_count") or 0) + 1
                job["requeued_at"] = now
                job["updated_at"] = now
                job["deadline_at"] = now + float(job.get("timeout_seconds") or 90.0)
                changed = True
        return changed

    def _requirement_satisfied_locked(self, device_id: str, requirement: Dict[str, Any]) -> bool:
        capability, target = _capability_key(requirement)
        if not capability:
            return False
        status = self._capabilities.get((device_id, capability, target))
        if status is None:
            status = self._capabilities.get((device_id, capability, ""))
        return status == "granted"

    def _extract_blocked_on(self, explicit: Any, result: Any, error: Any) -> list[Dict[str, Any]]:
        candidates = explicit
        if candidates is None and isinstance(result, dict):
            candidates = result.get("blocked_on")
        if candidates is None and isinstance(error, dict):
            candidates = error.get("blocked_on")
        if not isinstance(candidates, list):
            return []
        cleaned: list[Dict[str, Any]] = []
        for item in candidates[:20]:
            if not isinstance(item, dict):
                continue
            capability = str(item.get("capability") or "").strip()
            if not capability:
                continue
            cleaned.append(_clean_payload({**item, "capability": capability}))
        return cleaned

    def _sweep_locked(self, now: float) -> bool:
        changed = False
        terminal_ttl = _terminal_retention_seconds()
        for job_id in list(self._jobs):
            job = self._jobs[job_id]
            status = str(job.get("status") or "")
            if status in TERMINAL_STATUSES:
                completed_at = float(job.get("completed_at") or job.get("updated_at") or now)
                if now - completed_at > terminal_ttl:
                    self._jobs.pop(job_id, None)
                    changed = True
                continue
            if status == "blocked":
                expires = float(job.get("blocked_expires_at") or 0.0)
                if expires and expires <= now:
                    job["status"] = "timed_out"
                    job["error"] = {"message": "Local tool job expired while blocked on capability"}
                    job["updated_at"] = now
                    job["completed_at"] = now
                    changed = True
                continue
            if float(job.get("deadline_at") or 0.0) <= now:
                job["status"] = "timed_out"
                job["error"] = {"message": "Local tool job timed out"}
                job["updated_at"] = now
                job["completed_at"] = now
                changed = True
        return changed

    def _load(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception:
            return
        jobs = data.get("jobs") if isinstance(data, dict) else data
        if not isinstance(jobs, list):
            return
        loaded: Dict[str, Dict[str, Any]] = {}
        for job in jobs:
            if not isinstance(job, dict):
                continue
            job_id = str(job.get("job_id") or "")
            if not job_id:
                continue
            loaded[job_id] = _clean_payload(job)
        self._jobs = loaded
        if self._sweep_locked(_now()):
            self._persist_locked()

    def _persist_locked(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            payload = {"schema_version": 1, "jobs": list(self._jobs.values())}
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(self._state_path)
        except Exception:
            pass


_STORE = LocalToolJobStore()


def get_local_tool_job_store() -> LocalToolJobStore:
    return _STORE
