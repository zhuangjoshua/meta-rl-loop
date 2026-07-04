"""store_builder custody + purity pins (plugins/takyon/store_builder.py).

The money ordering is pinned by test_takyon_store_build.py (any pre-trigger raise → release).
Here we pin the leaf's own contracts: custody resolution is explicit-file, fail-closed, and never
os.environ; the unconfigured error subclasses the store_build gate error so the tool path treats
both identically; the capability map is pure and matches the live-proven ASC types.
"""

from __future__ import annotations

import json

import pytest

from plugins.takyon import store_builder as sbdr
from plugins.takyon.store_build import EasBuilderUnconfigured


def _write_custody(root, *, drop=()):
    (root / "asc").mkdir()
    (root / "expo").mkdir()
    (root / "dist").mkdir()
    files = {
        "asc/takyon-ci.meta.json": json.dumps(
            {"key_id": "KID123", "issuer_id": "ISS-456", "team_id": "TEAM789", "key_file": "AuthKey_KID123.p8"}
        ),
        "asc/AuthKey_KID123.p8": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----",
        "expo/expo_token": "tok_abc",
        "expo/eas-ci.meta.json": json.dumps({"account": "coscale"}),
        "dist/cert_id.txt": "CERTID1",
        "dist/dist.p12": "binary-ish",
        "dist/p12pw.txt": "pw123",
    }
    for rel, content in files.items():
        if rel in drop:
            continue
        (root / rel).write_text(content)


def test_resolves_full_custody_from_explicit_dir(tmp_path):
    _write_custody(tmp_path)
    creds = sbdr.resolve_local_store_credentials(str(tmp_path))
    assert creds.key_id == "KID123"
    assert creds.issuer_id == "ISS-456"
    assert creds.team_id == "TEAM789"
    assert "fake" in creds.private_key_pem
    assert creds.expo_token == "tok_abc"
    assert creds.expo_owner == "coscale"
    assert creds.dist_cert_id == "CERTID1"
    assert creds.dist_p12_password == "pw123"
    assert sbdr.is_configured(str(tmp_path)) is True


@pytest.mark.parametrize(
    "missing",
    ["asc/takyon-ci.meta.json", "asc/AuthKey_KID123.p8", "expo/expo_token", "dist/cert_id.txt", "dist/p12pw.txt"],
)
def test_fails_closed_on_any_missing_piece(tmp_path, missing):
    _write_custody(tmp_path, drop={missing})
    with pytest.raises(sbdr.StoreBuilderUnconfigured) as exc:
        sbdr.resolve_local_store_credentials(str(tmp_path))
    assert "store_builder_unconfigured" in str(exc.value)
    assert sbdr.is_configured(str(tmp_path)) is False


def test_unconfigured_is_the_store_build_gate_error(tmp_path):
    # The tool's fail-closed branch catches EasBuilderUnconfigured; the local builder's custody
    # error must be the SAME gate class so both paths refuse identically before reserve.
    assert issubclass(sbdr.StoreBuilderUnconfigured, EasBuilderUnconfigured)


def test_never_reads_secrets_from_environ(tmp_path, monkeypatch):
    # Only the explicit dir matters: a fully-populated environ must not rescue empty custody.
    for k in ("EXPO_TOKEN", "APP_STORE_CONNECT_PRIVATE_KEY", "TAKYON_EXPO_TOKEN"):
        monkeypatch.setenv(k, "should-never-be-read")
    (tmp_path / "empty").mkdir()
    with pytest.raises(sbdr.StoreBuilderUnconfigured):
        sbdr.resolve_local_store_credentials(str(tmp_path / "empty"))


def test_secrets_dir_env_is_a_path_pointer(tmp_path, monkeypatch):
    _write_custody(tmp_path)
    monkeypatch.setenv(sbdr.SECRETS_DIR_ENV, str(tmp_path))
    creds = sbdr.resolve_local_store_credentials()
    assert creds.key_id == "KID123"


def test_corrupt_meta_json_is_unconfigured_not_a_crash(tmp_path):
    _write_custody(tmp_path)
    (tmp_path / "asc/takyon-ci.meta.json").write_text("{not json")
    with pytest.raises(sbdr.StoreBuilderUnconfigured):
        sbdr.resolve_local_store_credentials(str(tmp_path))
    assert sbdr.is_configured(str(tmp_path)) is False


def test_repr_never_leaks_secret_material(tmp_path):
    _write_custody(tmp_path)
    creds = sbdr.resolve_local_store_credentials(str(tmp_path))
    text = repr(creds)
    assert "tok_abc" not in text and "pw123" not in text and "fake" not in text


def test_expected_bundle_identifier_is_the_isolation_rail():
    # The ONLY identity a business may sign on the shared team — deterministic, slug-derived.
    assert sbdr.expected_bundle_identifier("pocketgarden") == "com.coscale.pocketgarden"
    assert sbdr.expected_bundle_identifier("Pocket Garden!") == "com.coscale.pocketgarden"
    assert sbdr.expected_bundle_identifier("a-b-c") == "com.coscale.a-b-c"


def test_capability_map_is_pure_and_matches_proven_types():
    cfg = {"expo": {"ios": {"associatedDomains": ["applinks:x.coscale.app"]}}}
    assert sbdr.capabilities_from_app_config(cfg) == ["ASSOCIATED_DOMAINS"]
    cfg_push = {"expo": {"ios": {"entitlements": {"aps-environment": "production"}}}}
    assert sbdr.capabilities_from_app_config(cfg_push) == ["PUSH_NOTIFICATIONS"]
    assert sbdr.capabilities_from_app_config({"expo": {"ios": {}}}) == []
