#!/usr/bin/env python3
"""Build and validate the locked Claude Agent SDK production-skill manifest."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote

import yaml


SCHEMA_VERSION = 1
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"
DEFAULT_RELEASE_FILE = "release-skills.yaml"
DEFAULT_BINDINGS_FILE = "HANDOFF/bindings.yaml"
DEFAULT_LEGACY_INVENTORY_FILE = "HANDOFF/legacy-inventory.yaml"
DEFAULT_RETIRED_RESOURCES_FILE = "HANDOFF/retired-resources.yaml"
DEFAULT_PLUGIN_FILE = ".claude-plugin/plugin.json"
DEFAULT_OUTPUT_FILE = "approved-skills.json"

NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMANTIC_ID_RE = re.compile(r"^[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+$")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
RESERVED_PREFIXES = ("anthropic-", "claude-", "codex-", "deepseek-", "gemini-", "hermes-", "openai-")
ALLOWED_FRONTMATTER_KEYS = frozenset({"name", "description"})
ALLOWED_MODES = frozenset({"interactive", "bootstrap", "wake"})
ALLOWED_CAPABILITY_ADAPTERS = frozenset({"mcp", "sandbox", "web"})
ALLOWED_CAPABILITY_SCOPES = frozenset(
    {"current_business", "current_workspace", "current_operator", "current_session"}
)
ALLOWED_CAPABILITY_AUTHORITIES = frozenset(
    {
        "operator_session",
        "explicit_operator_interaction",
        "creative_credit",
        "mobile_release",
        "none",
    }
)
MAX_SKILL_LINES = 499
FORBIDDEN_PUBLISHED_SUFFIXES = frozenset({".py", ".js", ".mjs", ".cjs", ".sh", ".ts"})
FORBIDDEN_INSTRUCTION_PATTERNS = (
    ("Hermes routing metadata", re.compile(r"metadata\.hermes", re.I)),
    ("Hermes skill-root variable", re.compile(r"\$\{HERMES_SKILL_DIR\}", re.I)),
    ("removed nested-agent tool", re.compile(r"business_claude_agent_task", re.I)),
    ("nested model worker", re.compile(r"(?:Claude Agent SDK|coding|model) worker", re.I)),
)
FORBIDDEN_PUBLISHED_PATTERNS = (
    ("runtime-specific model tool", re.compile(r"\bbusiness_[a-z0-9_]+\b", re.I)),
    (
        "runtime environment binding",
        re.compile(r"\b(?:TAKYON|HERMES|ANTHROPIC|OPENAI|META|REDDIT|LIGHTREEL|FAL)_[A-Z0-9_]+\b"),
    ),
    (
        "workspace publication binding",
        re.compile(r"[`\"'](?:product|metrics|research|distribution)/", re.I),
    ),
    (
        "runtime/deployment policy",
        re.compile(r"Claude Agent SDK|Safebox|plugins/takyon|hermes-agent-main", re.I),
    ),
)
MODEL_TOOL_PATTERNS = (
    re.compile(r"[\"']name[\"']\s*:\s*[\"']([A-Za-z0-9_-]+)[\"']"),
    re.compile(r"registry\.register\(\s*(?:\n\s*)?name\s*=\s*[\"']([A-Za-z0-9_-]+)[\"']", re.S),
)
TAKYON_MODEL_TOOL_RE = re.compile(r"^(?:business_[a-z0-9_]+|web_search|web_extract|todo)$")
ROUTING_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_/-]*", re.I)
ROUTING_STOPWORDS = frozenset(
    {
        "about", "after", "also", "and", "before", "business", "does", "for", "from",
        "into", "needs", "not", "one", "only", "should", "that", "the", "their", "this",
        "through", "use", "using", "when", "with", "without",
    }
)


class ManifestValidationError(ValueError):
    """Raised when an approved skill release is unsafe or internally inconsistent."""


def _fail(message: str) -> None:
    raise ManifestValidationError(message)


def _load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"missing required file: {path}")
    except yaml.YAMLError as exc:
        _fail(f"invalid YAML in {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"expected a mapping in {path}")
    return value


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"missing required file: {path}")
    except json.JSONDecodeError as exc:
        _fail(f"invalid JSON in {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"expected an object in {path}")
    return value


def _safe_relative_path(root: Path, raw: object, *, label: str) -> Path:
    value = str(raw or "").strip()
    if "\\" in value:
        _fail(f"{label} must use POSIX separators: {value!r}")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        _fail(f"{label} must be a contained relative path: {value!r}")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        _fail(f"{label} escapes the skills root: {value!r}")
    return path


def _canonical_relative_prefix(raw: object, *, label: str) -> str:
    value = str(raw or "").strip()
    if not value or "\\" in value or value.startswith("/"):
        _fail(f"{label} must be a non-empty relative POSIX path: {value!r}")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        _fail(f"{label} contains an unsafe path: {value!r}")
    return "/".join(parts)


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, flags=re.S)
    if not match:
        _fail(f"{path} has no valid YAML frontmatter")
    try:
        value = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        _fail(f"invalid frontmatter in {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"frontmatter in {path} must be a mapping")
    return value, text


def _validate_name(name: object, *, label: str, allow_reserved: bool = False) -> str:
    value = str(name or "").strip()
    if not NAME_RE.fullmatch(value):
        _fail(f"{label} is not a lowercase hyphenated skill name: {value!r}")
    if not allow_reserved and value.startswith(RESERVED_PREFIXES):
        _fail(f"{label} uses a provider/runtime-reserved prefix: {value!r}")
    return value


def _validate_markdown_links(skill_dir: Path, published_files: set[str] | None = None) -> None:
    root = skill_dir.resolve()
    markdown_files = (
        [skill_dir / rel for rel in published_files if rel.endswith(".md")]
        if published_files is not None
        else list(skill_dir.rglob("*.md"))
    )
    for markdown in sorted(markdown_files):
        for raw_target in MARKDOWN_LINK_RE.findall(markdown.read_text(encoding="utf-8")):
            target = raw_target.strip()
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]
            target = target.split(maxsplit=1)[0]
            if not target or target.startswith(("#", "http://", "https://", "mailto:", "data:")):
                continue
            target = unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target:
                continue
            candidate = (markdown.parent / target).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                _fail(f"reference escapes skill directory in {markdown}: {raw_target!r}")
            if not candidate.exists():
                _fail(f"dangling reference in {markdown}: {raw_target!r}")
            if published_files is not None:
                relative_target = candidate.relative_to(root).as_posix()
                if candidate.is_file() and relative_target not in published_files:
                    _fail(f"published reference is excluded from release in {markdown}: {raw_target!r}")


def _validate_instruction_text(skill_dir: Path) -> None:
    for path in sorted(skill_dir.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        for label, pattern in FORBIDDEN_INSTRUCTION_PATTERNS:
            if pattern.search(text):
                _fail(f"{path} contains {label}")


def _validate_published_text(skill_dir: Path, published_files: list[str]) -> None:
    for rel in published_files:
        path = skill_dir / rel
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in FORBIDDEN_PUBLISHED_PATTERNS:
            if pattern.search(text):
                _fail(f"published resource {path} contains {label}; move the binding to HANDOFF")


def _validate_contract(path: Path) -> tuple[list[str], list[str]]:
    contract = _load_yaml(path)
    unknown = set(contract) - {"schema_version", "requires", "produces"}
    if unknown:
        _fail(f"{path} contains unsupported contract keys: {sorted(unknown)}")
    if contract.get("schema_version") != SCHEMA_VERSION:
        _fail(f"{path} has unsupported schema_version")
    values: dict[str, list[str]] = {}
    for field in ("requires", "produces"):
        raw = contract.get(field)
        if not isinstance(raw, list) or not raw:
            _fail(f"{path} field {field} must be a non-empty list")
        normalized = [str(item).strip() for item in raw]
        if len(normalized) != len(set(normalized)):
            _fail(f"{path} field {field} contains duplicates")
        invalid = [item for item in normalized if not SEMANTIC_ID_RE.fullmatch(item)]
        if invalid:
            _fail(f"{path} field {field} contains non-semantic identifiers: {invalid}")
        values[field] = normalized
    return values["requires"], values["produces"]


def content_digest(skill_dir: Path) -> str:
    """Hash every non-cache regular file by POSIX path and raw bytes."""
    files: list[tuple[str, Path]] = []
    for path in skill_dir.rglob("*"):
        rel = path.relative_to(skill_dir)
        if "__pycache__" in rel.parts or path.name.endswith((".pyc", ".pyo")):
            continue
        if path.is_symlink():
            _fail(f"skill content may not contain symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            _fail(f"skill content contains a non-regular file: {path}")
        files.append((rel.as_posix(), path))
    digest = hashlib.sha256()
    for rel, path in sorted(files, key=lambda item: item[0]):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def published_content_digest(skill_dir: Path, relative_files: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(relative_files):
        path = skill_dir / rel
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _validate_bindings(bindings: dict, skill_names: set[str]) -> tuple[dict, dict, dict, dict]:
    expected_fields = {
        "schema_version",
        "mode_tool_policy",
        "capabilities",
        "artifacts",
        "skill_policies",
    }
    if set(bindings) != expected_fields:
        _fail(f"HANDOFF bindings must contain exactly {sorted(expected_fields)}")
    if bindings.get("schema_version") != SCHEMA_VERSION:
        _fail("HANDOFF bindings have an unsupported schema_version")
    capabilities = bindings.get("capabilities")
    artifacts = bindings.get("artifacts")
    policies = bindings.get("skill_policies")
    mode_policy = bindings.get("mode_tool_policy")
    if not all(isinstance(value, dict) for value in (capabilities, artifacts, policies, mode_policy)):
        _fail("HANDOFF capabilities, artifacts, skill_policies, and mode_tool_policy must be mappings")
    if set(policies) != skill_names:
        missing = sorted(skill_names - set(policies))
        extra = sorted(set(policies) - skill_names)
        _fail(f"HANDOFF skill policy mismatch: missing={missing}, extra={extra}")
    for semantic_id, binding in capabilities.items():
        if not SEMANTIC_ID_RE.fullmatch(str(semantic_id)) or not isinstance(binding, dict):
            _fail(f"invalid HANDOFF capability binding: {semantic_id!r}")
        if set(binding) != {"adapter", "tools", "scope", "authority"}:
            _fail(f"HANDOFF capability {semantic_id} has invalid keys")
        tools = binding.get("tools")
        if not isinstance(tools, list) or not tools:
            _fail(f"HANDOFF capability {semantic_id} is malformed")
        if any(not isinstance(tool, str) or not tool.strip() for tool in tools):
            _fail(f"HANDOFF capability {semantic_id} has invalid tools")
        binding["tools"] = [tool.strip() for tool in tools]
        if len(binding["tools"]) != len(set(binding["tools"])):
            _fail(f"HANDOFF capability {semantic_id} has duplicate tools")
        adapter = str(binding.get("adapter") or "").strip()
        scope = str(binding.get("scope") or "").strip()
        authority = str(binding.get("authority") or "").strip()
        if adapter not in ALLOWED_CAPABILITY_ADAPTERS:
            _fail(f"HANDOFF capability {semantic_id} has unsupported adapter: {adapter!r}")
        if scope not in ALLOWED_CAPABILITY_SCOPES:
            _fail(f"HANDOFF capability {semantic_id} has unsupported scope: {scope!r}")
        if authority not in ALLOWED_CAPABILITY_AUTHORITIES:
            _fail(f"HANDOFF capability {semantic_id} has unsupported authority: {authority!r}")
        binding["adapter"] = adapter
        binding["scope"] = scope
        binding["authority"] = authority
    for semantic_id, binding in artifacts.items():
        if not SEMANTIC_ID_RE.fullmatch(str(semantic_id)) or not isinstance(binding, dict):
            _fail(f"invalid HANDOFF artifact binding: {semantic_id!r}")
        required_artifact_fields = {"paths", "publish", "receipt"}
        allowed_artifact_fields = {*required_artifact_fields, "runtime_owned_paths"}
        if not required_artifact_fields <= set(binding) or not set(binding) <= allowed_artifact_fields:
            _fail(f"HANDOFF artifact {semantic_id} has invalid keys")
        paths = binding.get("paths")
        if not isinstance(paths, list) or not paths or not isinstance(binding.get("publish"), bool) or not str(binding.get("receipt") or ""):
            _fail(f"HANDOFF artifact {semantic_id} is malformed")
        for raw_path in paths:
            _canonical_relative_prefix(raw_path, label=f"HANDOFF artifact {semantic_id}")
        binding["paths"] = [
            _canonical_relative_prefix(path, label=f"HANDOFF artifact {semantic_id}")
            for path in paths
        ]
        if len(binding["paths"]) != len(set(binding["paths"])):
            _fail(f"HANDOFF artifact {semantic_id} has duplicate canonical paths")
        runtime_owned_paths = binding.get("runtime_owned_paths") or []
        if not isinstance(runtime_owned_paths, list):
            _fail(f"HANDOFF artifact {semantic_id}.runtime_owned_paths must be a list")
        binding["runtime_owned_paths"] = [
            _canonical_relative_prefix(
                path,
                label=f"HANDOFF artifact {semantic_id} runtime-owned path",
            )
            for path in runtime_owned_paths
        ]
        if len(binding["runtime_owned_paths"]) != len(set(binding["runtime_owned_paths"])):
            _fail(f"HANDOFF artifact {semantic_id} has duplicate runtime-owned paths")
        for owned_path in binding["runtime_owned_paths"]:
            if not _publication_is_bound(owned_path, binding["paths"]):
                _fail(
                    f"HANDOFF artifact {semantic_id} runtime-owned path is outside its bound paths: "
                    f"{owned_path}"
                )
    for name, policy in policies.items():
        if not isinstance(policy, dict) or set(policy) != {"allowed_modes"}:
            _fail(f"HANDOFF skill policy {name} is malformed")
        modes = policy.get("allowed_modes")
        if not isinstance(modes, list) or not modes or len(modes) != len(set(modes)) or not set(modes) <= ALLOWED_MODES:
            _fail(f"HANDOFF skill policy {name} has invalid allowed_modes")
    if set(mode_policy) != ALLOWED_MODES:
        _fail("HANDOFF mode_tool_policy must define interactive, bootstrap, and wake")
    bound_tools = {str(tool) for binding in capabilities.values() for tool in binding["tools"]}
    for mode, policy in mode_policy.items():
        expected = {"baseline_tools", "denied_capabilities", "denied_tools", "denied_write_paths"}
        if not isinstance(policy, dict) or set(policy) != expected:
            _fail(f"HANDOFF mode tool policy {mode} must contain exactly {sorted(expected)}")
        for field in expected:
            values = policy.get(field)
            if not isinstance(values, list) or len(values) != len(set(map(str, values))):
                _fail(f"HANDOFF mode tool policy {mode}.{field} must be a unique list")
        missing_capabilities = sorted(set(map(str, policy["denied_capabilities"])) - set(capabilities))
        if missing_capabilities:
            _fail(f"HANDOFF mode tool policy {mode} denies unknown capabilities: {missing_capabilities}")
        missing_tools = sorted(set(map(str, policy["denied_tools"])) - bound_tools)
        if missing_tools:
            _fail(f"HANDOFF mode tool policy {mode} denies unbound tools: {missing_tools}")
        unbound_baseline = sorted(set(map(str, policy["baseline_tools"])) - bound_tools)
        if unbound_baseline:
            _fail(f"HANDOFF mode tool policy {mode} has unbound baseline tools: {unbound_baseline}")
        policy["denied_write_paths"] = [
            _canonical_relative_prefix(
                raw_path,
                label=f"HANDOFF mode tool policy {mode} denied write path",
            )
            for raw_path in policy["denied_write_paths"]
        ]
        if len(policy["denied_write_paths"]) != len(set(policy["denied_write_paths"])):
            _fail(f"HANDOFF mode tool policy {mode} has duplicate canonical denied write paths")
    return capabilities, artifacts, policies, mode_policy


def _discover_model_tools(repo_root: Path) -> set[str]:
    """Read the authoritative Python tool definitions without importing the runtime."""
    sources = list((repo_root / "plugins" / "takyon").rglob("*.py"))
    sources.extend((repo_root / "tools").rglob("*.py"))
    names: set[str] = set()
    for source in sources:
        text = source.read_text(encoding="utf-8", errors="replace")
        for pattern in MODEL_TOOL_PATTERNS:
            names.update(name for name in pattern.findall(text) if TAKYON_MODEL_TOOL_RE.fullmatch(name))
    return names


def _validate_bound_tools(repo_root: Path, capabilities: dict) -> None:
    available = _discover_model_tools(repo_root)
    if not available:
        _fail(f"no model-tool definitions found under {repo_root}")
    bound = {str(tool) for binding in capabilities.values() for tool in binding["tools"]}
    missing = sorted(bound - available)
    if missing:
        _fail(f"HANDOFF binds tools absent from model-tool definitions: {missing}")


def _validate_plugin(plugin: dict, release_plugin: dict, roots: list[str]) -> None:
    expected_keys = {"name", "version", "description", "skills"}
    if set(plugin) != expected_keys:
        _fail(f"plugin.json must contain exactly {sorted(expected_keys)}")
    if plugin.get("name") != release_plugin.get("name") or plugin.get("version") != release_plugin.get("version"):
        _fail("plugin.json identity does not match release-skills.yaml")
    if plugin.get("skills") != [f"./{root}" for root in roots]:
        _fail("plugin.json skill roots do not match the curated discovery roots")


def _validate_legacy_inventory(inventory: dict, skill_names: set[str], capabilities: dict) -> tuple[dict, dict, dict]:
    expected = {"schema_version", "retired_tools", "retired_environment_requirements", "skills"}
    if set(inventory) != expected or inventory.get("schema_version") != SCHEMA_VERSION:
        _fail("legacy inventory has unsupported or missing top-level fields")
    retired_tools = inventory.get("retired_tools")
    retired_env = inventory.get("retired_environment_requirements")
    skills = inventory.get("skills")
    if not all(isinstance(value, dict) for value in (retired_tools, retired_env, skills)):
        _fail("legacy inventory mappings are malformed")
    if set(skills) != skill_names:
        _fail(
            "legacy inventory skill mismatch: "
            f"missing={sorted(skill_names - set(skills))}, extra={sorted(set(skills) - skill_names)}"
        )
    for category, entries in (("retired tool", retired_tools), ("retired environment requirement", retired_env)):
        for name, policy in entries.items():
            if not isinstance(policy, dict) or set(policy) != {"replacement_capability", "reason"}:
                _fail(f"{category} {name} is malformed")
            replacement = str(policy.get("replacement_capability") or "")
            if replacement not in capabilities or not str(policy.get("reason") or "").strip():
                _fail(f"{category} {name} has no bound replacement and reason")
    for name, value in skills.items():
        if not isinstance(value, dict):
            _fail(f"legacy inventory skill {name} is malformed")
        required = {"required_tools", "allowed_roots", "publication_paths"}
        if not required <= set(value):
            _fail(f"legacy inventory skill {name} lacks required preservation fields")
        if set(value) - (required | {"execution_profiles", "invariants", "verification_floors", "routing"}):
            _fail(f"legacy inventory skill {name} has unsupported fields")
        for field in required:
            items = value.get(field)
            if not isinstance(items, list) or len(items) != len(set(map(str, items))):
                _fail(f"legacy inventory skill {name} field {field} must be a unique list")
        for root in value["allowed_roots"]:
            if root not in {"product", "distribution", "research", "metrics"}:
                _fail(f"legacy inventory skill {name} has unsafe workspace root: {root}")
        for field in ("invariants", "verification_floors"):
            items = value.get(field, [])
            if not isinstance(items, list) or len(items) != len(set(map(str, items))):
                _fail(f"legacy inventory skill {name} field {field} must be a unique list")
        profiles = value.get("execution_profiles", {})
        if not isinstance(profiles, dict):
            _fail(f"legacy inventory skill {name} execution_profiles must be a mapping")
        for profile_name, profile in profiles.items():
            if not NAME_RE.fullmatch(str(profile_name).replace("_", "-")) or not isinstance(profile, dict):
                _fail(f"legacy inventory skill {name} has malformed execution profile {profile_name}")
            if set(profile) != {"effort", "max_turns", "budget_usd", "timeout_ms"}:
                _fail(f"legacy inventory execution profile {name}.{profile_name} is incomplete")
            if profile["effort"] not in {"low", "medium", "high"}:
                _fail(f"legacy inventory execution profile {name}.{profile_name} has invalid effort")
            if not isinstance(profile["max_turns"], int) or profile["max_turns"] <= 0:
                _fail(f"legacy inventory execution profile {name}.{profile_name} has invalid max_turns")
            if profile["budget_usd"] is not None and not isinstance(profile["budget_usd"], (int, float)):
                _fail(f"legacy inventory execution profile {name}.{profile_name} has invalid budget_usd")
            if not isinstance(profile["timeout_ms"], int) or profile["timeout_ms"] <= 0:
                _fail(f"legacy inventory execution profile {name}.{profile_name} has invalid timeout_ms")
        routing = value.get("routing")
        if routing is not None:
            if not isinstance(routing, dict) or set(routing) != {"owns", "when_to_use", "do_not_use_for"}:
                _fail(f"legacy inventory skill {name} has malformed routing preservation")
            if not str(routing.get("owns") or "").strip():
                _fail(f"legacy inventory skill {name} has empty routing ownership")
            for field in ("when_to_use", "do_not_use_for"):
                rules = routing.get(field)
                if not isinstance(rules, list) or not rules or not all(str(rule).strip() for rule in rules):
                    _fail(f"legacy inventory skill {name} has empty routing field {field}")
    return retired_tools, retired_env, skills


def _validate_retired_resources(skills_root: Path, entries: list[dict], capabilities: dict) -> str:
    inventory = _load_yaml(skills_root / DEFAULT_RETIRED_RESOURCES_FILE)
    if set(inventory) != {"schema_version", "resources"} or inventory.get("schema_version") != SCHEMA_VERSION:
        _fail("retired resource inventory has unsupported or missing fields")
    resources = inventory.get("resources")
    if not isinstance(resources, list):
        _fail("retired resource inventory resources must be a list")
    published_sources = {
        f"{item['source_path']}/{rel}"
        for item in entries
        for rel in item["publish_files"]
    }
    executable_sources: set[str] = set()
    for item in entries:
        source_dir = skills_root / item["source_path"]
        for path in source_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in FORBIDDEN_PUBLISHED_SUFFIXES:
                executable_sources.add(path.relative_to(skills_root).as_posix())
    inventoried: set[str] = set()
    for resource in resources:
        expected = {"source_path", "status", "replacement_capability", "preserved_method", "reason"}
        if not isinstance(resource, dict) or set(resource) != expected:
            _fail(f"retired resource entry must contain exactly {sorted(expected)}")
        source_rel = _safe_relative_path(
            skills_root,
            resource["source_path"],
            label="retired resource source_path",
        ).as_posix()
        preserved_rel = _safe_relative_path(
            skills_root,
            resource["preserved_method"],
            label="retired resource preserved_method",
        ).as_posix()
        if source_rel in inventoried:
            _fail(f"duplicate retired resource inventory entry: {source_rel}")
        inventoried.add(source_rel)
        if not (skills_root / source_rel).is_file() or not (skills_root / preserved_rel).is_file():
            _fail(f"retired resource or preserved method is missing: {source_rel}")
        if source_rel in published_sources:
            _fail(f"retired resource is still published in the SDK plugin: {source_rel}")
        if preserved_rel not in published_sources:
            _fail(f"retired resource method is not preserved in publish_files: {preserved_rel}")
        if resource["status"] not in {
            "guarded_runtime_dependency",
            "retired_direct_entrypoint",
            "retired_skill_helper",
        }:
            _fail(f"retired resource has unsupported status: {source_rel}")
        if resource["replacement_capability"] not in capabilities:
            _fail(f"retired resource has unbound replacement capability: {source_rel}")
        if not str(resource["reason"] or "").strip():
            _fail(f"retired resource has no reason: {source_rel}")
    if inventoried != executable_sources:
        _fail(
            "executable skill-resource inventory mismatch: "
            f"missing={sorted(executable_sources - inventoried)}, extra={sorted(inventoried - executable_sources)}"
        )
    encoded = json.dumps(resources, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _publication_is_bound(publication: str, bound_paths: list[str]) -> bool:
    return any(
        publication == path or publication.startswith(path.rstrip("/") + "/")
        for path in bound_paths
    )


def _validate_routing_preservation(name: str, routing: dict | None, skill_text: str) -> str | None:
    if not routing:
        return None
    haystack = {token.lower() for token in ROUTING_TOKEN_RE.findall(skill_text)}
    rules = [routing["owns"], *routing["when_to_use"], *routing["do_not_use_for"]]
    for rule in rules:
        tokens = {
            token.lower()
            for token in ROUTING_TOKEN_RE.findall(str(rule))
            if len(token) > 2 and token.lower() not in ROUTING_STOPWORDS
        }
        if tokens and len(tokens & haystack) / len(tokens) < 0.55:
            _fail(f"portable skill {name} no longer represents legacy routing rule: {rule}")
    encoded = json.dumps(routing, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _name_inventory_digest(names: set[str] | list[str]) -> str:
    encoded = "\0".join(sorted(names)).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _handoff_guidance(capabilities: dict, artifacts: dict, mode_policy: dict) -> str:
    lines = [
        "HANDOFF binds portable skill contracts to the current runtime.",
        "Use only the tools exposed for the active mode; exact path and authority checks remain enforced by adapters.",
        "Capabilities:",
    ]
    for semantic_id, binding in sorted(capabilities.items()):
        lines.append(f"- {semantic_id} -> {', '.join(map(str, binding['tools']))}")
    lines.append("Artifacts:")
    for semantic_id, binding in sorted(artifacts.items()):
        publish = "publish" if binding["publish"] else "local"
        lines.append(
            f"- {semantic_id} -> {', '.join(map(str, binding['paths']))} "
            f"({publish}; evidence={binding['receipt']})"
        )
    lines.append("Runtime-owned artifact paths (inspect but never edit; repair worker-owned source instead):")
    owned_artifacts = [
        (semantic_id, binding["runtime_owned_paths"])
        for semantic_id, binding in sorted(artifacts.items())
        if binding.get("runtime_owned_paths")
    ]
    if not owned_artifacts:
        lines.append("- none")
    for semantic_id, paths in owned_artifacts:
        lines.append(f"- {semantic_id} -> {', '.join(map(str, paths))}")
    lines.append("Mode write exclusions:")
    for mode, policy in sorted(mode_policy.items()):
        paths = ", ".join(map(str, policy["denied_write_paths"])) or "none"
        lines.append(f"- {mode}: {paths}")
    return "\n".join(lines)


def build_manifest(skills_root: Path = DEFAULT_SKILLS_ROOT) -> dict:
    skills_root = skills_root.resolve()
    release = _load_yaml(skills_root / DEFAULT_RELEASE_FILE)
    if release.get("schema_version") != SCHEMA_VERSION:
        _fail("release-skills.yaml has an unsupported schema_version")
    if set(release) != {"schema_version", "plugin", "discovery_roots", "skills"}:
        _fail("release-skills.yaml has unsupported or missing top-level keys")
    release_plugin = release.get("plugin")
    roots_raw = release.get("discovery_roots")
    entries = release.get("skills")
    if not isinstance(release_plugin, dict) or set(release_plugin) != {"name", "version"}:
        _fail("release plugin identity is malformed")
    _validate_name(release_plugin.get("name"), label="plugin name")
    if not str(release_plugin.get("version") or "").strip():
        _fail("release plugin version is required")
    if not isinstance(roots_raw, list) or not roots_raw or len(roots_raw) != len(set(map(str, roots_raw))):
        _fail("discovery_roots must be a non-empty unique list")
    roots: list[str] = []
    for raw in roots_raw:
        rel = _safe_relative_path(skills_root, raw, label="discovery root")
        if len(rel.parts) != 1 or not (skills_root / rel).is_dir():
            _fail(f"discovery root must be one existing direct child: {raw!r}")
        roots.append(rel.as_posix())
    if not isinstance(entries, list) or not entries:
        _fail("release skills must be a non-empty list")

    declared_names: list[str] = []
    declared_sources: list[str] = []
    for item in entries:
        expected_skill_fields = {"name", "source_path", "version", "legacy_names", "publish_files"}
        if not isinstance(item, dict) or set(item) != expected_skill_fields:
            _fail(f"every release skill needs exactly {sorted(expected_skill_fields)}")
        declared_names.append(_validate_name(item.get("name"), label="release skill name"))
        source = _safe_relative_path(skills_root, item.get("source_path"), label="skill source")
        if len(source.parts) != 2 or source.parts[0] not in roots:
            _fail(f"skill source must be a direct skill under a discovery root: {source}")
        declared_sources.append(source.as_posix())
        if not str(item.get("version") or "").strip():
            _fail(f"release skill {item.get('name')} needs a version")
        legacy = item.get("legacy_names")
        if not isinstance(legacy, list) or len(legacy) != len(set(map(str, legacy))):
            _fail(f"release skill {item.get('name')} has invalid legacy_names")
        for legacy_name in legacy:
            _validate_name(legacy_name, label="legacy skill name", allow_reserved=True)
        publish_files = item.get("publish_files")
        if not isinstance(publish_files, list) or not publish_files:
            _fail(f"release skill {item.get('name')} needs a non-empty publish_files allowlist")
        normalized_publish_files: list[str] = []
        skill_dir = skills_root / source
        for raw_file in publish_files:
            rel = _safe_relative_path(skill_dir, raw_file, label=f"published file for {item.get('name')}")
            path = skill_dir / rel
            if path.is_symlink() or not path.is_file():
                _fail(f"published file for {item.get('name')} must be a regular non-symlink: {raw_file!r}")
            if path.suffix.lower() in FORBIDDEN_PUBLISHED_SUFFIXES or path.stat().st_mode & 0o111:
                _fail(f"published skill resource may not be executable: {path}")
            normalized_publish_files.append(rel.as_posix())
        if len(normalized_publish_files) != len(set(normalized_publish_files)):
            _fail(f"release skill {item.get('name')} has duplicate publish_files")
        if not {"SKILL.md", "contract.yaml"} <= set(normalized_publish_files):
            _fail(f"release skill {item.get('name')} must publish SKILL.md and contract.yaml")
        item["publish_files"] = normalized_publish_files
    if len(declared_names) != len(set(declared_names)):
        _fail("duplicate canonical skill names in release-skills.yaml")
    if len(declared_sources) != len(set(declared_sources)):
        _fail("duplicate skill source paths in release-skills.yaml")

    discovered_sources: set[str] = set()
    for root_name in roots:
        root = skills_root / root_name
        for skill_file in sorted(root.rglob("SKILL.md")):
            rel = skill_file.relative_to(root)
            if len(rel.parts) != 2 or rel.name != "SKILL.md":
                _fail(f"nested or malformed skill discovery root: {skill_file}")
            discovered_sources.add(skill_file.parent.relative_to(skills_root).as_posix())
    if discovered_sources != set(declared_sources):
        missing = sorted(set(declared_sources) - discovered_sources)
        extra = sorted(discovered_sources - set(declared_sources))
        _fail(f"release/discovery skill mismatch: missing={missing}, unapproved={extra}")

    bindings = _load_yaml(skills_root / DEFAULT_BINDINGS_FILE)
    capabilities, artifacts, policies, mode_policy = _validate_bindings(bindings, set(declared_names))
    _validate_bound_tools(skills_root.parent, capabilities)
    retired_resources_digest = _validate_retired_resources(skills_root, entries, capabilities)
    inventory = _load_yaml(skills_root / DEFAULT_LEGACY_INVENTORY_FILE)
    retired_tools, retired_env, legacy_skills = _validate_legacy_inventory(
        inventory, set(declared_names), capabilities
    )
    plugin = _load_json(skills_root / DEFAULT_PLUGIN_FILE)
    _validate_plugin(plugin, release_plugin, roots)

    manifest_entries = []
    for item in entries:
        name = str(item["name"])
        source_path = str(item["source_path"])
        skill_dir = skills_root / source_path
        skill_file = skill_dir / "SKILL.md"
        contract_file = skill_dir / "contract.yaml"
        frontmatter, skill_text = _frontmatter(skill_file)
        skill_lines = len(skill_text.splitlines())
        if skill_lines > MAX_SKILL_LINES:
            _fail(
                f"{skill_file} has {skill_lines} lines; portable SKILL.md files must be "
                f"{MAX_SKILL_LINES} lines or fewer"
            )
        unknown_frontmatter = set(frontmatter) - ALLOWED_FRONTMATTER_KEYS
        if unknown_frontmatter:
            _fail(f"{skill_file} contains non-portable frontmatter keys: {sorted(unknown_frontmatter)}")
        frontmatter_name = _validate_name(frontmatter.get("name"), label=f"frontmatter name in {skill_file}")
        if frontmatter_name != name:
            _fail(f"release/frontmatter name mismatch for {source_path}: {name!r} != {frontmatter_name!r}")
        description = str(frontmatter.get("description") or "").strip()
        description_lower = description.lower()
        if not description or "use when" not in description_lower or "do not use" not in description_lower:
            _fail(f"{skill_file} description must state both 'Use when' and 'Do not use'")
        _validate_instruction_text(skill_dir)
        _validate_published_text(skill_dir, item["publish_files"])
        _validate_markdown_links(skill_dir)
        _validate_markdown_links(skill_dir, set(item["publish_files"]))
        requires, produces = _validate_contract(contract_file)
        missing_capabilities = sorted(set(requires) - set(capabilities))
        missing_artifacts = sorted(set(produces) - set(artifacts))
        if missing_capabilities or missing_artifacts:
            _fail(f"unbound semantic contract for {name}: capabilities={missing_capabilities}, artifacts={missing_artifacts}")
        legacy = legacy_skills[name]
        routing_digest = _validate_routing_preservation(name, legacy.get("routing"), skill_text)
        bound_tools = {str(tool) for capability in requires for tool in capabilities[capability]["tools"]}
        for old_tool in legacy["required_tools"]:
            if old_tool in bound_tools:
                continue
            retired = retired_tools.get(old_tool)
            if not retired or retired["replacement_capability"] not in requires:
                _fail(f"legacy required tool for {name} is neither preserved nor rebound: {old_tool}")
        bound_paths = [str(path) for artifact in produces for path in artifacts[artifact]["paths"]]
        for publication in legacy["publication_paths"]:
            if not _publication_is_bound(str(publication), bound_paths):
                _fail(f"legacy publication for {name} is not represented by a produced artifact: {publication}")
        for env_name, retirement in retired_env.items():
            if env_name in {"TAKYON_GEMINI_API_KEY"} and name == "takyon-brand-logo":
                if retirement["replacement_capability"] not in requires:
                    _fail(f"legacy environment requirement for {name} is not rebound: {env_name}")
            if env_name in {"LIGHTREEL_API_KEY", "FAL_KEY"} and name == "takyon-lightreel-seedance-fal-ugc":
                if retirement["replacement_capability"] not in requires:
                    _fail(f"legacy environment requirement for {name} is not rebound: {env_name}")
        manifest_entries.append(
            {
                "name": name,
                "legacy_names": list(item["legacy_names"]),
                "source_path": source_path,
                "plugin_path": f"skills/{name}",
                "skill_file": f"{source_path}/SKILL.md",
                "contract_file": f"{source_path}/contract.yaml",
                "version": str(item["version"]),
                "description": description,
                "allowed_modes": list(policies[name]["allowed_modes"]),
                "workspace_roots": list(legacy["allowed_roots"]),
                "publication_paths": list(legacy["publication_paths"]),
                "execution_profiles": copy.deepcopy(legacy.get("execution_profiles", {})),
                "invariants": list(legacy.get("invariants", [])),
                "verification_floors": list(legacy.get("verification_floors", [])),
                "routing_preservation_digest": routing_digest,
                "requires": requires,
                "produces": produces,
                "bound_tools": sorted(bound_tools),
                "publish_files": [f"skills/{name}/{rel}" for rel in sorted(item["publish_files"])],
                "source_digest": content_digest(skill_dir),
                "content_digest": published_content_digest(skill_dir, item["publish_files"]),
            }
        )

    manifest_entries.sort(key=lambda entry: entry["name"])
    capability_bindings = {
        semantic_id: {
            "adapter": str(binding["adapter"]),
            "tools": sorted(map(str, binding["tools"])),
            "scope": str(binding["scope"]),
            "authority": str(binding["authority"]),
        }
        for semantic_id, binding in sorted(capabilities.items())
    }
    capability_tools = {
        semantic_id: list(binding["tools"])
        for semantic_id, binding in capability_bindings.items()
    }
    artifact_bindings = {
        semantic_id: {
            "paths": list(binding["paths"]),
            "publish": bool(binding["publish"]),
            "receipt": str(binding["receipt"]),
            "runtime_owned_paths": list(binding["runtime_owned_paths"]),
        }
        for semantic_id, binding in sorted(artifacts.items())
    }
    entries_by_name = {entry["name"]: entry for entry in manifest_entries}
    compiled_mode_policy = {}
    for mode in sorted(ALLOWED_MODES):
        allowed_skills = sorted(
            name for name, policy in policies.items() if mode in policy["allowed_modes"]
        )
        allowed_capabilities = sorted(
            {
                capability
                for name in allowed_skills
                for capability in entries_by_name[name]["requires"]
            }
        )
        denied_capabilities = set(map(str, mode_policy[mode]["denied_capabilities"]))
        forbidden_required = sorted(set(allowed_capabilities) & denied_capabilities)
        if forbidden_required:
            _fail(f"HANDOFF mode {mode} denies capabilities required by allowed skills: {forbidden_required}")
        allowed_tools = {
            *map(str, mode_policy[mode]["baseline_tools"]),
            *(tool for capability in allowed_capabilities for tool in capability_tools[capability]),
        }
        denied_tools = set(map(str, mode_policy[mode]["denied_tools"]))
        forbidden_tools = sorted(allowed_tools & denied_tools)
        if forbidden_tools:
            _fail(f"HANDOFF mode {mode} denies tools required by its exact allowlist: {forbidden_tools}")
        compiled_mode_policy[mode] = {
            "allowed_skills": allowed_skills,
            "allowed_capabilities": allowed_capabilities,
            "allowed_tools": sorted(allowed_tools),
            "baseline_tools": list(mode_policy[mode]["baseline_tools"]),
            "denied_capabilities": list(mode_policy[mode]["denied_capabilities"]),
            "denied_tools": list(mode_policy[mode]["denied_tools"]),
            "denied_write_paths": list(mode_policy[mode]["denied_write_paths"]),
        }
    model_tool_inventory = sorted(_discover_model_tools(skills_root.parent))
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": DEFAULT_RELEASE_FILE,
        "plugin": {"name": release_plugin["name"], "version": release_plugin["version"]},
        "discovery_roots": roots,
        "capability_bindings": capability_bindings,
        "capability_tools": capability_tools,
        "artifact_bindings": artifact_bindings,
        "mode_tool_policy": compiled_mode_policy,
        "model_tool_inventory": model_tool_inventory,
        "model_tool_inventory_digest": _name_inventory_digest(model_tool_inventory),
        "retired_resources_digest": retired_resources_digest,
        "handoff_guidance": _handoff_guidance(capabilities, artifacts, compiled_mode_policy),
        "skills": manifest_entries,
    }


def canonical_json(manifest: dict) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def _runtime_plugin_json(manifest: dict) -> str:
    plugin = manifest["plugin"]
    return json.dumps(
        {
            "name": plugin["name"],
            "version": plugin["version"],
            "description": "Versioned production skills approved for the Takyon operator agent",
        },
        indent=2,
    ) + "\n"


def verify_published_plugin(plugin_root: Path, manifest: dict) -> None:
    plugin_root = plugin_root.resolve()
    locked = _load_json(plugin_root / DEFAULT_OUTPUT_FILE)
    if locked != manifest:
        _fail(f"published plugin manifest mismatch: {plugin_root}")
    plugin = _load_json(plugin_root / DEFAULT_PLUGIN_FILE)
    expected_plugin = json.loads(_runtime_plugin_json(manifest))
    if plugin != expected_plugin:
        _fail(f"published plugin identity mismatch: {plugin_root}")
    expected_files = {
        DEFAULT_PLUGIN_FILE,
        DEFAULT_OUTPUT_FILE,
        *(path for entry in manifest["skills"] for path in entry["publish_files"]),
    }
    actual_files: set[str] = set()
    for path in plugin_root.rglob("*"):
        if path.is_symlink():
            _fail(f"published plugin contains a symlink: {path}")
        if path.is_file():
            actual_files.add(path.relative_to(plugin_root).as_posix())
        elif not path.is_dir():
            _fail(f"published plugin contains a non-regular path: {path}")
    if actual_files != expected_files:
        _fail(
            "published plugin file mismatch: "
            f"missing={sorted(expected_files - actual_files)}, extra={sorted(actual_files - expected_files)}"
        )
    expected_dirs = {".claude-plugin", "skills"}
    for relative_file in expected_files:
        parent = Path(relative_file).parent
        while parent != Path("."):
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    actual_dirs = {
        path.relative_to(plugin_root).as_posix()
        for path in plugin_root.rglob("*")
        if path.is_dir()
    }
    if actual_dirs != expected_dirs:
        _fail(
            "published plugin directory mismatch: "
            f"missing={sorted(expected_dirs - actual_dirs)}, extra={sorted(actual_dirs - expected_dirs)}"
        )
    expected_skill_files = {entry["plugin_path"] + "/SKILL.md" for entry in manifest["skills"]}
    actual_skill_files = {path for path in actual_files if path.endswith("/SKILL.md")}
    if actual_skill_files != expected_skill_files:
        _fail(
            "published plugin discovery mismatch: "
            f"missing={sorted(expected_skill_files - actual_skill_files)}, "
            f"extra={sorted(actual_skill_files - expected_skill_files)}"
        )
    for entry in manifest["skills"]:
        installed = plugin_root / entry["plugin_path"]
        if content_digest(installed) != entry["content_digest"]:
            _fail(f"published skill digest mismatch: {entry['name']}")
    for path in plugin_root.rglob("*"):
        mode = path.stat().st_mode
        if path.is_file() and (mode & 0o333 or not stat.S_ISREG(mode)):
            _fail(f"published plugin file must be read-only and non-executable: {path}")
        if path.is_dir() and mode & 0o222:
            _fail(f"published plugin path is writable: {path}")
    if plugin_root.stat().st_mode & 0o222:
        _fail(f"published plugin root is writable: {plugin_root}")


def publish_plugin(skills_root: Path, destination: Path, manifest: dict | None = None) -> Path:
    """Atomically copy the reviewed sources into one flat, read-only native plugin."""
    skills_root = skills_root.resolve()
    destination = destination.expanduser()
    if not destination.is_absolute():
        _fail("published plugin destination must be absolute")
    destination = destination.resolve()
    repo_root = skills_root.parent.resolve()
    try:
        destination.relative_to(repo_root)
    except ValueError:
        pass
    else:
        _fail("published plugin must live outside the writable repository source tree")
    manifest = manifest or build_manifest(skills_root)
    if destination.exists():
        verify_published_plugin(destination, manifest)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    stage: Path | None = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        (stage / ".claude-plugin").mkdir()
        (stage / "skills").mkdir()
        (stage / DEFAULT_PLUGIN_FILE).write_text(_runtime_plugin_json(manifest), encoding="utf-8")
        (stage / DEFAULT_OUTPUT_FILE).write_text(canonical_json(manifest), encoding="utf-8")
        for entry in manifest["skills"]:
            source = skills_root / entry["source_path"]
            plugin_prefix = Path(entry["plugin_path"])
            for published_path in entry["publish_files"]:
                relative = Path(published_path).relative_to(plugin_prefix)
                target = stage / published_path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / relative, target)
        expected_skill_files = {entry["plugin_path"] + "/SKILL.md" for entry in manifest["skills"]}
        actual_skill_files = {path.relative_to(stage).as_posix() for path in stage.rglob("SKILL.md")}
        if actual_skill_files != expected_skill_files:
            _fail("staged plugin contains an excluded or missing SKILL.md")
        for entry in manifest["skills"]:
            if content_digest(stage / entry["plugin_path"]) != entry["content_digest"]:
                _fail(f"staged skill digest mismatch: {entry['name']}")
        for path in sorted(stage.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            os.chmod(path, 0o555 if path.is_dir() else 0o444)
        os.chmod(stage, 0o555)
        stage.replace(destination)
        stage = None
        verify_published_plugin(destination, manifest)
        return destination
    finally:
        if stage is not None and stage.exists():
            for path in stage.rglob("*"):
                try:
                    os.chmod(path, 0o755 if path.is_dir() else 0o644)
                except OSError:
                    pass
            try:
                os.chmod(stage, 0o755)
            except OSError:
                pass
            shutil.rmtree(stage, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", type=Path, default=DEFAULT_SKILLS_ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true", help="fail if the locked manifest differs")
    parser.add_argument("--publish-root", type=Path, help="install a flat read-only native plugin outside the repository")
    args = parser.parse_args(argv)
    output = args.output or args.skills_root / DEFAULT_OUTPUT_FILE
    try:
        manifest = build_manifest(args.skills_root)
        rendered = canonical_json(manifest)
        if args.check:
            try:
                current = output.read_text(encoding="utf-8")
            except FileNotFoundError:
                _fail(f"locked manifest is missing: {output}")
            if current != rendered:
                _fail(f"locked manifest drift: regenerate {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_suffix(output.suffix + ".tmp")
            temporary.write_text(rendered, encoding="utf-8")
            temporary.replace(output)
        if args.publish_root:
            publish_plugin(args.skills_root, args.publish_root, manifest)
    except ManifestValidationError as exc:
        print(f"skill manifest validation failed: {exc}", file=sys.stderr)
        return 1
    action = "validated" if args.check else "generated"
    print(f"{action} {len(manifest['skills'])} approved skills: {output}")
    if args.publish_root:
        print(f"published read-only native plugin: {args.publish_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
