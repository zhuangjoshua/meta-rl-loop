#!/usr/bin/env python3
"""Build and publish the locked native Claude Agent SDK skill plugin."""

from __future__ import annotations

import argparse
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
DEFAULT_POLICY_FILE = "sdk-runtime-policy.yaml"
DEFAULT_PLUGIN_FILE = ".claude-plugin/plugin.json"
DEFAULT_OUTPUT_FILE = "approved-skills.json"
ALLOWED_MODES = ("bootstrap", "interactive", "wake")
ALLOWED_FRONTMATTER_KEYS = frozenset({"name", "description"})
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", re.S)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
MODEL_TOOL_PATTERNS = (
    re.compile(r"[\"']name[\"']\s*:\s*[\"']([A-Za-z0-9_-]+)[\"']"),
    re.compile(r"registry\.register\(\s*(?:\n\s*)?name\s*=\s*[\"']([A-Za-z0-9_-]+)[\"']", re.S),
)
MODEL_TOOL_RE = re.compile(r"^(?:business_[a-z0-9_]+|browser_[a-z0-9_]+|web_search|web_extract|todo)$")


class ManifestValidationError(ValueError):
    pass


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
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        _fail(f"invalid JSON file {path}: {exc}")
    if not isinstance(value, dict):
        _fail(f"expected an object in {path}")
    return value


def _safe_relative(root: Path, raw: object, *, label: str) -> Path:
    value = str(raw or "").strip()
    path = Path(value)
    if not value or "\\" in value or path.is_absolute() or ".." in path.parts:
        _fail(f"{label} must be a contained POSIX relative path: {value!r}")
    try:
        (root / path).resolve().relative_to(root.resolve())
    except ValueError:
        _fail(f"{label} escapes its root: {value!r}")
    return path


def _canonical_prefix(raw: object, *, label: str) -> str:
    value = str(raw or "").strip()
    if not value or value.startswith("/") or "\\" in value:
        _fail(f"{label} must be a relative POSIX path")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if not parts or ".." in parts:
        _fail(f"{label} contains an unsafe path: {value!r}")
    return "/".join(parts)


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        _fail(f"{path} has no valid YAML frontmatter")
    try:
        metadata = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        _fail(f"invalid frontmatter in {path}: {exc}")
    if not isinstance(metadata, dict):
        _fail(f"frontmatter in {path} must be a mapping")
    unknown = set(metadata) - ALLOWED_FRONTMATTER_KEYS
    if unknown:
        _fail(f"{path} contains non-standard frontmatter keys: {sorted(unknown)}")
    return metadata, text


def _validate_name(raw: object, *, label: str) -> str:
    value = str(raw or "").strip()
    if not NAME_RE.fullmatch(value):
        _fail(f"{label} is not a lowercase hyphenated name: {value!r}")
    return value


def _walk_files(root: Path) -> list[tuple[str, Path]]:
    output: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or path.name.endswith((".pyc", ".pyo")):
            continue
        if path.is_symlink():
            _fail(f"skill content may not contain symlinks: {path}")
        if path.is_dir():
            continue
        if not path.is_file():
            _fail(f"skill content contains a non-regular entry: {path}")
        output.append((relative.as_posix(), path))
    return sorted(output)


def _digest_files(files: list[tuple[str, Path]]) -> str:
    digest = hashlib.sha256()
    for relative, path in sorted(files):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def content_digest(skill_dir: Path) -> str:
    return _digest_files(_walk_files(skill_dir))


def published_content_digest(skill_dir: Path, relative_files: list[str]) -> str:
    return _digest_files([(relative, skill_dir / relative) for relative in relative_files])


def _validate_markdown_links(skill_dir: Path, published_files: set[str]) -> None:
    root = skill_dir.resolve()
    for relative in sorted(published_files):
        if not relative.endswith(".md"):
            continue
        markdown = skill_dir / relative
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
                relative_target = candidate.relative_to(root).as_posix()
            except ValueError:
                _fail(f"reference escapes skill directory in {markdown}: {raw_target!r}")
            if not candidate.is_file():
                _fail(f"dangling reference in {markdown}: {raw_target!r}")
            if relative_target not in published_files:
                _fail(f"published reference is excluded from release in {markdown}: {raw_target!r}")


def _discover_model_tools(repo_root: Path) -> list[str]:
    sources = list((repo_root / "plugins" / "takyon").rglob("*.py"))
    sources.extend((repo_root / "tools").rglob("*.py"))
    names: set[str] = {"skill_read_resource"}
    for source in sources:
        text = source.read_text(encoding="utf-8", errors="replace")
        for pattern in MODEL_TOOL_PATTERNS:
            names.update(name for name in pattern.findall(text) if MODEL_TOOL_RE.fullmatch(name))
    if not names:
        _fail(f"no model tools discovered under {repo_root}")
    return sorted(names)


def _inventory_digest(names: list[str]) -> str:
    return "sha256:" + hashlib.sha256("\0".join(sorted(names)).encode("utf-8")).hexdigest()


def _validate_plugin(plugin: dict, release_plugin: dict, roots: list[str]) -> None:
    expected = {"name", "version", "description", "skills"}
    if set(plugin) != expected:
        _fail(f"plugin.json must contain exactly {sorted(expected)}")
    if plugin.get("name") != release_plugin.get("name") or plugin.get("version") != release_plugin.get("version"):
        _fail("plugin.json identity does not match release-skills.yaml")
    if plugin.get("skills") != [f"./{root}" for root in roots]:
        _fail("plugin.json discovery roots do not match release-skills.yaml")


def _load_runtime_policy(skills_root: Path, inventory: list[str], skill_names: list[str]) -> dict:
    policy = _load_yaml(skills_root / DEFAULT_POLICY_FILE)
    if set(policy) != {"schema_version", "modes"} or policy.get("schema_version") != SCHEMA_VERSION:
        _fail(f"{DEFAULT_POLICY_FILE} has unsupported fields or schema")
    modes = policy.get("modes")
    if not isinstance(modes, dict) or set(modes) != set(ALLOWED_MODES):
        _fail(f"{DEFAULT_POLICY_FILE} must define exactly {list(ALLOWED_MODES)}")
    available = set(inventory)
    compiled: dict[str, dict] = {}
    for mode in ALLOWED_MODES:
        raw = modes.get(mode)
        expected = {"required_tools", "allowed_tools", "denied_tools", "denied_write_paths"}
        if not isinstance(raw, dict) or set(raw) != expected:
            _fail(f"{DEFAULT_POLICY_FILE} mode {mode} must contain exactly {sorted(expected)}")
        normalized: dict[str, list[str]] = {}
        for field in ("required_tools", "allowed_tools", "denied_tools"):
            values = raw.get(field)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values):
                _fail(f"{DEFAULT_POLICY_FILE} {mode}.{field} must be a string list")
            cleaned = [value.strip() for value in values]
            if len(cleaned) != len(set(cleaned)):
                _fail(f"{DEFAULT_POLICY_FILE} {mode}.{field} contains duplicates")
            normalized[field] = cleaned
        overlap = set(normalized["allowed_tools"]) & set(normalized["denied_tools"])
        if overlap:
            _fail(f"{DEFAULT_POLICY_FILE} {mode} both allows and denies {sorted(overlap)}")
        ungranted_required = set(normalized["required_tools"]) - set(normalized["allowed_tools"])
        if ungranted_required:
            _fail(
                f"{DEFAULT_POLICY_FILE} {mode}.required_tools are not allowed: "
                f"{sorted(ungranted_required)}"
            )
        missing = set(normalized["allowed_tools"]) - available
        if missing:
            _fail(f"{DEFAULT_POLICY_FILE} {mode} allows unknown tools: {sorted(missing)}")
        paths = raw.get("denied_write_paths")
        if not isinstance(paths, list):
            _fail(f"{DEFAULT_POLICY_FILE} {mode}.denied_write_paths must be a list")
        denied_paths = [_canonical_prefix(value, label=f"{mode} denied write path") for value in paths]
        if len(denied_paths) != len(set(denied_paths)):
            _fail(f"{DEFAULT_POLICY_FILE} {mode}.denied_write_paths contains duplicates")
        compiled[mode] = {
            "allowed_skills": sorted(skill_names),
            "required_tools": sorted(normalized["required_tools"]),
            "allowed_tools": sorted(normalized["allowed_tools"]),
            "denied_tools": sorted(normalized["denied_tools"]),
            "denied_write_paths": denied_paths,
        }
    return compiled


def build_manifest(skills_root: Path = DEFAULT_SKILLS_ROOT) -> dict:
    skills_root = skills_root.resolve()
    release = _load_yaml(skills_root / DEFAULT_RELEASE_FILE)
    if set(release) != {"schema_version", "plugin", "discovery_roots", "skills"} or release.get("schema_version") != SCHEMA_VERSION:
        _fail("release-skills.yaml has unsupported or missing top-level fields")
    release_plugin = release.get("plugin")
    roots_raw = release.get("discovery_roots")
    entries = release.get("skills")
    if not isinstance(release_plugin, dict) or set(release_plugin) != {"name", "version"}:
        _fail("release plugin identity is malformed")
    _validate_name(release_plugin.get("name"), label="plugin name")
    if not str(release_plugin.get("version") or "").strip():
        _fail("release plugin version is required")
    if not isinstance(roots_raw, list) or not roots_raw:
        _fail("discovery_roots must be a non-empty list")
    roots: list[str] = []
    for raw in roots_raw:
        relative = _safe_relative(skills_root, raw, label="discovery root")
        if len(relative.parts) != 1 or not (skills_root / relative).is_dir():
            _fail(f"discovery root must be an existing direct child: {raw!r}")
        roots.append(relative.as_posix())
    if len(roots) != len(set(roots)):
        _fail("discovery_roots contains duplicates")
    if not isinstance(entries, list) or not entries:
        _fail("release skills must be a non-empty list")

    declared_names: list[str] = []
    declared_sources: list[str] = []
    manifest_entries: list[dict] = []
    for item in entries:
        expected = {"name", "source_path", "version", "legacy_names", "publish_files"}
        if not isinstance(item, dict) or set(item) != expected:
            _fail(f"every release skill needs exactly {sorted(expected)}")
        name = _validate_name(item.get("name"), label="release skill name")
        source = _safe_relative(skills_root, item.get("source_path"), label=f"{name} source_path")
        if len(source.parts) != 2 or source.parts[0] not in roots:
            _fail(f"{name} source must be a direct child of a discovery root")
        skill_dir = skills_root / source
        if not skill_dir.is_dir():
            _fail(f"{name} source directory is missing: {skill_dir}")
        version = str(item.get("version") or "").strip()
        if not version:
            _fail(f"{name} has no version")
        legacy_names = item.get("legacy_names")
        if not isinstance(legacy_names, list) or any(not isinstance(value, str) or not NAME_RE.fullmatch(value) for value in legacy_names):
            _fail(f"{name} has invalid legacy_names")
        if len(legacy_names) != len(set(legacy_names)):
            _fail(f"{name} has duplicate legacy_names")
        publish_raw = item.get("publish_files")
        if not isinstance(publish_raw, list) or not publish_raw:
            _fail(f"{name} has no publish_files")
        publish_files: list[str] = []
        for raw_file in publish_raw:
            relative = _safe_relative(skill_dir, raw_file, label=f"{name} published file")
            path = skill_dir / relative
            if path.is_symlink() or not path.is_file():
                _fail(f"{name} published file must be a regular non-symlink: {raw_file!r}")
            publish_files.append(relative.as_posix())
        if len(publish_files) != len(set(publish_files)) or "SKILL.md" not in publish_files:
            _fail(f"{name} publish_files must be unique and contain SKILL.md")
        actual_files = {relative for relative, _ in _walk_files(skill_dir)}
        if set(publish_files) != actual_files:
            _fail(
                f"{name} release must publish its complete native skill bundle: "
                f"missing={sorted(actual_files - set(publish_files))}, "
                f"extra={sorted(set(publish_files) - actual_files)}"
            )
        metadata, skill_text = _frontmatter(skill_dir / "SKILL.md")
        if _validate_name(metadata.get("name"), label=f"{name} frontmatter name") != name:
            _fail(f"{name} release/frontmatter name mismatch")
        description = str(metadata.get("description") or "").strip()
        if not description:
            _fail(f"{name} has no frontmatter description")
        if len(description) > 1024:
            _fail(f"{name} frontmatter description exceeds the Agent Skills 1024-character limit")
        _validate_markdown_links(skill_dir, set(publish_files))
        declared_names.append(name)
        declared_sources.append(source.as_posix())
        manifest_entries.append(
            {
                "name": name,
                "legacy_names": list(legacy_names),
                "source_path": source.as_posix(),
                "plugin_path": f"skills/{name}",
                "skill_file": f"{source.as_posix()}/SKILL.md",
                "version": version,
                "description": description,
                "allowed_modes": list(ALLOWED_MODES),
                "publish_files": [f"skills/{name}/{relative}" for relative in sorted(publish_files)],
                "source_digest": content_digest(skill_dir),
                "content_digest": published_content_digest(skill_dir, publish_files),
                "line_count": len(skill_text.splitlines()),
            }
        )

    if len(declared_names) != len(set(declared_names)) or len(declared_sources) != len(set(declared_sources)):
        _fail("release-skills.yaml contains duplicate names or source paths")
    discovered = {
        path.parent.relative_to(skills_root).as_posix()
        for root in roots
        for path in (skills_root / root).glob("*/SKILL.md")
    }
    if discovered != set(declared_sources):
        _fail(
            "release/discovery skill mismatch: "
            f"missing={sorted(set(declared_sources) - discovered)}, "
            f"unapproved={sorted(discovered - set(declared_sources))}"
        )
    plugin = _load_json(skills_root / DEFAULT_PLUGIN_FILE)
    _validate_plugin(plugin, release_plugin, roots)
    inventory = _discover_model_tools(skills_root.parent)
    mode_policy = _load_runtime_policy(skills_root, inventory, declared_names)
    manifest_entries.sort(key=lambda entry: entry["name"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_from": DEFAULT_RELEASE_FILE,
        "runtime_policy_source": DEFAULT_POLICY_FILE,
        "plugin": {"name": release_plugin["name"], "version": release_plugin["version"]},
        "discovery_roots": roots,
        "mode_tool_policy": mode_policy,
        "model_tool_inventory": inventory,
        "model_tool_inventory_digest": _inventory_digest(inventory),
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
            "description": "Versioned native skills approved for the Takyon operator agent",
        },
        indent=2,
    ) + "\n"


def verify_published_plugin(plugin_root: Path, manifest: dict) -> None:
    plugin_root = plugin_root.resolve()
    if _load_json(plugin_root / DEFAULT_OUTPUT_FILE) != manifest:
        _fail(f"published plugin manifest mismatch: {plugin_root}")
    if _load_json(plugin_root / DEFAULT_PLUGIN_FILE) != json.loads(_runtime_plugin_json(manifest)):
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
            f"missing={sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)}"
        )
    for entry in manifest["skills"]:
        installed = plugin_root / entry["plugin_path"]
        if content_digest(installed) != entry["content_digest"]:
            _fail(f"published skill digest mismatch: {entry['name']}")
    for path in [plugin_root, *plugin_root.rglob("*")]:
        mode = path.stat().st_mode
        if path.is_file() and (mode & 0o333 or not stat.S_ISREG(mode)):
            _fail(f"published plugin file must be read-only and non-executable: {path}")
        if path.is_dir() and mode & 0o222:
            _fail(f"published plugin path is writable: {path}")


def publish_plugin(skills_root: Path, destination: Path, manifest: dict | None = None) -> Path:
    skills_root = skills_root.resolve()
    destination = destination.expanduser()
    if not destination.is_absolute():
        _fail("published plugin destination must be absolute")
    destination = destination.resolve()
    try:
        destination.relative_to(skills_root.parent.resolve())
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
