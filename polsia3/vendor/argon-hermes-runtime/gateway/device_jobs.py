"""In-process Mac device job queue for one-user VPS deployments."""

from __future__ import annotations

import os
import threading
import time
import uuid
from copy import deepcopy
from typing import Any, Dict, Iterable, Optional


TERMINAL_STATUSES = {"completed", "failed", "cancelled", "timed_out"}


def _now() -> float:
    return time.time()


def _terminal_retention_seconds() -> float:
    return max(60.0, float(os.getenv("ARGON_DEVICE_JOB_TERMINAL_TTL_SECONDS", "1800") or 1800))


def _clean_payload(value: Any) -> Any:
    """Keep queued job data JSON-shaped and bounded."""
    if isinstance(value, dict):
        return {str(k): _clean_payload(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean_payload(v) for v in value[:500]]
    if isinstance(value, tuple):
        return [_clean_payload(v) for v in value[:500]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > 500_000:
            return value[:500_000]
        return value
    return str(value)


class DeviceJobStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def create(
        self,
        *,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        device_id: str = "local-mac",
        timeout_seconds: float = 90.0,
        created_by: str = "agent",
    ) -> Dict[str, Any]:
        now = _now()
        timeout = max(5.0, min(float(timeout_seconds or 90.0), 300.0))
        job_id = f"device_job_{uuid.uuid4().hex[:24]}"
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
        }
        with self._lock:
            self._sweep_locked(now)
            self._jobs[job_id] = job
            return deepcopy(job)

    def claim(self, *, device_id: str, capabilities: Optional[Iterable[str]] = None) -> Optional[Dict[str, Any]]:
        now = _now()
        wanted_device = (device_id or "local-mac").strip() or "local-mac"
        allowed = {str(item) for item in capabilities or [] if str(item)}
        with self._lock:
            self._sweep_locked(now)
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
                return deepcopy(job)
        return None

    def complete(
        self,
        *,
        job_id: str,
        status: str,
        result: Any = None,
        error: Any = None,
        device_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        normalized = status if status in TERMINAL_STATUSES else "failed"
        now = _now()
        with self._lock:
            self._sweep_locked(now)
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job.get("status") in TERMINAL_STATUSES:
                return deepcopy(job)
            if device_id:
                job["completed_by_device_id"] = device_id
            job["status"] = normalized
            job["result"] = _clean_payload(result)
            job["error"] = _clean_payload(error)
            job["completed_at"] = now
            job["updated_at"] = now
            return deepcopy(job)

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._sweep_locked(_now())
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def wait(self, job_id: str, timeout_seconds: float) -> Optional[Dict[str, Any]]:
        deadline = _now() + max(1.0, min(float(timeout_seconds or 90.0), 310.0))
        while _now() < deadline:
            job = self.get(job_id)
            if not job:
                return None
            if job.get("status") in TERMINAL_STATUSES:
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
                job["error"] = {"message": "Mac device job timed out"}
                job["updated_at"] = now
                job["completed_at"] = now

    def _sweep_locked(self, now: float) -> None:
        terminal_ttl = _terminal_retention_seconds()
        for job_id in list(self._jobs):
            job = self._jobs[job_id]
            if job.get("status") in TERMINAL_STATUSES:
                completed_at = float(job.get("completed_at") or job.get("updated_at") or now)
                if now - completed_at > terminal_ttl:
                    self._jobs.pop(job_id, None)
                continue
            if float(job.get("deadline_at") or 0.0) <= now:
                job["status"] = "timed_out"
                job["error"] = {"message": "Mac device job timed out"}
                job["updated_at"] = now
                job["completed_at"] = now


_STORE = DeviceJobStore()


def get_device_job_store() -> DeviceJobStore:
    return _STORE
