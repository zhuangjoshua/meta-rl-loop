"""Node-gated behavior tests for canonical runtime-owned product record refs."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_KIT = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "takyon"
    / "subuser_app_kit"
    / "runtime-client.js"
)
_KIT_TYPES = _KIT.with_suffix(".d.ts")
_TSC = (
    _KIT.parent
    / "scaffold"
    / "node_modules"
    / ".bin"
    / "tsc"
)

_HARNESS = """
import { createSubuserRuntimeClient } from "KIT_URL";

const calls = [];
const responses = [];
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), method: init.method || "GET", body: init.body || "" });
  const body = responses.shift();
  if (!body) throw new Error("unexpected fetch");
  return { ok: true, status: 200, json: async () => body };
};

const client = createSubuserRuntimeClient({
  runtimeFeatures: ["records"],
  railState: { records: "live" },
  runtimeApiBase: "/api/takyon/apps/proposalforge",
  location: { href: "https://proposalforge.example/app", origin: "https://proposalforge.example" },
});

const canonical = {
  id: "runtime-owned-42",
  type: "proposal",
  title: "Acme SOW",
  data: { route_slug: "ui-invented-slug" },
};

responses.push({ success: true, record: canonical });
const saved = await client.saveRecord({
  type: "proposal",
  title: canonical.title,
  data: canonical.data,
});

responses.push({ success: true, record: canonical });
const read = await client.readRecord(saved.record.ref);

responses.push({ success: true, records: [canonical] });
const listed = await client.listRecords({ type: "proposal" });

responses.push({ success: true, record: canonical });
const legacy = await client.getRecord("proposal", canonical.id);

responses.push({ success: true, record: canonical });
const deleted = await client.deleteRecord(saved.record.ref);

let rawIdError = "";
try {
  await client.getRecord(canonical.id);
} catch (error) {
  rawIdError = String(error && error.message);
}

let adHocObjectError = "";
try {
  await client.readRecord({ type: "proposal", id: canonical.id });
} catch (error) {
  adHocObjectError = String(error && error.message);
}

console.log(JSON.stringify({
  ref: saved.record.ref,
  refType: typeof saved.record.ref,
  refStableOnRead: read.record.ref === saved.record.ref,
  refStableOnList: listed.records[0].ref === saved.record.ref,
  readUrl: calls[1].url,
  legacyUrl: calls[3].url,
  legacyRef: legacy.record.ref,
  deleteUrl: calls[4].url,
  deletedRef: deleted.record.ref,
  rawIdError,
  adHocObjectError,
  callCount: calls.length,
}));
"""


def test_save_returns_ref_that_roundtrips_without_an_ad_hoc_identifier(tmp_path):
    script = tmp_path / "record_ref_test.mjs"
    script.write_text(_HARNESS.replace("KIT_URL", _KIT.as_uri()), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["refType"] == "string"
    assert out["ref"].startswith("takyon-record-v1.")
    assert out["ref"] != "runtime-owned-42"
    assert out["refStableOnRead"] is True
    assert out["refStableOnList"] is True
    assert out["readUrl"].endswith("/records/proposal/runtime-owned-42")
    assert "ui-invented-slug" not in out["readUrl"]

    # Existing products using positional getRecord(type, id) remain operational.
    assert out["legacyUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["legacyRef"] == out["ref"]

    # A raw id or lookalike object cannot enter the new ref-only path and causes no request.
    assert "pass the ref returned by saveRecord" in out["rawIdError"]
    assert "pass the ref returned by saveRecord" in out["adHocObjectError"]
    assert out["deleteUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["deletedRef"] == out["ref"]
    assert out["callCount"] == 5


@pytest.mark.skipif(not _TSC.is_file(), reason="scaffold TypeScript compiler not installed")
def test_record_ref_type_cannot_be_replaced_with_an_ad_hoc_string(tmp_path):
    shutil.copy2(_KIT, tmp_path / "runtime-client.js")
    shutil.copy2(_KIT_TYPES, tmp_path / "runtime-client.d.ts")
    source = tmp_path / "contract.ts"
    source.write_text(
        """
import { createSubuserRuntimeClient, type RecordRef } from "./runtime-client.js";

const client = createSubuserRuntimeClient({ runtimeFeatures: ["records"] });

async function roundtrip() {
  const saved = await client.saveRecord({ type: "proposal", data: {} });
  const ref: RecordRef = saved.record.ref;
  await client.readRecord(ref);
  await client.getRecord(ref);
  // @ts-expect-error Positional type/id reads are runtime-compatibility only, not SDK API.
  await client.getRecord("proposal", "ui-invented-slug");
  // @ts-expect-error A route slug or raw record id is not a RecordRef.
  await client.readRecord("ui-invented-slug");
  // @ts-expect-error A lookalike object is not a RecordRef.
  await client.getRecord({ type: "proposal", id: "ui-invented-slug" });
  // @ts-expect-error Undeclared runtime capabilities do not exist in the SDK type environment.
  await client.publishRecord(ref);
}

void roundtrip;
""".strip()
        + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            str(_TSC),
            "--strict",
            "--noEmit",
            "--target",
            "ES2020",
            "--module",
            "ESNext",
            "--moduleResolution",
            "bundler",
            "--skipLibCheck",
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
