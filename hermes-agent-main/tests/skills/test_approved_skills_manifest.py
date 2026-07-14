from __future__ import annotations

import hashlib
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
ALL_MODES = {"bootstrap", "interactive", "wake"}
PINNED_TASTE_SKILL_SHA256 = "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89"


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _make_writable(path: Path) -> None:
    if not path.exists():
        return
    for child in path.rglob("*"):
        os.chmod(child, 0o755 if child.is_dir() else 0o644)
    os.chmod(path, 0o755)


def _minimal_release(tmp_path: Path, names: tuple[str, ...] = ("portable-method",)) -> Path:
    repo = tmp_path / "repo"
    root = repo / "skills"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / "takyon").mkdir()
    (repo / "plugins" / "takyon").mkdir(parents=True)
    (repo / "tools").mkdir()
    (repo / "plugins" / "takyon" / "tools.py").write_text(
        'TOOLS = [{"name": "business_read_business"}]\n', encoding="utf-8"
    )
    entries = []
    for index, name in enumerate(names):
        source = f"takyon/source-{index}"
        skill_dir = root / source
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            "description: Apply a complete evidence method. Use when evidence needs synthesis. "
            "Do not use for deployment changes.\n"
            "---\n\n# Portable Method\n",
            encoding="utf-8",
        )
        (skill_dir / "reference.md").write_text("reference\n", encoding="utf-8")
        entries.append(
            {
                "name": name,
                "source_path": source,
                "version": "1.0.0",
                "legacy_names": [],
                "publish_files": ["SKILL.md", "reference.md"],
            }
        )
    release = {
        "schema_version": 1,
        "plugin": {"name": "test-approved-skills", "version": "1.0.0"},
        "discovery_roots": ["takyon"],
        "skills": entries,
    }
    policy = {
        "schema_version": 1,
        "modes": {
            mode: {
                "required_tools": ["business_read_business"],
                "allowed_tools": ["business_read_business"],
                "denied_tools": [],
                "denied_write_paths": [],
            }
            for mode in sorted(ALL_MODES)
        },
    }
    plugin = {
        "name": "test-approved-skills",
        "version": "1.0.0",
        "description": "test",
        "skills": ["./takyon"],
    }
    (root / "release-skills.yaml").write_text(
        yaml.safe_dump(release, sort_keys=False), encoding="utf-8"
    )
    (root / "sdk-runtime-policy.yaml").write_text(
        yaml.safe_dump(policy, sort_keys=False), encoding="utf-8"
    )
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(plugin), encoding="utf-8"
    )
    return root


def test_production_release_is_exact_complete_and_locked() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    locked = json.loads((SKILLS_ROOT / "approved-skills.json").read_text(encoding="utf-8"))
    assert manifest == locked
    assert {entry["name"] for entry in manifest["skills"]} == EXPECTED_RELEASE_SKILLS
    assert len(manifest["skills"]) == 17
    for entry in manifest["skills"]:
        source = SKILLS_ROOT / entry["source_path"]
        actual = {
            path.relative_to(source).as_posix()
            for path in source.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        }
        published = {
            str(Path(path).relative_to(entry["plugin_path"]))
            for path in entry["publish_files"]
        }
        assert published == actual
        assert set(entry["allowed_modes"]) == ALL_MODES
        assert entry["content_digest"] == entry["source_digest"]
        assert entry["description"]


def test_every_mode_surfaces_every_skill_and_keeps_hard_tool_boundaries() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    policy = manifest["mode_tool_policy"]
    assert set(policy) == ALL_MODES
    for mode in policy.values():
        assert set(mode["allowed_skills"]) == EXPECTED_RELEASE_SKILLS
        assert not set(mode["allowed_tools"]) & set(mode["denied_tools"])
        assert set(mode["allowed_tools"]) <= set(manifest["model_tool_inventory"])
    assert {
        "web_search",
        "web_extract",
        "business_static_ad_generate",
        "business_ugc_ad_generate",
        "business_meta_ad_launch",
        "business_reddit_ad_launch",
    } <= set(policy["bootstrap"]["denied_tools"])
    assert {
        "business_upsert_app_surface_contract",
        "business_refresh_product_surface",
        "business_publish_mobile_release",
    } <= set(policy["wake"]["denied_tools"])
    assert set(policy["wake"]["denied_write_paths"]) == {
        "product/site",
        "product/surface.md",
        "product/app",
    }
    assert "business_request_credential" in policy["interactive"]["allowed_tools"]
    assert "business_request_credential" not in policy["bootstrap"]["allowed_tools"]
    assert "business_request_credential" not in policy["wake"]["allowed_tools"]


def test_taste_is_the_exact_pinned_npx_bundle() -> None:
    taste = SKILLS_ROOT / "creative" / "taste-frontend"
    assert hashlib.sha256((taste / "SKILL.md").read_bytes()).hexdigest() == PINNED_TASTE_SKILL_SHA256
    assert {path.name for path in taste.iterdir()} == {"LICENSE", "SKILL.md", "UPSTREAM.md"}
    upstream = (taste / "UPSTREAM.md").read_text(encoding="utf-8")
    assert "b17742737e796305d829b3ad39eda3add0d79060" in upstream


def test_routing_is_native_and_hermes_metadata_is_absent() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    for entry in manifest["skills"]:
        skill = (SKILLS_ROOT / entry["skill_file"]).read_text(encoding="utf-8")
        description = entry["description"].lower()
        assert len(description) <= 1024
        if entry["name"] != "design-taste-frontend":
            assert "use when" in description
            assert "do not use" in description
        assert "metadata:\n  hermes:" not in skill
        assert "${HERMES_SKILL_DIR}" not in skill
        assert "business_claude_agent_task" not in skill


def test_original_resource_rich_skills_publish_every_capability_file() -> None:
    entries = {entry["name"]: entry for entry in manifest_tool.build_manifest(SKILLS_ROOT)["skills"]}
    expected_counts = {
        "design-taste-frontend": 3,
        "takyon-autonomous-seo-geo-operator": 9,
        "takyon-static-ad-creative-generator": 23,
        "takyon-lightreel-seedance-fal-ugc": 10,
        "takyon-meta-ads-v2": 6,
        "takyon-reddit-ads": 3,
        "takyon-x": 8,
        "ugc-video-ad": 10,
    }
    for name, count in expected_counts.items():
        assert len(entries[name]["publish_files"]) == count
    assert entries["design-taste-frontend"]["line_count"] == 1206
    assert entries["takyon-autonomous-seo-geo-operator"]["line_count"] >= 500
    assert entries["takyon-meta-ads-v2"]["line_count"] >= 300
    assert entries["takyon-x"]["line_count"] >= 175


def test_handoff_is_documentation_only() -> None:
    handoff = SKILLS_ROOT / "HANDOFF"
    assert {path.name for path in handoff.iterdir()} == {"POLICY.md"}
    policy = (handoff / "POLICY.md").read_text(encoding="utf-8")
    assert "not loaded by the Claude Agent SDK" in policy
    manifest = json.loads((SKILLS_ROOT / "approved-skills.json").read_text(encoding="utf-8"))
    assert "handoff" not in json.dumps(manifest).lower()


def test_publish_creates_exact_flat_read_only_plugin(tmp_path: Path) -> None:
    destination = tmp_path / "installed-plugin"
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    try:
        manifest_tool.publish_plugin(SKILLS_ROOT, destination, manifest)
        manifest_tool.verify_published_plugin(destination, manifest)
        actual = {
            path.relative_to(destination).as_posix()
            for path in destination.rglob("SKILL.md")
        }
        assert actual == {f"skills/{name}/SKILL.md" for name in EXPECTED_RELEASE_SKILLS}
        assert (destination / "skills/takyon-static-ad-creative-generator/scripts/backends.py").is_file()
        assert (destination / "skills/ugc-video-ad/scripts/pipeline.py").is_file()
        assert (destination / "skills/takyon-lightreel-seedance-fal-ugc/scripts/query_lightreel.js").is_file()
        assert not any("HANDOFF" in path.as_posix() for path in destination.rglob("*"))
        assert not destination.stat().st_mode & 0o222
    finally:
        _make_writable(destination)
        shutil.rmtree(destination, ignore_errors=True)


def test_duplicate_names_fail_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path, ("same-name", "same-name"))
    with pytest.raises(manifest_tool.ManifestValidationError, match="duplicate"):
        manifest_tool.build_manifest(root)


def test_incomplete_bundle_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    release_path = root / "release-skills.yaml"
    release = _load_yaml(release_path)
    release["skills"][0]["publish_files"].remove("reference.md")
    release_path.write_text(yaml.safe_dump(release, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="complete native skill bundle"):
        manifest_tool.build_manifest(root)


def test_nonstandard_frontmatter_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon/source-0/SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8").replace(
            "description:", "metadata:\n  hermes: {}\ndescription:", 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(manifest_tool.ManifestValidationError, match="non-standard frontmatter"):
        manifest_tool.build_manifest(root)


def test_dangling_or_excluded_reference_fails_closed(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    skill = root / "takyon/source-0/SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\n[missing](references/missing.md)\n",
        encoding="utf-8",
    )
    with pytest.raises(manifest_tool.ManifestValidationError, match="dangling reference"):
        manifest_tool.build_manifest(root)


def test_runtime_policy_refuses_unknown_tools_and_unsafe_paths(tmp_path: Path) -> None:
    root = _minimal_release(tmp_path)
    policy_path = root / "sdk-runtime-policy.yaml"
    policy = _load_yaml(policy_path)
    policy["modes"]["wake"]["allowed_tools"] = ["business_missing"]
    policy["modes"]["wake"]["required_tools"] = []
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="unknown tools"):
        manifest_tool.build_manifest(root)
    policy["modes"]["wake"]["allowed_tools"] = ["business_read_business"]
    policy["modes"]["wake"]["required_tools"] = ["business_read_business"]
    policy["modes"]["wake"]["denied_write_paths"] = [r"product\site"]
    policy_path.write_text(yaml.safe_dump(policy, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifest_tool.ManifestValidationError, match="relative POSIX path"):
        manifest_tool.build_manifest(root)


def test_inventory_digest_is_stable() -> None:
    manifest = manifest_tool.build_manifest(SKILLS_ROOT)
    expected = "sha256:" + hashlib.sha256(
        "\0".join(sorted(manifest["model_tool_inventory"])).encode("utf-8")
    ).hexdigest()
    assert manifest["model_tool_inventory_digest"] == expected
