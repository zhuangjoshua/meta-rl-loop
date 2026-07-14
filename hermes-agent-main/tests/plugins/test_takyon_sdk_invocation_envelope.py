from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from plugins.takyon import safebox_app, safebox_provider_proxy
from plugins.takyon.claude_sdk_runtime import stable_sdk_invocation_id
from plugins.takyon.safebox_capability import (
    CapabilityError,
    CapabilityScope,
    _b64url,
    _b64url_decode,
    mint_capability,
    verify_capability,
)


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _EnvelopeConn:
    def __init__(self, *, total=100, per_call=100, active=True):
        self.lock = threading.RLock()
        self.invocation_id = str(uuid.uuid4())
        self.owner = str(uuid.uuid4())
        self.business = "acme"
        self.total = total
        self.per_call = per_call
        self.expires = 2_000_000_000
        self.active = active
        self.calls: dict[str, dict] = {}
        self.fail_next_settle = False
        self.local_bypass_count = 0
        self._transaction_depth = 0
        self._local_bypass = False

    @contextmanager
    def transaction(self):
        with self.lock:
            previous = self._local_bypass
            if self._transaction_depth == 0:
                # Model a transaction-pool backend carrying an explicit bypass=0 from a prior user.
                self._local_bypass = False
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
                self._local_bypass = previous

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select set_config('takyon.rls_bypass', '1', true)"):
            assert self._transaction_depth > 0
            self._local_bypass = True
            self.local_bypass_count += 1
            return _Cursor(("1",))
        if not self._local_bypass:
            raise PermissionError("simulated transaction-pool RLS refusal")
        if normalized.startswith("select owner_user_id::text"):
            if str(params[0]) != self.invocation_id:
                return _Cursor(None)
            return _Cursor(
                (
                    self.owner,
                    self.business,
                    self.total,
                    self.per_call,
                    self.expires,
                    self.active,
                )
            )
        if normalized.startswith("select estimate_microusd, actual_microusd, status"):
            call = self.calls.get(str(params[1]))
            return _Cursor(
                None
                if call is None
                else (call["estimate"], call["actual"], call["status"])
            )
        if normalized.startswith("select coalesce(sum(case"):
            consumed = sum(
                call["estimate"]
                if call["status"] == "held"
                else (call["actual"] or 0 if call["status"] == "settled" else 0)
                for call in self.calls.values()
            )
            return _Cursor((consumed,))
        if normalized.startswith("insert into operator_sdk_invocation_calls"):
            _invocation, call_id, estimate = params
            self.calls[str(call_id)] = {
                "estimate": int(estimate),
                "actual": None,
                "status": "held",
            }
            return _Cursor()
        if normalized.startswith("select status from operator_sdk_invocation_calls"):
            call = self.calls.get(str(params[1]))
            return _Cursor(None if call is None else (call["status"],))
        if normalized.startswith("update operator_sdk_invocation_calls set status = 'settled'"):
            if self.fail_next_settle:
                self.fail_next_settle = False
                raise RuntimeError("simulated settlement outage")
            actual, _invocation, call_id = params
            self.calls[str(call_id)].update(status="settled", actual=int(actual))
            return _Cursor()
        if normalized.startswith("update operator_sdk_invocation_calls set status = 'released'"):
            _invocation, call_id = params
            self.calls[str(call_id)].update(status="released")
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


class _InvocationMintConn:
    def __init__(self):
        self.lock = threading.RLock()
        self.row = None
        self.business_owner = ""
        self.local_bypass_count = 0
        self._transaction_depth = 0
        self._local_bypass = False

    @contextmanager
    def transaction(self):
        with self.lock:
            previous = self._local_bypass
            if self._transaction_depth == 0:
                self._local_bypass = False
            self._transaction_depth += 1
            try:
                yield
            finally:
                self._transaction_depth -= 1
                self._local_bypass = previous

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).lower()
        if normalized.startswith("select set_config('takyon.rls_bypass', '1', true)"):
            assert self._transaction_depth > 0
            self._local_bypass = True
            self.local_bypass_count += 1
            return _Cursor(("1",))
        if not self._local_bypass:
            raise PermissionError("simulated transaction-pool RLS refusal")
        if normalized.startswith("select id from users where auth0_sub"):
            return _Cursor((self.business_owner,))
        if normalized.startswith("select owner_user_id from businesses"):
            return _Cursor((self.business_owner,))
        if normalized.startswith("insert into operator_sdk_invocations"):
            if self.row is None:
                invocation, owner, business, total, per_call, expires = params
                self.row = [
                    str(invocation),
                    str(owner),
                    str(business or ""),
                    int(total),
                    int(per_call),
                    int(expires),
                ]
            return _Cursor()
        if normalized.startswith("select owner_user_id::text"):
            if self.row is None or str(params[0]) != self.row[0]:
                return _Cursor(None)
            return _Cursor(tuple(self.row[1:]))
        if normalized.startswith("update operator_sdk_invocations set expires_at"):
            expires, invocation = params
            assert self.row is not None and str(invocation) == self.row[0]
            self.row[5] = int(expires)
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {sql}")


def _auth(conn: _EnvelopeConn, *, business=None):
    scope = CapabilityScope(
        takyon_user_id=conn.owner,
        business_slug=conn.business if business is None else business,
        app_user_id=None,
        action="operator.session",
        max_cost_microusd=conn.per_call,
        invocation_id=conn.invocation_id,
        max_total_cost_microusd=conn.total,
    )
    return safebox_provider_proxy._ProxyAuth(
        scope=scope,
        ceiling_microusd=conn.per_call,
        enforce_ceiling=True,
        via="capability:operator.session",
        invocation_id=conn.invocation_id,
        invocation_total_ceiling_microusd=conn.total,
        capability_expires_at=conn.expires,
    )


@pytest.fixture
def envelope_db(monkeypatch):
    conn = _EnvelopeConn()

    @contextmanager
    def factory():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", factory)
    return conn


def test_cumulative_total_refuses_before_crossing_ceiling(envelope_db) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    first = envelope.claim(_auth(envelope_db), 60)
    envelope.settle(first, 50)
    second = envelope.claim(_auth(envelope_db), 50)
    with pytest.raises(HTTPException) as error:
        envelope.claim(_auth(envelope_db), 1)
    assert error.value.status_code == 402
    assert second["status"] == "held"


def test_bootstrap_continuations_and_retries_share_one_cumulative_envelope(
    envelope_db,
) -> None:
    session_id = str(uuid.uuid4())
    first_invocation = stable_sdk_invocation_id(
        session_id=session_id, epoch="bootstrap:1"
    )
    retry_invocation = stable_sdk_invocation_id(
        session_id=session_id, epoch="bootstrap:3"
    )
    assert retry_invocation == first_invocation
    envelope_db.invocation_id = first_invocation

    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    first = envelope.claim(_auth(envelope_db), 60)
    envelope.settle(first, 50)
    with pytest.raises(HTTPException) as error:
        envelope.claim(_auth(envelope_db), 51)
    assert error.value.status_code == 402


def test_same_scope_retry_can_renew_expiry_without_resetting_envelope() -> None:
    conn = _InvocationMintConn()
    scope = CapabilityScope(
        takyon_user_id=str(uuid.uuid4()),
        business_slug="acme",
        app_user_id=None,
        action="operator.session",
        max_cost_microusd=50,
        invocation_id=str(uuid.uuid4()),
        max_total_cost_microusd=100,
    )
    now = int(time.time())
    first_expiry = safebox_app._ensure_operator_sdk_invocation(
        conn, scope=scope, expires_at=now + 60
    )
    assert first_expiry == now + 60
    assert conn.row is not None
    original_scope = tuple(conn.row[:5])

    # Simulate a delayed job retry after the prior capability expired. Fresh
    # ownership proof happens before this helper; only expiry may move.
    conn.row[5] = now - 1
    renewed_expiry = safebox_app._ensure_operator_sdk_invocation(
        conn, scope=scope, expires_at=now + 3600
    )
    assert renewed_expiry == now + 3600
    assert tuple(conn.row[:5]) == original_scope
    assert conn.local_bypass_count == 2


def test_sdk_mint_pins_ownership_proof_and_envelope_to_local_rls_authority(
    monkeypatch,
) -> None:
    conn = _InvocationMintConn()
    conn.business_owner = str(uuid.uuid4())

    @contextmanager
    def factory():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", factory)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, "test-signing-key")
    invocation_id = str(uuid.uuid4())
    now = int(time.time())
    token = safebox_app._mint_capability_token(
        business="acme",
        action="operator.session",
        max_cost_microusd=50,
        session_token=None,
        operator_user_id=conn.business_owner,
        audience="operator.session",
        ttl_seconds=60,
        now=now,
        invocation_id=invocation_id,
        max_total_cost_microusd=100,
    )

    scope, _, _ = verify_capability(
        token,
        signing_key=b"test-signing-key",
        expected_audience="operator.session",
        now=now + 1,
    )
    assert scope.invocation_id == invocation_id
    assert scope.takyon_user_id == conn.business_owner
    assert conn.local_bypass_count == 2


def test_non_invocation_mint_still_pins_authorization_to_local_rls_authority(
    monkeypatch,
) -> None:
    conn = _InvocationMintConn()
    conn.business_owner = str(uuid.uuid4())

    @contextmanager
    def factory():
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", factory)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, "test-signing-key")
    safebox_app._mint_capability_token(
        business="acme",
        action="operator.session",
        max_cost_microusd=50,
        session_token=None,
        operator_user_id=conn.business_owner,
        audience="operator.session",
        ttl_seconds=60,
        now=int(time.time()),
    )

    assert conn.row is None
    assert conn.local_bypass_count == 1


def test_root_sdk_mint_pins_user_proof_and_envelope_atomically(monkeypatch) -> None:
    conn = _InvocationMintConn()
    conn.business_owner = str(uuid.uuid4())
    connection_count = 0

    @contextmanager
    def factory():
        nonlocal connection_count
        connection_count += 1
        yield conn

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", factory)
    monkeypatch.setattr(
        safebox_app.safebox,
        "auth0_verify_session",
        lambda **_kwargs: {"sub": "auth0|owner"},
    )
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, "internal-token")
    monkeypatch.setenv(safebox_app._OPERATOR_TOKEN_ENV, "operator-token")
    monkeypatch.setenv(safebox_app._OPERATOR_CLIENTS_ENV, "testclient")
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, "test-signing-key")
    endpoint = next(
        route.endpoint
        for route in safebox_app.build_safebox_app().routes
        if getattr(route, "path", "") == "/v1/operator/session-token"
    )
    invocation_id = str(uuid.uuid4())
    result = endpoint(
        SimpleNamespace(
            client=SimpleNamespace(host="testclient"),
            headers={safebox_app._OPERATOR_TOKEN_HEADER: "operator-token"},
        ),
        safebox_app._OperatorSessionTokenBody(
            business="",
            session_token="dashboard-session",
            operator_user_id=conn.business_owner,
            max_cost_microusd=50,
            invocation_id=invocation_id,
            max_total_cost_microusd=100,
        ),
        authorization="Bearer internal-token",
    )

    assert result["invocation_id"] == invocation_id
    assert connection_count == 1
    assert conn.local_bypass_count == 2


def test_invocation_claim_and_finalizers_pin_transaction_local_rls_authority(
    envelope_db,
) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    claim = envelope.claim(_auth(envelope_db), 40)
    envelope.settle(claim, 25)
    released = envelope.claim(_auth(envelope_db), 30)
    envelope.release(released)

    assert envelope_db.local_bypass_count == 4


def test_concurrent_claims_are_serialized_under_one_total(envelope_db) -> None:
    envelope_db.total = 100
    envelope_db.per_call = 60
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    barrier = threading.Barrier(3)
    results: list[str] = []

    def claim():
        barrier.wait()
        try:
            envelope.claim(_auth(envelope_db), 60)
            results.append("ok")
        except HTTPException as exc:
            results.append(str(exc.status_code))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["402", "ok"]


def test_call_replay_and_finalizers_are_idempotent(envelope_db) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    call_id = str(uuid.uuid4())
    first = envelope.claim(_auth(envelope_db), 40, call_id=call_id)
    replay = envelope.claim(_auth(envelope_db), 40, call_id=call_id)
    assert replay["call_id"] == first["call_id"]
    assert len(envelope_db.calls) == 1
    envelope.settle(first, 25)
    envelope.settle(first, 25)
    with pytest.raises(RuntimeError, match="mismatch"):
        envelope.settle(first, 24)


def test_authenticated_sdk_call_id_is_stable_per_operation_and_payload(
    envelope_db,
) -> None:
    auth = _auth(envelope_db)
    first = safebox_provider_proxy._stable_sdk_call_id(
        auth,
        operation="anthropic.messages",
        payload={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
    )
    reordered = safebox_provider_proxy._stable_sdk_call_id(
        auth,
        operation="anthropic.messages",
        payload={"messages": [{"content": "hi", "role": "user"}], "model": "deepseek-v4-pro"},
    )
    changed_payload = safebox_provider_proxy._stable_sdk_call_id(
        auth,
        operation="anthropic.messages",
        payload={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "bye"}]},
    )
    changed_operation = safebox_provider_proxy._stable_sdk_call_id(
        auth,
        operation="openai.responses",
        payload={"model": "deepseek-v4-pro", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert str(uuid.UUID(str(first))) == first
    assert reordered == first
    assert changed_payload != first
    assert changed_operation != first


def test_replayed_sdk_call_is_refused_before_second_operator_reserve(
    envelope_db, monkeypatch
) -> None:
    auth = _auth(envelope_db)
    payload = {"model": "deepseek-v4-pro", "messages": []}
    call_id = safebox_provider_proxy._stable_sdk_call_id(
        auth,
        operation="anthropic.messages",
        payload=payload,
    )
    safebox_provider_proxy._SdkInvocationEnvelope().claim(
        auth,
        40,
        call_id=call_id,
    )

    class _MustNotReserveAgain:
        def __init__(self):
            raise AssertionError("operator ledger must not be reached on SDK replay")

    monkeypatch.setattr(safebox_app, "_OperatorBudgetAdapter", _MustNotReserveAgain)

    with pytest.raises(HTTPException) as error:
        safebox_provider_proxy._reserve_or_refuse(
            auth,
            40,
            call_id=call_id,
        )

    assert error.value.status_code == 409
    assert error.value.headers == {"x-should-retry": "false"}
    assert error.value.detail == {
        "error": "operator_sdk_invocation_call_replay",
        "call_id": call_id,
    }
    assert len(envelope_db.calls) == 1


def test_failure_release_restores_capacity(envelope_db) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    failed = envelope.claim(_auth(envelope_db), 100)
    envelope.release(failed)
    envelope.release(failed)
    replacement = envelope.claim(_auth(envelope_db), 100)
    assert replacement["status"] == "held"


def test_settlement_failure_leaves_recoverable_conservative_hold(envelope_db) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    claim = envelope.claim(_auth(envelope_db), 100)
    envelope_db.fail_next_settle = True
    with pytest.raises(RuntimeError, match="settlement outage"):
        envelope.settle(claim, 20)
    assert envelope_db.calls[claim["call_id"]]["status"] == "held"
    with pytest.raises(HTTPException) as error:
        envelope.claim(_auth(envelope_db), 1)
    assert error.value.status_code == 402
    envelope.settle(claim, 20)
    assert envelope_db.calls[claim["call_id"]]["status"] == "settled"


def test_envelope_overrun_is_rejected_without_clamping_or_finalizing(
    envelope_db,
) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    claim = envelope.claim(_auth(envelope_db), 100)

    with pytest.raises(RuntimeError, match="actual_exceeds_reserved_estimate"):
        envelope.settle(claim, 101)

    persisted = envelope_db.calls[claim["call_id"]]
    assert persisted == {"estimate": 100, "actual": None, "status": "held"}
    envelope.settle(claim, 73)
    assert persisted == {"estimate": 100, "actual": 73, "status": "settled"}


def test_invocation_ledger_prevalidates_before_charging_operator_hold() -> None:
    events = []

    class _OperatorLedger:
        def settle(self, reservation, actual):
            events.append(("operator", reservation, actual))

    class _Envelope:
        def settle(self, claim, actual):
            events.append(("envelope", claim, actual))

    claim = {"estimate_microusd": 100}
    ledger = safebox_provider_proxy._InvocationBoundLedger(
        _OperatorLedger(), _Envelope(), claim
    )

    with pytest.raises(RuntimeError, match="actual_exceeds_reserved_estimate"):
        ledger.settle("reservation", 101)
    assert events == []

    ledger.settle("reservation", 73)
    assert events == [
        ("operator", "reservation", 73),
        ("envelope", claim, 73),
    ]


def test_operator_budget_settlement_rejects_overrun_and_passes_exact_actual(
    monkeypatch,
) -> None:
    from plugins.takyon import billing

    settled = []

    @contextmanager
    def factory():
        yield object()

    monkeypatch.setattr(safebox_app, "_safebox_db_conn", factory)
    monkeypatch.setattr(
        billing,
        "settle",
        lambda _conn, key, actual: settled.append((key, actual)),
    )
    adapter = safebox_app._OperatorBudgetAdapter()
    reservation = {"reservation_key": "hold-1", "reserved_cents": 2}

    with pytest.raises(RuntimeError, match="actual_exceeds_reserved_estimate"):
        adapter.settle(reservation, 20_001)
    assert settled == []

    adapter.settle(reservation, 15_001)
    assert settled == [("hold-1", 2)]


def test_settlement_failure_is_logged_with_hold_retained(caplog) -> None:
    class _OverrunLedger:
        def settle(self, _reservation, _actual):
            raise RuntimeError("operator_actual_exceeds_reserved_estimate")

    with caplog.at_level("ERROR", logger="takyon.safebox_provider_proxy"):
        safebox_provider_proxy._settle(_OverrunLedger(), "hold", 101)

    assert "provider settlement failed; hold retained" in caplog.text
    assert "RuntimeError" in caplog.text


def test_cross_scope_and_expired_invocation_fail_closed(envelope_db) -> None:
    envelope = safebox_provider_proxy._SdkInvocationEnvelope()
    with pytest.raises(RuntimeError, match="scope_mismatch"):
        envelope.claim(_auth(envelope_db, business="other"), 1)
    envelope_db.active = False
    with pytest.raises(HTTPException) as error:
        envelope.claim(_auth(envelope_db), 1)
    assert error.value.status_code == 401


def test_invocation_capability_cryptographically_binds_all_envelope_fields() -> None:
    key = b"safebox-only"
    scope = CapabilityScope(
        takyon_user_id=str(uuid.uuid4()),
        business_slug="acme",
        app_user_id=None,
        action="operator.session",
        max_cost_microusd=50,
        invocation_id=str(uuid.uuid4()),
        max_total_cost_microusd=100,
    )
    token = mint_capability(
        scope,
        signing_key=key,
        audience="operator.session",
        nonce="n",
        issued_at=100,
        ttl_seconds=60,
    )
    verified, _, exp = verify_capability(
        token,
        signing_key=key,
        expected_audience="operator.session",
        now=101,
    )
    assert verified == scope and exp == 160
    body_b64, signature = token.split(".")
    body = json.loads(_b64url_decode(body_b64))
    body["mt"] = 1_000
    tampered = _b64url(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ) + "." + signature
    with pytest.raises(CapabilityError, match="bad signature"):
        verify_capability(
            tampered,
            signing_key=key,
            expected_audience="operator.session",
            now=101,
        )


def test_invocation_migration_is_safebox_only() -> None:
    migration = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "takyon"
        / "db"
        / "migrations"
        / "0090_operator_sdk_invocation_envelopes.sql"
    ).read_text(encoding="utf-8").lower()
    assert "for update" not in migration  # locking lives in the Safebox adapter
    assert "force row level security" in migration
    assert "to takyon_safebox_authority" in migration
    assert "revoke all on table public.operator_sdk_invocations from %i" in migration
    assert "grant select, insert, update on public.operator_sdk_invocations\n            to takyon_operator_runtime" not in migration
