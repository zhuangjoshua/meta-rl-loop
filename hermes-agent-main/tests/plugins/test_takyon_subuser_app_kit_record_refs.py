"""Node-gated behavior tests for canonical runtime-owned product record refs."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from plugins.takyon import core as takyon_core


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

const serverRef = "takyon-record-v1.%5b1%2c%20%22proposal%22%2c%20%22runtime-owned-42%22%5d";
const canonical = {
  id: "runtime-owned-42",
  type: "proposal",
  title: "Acme SOW",
  data: { route_slug: "ui-invented-slug" },
};

responses.push({
  success: true,
  id: "envelope-operation-id",
  ref: "envelope-not-a-record-ref",
  record: canonical,
});
const saved = await client.saveRecord({
  type: "proposal",
  title: canonical.title,
  data: canonical.data,
});

responses.push({ success: true, record: canonical });
const read = await client.readRecord(saved.record.ref);

responses.push({ success: true, record: { ...canonical, title: "Acme SOW revised" } });
const updated = await client.saveRecord({
  ref: saved.ref,
  title: "Acme SOW revised",
  data: canonical.data,
});

let missingDataError = "";
try {
  await client.saveRecord({ ref: saved.ref, title: "Missing data" });
} catch (error) {
  missingDataError = String(error && error.message);
}

let mismatchedTypeError = "";
try {
  await client.saveRecord({ ref: saved.ref, type: "invoice", data: canonical.data });
} catch (error) {
  mismatchedTypeError = String(error && error.message);
}

let mismatchedIdError = "";
try {
  await client.saveRecord({ ref: saved.ref, id: "another-record", data: canonical.data });
} catch (error) {
  mismatchedIdError = String(error && error.message);
}

let conflictingRefAliasesError = "";
try {
  await client.saveRecord({
    ref: saved.ref,
    record_ref: "takyon-record-v1.%5B1%2C%22proposal%22%2C%22other%22%5D",
    data: canonical.data,
  });
} catch (error) {
  conflictingRefAliasesError = String(error && error.message);
}

let conflictingTypeAliasesError = "";
try {
  await client.saveRecord({ record_type: "proposal", type: "invoice", data: canonical.data });
} catch (error) {
  conflictingTypeAliasesError = String(error && error.message);
}

let conflictingIdAliasesError = "";
try {
  await client.saveRecord({
    record_type: "proposal",
    record_id: canonical.id,
    id: "another-record",
    data: canonical.data,
  });
} catch (error) {
  conflictingIdAliasesError = String(error && error.message);
}

responses.push({ success: true, records: [canonical] });
const listed = await client.listRecords({ type: "proposal" });

responses.push({ success: true, record: canonical });
const legacy = await client.getRecord("proposal", canonical.id);

responses.push({ success: true, record: canonical });
const legacyDeleted = await client.deleteRecord("proposal", canonical.id);

responses.push({ success: true, record: canonical });
const deleted = await client.deleteRecord(saved.record.ref);

responses.push({ success: true, records: [{ ...canonical, ref: serverRef }] });
const serverReferenced = await client.listRecords({ type: "proposal" });

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
  flatRef: saved.ref,
  flatId: saved.id,
  refType: typeof saved.record.ref,
  refStableOnRead: read.record.ref === saved.record.ref,
  flatReadRefStable: read.ref === saved.record.ref,
  updateRefStable: updated.ref === saved.record.ref,
  updateUrl: calls[2].url,
  updateBody: calls[2].body,
  refStableOnList: listed.records[0].ref === saved.record.ref,
  readUrl: calls[1].url,
  legacyUrl: calls[4].url,
  legacyRef: legacy.record.ref,
  legacyDeleteUrl: calls[5].url,
  legacyDeletedRef: legacyDeleted.record.ref,
  deleteUrl: calls[6].url,
  deletedRef: deleted.record.ref,
  serverRefPreserved: serverReferenced.records[0].ref,
  rawIdError,
  adHocObjectError,
  missingDataError,
  mismatchedTypeError,
  mismatchedIdError,
  conflictingRefAliasesError,
  conflictingTypeAliasesError,
  conflictingIdAliasesError,
  callCount: calls.length,
}));
"""


def test_product_worker_contract_requires_record_refs_for_every_locator_operation():
    contract = takyon_core._subuser_app_kit_contract_block(None)

    assert "`saveRecord({ ref, data, ...fields })`" in contract
    assert "`deleteRecord(ref)`" in contract
    assert "Never update with raw `id`/`record_id`" in contract
    assert "never use positional `getRecord(type, id)`" in contract
    assert "preserve and send the complete `data` value" in contract


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
    assert out["flatRef"] == out["ref"]
    assert out["flatId"] == "runtime-owned-42"
    assert out["refStableOnRead"] is True
    assert out["flatReadRefStable"] is True
    assert out["updateRefStable"] is True
    assert out["refStableOnList"] is True
    assert out["readUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["updateUrl"].endswith("/records/proposal/runtime-owned-42")
    assert '"record_id":"runtime-owned-42"' in out["updateBody"]
    assert "takyon-record-v1" not in out["updateBody"]
    assert "ui-invented-slug" not in out["readUrl"]

    # Existing products using positional getRecord(type, id) remain operational.
    assert out["legacyUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["legacyRef"] == out["ref"]
    assert out["legacyDeleteUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["legacyDeletedRef"] == out["ref"]

    # A raw id or lookalike object cannot enter the new ref-only path and causes no request.
    assert "pass the ref returned by saveRecord" in out["rawIdError"]
    assert "pass the ref returned by saveRecord" in out["adHocObjectError"]
    assert out["missingDataError"] == "data is required"
    assert out["mismatchedTypeError"] == "record_type does not match the supplied record ref"
    assert out["mismatchedIdError"] == "record_id does not match the supplied record ref"
    assert out["conflictingRefAliasesError"] == "ref does not match the supplied record_ref"
    assert out["conflictingTypeAliasesError"] == "record_type does not match the supplied type"
    assert out["conflictingIdAliasesError"] == "record_id does not match the supplied id"
    assert out["deleteUrl"].endswith("/records/proposal/runtime-owned-42")
    assert out["deletedRef"] == out["ref"]
    assert out["serverRefPreserved"] == (
        "takyon-record-v1.%5b1%2c%20%22proposal%22%2c%20%22runtime-owned-42%22%5d"
    )
    assert out["callCount"] == 8


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
  await client.saveRecord({ record_type: "proposal", data: {} });
  const ref: RecordRef = saved.record.ref;
  const flatRef: RecordRef = saved.ref;
  await client.readRecord(ref);
  await client.getRecord(ref);
  await client.saveRecord({ ref, title: "Updated", data: {} });
  // @ts-expect-error Record upserts require the complete data value.
  await client.saveRecord({ ref, title: "Missing data" });
  // @ts-expect-error The backend rejects null as missing record data.
  await client.saveRecord({ ref, data: null });
  // @ts-expect-error Positional type/id reads are runtime-compatibility only, not SDK API.
  await client.getRecord("proposal", "ui-invented-slug");
  // @ts-expect-error Positional type/id deletes are runtime-compatibility only, not SDK API.
  await client.deleteRecord("proposal", "ui-invented-slug");
  // @ts-expect-error A route slug or raw record id is not a RecordRef.
  await client.readRecord("ui-invented-slug");
  // @ts-expect-error A lookalike object is not a RecordRef.
  await client.getRecord({ type: "proposal", id: "ui-invented-slug" });
  // @ts-expect-error Raw record IDs are runtime compatibility, not generated-app SDK input.
  await client.saveRecord({ record_type: "proposal", record_id: "runtime-owned-42", data: {} });
  // @ts-expect-error Updates must preserve the opaque ref rather than a response id.
  await client.saveRecord({ type: "proposal", id: "runtime-owned-42", data: {} });
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
