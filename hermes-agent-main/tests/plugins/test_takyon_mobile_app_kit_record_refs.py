"""Compiled behavior and type-contract tests for mobile RecordRef parity."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parents[2]
_CLIENT = (
    _ROOT
    / "plugins"
    / "takyon"
    / "mobile_app_kit"
    / "scaffold"
    / "_takyon"
    / "runtime-client.ts"
)


def _available_tsc() -> Path | None:
    candidates = (
        _ROOT
        / "plugins"
        / "takyon"
        / "subuser_app_kit"
        / "scaffold"
        / "node_modules"
        / ".bin"
        / "tsc",
        _ROOT / "web" / "node_modules" / ".bin" / "tsc",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    executable = shutil.which("tsc")
    return Path(executable) if executable else None


_TSC = _available_tsc()
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or _TSC is None,
    reason="Node and a workspace TypeScript compiler are required",
)


def _tsc(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    assert _TSC is not None
    return subprocess.run(
        [
            str(_TSC),
            "--strict",
            "--target",
            "ES2022",
            "--module",
            "ESNext",
            "--moduleResolution",
            "bundler",
            "--lib",
            "ES2022,DOM",
            "--skipLibCheck",
            *args,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _compile_client(tmp_path: Path) -> Path:
    shutil.copy2(_CLIENT, tmp_path / "runtime-client.ts")
    (tmp_path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    proc = _tsc("--outDir", "compiled", "runtime-client.ts", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    output = tmp_path / "compiled" / "runtime-client.js"
    assert output.is_file()
    return output


def test_mobile_record_refs_roundtrip_as_server_owned_opaque_locators(tmp_path):
    compiled = _compile_client(tmp_path)
    harness = tmp_path / "behavior.mjs"
    harness.write_text(
        f"""
import {{ createMobileRuntimeClient }} from {json.dumps(compiled.as_uri())};

const calls = [];
const responses = [];
globalThis.fetch = async (url, init = {{}}) => {{
  calls.push({{
    url: String(url),
    method: init.method || "GET",
    body: init.body || "",
    authorization: init.headers && init.headers.Authorization,
  }});
  const body = responses.shift();
  if (!body) throw new Error("unexpected fetch");
  return {{
    ok: true,
    status: 200,
    text: async () => JSON.stringify(body),
  }};
}};

const client = createMobileRuntimeClient(
  {{
    runtimeApiBase: "https://proposal.coscale.app/api/takyon/apps/proposal",
    runtimeFeatures: ["records"],
  }},
  {{
    getToken: async () => "mobile-session",
    setToken: async () => undefined,
    clearToken: async () => undefined,
  }},
);
const serverRef = "tkr_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const canonical = {{
  id: "runtime-owned-42",
  type: "proposal",
  ref: serverRef,
  title: "Acme SOW",
  data: {{ route_slug: "ui-invented-slug" }},
}};

responses.push({{
  success: true,
  id: "envelope-operation-id",
  record: canonical,
}});
const saved = await client.saveRecord({{
  type: "proposal",
  title: canonical.title,
  data: canonical.data,
}});

responses.push({{ success: true, record: canonical }});
const read = await client.readRecord(saved.record.ref);

responses.push({{ success: true, record: {{ ...canonical, title: "Acme SOW revised" }} }});
const updated = await client.saveRecord({{
  ref: saved.ref,
  title: "Acme SOW revised",
  data: canonical.data,
}});

let missingDataError = "";
try {{
  await client.saveRecord({{ ref: saved.ref, title: "Missing data" }});
}} catch (error) {{
  missingDataError = String(error && error.message);
}}

let mismatchedTypeError = "";
try {{
  await client.saveRecord({{ ref: saved.ref, type: "invoice", data: canonical.data }});
}} catch (error) {{
  mismatchedTypeError = String(error && error.message);
}}

let mismatchedIdError = "";
try {{
  await client.saveRecord({{ ref: saved.ref, id: "another-record", data: canonical.data }});
}} catch (error) {{
  mismatchedIdError = String(error && error.message);
}}

let rawRefAliasError = "";
try {{
  await client.saveRecord({{
    ref: saved.ref,
    record_ref: "tkr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    data: canonical.data,
  }});
}} catch (error) {{
  rawRefAliasError = String(error && error.message);
}}

let conflictingTypeAliasesError = "";
try {{
  await client.saveRecord({{ record_type: "proposal", type: "invoice", data: canonical.data }});
}} catch (error) {{
  conflictingTypeAliasesError = String(error && error.message);
}}

let conflictingIdAliasesError = "";
try {{
  await client.saveRecord({{
    record_type: "proposal",
    record_id: canonical.id,
    id: "another-record",
    data: canonical.data,
  }});
}} catch (error) {{
  conflictingIdAliasesError = String(error && error.message);
}}

responses.push({{ success: true, records: [canonical] }});
const listed = await client.listRecords({{ type: "proposal" }});

responses.push({{ success: true, record: canonical }});
const deleted = await client.deleteRecord(saved.record.ref);

const secondRef = "tkr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
responses.push({{ success: true, records: [{{ ...canonical, ref: secondRef }}] }});
const serverReferenced = await client.listRecords({{ type: "proposal" }});

let rawIdError = "";
try {{
  await client.getRecord(canonical.id);
}} catch (error) {{
  rawIdError = String(error && error.message);
}}

console.log(JSON.stringify({{
  ref: saved.record.ref,
  flatRef: saved.ref,
  hasRawId: "id" in saved || "id" in saved.record || "record_id" in saved.record,
  refStableOnRead: read.ref === saved.ref,
  refStableOnUpdate: updated.record.ref === saved.ref,
  refStableOnList: listed.records[0].ref === saved.ref,
  createUrl: calls[0].url,
  readUrl: calls[1].url,
  updateUrl: calls[2].url,
  updateBody: calls[2].body,
  deleteUrl: calls[4].url,
  deletedRef: deleted.ref,
  serverRefPreserved: serverReferenced.records[0].ref,
  authorization: calls[0].authorization,
  missingDataError,
  mismatchedTypeError,
  mismatchedIdError,
  rawRefAliasError,
  conflictingTypeAliasesError,
  conflictingIdAliasesError,
  rawIdError,
  callCount: calls.length,
}}));
""".strip()
        + "\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        ["node", str(harness)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])

    assert out["ref"].startswith("tkr_")
    assert out["flatRef"] == out["ref"]
    assert out["hasRawId"] is False
    assert out["refStableOnRead"] is True
    assert out["refStableOnUpdate"] is True
    assert out["refStableOnList"] is True
    assert out["createUrl"].endswith("/records")
    assert out["readUrl"].endswith(f"/records/by-ref/{out['ref']}")
    assert out["updateUrl"].endswith(f"/records/by-ref/{out['ref']}")
    assert "record_id" not in out["updateBody"]
    assert "record_type" not in out["updateBody"]
    assert out["deleteUrl"].endswith(f"/records/by-ref/{out['ref']}")
    assert out["deletedRef"] == out["ref"]
    assert out["serverRefPreserved"] == "tkr_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert out["authorization"] == "Bearer mobile-session"
    assert out["missingDataError"] == "data is required"
    assert out["mismatchedTypeError"] == "record_type cannot accompany a ref update"
    assert out["mismatchedIdError"] == "raw record identifiers are not accepted; use the runtime-owned ref"
    assert out["rawRefAliasError"] == "raw record identifiers are not accepted; use the runtime-owned ref"
    assert out["conflictingTypeAliasesError"] == "record_type does not match the supplied type"
    assert out["conflictingIdAliasesError"] == "raw record identifiers are not accepted; use the runtime-owned ref"
    assert "pass the ref returned by saveRecord" in out["rawIdError"]
    assert out["callCount"] == 6


def test_mobile_generated_sdk_exposes_only_ref_based_record_mutations(tmp_path):
    shutil.copy2(_CLIENT, tmp_path / "runtime-client.ts")
    (tmp_path / "contract.ts").write_text(
        """
import { createMobileRuntimeClient, type RecordRef } from "./runtime-client";

const client = createMobileRuntimeClient(
  { runtimeApiBase: "https://proposal.coscale.app/api/takyon/apps/proposal", runtimeFeatures: ["records"] },
  { getToken: async () => "", setToken: async () => undefined, clearToken: async () => undefined },
);

async function roundtrip() {
  const byType = await client.saveRecord({ type: "proposal", data: {} });
  await client.saveRecord({ record_type: "proposal", data: {} });
  const nestedRef: RecordRef = byType.record.ref;
  const flatRef: RecordRef = byType.ref;
  await client.readRecord(nestedRef);
  await client.getRecord(flatRef);
  await client.saveRecord({ ref: flatRef, title: "Updated", data: {} });
  await client.deleteRecord(flatRef);
  // @ts-expect-error Record upserts require the complete data value.
  await client.saveRecord({ ref: flatRef, title: "Missing data" });
  // @ts-expect-error The backend rejects null as missing record data.
  await client.saveRecord({ ref: flatRef, data: null });
  // @ts-expect-error Positional reads are runtime compatibility, not generated SDK API.
  await client.getRecord("proposal", "ui-invented-slug");
  // @ts-expect-error Positional deletes are runtime compatibility, not generated SDK API.
  await client.deleteRecord("proposal", "ui-invented-slug");
  // @ts-expect-error A raw string is not a RecordRef.
  await client.readRecord("ui-invented-slug");
  // @ts-expect-error Raw record IDs are runtime compatibility, not generated SDK input.
  await client.saveRecord({ record_type: "proposal", record_id: "runtime-owned-42", data: {} });
  // @ts-expect-error Updates preserve the opaque ref rather than a response id.
  await client.saveRecord({ type: "proposal", id: "runtime-owned-42", data: {} });
}

void roundtrip;
""".strip()
        + "\n",
        encoding="utf-8",
    )

    proc = _tsc("--noEmit", "contract.ts", cwd=tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
