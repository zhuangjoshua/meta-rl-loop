from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


WORKER = Path(__file__).resolve().parents[3] / "deploy" / "cloudflare" / "product-worker" / "worker.js"


def _run_edge_case(*, deadline: str, path: str) -> dict[str, object]:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    source = WORKER.read_text(encoding="utf-8")
    script = f"""
const source = {json.dumps(source)};
const worker = (await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'))).default;
const current = {'b' * 32!r};
const previous = {'a' * 32!r};
const values = new Map([
  ['demo/current', current],
  ['demo/previous', JSON.stringify({{build_id: previous, servable_until: {json.dumps(deadline)}}})],
  ['demo/' + previous + '/assets/app-ABCdef12.js', 'previous-js'],
  ['demo/' + previous + '/assets/logo.png', 'previous-logo'],
]);
const bucket = {{
  async get(key) {{
    if (!values.has(key)) return null;
    const value = values.get(key);
    return {{
      body: value,
      size: value.length,
      httpEtag: '"etag"',
      async text() {{ return value; }},
      writeHttpMetadata(_headers) {{}},
    }};
  }},
}};
const response = await worker.fetch(
  new Request('https://demo.coscale.app/' + {json.dumps(path)}),
  {{PRODUCT_SITES: bucket, ORIGIN_HOST: 'origin.coscale.app'}},
);
console.log(JSON.stringify({{status: response.status, body: await response.text()}}));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_edge_falls_back_to_unexpired_previous_build_for_hashed_asset() -> None:
    result = _run_edge_case(
        deadline="2999-01-01T00:00:00Z",
        path="assets/app-ABCdef12.js",
    )
    assert result == {"status": 200, "body": "previous-js"}


@pytest.mark.parametrize(
    ("deadline", "path"),
    [
        ("2000-01-01T00:00:00Z", "assets/app-ABCdef12.js"),
        ("2999-01-01T00:00:00Z", "assets/logo.png"),
    ],
)
def test_edge_never_uses_expired_or_stable_named_previous_asset(
    deadline: str,
    path: str,
) -> None:
    result = _run_edge_case(deadline=deadline, path=path)
    assert result["status"] == 404
