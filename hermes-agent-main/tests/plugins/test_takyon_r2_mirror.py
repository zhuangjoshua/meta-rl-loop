"""Focused unit tests for the PUBLIC Cloudflare R2 product-site mirror
(`plugins.takyon.storage.write_public_site_to_r2` + `r2_configured`).

These are pure, offline tests: the R2 backend is a recording fake injected via ``backend=``, and
``r2_configured`` is exercised by monkeypatching the same env-backed / safebox config seam the real
backend reads. No boto3, no network.

What they pin (the contract the edge depends on):
  * key layout — every build file lands at ``<slug>/<build_id>/<rel>`` and the pointer at
    ``<slug>/current`` holds the build_id bytes;
  * pointer-last ordering — the ``<slug>/current`` flip is the LAST put, so a torn/partial upload is
    never pointed at by the edge;
  * digest space — each file is put with its sha256 (shared digest space across backends);
  * fail-closed config — ``r2_configured`` is true iff all four required R2 values are present, and
    is independent of ``R2_S3_REGION`` (which defaults to ``"auto"``).
"""

from __future__ import annotations

from pathlib import Path
import json

import pytest

from plugins.takyon import storage


class _RecordingR2Backend:
    """A stand-in for ``R2StorageBackend`` that records puts in call order (no network)."""

    name = "r2-fake"

    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes, *, digest: str) -> None:
        self.puts.append((key, data, digest))
        self.objects[key] = data

    def get(self, key: str) -> bytes:
        try:
            return self.objects[key]
        except KeyError as exc:
            raise storage.ObjectNotFound(key) from exc

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)


def _seed_dist(root: Path) -> None:
    """A realistic built static site: nested asset + index + a binary."""
    (root / "index.html").write_text("<!doctype html><title>acme</title>")
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "app.js").write_text("console.log('hi')")
    (root / "favicon.ico").write_bytes(b"\x00\x01\x02\x03")


def test_write_public_site_to_r2_key_layout(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "a" * 32  # valid 16–64 hex build id

    fake = _RecordingR2Backend()
    result = storage.write_public_site_to_r2("acme", build_id, dist, backend=fake)

    keys = [k for (k, _data, _dg) in fake.puts]
    # Every build file is keyed <slug>/<build_id>/<rel> (flat, public — no __takyon/ prefix).
    assert f"acme/{build_id}/index.html" in keys
    assert f"acme/{build_id}/assets/app.js" in keys
    assert f"acme/{build_id}/favicon.ico" in keys
    # Nothing escapes the <slug>/ namespace.
    assert all(k.startswith("acme/") for k in keys)

    # The pointer is <slug>/current and holds the build_id bytes.
    pointer_key = "acme/current"
    assert pointer_key in keys
    pointer_put = next(p for p in fake.puts if p[0] == pointer_key)
    assert pointer_put[1] == build_id.encode("utf-8")

    # Returned receipt reflects exactly what was uploaded.
    assert result["slug"] == "acme"
    assert result["build_id"] == build_id
    assert result["pointer_key"] == pointer_key
    assert set(result["files"]) == {"index.html", "assets/app.js", "favicon.ico"}


def test_pointer_is_written_last(tmp_path: Path) -> None:
    """The <slug>/current flip must be the FINAL put so the edge never resolves a half-uploaded build."""
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "b" * 40

    fake = _RecordingR2Backend()
    storage.write_public_site_to_r2("acme", build_id, dist, backend=fake)

    assert fake.puts, "expected at least the pointer put"
    last_key = fake.puts[-1][0]
    assert last_key == "acme/current"
    # No build-file put occurs after the pointer.
    pointer_index = next(i for i, p in enumerate(fake.puts) if p[0] == "acme/current")
    assert pointer_index == len(fake.puts) - 1


def test_database_activation_hook_runs_after_uploads_and_before_pointer(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "d" * 32
    events: list[str] = []

    class Backend(_RecordingR2Backend):
        def put(self, key: str, data: bytes, *, digest: str) -> None:
            super().put(key, data, digest=digest)
            events.append("pointer" if key == "acme/current" else "object")

    from contextlib import contextmanager

    @contextmanager
    def pointer_guard():
        events.append("guard-enter")
        try:
            yield
        finally:
            events.append("guard-exit")

    storage.write_public_site_to_r2(
        "acme",
        build_id,
        dist,
        backend=Backend(),
        before_pointer=lambda: events.append("database"),
        pointer_guard=pointer_guard,
    )

    assert events[-4:] == ["guard-enter", "database", "pointer", "guard-exit"]
    assert all(event == "object" for event in events[:-4])


def test_already_current_build_still_runs_database_activation_hook(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "e" * 32

    class Backend(_RecordingR2Backend):
        def get(self, key: str) -> bytes:
            if key == "acme/current":
                return build_id.encode("utf-8")
            raise storage.ObjectNotFound(key)

    backend = Backend()
    activated: list[bool] = []
    result = storage.write_public_site_to_r2(
        "acme",
        build_id,
        dist,
        backend=backend,
        before_pointer=lambda: activated.append(True),
    )

    assert activated == [True]
    assert backend.puts == []
    assert result["skipped"] == "pointer_already_current"


def test_put_digests_match_file_bytes(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "c" * 32

    fake = _RecordingR2Backend()
    storage.write_public_site_to_r2("acme", build_id, dist, backend=fake)

    for key, data, digest in fake.puts:
        if key == "acme/current":
            continue
        assert digest == storage.digest_bytes(data), key


def test_public_site_object_key_rejects_bad_build_id(tmp_path: Path) -> None:
    with pytest.raises(storage.UnsafePath):
        storage.public_site_object_key("acme", "not-hex!!", "index.html")
    with pytest.raises(storage.UnsafePath):
        # path traversal in the rel must be refused
        storage.public_site_object_key("acme", "a" * 32, "../secret")


def test_public_site_object_key_rejects_bad_slug() -> None:
    with pytest.raises(storage.UnsafePath):
        storage.public_site_object_key("../evil", "a" * 32, "index.html")


def test_restore_pointer_only_reverts_the_failed_build() -> None:
    class Backend(_RecordingR2Backend):
        def __init__(self, current: str) -> None:
            super().__init__()
            self.current = current

        def get(self, key: str) -> bytes:
            if key == "acme/current":
                if not self.current:
                    raise storage.ObjectNotFound(key)
                return self.current.encode("utf-8")
            return super().get(key)

        def put(self, key: str, data: bytes, *, digest: str) -> None:
            super().put(key, data, digest=digest)
            if key == "acme/current":
                self.current = data.decode("utf-8")

        def delete(self, key: str) -> None:
            super().delete(key)
            if key == "acme/current":
                self.current = ""

    failed = "b" * 32
    previous = "a" * 32
    backend = Backend(failed)
    restored = storage.restore_public_site_pointer_from_r2(
        "acme",
        failed_build_id=failed,
        previous_build_id=previous,
        backend=backend,
    )
    assert restored["restored"] is True
    assert backend.current == previous

    newer = Backend("c" * 32)
    untouched = storage.restore_public_site_pointer_from_r2(
        "acme",
        failed_build_id=failed,
        previous_build_id=previous,
        backend=newer,
    )
    assert untouched["restored"] is False
    assert untouched["status"] == "pointer_changed"
    assert newer.puts == []

    already_previous = Backend(previous)
    prior_descriptor = b'{"build_id":"0' + (b"0" * 30) + b'","servable_until":"2999-01-01T00:00:00Z"}\n'
    restored_descriptor = storage.restore_public_site_pointer_from_r2(
        "acme",
        failed_build_id=failed,
        previous_build_id=previous,
        prior_previous_pointer=prior_descriptor.decode(),
        backend=already_previous,
    )
    assert restored_descriptor["restored"] is True
    assert restored_descriptor["status"] == "current_already_previous"
    assert already_previous.objects["acme/previous"] == prior_descriptor


def test_activation_receipt_publishes_bounded_previous_before_current(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    current = "b" * 32
    previous = "a" * 32
    backend = _RecordingR2Backend()
    backend.objects["acme/current"] = previous.encode()
    deadline = "2999-01-01T00:00:00+00:00"

    result = storage.write_public_site_to_r2(
        "acme",
        current,
        dist,
        backend=backend,
        before_pointer=lambda: {
            "live_build_id": current,
            "previous_build_id": previous,
            "previous_servable_until": deadline,
        },
    )

    descriptor = json.loads(backend.objects["acme/previous"])
    assert descriptor == {"build_id": previous, "servable_until": deadline}
    assert backend.objects["acme/current"] == current.encode()
    pointer_puts = [key for key, _body, _digest in backend.puts if key in {"acme/previous", "acme/current"}]
    assert pointer_puts == ["acme/previous", "acme/current"]
    assert result["activation"]["previous_build_id"] == previous


def test_ambiguous_put_response_is_success_when_exact_readback_matches(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "f" * 32

    class Backend(_RecordingR2Backend):
        def put(self, key: str, data: bytes, *, digest: str) -> None:
            super().put(key, data, digest=digest)
            if key == "acme/current":
                raise RuntimeError("response lost after commit")

    backend = Backend()
    result = storage.write_public_site_to_r2("acme", build_id, dist, backend=backend)
    assert result["build_id"] == build_id
    assert backend.objects["acme/current"] == build_id.encode()


def test_pointer_failure_callback_runs_before_business_fence_releases(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _seed_dist(dist)
    build_id = "9" * 32
    events: list[str] = []

    class Backend(_RecordingR2Backend):
        def put(self, key: str, data: bytes, *, digest: str) -> None:
            if key == "acme/current":
                raise RuntimeError("pointer unavailable")
            super().put(key, data, digest=digest)

    from contextlib import contextmanager

    @contextmanager
    def guard():
        events.append("guard-enter")
        try:
            yield
        finally:
            events.append("guard-exit")

    with pytest.raises(storage.PointerStateAmbiguous):
        storage.write_public_site_to_r2(
            "acme",
            build_id,
            dist,
            backend=Backend(),
            before_pointer=lambda: {"live_build_id": build_id},
            pointer_guard=guard,
            on_pointer_failure=lambda _exc, _receipt: events.append("reconcile"),
        )
    assert events == ["guard-enter", "reconcile", "guard-exit"]


@pytest.fixture
def _clear_r2_env(monkeypatch):
    """Resolve R2 config from a controlled dict so the test is env-independent.

    ``_env_backed_config_value`` and ``_sensitive_config_value`` are the exact seam the real backend
    uses; patching them keeps the test honest (it exercises the same lookup) without touching boto3,
    the safebox, or the process env.
    """
    values: dict[str, str] = {}
    monkeypatch.setattr(storage, "_env_backed_config_value", lambda name: values.get(name, ""))
    monkeypatch.setattr(storage, "_sensitive_config_value", lambda name: values.get(name, ""))
    return values


def test_r2_configured_false_when_unset(_clear_r2_env) -> None:
    assert storage.r2_configured() is False


def test_r2_configured_true_when_all_present(_clear_r2_env) -> None:
    _clear_r2_env.update(
        {
            "R2_S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
            "R2_BUCKET": "product-sites",
            "R2_S3_ACCESS_KEY_ID": "ak",
            "R2_S3_SECRET_ACCESS_KEY": "sk",
        }
    )
    # Region is intentionally NOT required (defaults to "auto").
    assert storage.r2_configured() is True


def test_r2_configured_region_not_required(_clear_r2_env) -> None:
    _clear_r2_env.update(
        {
            "R2_S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
            "R2_BUCKET": "product-sites",
            "R2_S3_ACCESS_KEY_ID": "ak",
            "R2_S3_SECRET_ACCESS_KEY": "sk",
            # R2_S3_REGION deliberately absent
        }
    )
    assert storage.r2_configured() is True


@pytest.mark.parametrize(
    "drop",
    ["R2_S3_ENDPOINT", "R2_BUCKET", "R2_S3_ACCESS_KEY_ID", "R2_S3_SECRET_ACCESS_KEY"],
)
def test_r2_configured_false_when_any_required_missing(_clear_r2_env, drop) -> None:
    _clear_r2_env.update(
        {
            "R2_S3_ENDPOINT": "https://acct.r2.cloudflarestorage.com",
            "R2_BUCKET": "product-sites",
            "R2_S3_ACCESS_KEY_ID": "ak",
            "R2_S3_SECRET_ACCESS_KEY": "sk",
        }
    )
    _clear_r2_env.pop(drop)
    assert storage.r2_configured() is False


def test_r2_backend_unconfigured_raises(_clear_r2_env) -> None:
    """Constructing the real backend with no creds is the invariant-#8 block, not a silent no-op."""
    with pytest.raises(storage.StorageUnconfigured):
        storage.R2StorageBackend()
