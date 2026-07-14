from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILLS_ROOT = PROJECT_ROOT / "skills"
SCRIPT = PROJECT_ROOT / "scripts" / "build_approved_skills_manifest.py"

spec = importlib.util.spec_from_file_location("approved_skill_manifest", SCRIPT)
assert spec and spec.loader
manifest_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest_tool)


EXPECTED_RELEASE_SKILLS = {
    "surface-refresh-audit",
    "design-taste-frontend",
    "taste-imagegen-web",
    "takyon-autonomous-seo-geo-operator",
    "takyon-static-ad-creative-generator",
    "takyon-app-runtime",
    "takyon-brand-logo",
    "takyon-business-metrics",
    "takyon-distribution",
    "takyon-lightreel-seedance-fal-ugc",
    "takyon-market-research",
    "takyon-meta-ads-v2",
    "takyon-mobile-app",
    "takyon-product",
    "takyon-reddit-ads",
    "takyon-x",
    "ugc-video-ad",
}


def _minimal_release(tmp_path: Path, names: tuple[str, ...] = ("portable-method",)) -> Path:
    repo = tmp_path / "repo"
    root = repo / "skills"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "HANDOFF").mkdir()
    (root / "takyon").mkdir()
    (repo / "plugins" / "takyon").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "plugins" / "takyon" / "model_tools.py").write_text(
        'TOOLS = [{"name": "business_read_business"}]\n', encoding="utf-8"
    )
    release_entries = []
    policies = {}
    inventory = {}
    for index, name in enumerate(names):
        source = f"takyon/source-{index}"
        skill_dir = root / source
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Apply a portable method. Use when evidence needs synthesis. "
            "Do not use for deployment changes.\n"
            "---\n\n# Portable Method\n",
            encoding="utf-8",
        )
        (skill_dir / "contract.yaml").write_text(
            "schema_version: 1\n"
            "requires: [business.state.read]\n"
            "produces: [report.output]\n",
            encoding="utf-8",
        )
        release_entries.append(
            {
                "name": name,
                "source_path": source,
                "version": "1.0.0",
                "legacy_names": [],
                "publish_files": ["SKILL.md", "contract.yaml"],
            }
        )
        policies[name] = {"allowed_modes": ["interactive"]}
        inventory[name] = {
            "required_tools": ["business_read_business"],
            "allowed_roots": ["research"],
            "publication_paths": ["research/report.md"],
        }
    release = {
        "schema_version": 1,
        "plugin": {"name": "test-approved-skills", "version": "1.0.0"},
        "discovery_roots": ["takyon"],
        "skills": release_entries,
    }
    bindings = {
        "schema_version": 1,
        "mode_tool_policy": {
            "interactive": {
                "baseline_tools": ["business_read_business"],
                "denied_capabilities": [],
                "denied_tools": [],
                "denied_write_paths": [],
            },
            "bootstrap": {
                "baseline_tools": ["business_read_business"],
                "denied_capabilities": [],
                "denied_tools": [],
                "denied_write_paths": [],
            },
            "wake": {
                "baseline_tools": ["business_read_business"],
                "denied_capabilities": [],
                "denied_tools": [],
                "denied_write_paths": [],
            },
        },
        "capabilities": {
            "business.state.read": {
                "adapter": "mcp",
                "tools": ["business_read_business"],
                "scope": "current_business",
                "authority": "operator_session",
            }
        },
        "artifacts": {
            "report.output": {
                "paths": ["research/report.md"],
                "publish": False,
                "receipt": "artifact_digest",
            }
        },
        "skill_policies": policies,
    }
    legacy = {
        "schema_version": 1,
        "retired_tools": {},
        "retired_environment_requirements": {},
        "skills": inventory,
    }
    plugin = {
        "name": "test-approved-skills",
        "version": "1.0.0",
        "description": "test",
        "skills": ["./takyon"],
    }
    (root / "release-skills.yaml").write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
    (root / "HANDOFF" / "bindings.yaml").write_text(yaml.safe_dump(bindings, sort_keys=False), encoding="utf-8")
    (root / "HANDOFF" / "legacy-inventory.yaml").write_text(yaml.safe_dump(legacy, sort_keys=False), encoding="utf-8")
    (root / "HANDOFF" / "retired-resources.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "resources": []}, sort_keys=False),
        encoding="utf-8",
    )
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(plugin), encoding="utf-8")
    return root


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for child in path.rglob("*"):
        os.chmod(child, 0o755 if child.is_dir() else 0o644)
    os.chmod(path, 0o755)


def test_production_release_is_exact_and_locked() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    locked = json.loads((SKILLS_ROOT / "approved-skills.json").read_text(encoding="utf-8"))
    assert manifest == locked
    assert {entry["name"] for entry in manifest["skills"]} == EXPECTED_RELEASE_SKILLS
    assert len(manifest["skills"]) == 17
    assert all(entry["content_digest"].startswith("sha256:") for entry in manifest["skills"])
    assert all(entry["source_digest"].startswith("sha256:") for entry in manifest["skills"])
    assert all(entry["plugin_path"] == f"skills/{entry['name']}" for entry in manifest["skills"])
    assert all(
        set(entry["publish_files"]) >= {
            f"skills/{entry['name']}/SKILL.md",
            f"skills/{entry['name']}/contract.yaml",
        }
        for entry in manifest["skills"]
    )
    assert not any(
        Path(path).suffix in manifest_tool.FORBIDDEN_PUBLISHED_SUFFIXES
        for entry in manifest["skills"]
        for path in entry["publish_files"]
    )


def test_production_modes_preserve_bootstrap_exclusions() -> None:
    entries = {entry["name"]: entry for entry in manifest_tool.build_manifest(SKILLS_ROOT)["skills"]}
    for excluded in (
        "takyon-market-research",
        "takyon-distribution",
        "takyon-x",
        "takyon-business-metrics",
        "takyon-autonomous-seo-geo-operator",
    ):
        assert "bootstrap" not in entries[excluded]["allowed_modes"]
    for required in (
        "takyon-app-runtime",
        "takyon-brand-logo",
        "takyon-product",
        "design-taste-frontend",
    ):
        assert "bootstrap" in entries[required]["allowed_modes"]


def test_mode_tool_policy_is_compiled_and_preserves_wake_creative_paths() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    capability_tools = manifest["capability_tools"]
    policy = manifest["mode_tool_policy"]
    all_bound_tools = {tool for tools in capability_tools.values() for tool in tools}
    assert set(policy) == {"interactive", "bootstrap", "wake"}
    assert set(policy["bootstrap"]["allowed_skills"]) == {
        "design-taste-frontend",
        "taste-imagegen-web",
        "takyon-app-runtime",
        "takyon-brand-logo",
        "takyon-mobile-app",
        "takyon-product",
    }
    assert {
        "web_search",
        "web_extract",
        "business_calculate_pulse",
        "business_x_publish_outreach",
        "business_static_ad_generate",
        "business_ugc_ad_generate",
        "business_meta_ad_launch",
        "business_reddit_ad_launch",
    } <= set(policy["bootstrap"]["denied_tools"])
    assert {
        "business_upsert_app_surface_contract",
        "business_upsert_app_plan",
        "business_refresh_product_surface",
        "business_invoke_app_action",
        "business_publish_mobile_release",
    } <= set(policy["wake"]["denied_tools"])
    assert set(policy["wake"]["denied_write_paths"]) == {
        "product/site",
        "product/surface.md",
        "product/app",
    }
    assert not {
        "product/static-ads",
        "product/ugc-ads",
        "product/lightreel-seedance-fal-ugc-workflow.md",
        "product/public-assets",
        "product/brand/logos",
    } & set(policy["wake"]["denied_write_paths"])
    assert not {
        "business_static_ad_generate",
        "business_ugc_ad_generate",
        "business_meta_ad_launch",
        "business_reddit_ad_launch",
    } & set(policy["wake"]["denied_tools"])
    assert all(set(mode["denied_tools"]) <= all_bound_tools for mode in policy.values())
    assert all(set(mode["allowed_tools"]) <= all_bound_tools for mode in policy.values())
    for mode in policy.values():
        expected_allowed = set(mode["baseline_tools"])
        for capability in mode["allowed_capabilities"]:
            expected_allowed.update(capability_tools[capability])
        assert set(mode["allowed_tools"]) == expected_allowed
    assert all(
        not set(mode["allowed_tools"]) & set(mode["denied_tools"])
        for mode in policy.values()
    )
    for dangerous in (
        "business_delete_business",
        "business_set_mode",
        "business_set_control",
        "business_decide_operator_approval",
        "business_delete_app_record",
        "business_upsert_app_customer",
        "business_upsert_app_profile",
        "business_grant_app_entitlement",
        "business_record_stripe_webhook",
        "business_record_app_usage",
    ):
        assert all(dangerous not in mode["allowed_tools"] for mode in policy.values())
    assert "business_request_credential" in policy["interactive"]["allowed_tools"]
    assert "business_request_credential" not in policy["bootstrap"]["allowed_tools"]
    assert "business_request_credential" not in policy["wake"]["allowed_tools"]
    assert all(set(entry["bound_tools"]) <= all_bound_tools for entry in manifest["skills"])
    assert manifest["model_tool_inventory_digest"] == manifest_tool._name_inventory_digest(
        manifest["model_tool_inventory"]
    )


def test_product_tuning_invariants_and_floors_are_rebound() -> None:
    entries = {entry["name"]: entry for entry in manifest_tool.build_manifest(SKILLS_ROOT)["skills"]}
    product = entries["takyon-product"]
    assert product["execution_profiles"] == {
        "initial_landing": {"effort": "medium", "max_turns": 60, "budget_usd": None, "timeout_ms": 900000},
        "product_workflow": {"effort": "high", "max_turns": 90, "budget_usd": 25.0, "timeout_ms": 1800000},
    }
    assert "preserve_user_plus_entitlements_account_truth" in product["invariants"]
    assert "forbid_has_active_subscription_gate" in product["invariants"]
    assert "publication_status_published_with_public_url" in product["verification_floors"]
    assert "changed_action_certified_invoked_and_receipted" in product["verification_floors"]
    assert product["routing_preservation_digest"].startswith("sha256:")


def test_operator_host_builtins_and_ambient_web_are_not_bound() -> None:
    bindings = _load_yaml(SKILLS_ROOT / "HANDOFF" / "bindings.yaml")["capabilities"]
    assert bindings["product.source.edit"]["tools"] == [
        "business_read_file",
        "business_list_files",
        "business_write_file",
        "business_patch_file",
    ]
    assert bindings["creative.ugc-workflow.compose"]["tools"] == [
        "business_read_file",
        "business_list_files",
        "business_write_file",
        "business_patch_file",
    ]
    assert bindings["web.evidence.search"]["tools"] == ["web_search", "web_extract"]
    all_tools = {tool for binding in bindings.values() for tool in binding["tools"]}
    assert not all_tools & {"Read", "Write", "Edit", "Bash", "WebSearch", "WebFetch"}


def test_publish_creates_exact_flat_read_only_plugin(tmp_path: Path) -> None:
    destination = tmp_path / "installed-plugin"
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    try:
        manifest_tool.publish_plugin(SKILLS_ROOT, destination, manifest)
        manifest_tool.verify_published_plugin(destination, manifest)
        actual = {path.relative_to(destination).as_posix() for path in destination.rglob("SKILL.md")}
        assert actual == {f"skills/{name}/SKILL.md" for name in EXPECTED_RELEASE_SKILLS}
        assert not any("HANDOFF" in path for path in actual)
        assert not (destination / "skills/takyon-static-ad-creative-generator/scripts/backends.py").exists()
        assert not (destination / "skills/ugc-video-ad/scripts/pipeline.py").exists()
        assert not (destination / "skills/takyon-lightreel-seedance-fal-ugc/scripts/query_lightreel.js").exists()
        assert not (destination.stat().st_mode & 0o222)
    finally:
        _make_writable(destination)
        shutil.rmtree(destination, ignore_errors=True)


def test_duplicate_names_fail_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path, ("same-name", "same-name"))
    with pytest.raises(manifest_tool.ManifestValidationError, match="duplicate canonical skill names"):
        manifest_tool.build_manifest(root)


def test_reserved_name_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path, ("claude-provider-method",))
    with pytest.raises(manifest_tool.ManifestValidationError, match="reserved prefix"):
        manifest_tool.build_manifest(root)


def test_nested_skill_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    nested = root / "takyon" / "source-0" / "nested"
    nested.mkdir()
    (nested / "SKILL.md").write_text("---\nname: nested\ndescription: nope\n---\n", encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="nested or malformed"):
        manifest_tool.build_manifest(root)


def test_executable_publish_resource_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    script = root / "takyon" / "source-0" / "unsafe.py"
    script.write_text("print('unsafe')\n", encoding="utf-8")
    release_path = root / "release-skills.yaml"
    release = _load_yaml(release_path)
    release["skills"][0]["publish_files"].append("unsafe.py")
    release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="may not be executable"):
        manifest_tool.build_manifest(root)


def test_runtime_binding_in_published_resource_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\nCall business_delete_business.\n",
        encoding="utf-8",
    )
    with pytest.raises(manifest_tool.ManifestValidationError, match="runtime-specific model tool"):
        manifest_tool.build_manifest(root)


def test_mode_write_paths_are_canonical_and_backslashes_fail(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    bindings_path = root / "HANDOFF" / "bindings.yaml"
    bindings = _load_yaml(bindings_path)
    bindings["mode_tool_policy"]["wake"]["denied_write_paths"] = ["product/.//site"]
    bindings_path.write_text(yaml.safe_dump(bindings, sort_keys=False), encoding="utf-8")
    manifest = manifest_tool.build_manifest(root)
    assert manifest["mode_tool_policy"]["wake"]["denied_write_paths"] == ["product/site"]
    bindings["mode_tool_policy"]["wake"]["denied_write_paths"] = [r"product\site"]
    bindings_path.write_text(yaml.safe_dump(bindings, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="relative POSIX path"):
        manifest_tool.build_manifest(root)


def test_dangling_reference_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\n[missing](references/missing.md)\n", encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="dangling reference"):
        manifest_tool.build_manifest(root)


def test_hermes_metadata_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8").replace("---\n\n#", "metadata:\n  hermes: {}\n---\n\n#", 1), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="non-portable frontmatter"):
        manifest_tool.build_manifest(root)


def test_nested_agent_delegation_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nCall business_claude_agent_task.\n", encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="removed nested-agent tool"):
        manifest_tool.build_manifest(root)


def test_oversized_skill_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + ("\nPortable instruction." * 500),
        encoding="utf-8",
    )
    with pytest.raises(manifest_tool.ManifestValidationError, match="499 lines or fewer"):
        manifest_tool.build_manifest(root)


def test_unapproved_skill_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    extra = root / "takyon" / "extra"
    extra.mkdir()
    (extra / "SKILL.md").write_text(
        "---\nname: extra\ndescription: Use when extra. Do not use elsewhere.\n---\n", encoding="utf-8"
    )
    with pytest.raises(manifest_tool.ManifestValidationError, match="unapproved"):
        manifest_tool.build_manifest(root)


def test_bound_tool_must_exist_in_model_definitions(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    bindings_path = root / "HANDOFF" / "bindings.yaml"
    bindings = _load_yaml(bindings_path)
    bindings["capabilities"]["business.state.read"]["tools"] = ["missing_tool"]
    for policy in bindings["mode_tool_policy"].values():
        policy["baseline_tools"] = ["missing_tool"]
    bindings_path.write_text(yaml.safe_dump(bindings, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="absent from model-tool definitions"):
        manifest_tool.build_manifest(root)


def test_locked_manifest_check_detects_content_drift(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    output = root / "approved-skills.json"
    assert manifest_tool.main(["--skills-root", str(root), "--output", str(output)]) == 0
    assert manifest_tool.main(["--skills-root", str(root), "--output", str(output), "--check"]) == 0
    skill = root / "takyon" / "source-0" / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nChanged method.\n", encoding="utf-8")
    assert manifest_tool.main(["--skills-root", str(root), "--output", str(output), "--check"]) == 1
