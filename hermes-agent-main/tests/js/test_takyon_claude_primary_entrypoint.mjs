import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { Duplex } from "node:stream";
import test from "node:test";

import {
  createBridgeMcpServer,
  createBridgeSessionStore,
  runPrimaryEntrypoint,
  validatePrimaryEntrypointRequest,
} from "../../scripts/takyon-claude-primary-entrypoint.mjs";

function request(overrides = {}) {
  return {
    prompt: "Build the business.",
    systemPrompt: "Follow Takyon policy.",
    cwd: "/tmp/workspace",
    workspaceRoot: "/tmp/workspace",
    configDir: "/tmp/config",
    pluginPath: "/opt/takyon-skills/release",
    manifestPath: "/opt/takyon-skills/release/approved-skills.json",
    mode: "ceo_bootstrap",
    epoch: "bootstrap",
    sessionId: randomUUID(),
    resumeSessionId: "",
    sessionProjectKey: "takyon:operator:user:business:acme",
    maxTurns: 90,
    maxBudgetUsd: 20,
    effort: "high",
    toolDefinitions: [{
      name: "business_read_business",
      description: "Read the bound business.",
      inputSchema: { type: "object", properties: {} },
    }],
    pathToClaudeCodeExecutable: "",
    ...overrides,
  };
}

class ReplyingBridgeSocket extends Duplex {
  constructor(handler) {
    super();
    this.handler = handler;
    this.buffer = "";
  }

  _read() {}

  _write(chunk, _encoding, callback) {
    this.buffer += chunk.toString("utf8");
    while (this.buffer.includes("\n")) {
      const split = this.buffer.indexOf("\n");
      const line = this.buffer.slice(0, split);
      this.buffer = this.buffer.slice(split + 1);
      const message = JSON.parse(line);
      const result = this.handler(message);
      this.push(`${JSON.stringify({ id: message.id, ok: true, result })}\n`);
    }
    callback();
  }
}

test("entrypoint request is exact, bounded, and requires one stable session", () => {
  const sessionId = randomUUID();
  const parsed = validatePrimaryEntrypointRequest(request({ sessionId }));
  assert.equal(parsed.sessionId, sessionId);
  assert.equal(parsed.maxTurns, 90);
  assert.throws(
    () => validatePrimaryEntrypointRequest(request({ extraAuthority: "forbidden" })),
    (error) => error.code === "entrypoint_request" && /unknown request fields/.test(error.message)
  );
  assert.throws(
    () => validatePrimaryEntrypointRequest(request({ sessionId: "", resumeSessionId: "" })),
    (error) => error.code === "entrypoint_request" && /exactly one/.test(error.message)
  );
});

test("manual compaction request is canonical and requires a resumed session", () => {
  const resumeSessionId = randomUUID();
  const parsed = validatePrimaryEntrypointRequest(request({
    operation: "compact",
    prompt: "/compact preserve launch decisions",
    sessionId: "",
    resumeSessionId,
  }));
  assert.equal(parsed.operation, "compact");
  assert.equal(parsed.resumeSessionId, resumeSessionId);
  assert.throws(
    () => validatePrimaryEntrypointRequest(request({ operation: "compact", prompt: "/compact" })),
    (error) => error.code === "entrypoint_request" && /resumed session/.test(error.message)
  );
  assert.throws(
    () => validatePrimaryEntrypointRequest(request({
      operation: "compact",
      prompt: "/compact ok\n/run something",
      sessionId: "",
      resumeSessionId,
    })),
    (error) => error.code === "entrypoint_request" && /canonical/.test(error.message)
  );
});

test("bridge SessionStore pins project/session scope and exposes all resume operations", async () => {
  const sessionId = randomUUID();
  const calls = [];
  const store = createBridgeSessionStore({
    projectKey: "stable-project",
    sessionId,
    request: async (type, payload) => {
      calls.push({ type, payload });
      if (type === "session_load") return [{ type: "user", uuid: "entry-1" }];
      if (type === "session_list_subkeys") return ["subagents/agent-a"];
      return { appended: true };
    },
  });
  await store.append(
    { projectKey: "host-path-that-is-ignored", sessionId },
    [{ type: "user", uuid: "entry-1", opaque: { value: 1 } }]
  );
  assert.deepEqual(
    await store.load({ projectKey: "different-host-path", sessionId }),
    [{ type: "user", uuid: "entry-1" }]
  );
  assert.deepEqual(
    await store.listSubkeys({ projectKey: "ignored", sessionId }),
    ["subagents/agent-a"]
  );
  await store.delete({ projectKey: "ignored", sessionId });
  assert.deepEqual(calls.map((call) => call.type), [
    "session_append",
    "session_load",
    "session_list_subkeys",
    "session_delete",
  ]);
  assert.ok(calls.every((call) => call.payload.key.projectKey === "stable-project"));
  await assert.rejects(
    store.load({ projectKey: "ignored", sessionId: randomUUID() }),
    (error) => error.code === "session_scope"
  );
});

test("dynamic MCP tools invoke only the private bridge with converted schemas", async () => {
  const calls = [];
  const definitions = [{
    name: "business_write_file",
    description: "Write a bound business file.",
    inputSchema: {
      type: "object",
      properties: { path: { type: "string" } },
      required: ["path"],
    },
  }];
  const sdk = {
    tool(name, description, shape, handler, extras) {
      return { name, description, shape, handler, extras };
    },
    createSdkMcpServer(options) {
      return options;
    },
  };
  const server = createBridgeMcpServer({
    sdk,
    z: { fromJSONSchema: () => ({ shape: { path: "zod-string" } }) },
    toolDefinitions: definitions,
    request: async (type, payload) => {
      calls.push({ type, payload });
      return JSON.stringify({ success: true });
    },
  });
  assert.equal(server.name, "takyon");
  assert.equal(server.alwaysLoad, true);
  assert.deepEqual(server.tools[0].shape, { path: "zod-string" });
  const result = await server.tools[0].handler({ path: "product/site/app.ts" }, {});
  assert.equal(calls[0].type, "tool");
  assert.equal(calls[0].payload.name, "business_write_file");
  assert.deepEqual(result, {
    content: [{ type: "text", text: JSON.stringify({ success: true }) }],
  });
});

test("entrypoint always wires eager durable sessions and Skill-only exact MCP tools", async () => {
  const sessionId = randomUUID();
  const bridgeCalls = [];
  const socket = new ReplyingBridgeSocket((message) => {
    bridgeCalls.push(message);
    if (message.type === "session_load") return [{ type: "user", uuid: "u1" }];
    if (message.type === "session_list_subkeys") return [];
    if (message.type === "tool") return JSON.stringify({ success: true });
    return { appended: true };
  });
  const sdkModule = {
    tool(name, _description, _shape, handler) {
      return { name, handler };
    },
    createSdkMcpServer(options) {
      return options;
    },
  };
  const result = await runPrimaryEntrypoint(request({ sessionId }), {
    socket,
    sdkModule,
    zodModule: { z: { fromJSONSchema: () => ({ shape: {} }) } },
    sourceEnv: {},
    runTurn: async (configuration) => {
      assert.equal(configuration.mode, "bootstrap");
      assert.deepEqual(configuration.localTools, ["Skill"]);
      assert.deepEqual(configuration.allowedMcpTools, [
        "mcp__takyon__business_read_business",
      ]);
      assert.equal(configuration.allowBash, false);
      assert.equal(configuration.sessionStoreFlush, "eager");
      assert.ok(configuration.sessionStore);
      await configuration.sessionStore.append(
        { projectKey: "/different/mac/path", sessionId },
        [{ type: "user", uuid: "u1" }]
      );
      await configuration.sessionStore.load({ projectKey: "/different/vps/path", sessionId });
      await configuration.mcpServers.takyon.tools[0].handler({}, {});
      return { session_id: sessionId, summary: "done" };
    },
  });
  assert.equal(result.summary, "done");
  assert.deepEqual(bridgeCalls.map((call) => call.type), [
    "session_append",
    "session_load",
    "tool",
  ]);
});

for (const [wireMode, sdkMode] of [
  ["ceo_bootstrap", "bootstrap"],
  ["ceo_wake", "wake"],
]) {
  test(`entrypoint maps ${wireMode} wire mode to ${sdkMode} SDK mode`, async () => {
    const sessionId = randomUUID();
    const socket = new ReplyingBridgeSocket(() => ({ appended: true }));
    const sdkModule = {
      tool(name, _description, _shape, handler) {
        return { name, handler };
      },
      createSdkMcpServer(options) {
        return options;
      },
    };

    const result = await runPrimaryEntrypoint(request({ mode: wireMode, sessionId }), {
      socket,
      sdkModule,
      zodModule: { z: { fromJSONSchema: () => ({ shape: {} }) } },
      sourceEnv: {},
      runTurn: async (configuration) => {
        assert.equal(configuration.mode, sdkMode);
        return { session_id: sessionId, summary: "done" };
      },
    });

    assert.equal(result.summary, "done");
  });
}

test("entrypoint refuses normalized HANDOFF modes on its task-kind wire boundary", () => {
  for (const mode of ["bootstrap", "wake"]) {
    assert.throws(
      () => validatePrimaryEntrypointRequest(request({ mode })),
      (error) => error.code === "entrypoint_request" && /unsupported primary runtime mode/.test(error.message)
    );
  }
});

test("manual compaction entrypoint exposes no Skill or MCP tools", async () => {
  const resumeSessionId = randomUUID();
  const socket = new ReplyingBridgeSocket((message) => {
    if (message.type === "session_load") return [{ type: "user", uuid: "u1" }];
    return { appended: true };
  });
  const sdkModule = {
    tool() {
      throw new Error("compact must not construct MCP tools");
    },
    createSdkMcpServer() {
      throw new Error("compact must not construct an MCP server");
    },
  };
  const result = await runPrimaryEntrypoint(request({
    operation: "compact",
    prompt: "/compact preserve launch decisions",
    sessionId: "",
    resumeSessionId,
  }), {
    socket,
    sdkModule,
    zodModule: { z: {} },
    sourceEnv: {},
    runTurn: async (configuration) => {
      assert.equal(configuration.operation, "compact");
      assert.equal(configuration.resumeSessionId, resumeSessionId);
      assert.deepEqual(configuration.localTools, []);
      assert.deepEqual(configuration.allowedMcpTools, []);
      assert.deepEqual(configuration.mcpServers, {});
      assert.equal(configuration.sessionStoreFlush, "eager");
      return { session_id: resumeSessionId, operation: "compact" };
    },
  });
  assert.equal(result.operation, "compact");
});
