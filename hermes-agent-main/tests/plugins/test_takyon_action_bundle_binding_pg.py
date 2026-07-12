from __future__ import annotations

import hashlib
import json
import uuid

import pytest

pytest.importorskip("psycopg")

from plugins.takyon import app_identity  # noqa: E402


def _seed_live_action_builds(conn):
    owner_id = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub, email) values (%s, %s, %s)",
        (owner_id, f"auth0|{uuid.uuid4().hex}", f"owner-{uuid.uuid4().hex}@example.com"),
    )
    slug = f"bundle-{uuid.uuid4().hex[:10]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'test', 'active', 'live', %s)",
        (slug, slug, owner_id),
    )
    build_a = f"build-a-{uuid.uuid4().hex}"
    build_b = f"build-b-{uuid.uuid4().hex}"
    for build_id, marker in ((build_a, "A"), (build_b, "B")):
        status = "live" if build_id == build_a else "built"
        activation_state = "live" if build_id == build_a else "inactive"
        encoded = json.dumps(
            {
                "files": [
                    {
                        "path": "actions/identify.ts",
                        "sha256": hashlib.sha256(marker.encode("utf-8")).hexdigest(),
                        "content": marker,
                    }
                ],
                "http_action_names": ["identify"],
                "version": 1,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        conn.execute(
            "insert into product_builds "
            "(build_id, business_slug, source_revision, artifact_prefix, status, created_at, "
            "action_bundle_json, action_bundle_sha256, activation_state) "
            "values (%s, %s, 1, %s, %s, now(), %s, %s, %s)",
            (
                build_id,
                slug,
                f"products/{slug}/{build_id}",
                status,
                encoded,
                hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                activation_state,
            ),
        )
    conn.execute(
        "insert into app_surface_contracts "
        "(business_slug, status, source_path, publish_target, publish_policy, mode_behavior, "
        "done_gate, public_url, publish_status, live_build_id, created_at, updated_at) "
        "values (%s, 'active', 'product/site', %s, 'publish_after_verify', 'live_only', "
        "'published', %s, 'published', %s, now()::text, now()::text)",
        (slug, f"https://{slug}.coscale.app/", f"https://{slug}.coscale.app/", build_a),
    )
    user = app_identity.upsert_app_user(conn, slug, "customer@example.com")
    _session, raw_session = app_identity.start_session(conn, slug, user.id)
    return (
        slug,
        build_a,
        build_b,
        hashlib.sha256(raw_session.encode("utf-8")).hexdigest(),
        raw_session,
    )


def _app_role_bundle(conn, slug: str, session_hash: str, expected_build_id: str):
    conn.execute("set role takyon_app_runtime")
    try:
        return conn.execute(
            "select * from takyon_app_live_action_bundle(%s, %s, %s)",
            (slug, session_hash, expected_build_id),
        ).fetchone()
    finally:
        conn.execute("reset role")


def _app_role_legacy_build(conn, slug: str, session_hash: str):
    conn.execute("set role takyon_app_runtime")
    try:
        return conn.execute(
            "select * from takyon_app_legacy_unbound_live_build(%s, %s)",
            (slug, session_hash),
        ).fetchone()
    finally:
        conn.execute("reset role")


def test_live_action_bundle_function_rejects_stale_expected_build_after_pointer_flip(pg_conn):
    slug, build_a, build_b, session_hash, _raw_session = _seed_live_action_builds(pg_conn)

    initial = _app_role_bundle(pg_conn, slug, session_hash, build_a)
    assert initial is not None
    assert initial[0] == build_a
    assert _app_role_bundle(pg_conn, slug, session_hash, build_b) is None

    # This is the exact production race: the request already holds surface.live_build_id=A, while
    # publish atomically advances the durable pointer to B before the action bundle read.
    pg_conn.execute(
        "update product_builds set status = 'live', activation_state = 'pointer_pending' "
        "where business_slug = %s and build_id = %s",
        (slug, build_b),
    )
    pg_conn.execute(
        "update app_surface_contracts set live_build_id = %s where business_slug = %s",
        (build_b, slug),
    )

    assert _app_role_bundle(pg_conn, slug, session_hash, build_a) is None
    current = _app_role_bundle(pg_conn, slug, session_hash, build_b)
    assert current is not None
    assert current[0] == build_b


def test_legacy_unbound_function_is_authenticated_build_scoped_and_expiring(pg_conn):
    slug, build_a, _build_b, session_hash, _raw_session = _seed_live_action_builds(pg_conn)

    # Builds created after the one-time migration stamp receive no compatibility grant.
    assert _app_role_legacy_build(pg_conn, slug, session_hash) is None

    pg_conn.execute(
        "update product_builds set legacy_unbound_until = now() + interval '5 minutes' "
        "where business_slug = %s and build_id = %s",
        (slug, build_a),
    )
    allowed = _app_role_legacy_build(pg_conn, slug, session_hash)
    assert allowed is not None
    assert allowed[0] == build_a
    assert _app_role_legacy_build(pg_conn, slug, "0" * 64) is None

    pg_conn.execute(
        "update product_builds set legacy_unbound_until = now() - interval '1 second' "
        "where business_slug = %s and build_id = %s",
        (slug, build_a),
    )
    assert _app_role_legacy_build(pg_conn, slug, session_hash) is None


def test_live_action_bundle_function_rejects_staged_and_bounds_previous_rollout_window(pg_conn):
    slug, _build_a, build_b, session_hash, _raw_session = _seed_live_action_builds(pg_conn)

    pg_conn.execute(
        "update product_builds set status = 'staged', created_at = now() "
        "where business_slug = %s and build_id = %s",
        (slug, build_b),
    )
    staged = _app_role_bundle(pg_conn, slug, session_hash, build_b)
    assert staged is None

    pg_conn.execute(
        "update product_builds set status = 'previous', servable_until = now() + interval '5 minutes' "
        "where business_slug = %s and build_id = %s",
        (slug, build_b),
    )
    previous = _app_role_bundle(pg_conn, slug, session_hash, build_b)
    assert previous is not None
    assert previous[0] == build_b

    pg_conn.execute(
        "update product_builds set servable_until = now() - interval '1 second' "
        "where business_slug = %s and build_id = %s",
        (slug, build_b),
    )
    assert _app_role_bundle(pg_conn, slug, session_hash, build_b) is None


def test_action_claim_finish_capability_survives_session_revocation(pg_conn):
    slug, build_a, _build_b, session_hash, _raw_session = _seed_live_action_builds(pg_conn)
    reservation_key = f"reservation-{uuid.uuid4().hex}"
    finish_token_hash = hashlib.sha256(uuid.uuid4().bytes).hexdigest()
    claimed = pg_conn.execute(
        "select * from takyon_app_claim_action_invocation(%s, %s, %s, %s, %s, %s, %s)",
        (
            slug,
            session_hash,
            finish_token_hash,
            reservation_key,
            "identify",
            build_a,
            "metrics/receipt.json",
        ),
    ).fetchone()
    assert claimed is not None
    assert claimed[0] is True

    pg_conn.execute(
        "update app_sessions set revoked_at = now() where business_slug = %s and token_hash = %s",
        (slug, session_hash),
    )
    finished = pg_conn.execute(
        "select takyon_app_finish_action_invocation(%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            slug,
            finish_token_hash,
            reservation_key,
            "completed",
            '{"ok":true}',
            '{}',
            "metrics/receipt.json",
            "",
        ),
    ).fetchone()
    assert finished[0] is True
    assert pg_conn.execute(
        "select status from app_action_invocations where business_slug = %s and reservation_key = %s",
        (slug, reservation_key),
    ).fetchone()[0] == "completed"


def test_live_action_bundle_acl_grants_expected_build_signature_to_app_roles(pg_conn):
    # The two-argument signature is deliberately retained for the old consumer during the rolling
    # migration-before-restart window. The deployed consumer never calls it; a later migration can
    # remove it after both replicas are verified on the bound signature.
    assert pg_conn.execute(
        "select to_regprocedure('takyon_app_live_action_bundle(text,text)')"
    ).fetchone()[0] is not None
    assert pg_conn.execute(
        "select to_regprocedure('takyon_app_live_action_bundle(text,text,text)')"
    ).fetchone()[0] is not None
    assert pg_conn.execute(
        "select to_regprocedure('takyon_app_legacy_unbound_live_build(text,text)')"
    ).fetchone()[0] is not None
    assert pg_conn.execute(
        "select to_regprocedure('takyon_app_claim_action_invocation(text,text,text,text,text,text,text)')"
    ).fetchone()[0] is not None

    for role in ("takyon_app", "takyon_app_runtime"):
        assert pg_conn.execute(
            "select has_function_privilege(%s, "
            "'takyon_app_live_action_bundle(text,text,text)', 'execute')",
            (role,),
        ).fetchone()[0] is True
        assert pg_conn.execute(
            "select has_function_privilege(%s, "
            "'takyon_app_legacy_unbound_live_build(text,text)', 'execute')",
            (role,),
        ).fetchone()[0] is True
    for role in ("takyon_runtime", "takyon_operator_runtime", "takyon_safebox_authority"):
        assert pg_conn.execute(
            "select has_function_privilege(%s, "
            "'takyon_app_live_action_bundle(text,text,text)', 'execute')",
            (role,),
        ).fetchone()[0] is False
        assert pg_conn.execute(
            "select has_function_privilege(%s, "
            "'takyon_app_legacy_unbound_live_build(text,text)', 'execute')",
            (role,),
        ).fetchone()[0] is False
