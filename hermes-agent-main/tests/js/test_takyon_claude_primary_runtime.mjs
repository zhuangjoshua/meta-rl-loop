import assert from "node:assert/strict";
import { createHash, randomUUID } from "node:crypto";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  PINNED_CLAUDE_BUILTIN_SKILLS,
  PINNED_CLAUDE_INIT_TOOLS,
  PRIMARY_AGENT_MODEL,
  PrimaryRuntimeError,
  buildPrimaryRuntimeOptions,
  createBoundedSessionStore,
  createJsonLineProgressSink,
  createPrimaryToolGuard,
  createProgressProjector,
  createPrimaryStreamingInput,
  digestSkillDirectory,
  resolveSafeboxBrokerConfiguration,
  runPrimaryAgentTurn,
  verifyApprovedSkillPlugin,
} from "../../scripts/takyon-claude-primary-runtime.mjs";

const BROKER_ENV = Object.freeze({
  PATH: "/usr/bin:/bin",
  HOME: "/tmp",
  ANTHROPIC_API_KEY: "short-lived-safebox-capability",
  ANTHROPIC_BASE_URL: "http://10.116.0.2:8000",
  TAKYON_CLAUDE_AGENT_MODEL: PRIMARY_AGENT_MODEL,
  ANTHROPIC_MODEL: PRIMARY_AGENT_MODEL,
  ANTHROPIC_DEFAULT_OPUS_MODEL: PRIMARY_AGENT_MODEL,
  ANTHROPIC_DEFAULT_SONNET_MODEL: PRIMARY_AGENT_MODEL,
  ANTHROPIC_DEFAULT_HAIKU_MODEL: PRIMARY_AGENT_MODEL,
  CLAUDE_CODE_SUBAGENT_MODEL: PRIMARY_AGENT_MODEL,
});

async function chmodTree(root, fileMode, directoryMode) {
  const stat = await fs.lstat(root);
  if (stat.isDirectory()) {
    await fs.chmod(root, directoryMode | 0o200);
    for (const name of await fs.readdir(root)) {
      await chmodTree(path.join(root, name), fileMode, directoryMode);
    }
    await fs.chmod(root, directoryMode);
  } else {
    await fs.chmod(root, fileMode);
  }
}

async function createApprovedPlugin(t, { writable = false, secondSkill = false } = {}) {
  const temporaryRoot = await fs.mkdtemp(path.join(os.tmpdir(), "takyon-primary-runtime-"));
  const pluginPath = path.join(temporaryRoot, "plugin");
  const workspaceRoot = path.join(temporaryRoot, "workspace");
  const configDir = path.join(temporaryRoot, "sessions");
  await fs.mkdir(path.join(pluginPath, ".claude-plugin"), { recursive: true });
  await fs.mkdir(path.join(pluginPath, "skills", "market-research"), { recursive: true });
  await fs.mkdir(workspaceRoot, { recursive: true });
  await fs.mkdir(configDir, { recursive: true });
  await fs.writeFile(
    path.join(pluginPath, ".claude-plugin", "plugin.json"),
    JSON.stringify({
      name: "takyon-approved-skills",
      version: "test-1",
      skills: ["./skills"],
    })
  );
  await fs.writeFile(
    path.join(pluginPath, "skills", "market-research", "SKILL.md"),
    [
      "---",
      "name: market-research",
      "description: Research current markets. Use for evidence, not implementation.",
      "---",
      "",
      "# Market Research",
      "",
      "Use primary evidence.",
      "",
    ].join("\n")
  );
  await fs.writeFile(
    path.join(pluginPath, "skills", "market-research", "reference.txt"),
    "evidence rules\n"
  );
  const specs = [{
    name: "market-research",
    source_path: "takyon/market-research",
    plugin_path: "skills/market-research",
    skill_file: "takyon/market-research/SKILL.md",
    version: "1",
    description: "Research current markets. Use for evidence, not implementation.",
    allowed_modes: ["interactive", "bootstrap", "wake"],
    content_digest: await digestSkillDirectory(path.join(pluginPath, "skills", "market-research")),
  }];
  if (secondSkill) {
    await fs.mkdir(path.join(pluginPath, "skills", "design-taste"), { recursive: true });
    await fs.writeFile(
      path.join(pluginPath, "skills", "design-taste", "SKILL.md"),
      "---\nname: design-taste\ndescription: Designs product surfaces. Use for visual work, not research.\n---\n\n# Taste\n"
    );
    specs.push({
      name: "design-taste",
      source_path: "creative/taste",
      plugin_path: "skills/design-taste",
      skill_file: "creative/taste/SKILL.md",
      version: "1",
      description: "Designs product surfaces. Use for visual work, not research.",
      allowed_modes: ["bootstrap"],
      content_digest: await digestSkillDirectory(path.join(pluginPath, "skills", "design-taste")),
    });
  }
  const manifestPath = path.join(pluginPath, "approved-skills.json");
  const modeToolPolicy = Object.fromEntries(
    ["interactive", "bootstrap", "wake"].map((mode) => [mode, {
      allowed_skills: specs.filter((skill) => skill.allowed_modes.includes(mode)).map((skill) => skill.name),
    }])
  );
  await fs.writeFile(manifestPath, JSON.stringify({
    schema_version: 1,
    generated_from: "release-skills.yaml",
    plugin: { name: "takyon-approved-skills", version: "test-1" },
    discovery_roots: secondSkill ? ["creative", "takyon"] : ["takyon"],
    mode_tool_policy: modeToolPolicy,
    skills: specs,
  }));
  if (!writable) await chmodTree(pluginPath, 0o444, 0o555);
  t.after(async () => {
    await chmodTree(pluginPath, 0o644, 0o755).catch(() => {});
    await fs.rm(temporaryRoot, { recursive: true, force: true });
  });
  return { temporaryRoot, pluginPath, manifestPath, workspaceRoot, configDir, specs };
}

function sdkInit(plugin, sessionId, skillNames, tools = ["Skill"]) {
  return {
    type: "system",
    subtype: "init",
    session_id: sessionId,
    uuid: randomUUID(),
    model: PRIMARY_AGENT_MODEL,
    tools,
    skills: skillNames.map((name) => `${plugin}:` + name),
    plugins: [{ name: plugin, path: "" }],
  };
}

function fakeSdk(messages, capture = {}) {
  return {
    query(args) {
      capture.args = args;
      return (async function* stream() {
        for (const message of messages) yield message;
      })();
    },
  };
}

function streamingLifecycleSdk(messages, capture = {}) {
  return {
    query(args) {
      capture.args = args;
      return (async function* stream() {
        const prompt = args.prompt[Symbol.asyncIterator]();
        capture.initialPrompt = await prompt.next();
        for (const message of messages) yield message;
        capture.inputAfterResult = await prompt.next();
        capture.drained = true;
      })();
    },
  };
}

function successfulResult(sessionId, result = "Completed") {
  return {
    type: "result",
    subtype: "success",
    is_error: false,
    session_id: sessionId,
    uuid: randomUUID(),
    result,
    total_cost_usd: 0.125,
    usage: { input_tokens: 10, output_tokens: 5 },
  };
}

test("skill directory digest uses sorted POSIX paths, NUL separators, and raw bytes", async (t) => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), "takyon-skill-digest-"));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  await fs.mkdir(path.join(root, "nested"));
  await fs.writeFile(path.join(root, "z.txt"), "z\n");
  await fs.writeFile(path.join(root, "nested", "a.txt"), Buffer.from([0, 1, 2]));
  await fs.mkdir(path.join(root, "__pycache__"));
  await fs.writeFile(path.join(root, "__pycache__", "ignored.pyc"), "ignored");
  const expected = createHash("sha256")
    .update("nested/a.txt", "utf8").update(Buffer.from([0])).update(Buffer.from([0, 1, 2])).update(Buffer.from([0]))
    .update("z.txt", "utf8").update(Buffer.from([0])).update("z\n").update(Buffer.from([0]))
    .digest("hex");
  assert.equal(await digestSkillDirectory(root), `sha256:${expected}`);
});

test("approved plugin verification proves exact manifest coverage, frontmatter, digest, and read-only tree", async (t) => {
  const fixture = await createApprovedPlugin(t, { secondSkill: true });
  const verified = await verifyApprovedSkillPlugin(fixture);
  assert.equal(verified.pluginName, "takyon-approved-skills");
  assert.deepEqual(verified.skillNames, ["market-research", "design-taste"]);
  assert.deepEqual(verified.qualifiedSkillNames, [
    "takyon-approved-skills:market-research",
    "takyon-approved-skills:design-taste",
  ]);
});

test("approved plugin rejects writable content before the SDK is called", async (t) => {
  const fixture = await createApprovedPlugin(t, { writable: true });
  await assert.rejects(
    verifyApprovedSkillPlugin(fixture),
    (error) => error instanceof PrimaryRuntimeError && error.code === "skill_plugin_writable"
  );
});

test("approved plugin rejects digest drift and unmanifested skills", async (t) => {
  const drift = await createApprovedPlugin(t);
  await chmodTree(drift.pluginPath, 0o644, 0o755);
  await fs.appendFile(path.join(drift.pluginPath, "skills", "market-research", "SKILL.md"), "drift\n");
  await chmodTree(drift.pluginPath, 0o444, 0o555);
  await assert.rejects(
    verifyApprovedSkillPlugin(drift),
    (error) => error instanceof PrimaryRuntimeError && error.code === "skill_digest_mismatch"
  );

  const extra = await createApprovedPlugin(t);
  await chmodTree(extra.pluginPath, 0o644, 0o755);
  await fs.mkdir(path.join(extra.pluginPath, "skills", "unapproved"));
  await fs.writeFile(
    path.join(extra.pluginPath, "skills", "unapproved", "SKILL.md"),
    "---\nname: unapproved\ndescription: Not approved.\n---\n"
  );
  await chmodTree(extra.pluginPath, 0o444, 0o555);
  await assert.rejects(
    verifyApprovedSkillPlugin(extra),
    (error) => error instanceof PrimaryRuntimeError && error.code === "skill_manifest_coverage"
  );
});

test("approved plugin rejects top-level allowed_skills drift from per-skill allowed_modes", async (t) => {
  const fixture = await createApprovedPlugin(t);
  await chmodTree(fixture.pluginPath, 0o644, 0o755);
  const manifest = JSON.parse(await fs.readFile(fixture.manifestPath, "utf8"));
  manifest.mode_tool_policy.bootstrap.allowed_skills = [];
  await fs.writeFile(fixture.manifestPath, JSON.stringify(manifest));
  await chmodTree(fixture.pluginPath, 0o444, 0o555);
  await assert.rejects(
    verifyApprovedSkillPlugin(fixture),
    (error) => error instanceof PrimaryRuntimeError && error.code === "skill_manifest_mode_policy"
  );
});

test("Safebox broker and DeepSeek deployment pins are mandatory and conflict-checked", () => {
  const broker = resolveSafeboxBrokerConfiguration({ sourceEnv: BROKER_ENV });
  assert.deepEqual(broker, {
    baseUrl: "http://10.116.0.2:8000",
    capabilityToken: "short-lived-safebox-capability",
    model: PRIMARY_AGENT_MODEL,
  });
  assert.throws(
    () => resolveSafeboxBrokerConfiguration({ sourceEnv: { ...BROKER_ENV, ANTHROPIC_BASE_URL: "" } }),
    (error) => error.code === "broker_configuration"
  );
  assert.throws(
    () => resolveSafeboxBrokerConfiguration({
      sourceEnv: { ...BROKER_ENV, ANTHROPIC_DEFAULT_HAIKU_MODEL: "other" },
    }),
    (error) => error.code === "model_alias_conflict"
  );
  assert.throws(
    () => resolveSafeboxBrokerConfiguration({
      sourceEnv: { ...BROKER_ENV, TAKYON_CLAUDE_AGENT_MODEL: "claude-opus" },
    }),
    (error) => error.code === "model_pin_conflict"
  );
});

test("primary defaults expose only Skill and the approved plugin with isolated settings", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const prepared = await buildPrimaryRuntimeOptions({
    prompt: "Inspect the business state.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
  });
  assert.deepEqual(prepared.options.settingSources, []);
  assert.deepEqual(prepared.options.tools, ["Skill"]);
  assert.equal(prepared.options.skills, "all");
  assert.deepEqual(prepared.options.plugins, [{ type: "local", path: fixture.pluginPath }]);
  assert.equal(prepared.options.persistSession, true);
  assert.equal(prepared.options.strictMcpConfig, true);
  assert.deepEqual(prepared.options.mcpServers, {});
  assert.deepEqual(prepared.options.allowedTools, []);
  assert.equal(prepared.options.permissionMode, "dontAsk");
  assert.equal(prepared.options.settings.autoMemoryEnabled, false);
  assert.equal(prepared.options.settings.autoDreamEnabled, false);
  assert.ok(prepared.options.disallowedTools.includes("Agent"));
  assert.ok(prepared.options.disallowedTools.includes("Task"));
  assert.equal(prepared.options.env.ANTHROPIC_BASE_URL, BROKER_ENV.ANTHROPIC_BASE_URL);
  assert.equal(prepared.options.env.ANTHROPIC_API_KEY, BROKER_ENV.ANTHROPIC_API_KEY);
  assert.equal(prepared.options.env.ANTHROPIC_MODEL, PRIMARY_AGENT_MODEL);
  assert.equal(prepared.options.env.CLAUDE_CODE_MAX_RETRIES, "0");
  assert.equal(prepared.options.env.ANTHROPIC_TOKEN, undefined);
});

test("MCP capabilities are exact allowedTools and never widen the local built-ins", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const allowedMcpTools = [
    "mcp__takyon_parent__business_read_business",
    "mcp__takyon_parent__business_write_file",
  ];
  const mcpServers = {
    takyon_parent: { type: "stdio", command: "/bin/false", args: [] },
  };
  const prepared = await buildPrimaryRuntimeOptions({
    prompt: "Inspect business state.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    mcpServers,
    allowedMcpTools,
  });
  assert.deepEqual(prepared.options.tools, ["Skill"]);
  assert.deepEqual(prepared.options.allowedTools, allowedMcpTools);
  assert.equal(prepared.options.mcpServers, mcpServers);
  assert.equal((await prepared.guard.decide(allowedMcpTools[0], {})).allowed, true);
  assert.equal((await prepared.guard.decide("mcp__takyon_parent__business_delete_business", {})).allowed, false);
});

test("tool guard fails closed on unscoped tools, Agent, path escape, and unsafe Bash defaults", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const guard = createPrimaryToolGuard({
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    workspace: "product/site",
    localTools: ["Read", "Skill"],
    allowedMcpTools: ["mcp__takyon__business_get_state"],
  });
  assert.equal((await guard.decide("Agent", {})).allowed, false);
  assert.equal((await guard.decide("Write", { file_path: "x" })).allowed, false);
  assert.equal((await guard.decide("Read", { file_path: "../../secrets" })).allowed, false);
  assert.deepEqual(
    await guard.decide("Read", { file_path: "product/site/src/main.ts" }),
    { allowed: true, updatedInput: { file_path: "src/main.ts" } }
  );
  assert.equal((await guard.decide("mcp__takyon__business_get_state", {})).allowed, true);
  assert.throws(
    () => createPrimaryToolGuard({
      cwd: fixture.workspaceRoot,
      workspaceRoot: fixture.workspaceRoot,
      localTools: ["Bash"],
    }),
    (error) => error.code === "bash_configuration"
  );
});

test("Skill stays surfaced globally while PreToolUse enforces HANDOFF allowed_modes", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const guard = createPrimaryToolGuard({
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    localTools: ["Skill"],
    mode: "bootstrap",
    pluginName: "takyon-approved-skills",
    approvedSkills: [
      { name: "takyon-product", allowed_modes: ["interactive", "bootstrap"] },
      { name: "takyon-x", allowed_modes: ["interactive", "wake"] },
      { name: "takyon-market-research", allowed_modes: ["interactive", "wake"] },
      { name: "takyon-distribution", allowed_modes: ["interactive", "wake"] },
      { name: "takyon-business-metrics", allowed_modes: ["interactive", "wake"] },
    ],
  });
  assert.equal((await guard.decide("Skill", {
    skill: "takyon-approved-skills:takyon-product",
  })).allowed, true);
  for (const name of [
    "takyon-x",
    "takyon-market-research",
    "takyon-distribution",
    "takyon-business-metrics",
  ]) {
    const decision = await guard.decide("Skill", { skill: `takyon-approved-skills:${name}` });
    assert.equal(decision.allowed, false, `${name} must be denied in bootstrap mode`);
    assert.match(decision.reason, /not allowed during bootstrap/);
  }
  assert.equal((await guard.decide("Skill", { skill: "other-plugin:takyon-product" })).allowed, false);
  assert.equal((await guard.decide("Skill", { skill: "unapproved" })).allowed, false);
  assert.equal((await guard.decide("Skill", { skill: "verify" })).allowed, true);
  assert.equal((await guard.decide("Monitor", {})).allowed, false);
  assert.equal((await guard.decide("PushNotification", {})).allowed, false);
});

test("JSON-line progress sink preserves the established structured event prefix", async () => {
  const writes = [];
  const sink = createJsonLineProgressSink({
    stream: { write: (value) => { writes.push(value); return true; } },
  });
  await sink({ version: 1, epoch: "wake", kind: "turn", status: "started" });
  assert.equal(writes.length, 1);
  assert.match(writes[0], /^TAKYON_SDK_EVENT \{/);
  assert.deepEqual(
    JSON.parse(writes[0].slice("TAKYON_SDK_EVENT ".length)),
    { version: 1, epoch: "wake", kind: "turn", status: "started" }
  );
});

test("SessionStore append and load are bounded before data crosses the bridge", async () => {
  const calls = [];
  const store = createBoundedSessionStore({
    append: async (key, entries) => calls.push(["append", key, entries]),
    load: async () => [{ type: "assistant", text: "x".repeat(40) }],
  }, {
    maxAppendBytes: 64,
    maxLoadBytes: 32,
    maxAppendEntries: 2,
    maxLoadEntries: 2,
  });
  await store.append({ sessionId: "one" }, [{ type: "user", text: "ok" }]);
  assert.equal(calls.length, 1);
  await assert.rejects(
    store.append({ sessionId: "one" }, [{ type: "user", text: "x".repeat(100) }]),
    (error) => error.code === "session_store_payload"
  );
  await assert.rejects(
    store.load({ sessionId: "one" }),
    (error) => error.code === "session_store_payload"
  );
});

test("assistant text streams exact deltas and final output omits thinking content", async () => {
  const events = [];
  const projector = createProgressProjector({ epoch: "interactive", onProgress: (event) => events.push(event) });
  await projector.project({
    type: "stream_event",
    uuid: "assistant-stream",
    event: { type: "content_block_delta", delta: { type: "text_delta", text: "partial " } },
  });
  const fullText = `First line\n\n${"complete customer-facing update ".repeat(20)}`;
  await projector.project({
    type: "assistant",
    uuid: "assistant-one",
    message: {
      content: [
        { type: "thinking", thinking: "private chain of thought" },
        { type: "text", text: fullText },
      ],
    },
  });
  assert.equal(events.length, 2);
  assert.equal(events[0].kind, "assistant");
  assert.equal(events[0].status, "delta");
  assert.equal(events[0].detail, "partial ");
  assert.equal(events[1].kind, "assistant");
  assert.equal(events[1].status, "output");
  assert.equal(events[1].detail, fullText.trim());
  assert.equal(events[1].trace.message_role, "final");
  assert.equal(events[1].detail.includes("private chain of thought"), false);

  await projector.project({
    type: "assistant",
    uuid: "assistant-interim",
    message: {
      content: [
        { type: "text", text: "Research is complete; beginning the build." },
        { type: "tool_use", id: "build-1", name: "mcp__takyon__build", input: {} },
      ],
    },
  });
  assert.equal(events[2].trace.message_role, "interim");
});

test("streaming input applies mid-turn steering with immediate priority", async () => {
  const input = createPrimaryStreamingInput("initial request");
  const iterator = input.messages[Symbol.asyncIterator]();
  const initial = await iterator.next();
  assert.equal(initial.value.message.content, "initial request");
  assert.equal(initial.value.priority, "now");
  assert.equal(input.steer("also inspect auth.log"), true);
  const steer = await iterator.next();
  assert.equal(steer.value.message.content, "also inspect auth.log");
  assert.equal(steer.value.priority, "now");
  input.close();
  assert.equal((await iterator.next()).done, true);
});

test("streaming turn closes async input after its result so the SDK query can finish", { timeout: 2_000 }, async (t) => {
  const fixture = await createApprovedPlugin(t);
  const sessionId = randomUUID();
  const capture = {};
  const streamingInput = createPrimaryStreamingInput("initial request");
  const init = sdkInit("takyon-approved-skills", sessionId, ["market-research"]);
  init.plugins[0].path = fixture.pluginPath;

  const result = await runPrimaryAgentTurn({
    prompt: "initial request",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    streamingInput,
  }, {
    sdk: streamingLifecycleSdk([init, successfulResult(sessionId)], capture),
  });

  assert.equal(result.session_id, sessionId);
  assert.equal(capture.initialPrompt.value.message.content, "initial request");
  assert.equal(capture.inputAfterResult.done, true);
  assert.equal(capture.drained, true);
  assert.equal(streamingInput.steer("too late"), false);
});

test("new primary turn persists session, verifies SDK init, and emits concise epoch progress", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const sessionId = randomUUID();
  const events = [];
  const sessions = [];
  const capture = {};
  const init = sdkInit("takyon-approved-skills", sessionId, ["market-research"]);
  init.plugins[0].path = fixture.pluginPath;
  const result = await runPrimaryAgentTurn({
    prompt: "Research this market.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    epoch: "bootstrap:market-research",
    sessionId,
    onProgress: (event) => events.push(event),
    onSession: (receipt) => sessions.push(receipt),
  }, {
    sdk: fakeSdk([
      init,
      {
        type: "assistant",
        session_id: sessionId,
        uuid: randomUUID(),
        message: {
          model: PRIMARY_AGENT_MODEL,
          content: [{
            type: "tool_use",
            id: "skill-1",
            name: "Skill",
            input: { skill: "takyon-approved-skills:market-research" },
          }],
        },
      },
      {
        type: "user",
        session_id: sessionId,
        uuid: randomUUID(),
        message: { content: [{ type: "tool_result", tool_use_id: "skill-1", content: "loaded" }] },
      },
      successfulResult(sessionId, "Market evidence written."),
    ], capture),
  });
  assert.equal(result.session_id, sessionId);
  assert.equal(result.resumed, false);
  assert.equal(result.summary, "Market evidence written.");
  assert.deepEqual(result.skill_receipt.attempted, ["takyon-approved-skills:market-research"]);
  assert.deepEqual(result.skill_receipt.invoked, ["takyon-approved-skills:market-research"]);
  assert.equal(
    result.skill_receipt.digests["takyon-approved-skills:market-research"],
    fixture.specs[0].content_digest
  );
  assert.deepEqual(sessions, [{ sessionId, resumed: false, requestedSessionId: sessionId }]);
  assert.equal(capture.args.options.sessionId, sessionId);
  assert.equal(capture.args.options.resume, undefined);
  assert.ok(events.length >= 4);
  assert.ok(events.every((event) => event.epoch === "bootstrap:market-research"));
  assert.ok(events.every((event, index) => event.sequence === index + 1));
  assert.ok(events.some((event) => event.kind === "skill" && event.status === "started"));
  assert.ok(events.some((event) => event.kind === "skill" && event.status === "completed"));
  assert.equal(JSON.stringify(events).includes("private reasoning"), false);
});

test("pinned Claude built-ins may initialize without widening scoped tools", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const sessionId = randomUUID();
  const init = sdkInit(
    "takyon-approved-skills",
    sessionId,
    ["market-research"],
    ["Skill", ...PINNED_CLAUDE_INIT_TOOLS],
  );
  init.skills.push(...PINNED_CLAUDE_BUILTIN_SKILLS);
  init.plugins[0].path = fixture.pluginPath;

  const result = await runPrimaryAgentTurn({
    prompt: "Research this market.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    epoch: "bootstrap:market-research",
    sessionId,
  }, {
    sdk: fakeSdk([init, successfulResult(sessionId, "Initialized safely.")]),
  });

  assert.equal(result.summary, "Initialized safely.");
});

test("resume uses the exact persisted session and reports the resume hook", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const sessionId = randomUUID();
  const receipts = [];
  const capture = {};
  const sessionStore = {
    append: async () => {},
    load: async () => [],
  };
  const init = sdkInit("takyon-approved-skills", sessionId, ["market-research"]);
  init.plugins[0].path = fixture.pluginPath;
  const result = await runPrimaryAgentTurn({
    prompt: "Continue the same bootstrap phase.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    resumeSessionId: sessionId,
    sessionStore,
    sessionStoreFlush: "eager",
    onSession: (receipt) => receipts.push(receipt),
  }, { sdk: fakeSdk([init, successfulResult(sessionId)], capture) });
  assert.equal(result.resumed, true);
  assert.equal(capture.args.options.resume, sessionId);
  assert.equal(capture.args.options.sessionId, undefined);
  assert.notEqual(capture.args.options.sessionStore, sessionStore);
  assert.equal(typeof capture.args.options.sessionStore.append, "function");
  assert.equal(typeof capture.args.options.sessionStore.load, "function");
  assert.equal(capture.args.options.sessionStoreFlush, "eager");
  assert.deepEqual(receipts, [{ sessionId, resumed: true, requestedSessionId: sessionId }]);
});

test("manual compact resumes exactly, exposes no tools, and requires its boundary", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const sessionId = randomUUID();
  const capture = {};
  const events = [];
  const sessionStore = {
    append: async () => {},
    load: async () => [{ type: "user", uuid: randomUUID() }],
  };
  const init = sdkInit("takyon-approved-skills", sessionId, ["market-research"], []);
  init.plugins[0].path = fixture.pluginPath;
  const boundary = {
    type: "system",
    subtype: "compact_boundary",
    session_id: sessionId,
    uuid: randomUUID(),
    compact_metadata: {
      trigger: "manual",
      pre_tokens: 1200,
      post_tokens: 300,
      duration_ms: 25,
    },
  };
  const result = await runPrimaryAgentTurn({
    prompt: "/compact preserve launch decisions",
    operation: "compact",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    resumeSessionId: sessionId,
    sessionStore,
    sessionStoreFlush: "eager",
    localTools: ["Skill", "Bash"],
    allowedMcpTools: ["mcp__takyon__business_write_file"],
    mcpServers: { takyon: { dangerous: true } },
    onProgress: (event) => events.push(event),
  }, { sdk: fakeSdk([init, boundary, successfulResult(sessionId)], capture) });

  assert.equal(capture.args.options.resume, sessionId);
  assert.deepEqual(capture.args.options.tools, []);
  assert.deepEqual(capture.args.options.allowedTools, []);
  assert.deepEqual(capture.args.options.mcpServers, {});
  assert.equal(result.operation, "compact");
  assert.equal(result.summary, "Durable agent context compacted.");
  assert.deepEqual(result.compact_receipt, {
    uuid: boundary.uuid,
    trigger: "manual",
    pre_tokens: 1200,
    post_tokens: 300,
    duration_ms: 25,
  });
  assert.ok(events.some((event) => event.status === "compacted"));
});

test("manual compact fails closed without a boundary or on any tool use", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const base = {
    prompt: "/compact",
    operation: "compact",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
    sessionStore: { append: async () => {}, load: async () => [] },
  };

  const missingSession = randomUUID();
  const missingInit = sdkInit(
    "takyon-approved-skills", missingSession, ["market-research"], []
  );
  missingInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(
      { ...base, resumeSessionId: missingSession },
      { sdk: fakeSdk([missingInit, successfulResult(missingSession)]) }
    ),
    (error) => error.code === "compact_boundary_missing"
  );

  const toolSession = randomUUID();
  const toolInit = sdkInit(
    "takyon-approved-skills", toolSession, ["market-research"], []
  );
  toolInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(
      { ...base, resumeSessionId: toolSession },
      {
        sdk: fakeSdk([
          toolInit,
          {
            type: "assistant",
            session_id: toolSession,
            uuid: randomUUID(),
            message: {
              model: PRIMARY_AGENT_MODEL,
              content: [{
                type: "tool_use",
                id: "forbidden",
                name: "Skill",
                input: { skill: "market-research" },
              }],
            },
          },
        ]),
      }
    ),
    (error) => error.code === "compact_tool_forbidden"
  );
});

test("SDK init fails closed on extra skills, forbidden Agent tool, or a model mismatch", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const baseConfig = {
    prompt: "Do work.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
  };
  const extraSkillSession = randomUUID();
  const extraSkillInit = sdkInit(
    "takyon-approved-skills",
    extraSkillSession,
    ["market-research", "unapproved"]
  );
  extraSkillInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([extraSkillInit, successfulResult(extraSkillSession)]) }),
    (error) => error.code === "sdk_skill_set_mismatch"
  );

  const agentSession = randomUUID();
  const agentInit = sdkInit("takyon-approved-skills", agentSession, ["market-research"], ["Skill", "Agent"]);
  agentInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([agentInit, successfulResult(agentSession)]) }),
    (error) => error.code === "agent_tool_exposed"
  );

  const extraToolSession = randomUUID();
  const extraToolInit = sdkInit(
    "takyon-approved-skills",
    extraToolSession,
    ["market-research"],
    ["Skill", "Read"]
  );
  extraToolInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([extraToolInit, successfulResult(extraToolSession)]) }),
    (error) => error.code === "sdk_tool_set_mismatch"
  );

  const modelSession = randomUUID();
  const modelInit = sdkInit("takyon-approved-skills", modelSession, ["market-research"]);
  modelInit.plugins[0].path = fixture.pluginPath;
  modelInit.model = "other-model";
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([modelInit, successfulResult(modelSession)]) }),
    (error) => error.code === "actual_model_mismatch"
  );

  const assistantMismatchSession = randomUUID();
  const assistantMismatchInit = sdkInit(
    "takyon-approved-skills",
    assistantMismatchSession,
    ["market-research"]
  );
  assistantMismatchInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([
      assistantMismatchInit,
      {
        type: "assistant",
        session_id: assistantMismatchSession,
        uuid: randomUUID(),
        message: { role: "assistant", model: "other-model", content: [{ type: "text", text: "No." }] },
      },
    ]) }),
    (error) => error.code === "actual_model_mismatch"
  );

  const syntheticResultSession = randomUUID();
  const syntheticResultInit = sdkInit(
    "takyon-approved-skills",
    syntheticResultSession,
    ["market-research"]
  );
  syntheticResultInit.plugins[0].path = fixture.pluginPath;
  const syntheticResult = successfulResult(syntheticResultSession);
  syntheticResult.model = "<synthetic>";
  const syntheticReceipt = await runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([
    syntheticResultInit,
    {
      type: "assistant",
      session_id: syntheticResultSession,
      uuid: randomUUID(),
      message: {
        role: "assistant",
        model: PRIMARY_AGENT_MODEL,
        content: [{ type: "text", text: "Completed" }],
      },
    },
    {
      type: "assistant",
      session_id: syntheticResultSession,
      uuid: randomUUID(),
      parent_tool_use_id: null,
      message: {
        role: "assistant",
        model: "<synthetic>",
        stop_reason: "stop_sequence",
        content: [{ type: "text", text: "SDK terminal bookkeeping" }],
      },
    },
    syntheticResult,
  ]) });
  assert.deepEqual(syntheticReceipt.actual_models, [PRIMARY_AGENT_MODEL]);

  const syntheticOnlySession = randomUUID();
  const syntheticOnlyInit = sdkInit(
    "takyon-approved-skills",
    syntheticOnlySession,
    ["market-research"]
  );
  syntheticOnlyInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([
      syntheticOnlyInit,
      {
        type: "assistant",
        session_id: syntheticOnlySession,
        uuid: randomUUID(),
        parent_tool_use_id: null,
        message: {
          role: "assistant",
          model: "<synthetic>",
          stop_reason: "stop_sequence",
          content: [{ type: "text", text: "No paid-model evidence" }],
        },
      },
    ]) }),
    (error) => error.code === "actual_model_mismatch"
  );
});

test("provider retries and session mirror failures abort instead of silently continuing", async (t) => {
  const fixture = await createApprovedPlugin(t);
  const baseConfig = {
    prompt: "Do work.",
    systemPrompt: "Follow Takyon policy.",
    cwd: fixture.workspaceRoot,
    workspaceRoot: fixture.workspaceRoot,
    configDir: fixture.configDir,
    pluginPath: fixture.pluginPath,
    manifestPath: fixture.manifestPath,
    sourceEnv: BROKER_ENV,
  };
  const retrySession = randomUUID();
  const retryInit = sdkInit("takyon-approved-skills", retrySession, ["market-research"]);
  retryInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([
      retryInit,
      {
        type: "system",
        subtype: "api_retry",
        session_id: retrySession,
        uuid: randomUUID(),
        error_status: 529,
      },
    ]) }),
    (error) => error.code === "provider_retry_refused"
  );

  const mirrorSession = randomUUID();
  const mirrorInit = sdkInit("takyon-approved-skills", mirrorSession, ["market-research"]);
  mirrorInit.plugins[0].path = fixture.pluginPath;
  await assert.rejects(
    runPrimaryAgentTurn(baseConfig, { sdk: fakeSdk([
      mirrorInit,
      {
        type: "system",
        subtype: "mirror_error",
        session_id: mirrorSession,
        uuid: randomUUID(),
        error: "store unavailable",
      },
    ]) }),
    (error) => error.code === "session_mirror_failed"
  );
});
