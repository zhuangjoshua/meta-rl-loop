from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_primary_sdk_prepare_removes_mutable_legacy_skills_after_activation():
    source = (ROOT / "scripts/prepare-claude-agent-sdk-runtime.sh").read_text()
    activation = source.index('os.replace(sys.argv[1], sys.argv[2])')
    removal = source.index('rm -rf "$legacy_skills"')
    exports = source.index("printf 'export TAKYON_CLAUDE_SKILLS_PLUGIN")

    assert 'if [[ -L "$legacy_skills" ]]' in source
    assert activation < removal < exports


def test_canonical_entrypoint_prepares_primary_sdk_policy_before_exec(tmp_path):
    root = tmp_path / "takyon"
    runtime = root / "hermes-agent-main"
    scripts = root / "scripts"
    (runtime / ".venv" / "bin").mkdir(parents=True)
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / "takyon", root / "takyon")
    (root / "takyon").chmod(0o755)

    python = runtime / ".venv" / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    prepare = scripts / "prepare-claude-agent-sdk-runtime.sh"
    prepare.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'export TAKYON_CLAUDE_SKILLS_PLUGIN=/sealed/plugin'\n"
        "printf '%s\\n' 'export TAKYON_CLAUDE_SKILLS_MANIFEST=/sealed/plugin/approved-skills.json'\n"
        "printf '%s\\n' 'export TAKYON_CLAUDE_NODE_RUNTIME=/sealed/node-runtime'\n"
        "printf '%s\\n' 'export TAKYON_DISABLE_LEGACY_SKILL_SYNC=1'\n",
        encoding="utf-8",
    )
    prepare.chmod(0o755)
    nested = runtime / "takyon"
    nested.write_text(
        "#!/bin/sh\n"
        "python3 - \"$@\" <<'PY'\n"
        "import json, os, sys\n"
        "keys = ['TAKYON_HOME', 'TAKYON_CLAUDE_SKILLS_PLUGIN', "
        "'TAKYON_CLAUDE_NODE_RUNTIME', 'TAKYON_DISABLE_LEGACY_SKILL_SYNC', "
        "'TAKYON_MODEL']\n"
        "print(json.dumps({'argv': sys.argv[1:], 'env': {key: os.environ.get(key) for key in keys}}))\n"
        "PY\n",
        encoding="utf-8",
    )
    nested.chmod(0o755)

    env = dict(os.environ)
    env.pop("TAKYON_HOME", None)
    completed = subprocess.run(
        [str(root / "takyon"), "shell", "fresh-business"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        env=env,
    )
    payload = json.loads(completed.stdout)

    assert payload["argv"] == ["shell", "fresh-business"]
    assert payload["env"] == {
        "TAKYON_HOME": str(root / ".takyon"),
        "TAKYON_CLAUDE_SKILLS_PLUGIN": "/sealed/plugin",
        "TAKYON_CLAUDE_NODE_RUNTIME": "/sealed/node-runtime",
        "TAKYON_DISABLE_LEGACY_SKILL_SYNC": "1",
        "TAKYON_MODEL": "deepseek-v4-pro",
    }
