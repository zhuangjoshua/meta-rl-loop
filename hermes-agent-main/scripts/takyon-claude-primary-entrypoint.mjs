#!/usr/bin/env node

/**
 * One-turn subprocess boundary for the primary Takyon Claude Agent SDK runtime.
 *
 * stdin is one bounded JSON request. stdout is one final JSON receipt. Progress
 * is emitted only as prefixed JSON on stderr. Takyon tools and the durable
 * SessionStore use separate inherited sockets owned by the Python parent; this
 * process receives no database credential, Safebox authority token, shell, host
 * filesystem tool, or model-agent tool.
 */

import net from "node:net";
import path from "node:path";
import process from "node:process";
import readline from "node:readline";
import { pathToFileURL } from "node:url";

import {
  PRIMARY_AGENT_MODEL,
  PrimaryRuntimeError,
  SDK_MODULE_PATH_ENV,
  createPrimaryStreamingInput,
  runPrimaryAgentTurn,
} from "./takyon-claude-primary-runtime.mjs";

const BRIDGE_FD_ENV = "TAKYON_SDK_TOOL_BRIDGE_FD";
const SESSION_BRIDGE_FD_ENV = "TAKYON_SDK_SESSION_BRIDGE_FD";
const ZOD_MODULE_PATH_ENV = "TAKYON_CLAUDE_ZOD_MODULE";
const PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
const MAX_REQUEST_BYTES = 8 * 1024 * 1024;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TOOL_NAME_PATTERN = /^[A-Za-z0-9_.-]{1,128}$/;
const MODES = new Set(["ceo_bootstrap", "ceo_wake", "interactive"]);
const OPERATIONS = new Set(["turn", "compact"]);
const RUNTIME_MODES = Object.freeze({
  ceo_bootstrap: "bootstrap",
  ceo_wake: "wake",
  interactive: "interactive",
});
const REQUEST_KEYS = new Set([
  "prompt",
  "systemPrompt",
  "cwd",
  "workspaceRoot",
  "configDir",
  "pluginPath",
  "manifestPath",
  "mode",
  "operation",
  "epoch",
  "sessionId",
  "resumeSessionId",
  "sessionProjectKey",
  "maxTurns",
  "maxBudgetUsd",
  "effort",
  "toolDefinitions",
  "pathToClaudeCodeExecutable",
]);

function fail(code, message, receipt = null) {
  throw new PrimaryRuntimeError(message, { code, receipt });
}

function cleanString(value) {
  return typeof value === "string" ? value.trim() : "";
}

async function loadConfiguredModule(sourceEnv, envName, packageName) {
  const configured = cleanString(sourceEnv?.[envName]);
  if (!configured) return import(packageName);
  if (!path.isAbsolute(configured)) {
    fail("sdk_module_path", `${envName} must be an absolute file path`);
  }
  return import(pathToFileURL(configured).href);
}

function jsonClone(value) {
  return JSON.parse(JSON.stringify(value));
}

function assertJsonObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail("entrypoint_request", `${label} must be an object`);
  }
  return value;
}

export function validatePrimaryEntrypointRequest(value) {
  const input = assertJsonObject(value, "primary runtime request");
  const unknown = Object.keys(input).filter((key) => !REQUEST_KEYS.has(key));
  if (unknown.length) fail("entrypoint_request", `unknown request fields: ${unknown.join(", ")}`);
  for (const key of [
    "prompt",
    "systemPrompt",
    "cwd",
    "workspaceRoot",
    "configDir",
    "pluginPath",
    "manifestPath",
    "sessionProjectKey",
  ]) {
    if (!cleanString(input[key])) fail("entrypoint_request", `${key} is required`);
  }
  const mode = cleanString(input.mode);
  if (!MODES.has(mode)) fail("entrypoint_request", `unsupported primary runtime mode ${JSON.stringify(mode)}`);
  const operation = cleanString(input.operation) || "turn";
  if (!OPERATIONS.has(operation)) {
    fail("entrypoint_request", `unsupported primary runtime operation ${JSON.stringify(operation)}`);
  }
  if (
    operation === "compact"
    && !/^\/compact(?: [^\r\n\0]{1,500})?$/u.test(cleanString(input.prompt))
  ) {
    fail("entrypoint_request", "manual compaction prompt must be canonical /compact [focus]");
  }
  const sessionId = cleanString(input.sessionId);
  const resumeSessionId = cleanString(input.resumeSessionId);
  if (Boolean(sessionId) === Boolean(resumeSessionId)) {
    fail("entrypoint_request", "exactly one of sessionId or resumeSessionId is required");
  }
  if (operation === "compact" && !resumeSessionId) {
    fail("entrypoint_request", "manual compaction requires an exact resumed session");
  }
  for (const [label, candidate] of [["sessionId", sessionId], ["resumeSessionId", resumeSessionId]]) {
    if (candidate && !UUID_PATTERN.test(candidate)) fail("entrypoint_request", `${label} must be a UUID`);
  }
  const maxTurns = Number(input.maxTurns);
  const maxBudgetUsd = Number(input.maxBudgetUsd);
  if (!Number.isInteger(maxTurns) || maxTurns < 1 || maxTurns > 200) {
    fail("entrypoint_request", "maxTurns must be an integer from 1 through 200");
  }
  if (!Number.isFinite(maxBudgetUsd) || maxBudgetUsd <= 0 || maxBudgetUsd > 100) {
    fail("entrypoint_request", "maxBudgetUsd must be greater than 0 and at most 100");
  }
  if (!Array.isArray(input.toolDefinitions) || input.toolDefinitions.length > 512) {
    fail("entrypoint_request", "toolDefinitions must be an array of at most 512 tools");
  }
  const names = new Set();
  const toolDefinitions = input.toolDefinitions.map((raw, index) => {
    const definition = assertJsonObject(raw, `toolDefinitions[${index}]`);
    const name = cleanString(definition.name);
    const description = cleanString(definition.description);
    if (!TOOL_NAME_PATTERN.test(name)) fail("entrypoint_request", `invalid tool name ${JSON.stringify(name)}`);
    if (names.has(name)) fail("entrypoint_request", `duplicate tool name ${name}`);
    names.add(name);
    if (!description) fail("entrypoint_request", `tool ${name} has no description`);
    const inputSchema = assertJsonObject(definition.inputSchema, `tool ${name} inputSchema`);
    if (inputSchema.type !== "object") fail("entrypoint_request", `tool ${name} inputSchema must be an object schema`);
    return { name, description, inputSchema: jsonClone(inputSchema) };
  });
  return Object.freeze({
    ...input,
    prompt: cleanString(input.prompt),
    systemPrompt: cleanString(input.systemPrompt),
    cwd: cleanString(input.cwd),
    workspaceRoot: cleanString(input.workspaceRoot),
    configDir: cleanString(input.configDir),
    pluginPath: cleanString(input.pluginPath),
    manifestPath: cleanString(input.manifestPath),
    mode,
    operation,
    epoch: cleanString(input.epoch) || mode,
    sessionId,
    resumeSessionId,
    sessionProjectKey: cleanString(input.sessionProjectKey),
    maxTurns,
    maxBudgetUsd,
    effort: cleanString(input.effort) || "high",
    toolDefinitions: Object.freeze(toolDefinitions.map(Object.freeze)),
    pathToClaudeCodeExecutable: cleanString(input.pathToClaudeCodeExecutable),
  });
}

export function createJsonLineBridge(socket) {
  if (!socket || typeof socket.write !== "function") fail("bridge_configuration", "bridge socket is required");
  const pending = new Map();
  const lines = readline.createInterface({ input: socket, crlfDelay: Infinity });
  let sequence = 0;
  let closedError = null;
  const rejectPending = (error) => {
    if (!closedError) closedError = error instanceof Error ? error : new Error(String(error || "bridge closed"));
    for (const { reject } of pending.values()) reject(closedError);
    pending.clear();
  };
  lines.on("line", (line) => {
    let response;
    try {
      response = JSON.parse(line);
    } catch {
      rejectPending(new PrimaryRuntimeError("tool bridge returned invalid JSON", { code: "bridge_protocol" }));
      socket.destroy();
      return;
    }
    const id = cleanString(response?.id);
    const waiter = pending.get(id);
    if (!waiter) {
      rejectPending(new PrimaryRuntimeError("tool bridge returned an unknown response id", { code: "bridge_protocol" }));
      socket.destroy();
      return;
    }
    pending.delete(id);
    if (response.ok === true) waiter.resolve(response.result);
    else waiter.reject(new PrimaryRuntimeError(cleanString(response.error) || "tool bridge refused request", {
      code: "bridge_request_failed",
    }));
  });
  lines.on("close", () => rejectPending(new PrimaryRuntimeError("tool bridge closed", { code: "bridge_closed" })));
  socket.on("error", rejectPending);
  return Object.freeze({
    request(type, payload = {}) {
      if (closedError || socket.destroyed) return Promise.reject(closedError || new Error("bridge closed"));
      const id = `rpc-${process.pid}-${++sequence}`;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        const line = `${JSON.stringify({ id, type, ...payload })}\n`;
        socket.write(line, "utf8", (error) => {
          if (!error) return;
          pending.delete(id);
          reject(error);
        });
      });
    },
    close() {
      lines.close();
      socket.destroy();
      rejectPending(new PrimaryRuntimeError("tool bridge closed", { code: "bridge_closed" }));
    },
  });
}

function normalizedSessionKey(raw, projectKey, expectedSessionId, { allowSubpath = true } = {}) {
  const key = assertJsonObject(raw, "session key");
  const sessionId = cleanString(key.sessionId);
  if (sessionId !== expectedSessionId) fail("session_scope", "SessionStore attempted a different session ID");
  const subpath = cleanString(key.subpath);
  if (!allowSubpath && subpath) fail("session_scope", "SessionStore listSubkeys key may not have a subpath");
  return {
    projectKey,
    sessionId,
    ...(subpath ? { subpath } : {}),
  };
}

export function createBridgeSessionStore({ request, projectKey, sessionId }) {
  if (typeof request !== "function") fail("session_store_configuration", "SessionStore bridge request is required");
  const stableProjectKey = cleanString(projectKey);
  const stableSessionId = cleanString(sessionId);
  if (!stableProjectKey || !UUID_PATTERN.test(stableSessionId)) {
    fail("session_store_configuration", "stable SessionStore project and session IDs are required");
  }
  return Object.freeze({
    async append(key, entries) {
      if (!Array.isArray(entries) || entries.length > 2000) {
        fail("session_store_protocol", "SessionStore append entries must be a bounded array");
      }
      const opaqueEntries = entries.map((entry, index) => {
        const value = assertJsonObject(entry, `session entry ${index}`);
        if (!cleanString(value.type)) fail("session_store_protocol", `session entry ${index} has no type`);
        return jsonClone(value);
      });
      await request("session_append", {
        key: normalizedSessionKey(key, stableProjectKey, stableSessionId),
        entries: opaqueEntries,
      });
    },
    async load(key) {
      const result = await request("session_load", {
        key: normalizedSessionKey(key, stableProjectKey, stableSessionId),
      });
      if (result === null) return null;
      if (!Array.isArray(result)) fail("session_store_protocol", "SessionStore load returned a non-array");
      return result.map((entry, index) => {
        const value = assertJsonObject(entry, `loaded session entry ${index}`);
        if (!cleanString(value.type)) fail("session_store_protocol", `loaded session entry ${index} has no type`);
        return jsonClone(value);
      });
    },
    async listSubkeys(key) {
      const result = await request("session_list_subkeys", {
        key: normalizedSessionKey(key, stableProjectKey, stableSessionId, { allowSubpath: false }),
      });
      if (!Array.isArray(result) || result.some((value) => !cleanString(value))) {
        fail("session_store_protocol", "SessionStore listSubkeys returned invalid data");
      }
      return result.map(cleanString);
    },
    async delete(key) {
      await request("session_delete", {
        key: normalizedSessionKey(key, stableProjectKey, stableSessionId, { allowSubpath: false }),
      });
    },
  });
}

export function createBridgeMcpServer({ sdk, z, request, toolDefinitions }) {
  if (typeof sdk?.createSdkMcpServer !== "function" || typeof sdk?.tool !== "function") {
    fail("sdk_unavailable", "Claude Agent SDK MCP helpers are unavailable");
  }
  if (typeof z?.fromJSONSchema !== "function") fail("schema_unavailable", "Zod JSON Schema conversion is unavailable");
  const tools = toolDefinitions.map((definition) => {
    let converted;
    try {
      converted = z.fromJSONSchema(definition.inputSchema);
    } catch (error) {
      fail("tool_schema", `tool ${definition.name} schema is unsupported: ${error?.message || error}`);
    }
    if (!converted?.shape || typeof converted.shape !== "object") {
      fail("tool_schema", `tool ${definition.name} schema did not produce a Zod object`);
    }
    return sdk.tool(
      definition.name,
      definition.description,
      converted.shape,
      async (args, extra) => {
        const result = await request("tool", {
          name: definition.name,
          args: jsonClone(args || {}),
          toolUseId: cleanString(extra?.toolUseId || extra?.requestId),
        });
        if (
          result
          && typeof result === "object"
          && !Array.isArray(result)
          && Array.isArray(result.nativeMcpContent)
          && result.nativeMcpContent.length > 0
        ) {
          const content = result.nativeMcpContent.map((block, index) => {
            const value = assertJsonObject(block, `native MCP content ${index}`);
            if (value.type === "text" && typeof value.text === "string" && value.text.length <= 32_768) {
              return { type: "text", text: value.text };
            }
            if (
              value.type === "image"
              && value.mimeType === "image/png"
              && typeof value.data === "string"
              && value.data.length <= 6 * 1024 * 1024
              && /^[A-Za-z0-9+/]+={0,2}$/.test(value.data)
            ) {
              return { type: "image", data: value.data, mimeType: "image/png" };
            }
            fail("bridge_protocol", `invalid native MCP content block ${index}`);
          });
          return { content };
        }
        return { content: [{ type: "text", text: typeof result === "string" ? result : JSON.stringify(result) }] };
      },
      { alwaysLoad: true }
    );
  });
  return sdk.createSdkMcpServer({
    name: "takyon",
    version: "1.0.0",
    alwaysLoad: true,
    instructions: "Takyon business tools are parent-scoped capabilities. Use only the exposed tools.",
    tools,
  });
}

function bridgeSocketFromEnvironment(envName, sourceEnv = process.env) {
  const fdText = cleanString(sourceEnv[envName]);
  if (!/^\d+$/.test(fdText) || Number(fdText) < 3) {
    fail("bridge_configuration", `${envName} must name an inherited private descriptor`);
  }
  return new net.Socket({ fd: Number(fdText), readable: true, writable: true });
}

export async function runPrimaryEntrypoint(
  rawRequest,
  {
    sourceEnv = process.env,
    sdkModule = null,
    zodModule = null,
    toolSocket = null,
    sessionSocket = null,
    runTurn = runPrimaryAgentTurn,
    streamingInput = null,
  } = {}
) {
  const request = validatePrimaryEntrypointRequest(rawRequest);
  const sdk = sdkModule || await loadConfiguredModule(
    sourceEnv,
    SDK_MODULE_PATH_ENV,
    "@anthropic-ai/claude-agent-sdk",
  );
  const zod = zodModule || await loadConfiguredModule(sourceEnv, ZOD_MODULE_PATH_ENV, "zod");
  let resolvedToolSocket = null;
  let resolvedSessionSocket = null;
  let toolBridge = null;
  let sessionBridge = null;
  try {
    const configuredToolFd = cleanString(sourceEnv[BRIDGE_FD_ENV]);
    const configuredSessionFd = cleanString(sourceEnv[SESSION_BRIDGE_FD_ENV]);
    if (
      !toolSocket
      && !sessionSocket
      && configuredToolFd
      && configuredToolFd === configuredSessionFd
    ) {
      fail("bridge_configuration", "tool and SessionStore bridges must use distinct descriptors");
    }
    resolvedToolSocket = toolSocket || bridgeSocketFromEnvironment(BRIDGE_FD_ENV, sourceEnv);
    resolvedSessionSocket = sessionSocket
      || bridgeSocketFromEnvironment(SESSION_BRIDGE_FD_ENV, sourceEnv);
    if (resolvedToolSocket === resolvedSessionSocket) {
      fail("bridge_configuration", "tool and SessionStore bridges must use distinct sockets");
    }
    toolBridge = createJsonLineBridge(resolvedToolSocket);
    sessionBridge = createJsonLineBridge(resolvedSessionSocket);
    const expectedSessionId = request.resumeSessionId || request.sessionId;
    const sessionStore = createBridgeSessionStore({
      request: sessionBridge.request,
      projectKey: request.sessionProjectKey,
      sessionId: expectedSessionId,
    });
    const compactOperation = request.operation === "compact";
    const mcpServer = compactOperation
      ? null
      : createBridgeMcpServer({
          sdk,
          z: zod.z,
          request: toolBridge.request,
          toolDefinitions: request.toolDefinitions,
        });
    const qualifiedTools = compactOperation
      ? []
      : request.toolDefinitions.map((definition) => `mcp__takyon__${definition.name}`);
    return await runTurn({
      prompt: request.prompt,
      systemPrompt: request.systemPrompt,
      cwd: request.cwd,
      workspaceRoot: request.workspaceRoot,
      configDir: request.configDir,
      pluginPath: request.pluginPath,
      manifestPath: request.manifestPath,
      mode: RUNTIME_MODES[request.mode],
      operation: request.operation,
      epoch: request.epoch,
      sessionId: request.sessionId,
      resumeSessionId: request.resumeSessionId,
      sessionStore,
      sessionStoreFlush: "eager",
      sourceEnv,
      requestedModel: PRIMARY_AGENT_MODEL,
      expectedModel: PRIMARY_AGENT_MODEL,
      localTools: compactOperation ? [] : ["Skill"],
      allowedMcpTools: qualifiedTools,
      mcpServers: compactOperation ? {} : { takyon: mcpServer },
      allowBash: false,
      maxTurns: request.maxTurns,
      maxBudgetUsd: request.maxBudgetUsd,
      effort: request.effort,
      failOnApiRetry: true,
      pathToClaudeCodeExecutable: request.pathToClaudeCodeExecutable,
      onProgress: async (event) => {
        process.stderr.write(`${PROGRESS_PREFIX}${JSON.stringify(event)}\n`);
      },
      ...(streamingInput ? { streamingInput } : {}),
    });
  } finally {
    sessionBridge?.close();
    toolBridge?.close();
    if (!sessionBridge) resolvedSessionSocket?.destroy?.();
    if (!toolBridge) resolvedToolSocket?.destroy?.();
  }
}

function openStdinControlSession(input = process.stdin) {
  const lines = readline.createInterface({ input, crlfDelay: Infinity });
  let bytes = 0;
  let first = true;
  let streamingInput = null;
  const pendingControls = [];
  let resolveRequest;
  let rejectRequest;
  const request = new Promise((resolve, reject) => {
    resolveRequest = resolve;
    rejectRequest = reject;
  });
  const applyControl = (line) => {
    let control;
    try {
      control = JSON.parse(line);
    } catch {
      fail("steer_payload", "primary runtime control is not valid JSON");
    }
    if (
      !control
      || typeof control !== "object"
      || Array.isArray(control)
      || Object.keys(control).some((key) => !["type", "text"].includes(key))
      || control.type !== "steer"
      || typeof control.text !== "string"
      || !control.text.trim()
    ) {
      fail("steer_payload", "primary runtime control must be a non-empty steer message");
    }
    if (!streamingInput) pendingControls.push(control.text);
    else streamingInput.steer(control.text);
  };
  lines.on("line", (line) => {
    bytes += Buffer.byteLength(line, "utf8") + 1;
    if (bytes > MAX_REQUEST_BYTES) {
      rejectRequest(new PrimaryRuntimeError("primary runtime input is too large", { code: "entrypoint_request" }));
      streamingInput?.close();
      lines.close();
      return;
    }
    if (first) {
      first = false;
      try {
        resolveRequest(JSON.parse(line));
      } catch {
        rejectRequest(new PrimaryRuntimeError("primary runtime request is not valid JSON", { code: "entrypoint_request" }));
      }
      return;
    }
    try {
      applyControl(line);
    } catch (error) {
      streamingInput?.close();
      process.stderr.write(`${PROGRESS_PREFIX}${JSON.stringify({
        version: 1,
        source: "claude-agent-sdk",
        epoch: "interactive",
        kind: "steer",
        status: "failed",
        detail: cleanString(error?.message) || "Steering failed.",
      })}\n`);
    }
  });
  lines.on("close", () => {
    if (first) rejectRequest(new PrimaryRuntimeError("primary runtime request is empty", { code: "entrypoint_request" }));
  });
  return Object.freeze({
    request,
    bind(initialPrompt) {
      streamingInput = createPrimaryStreamingInput(initialPrompt);
      for (const text of pendingControls.splice(0)) streamingInput.steer(text);
      return streamingInput;
    },
    close() {
      streamingInput?.close();
      lines.close();
      input.pause?.();
    },
  });
}

export async function main() {
  const controls = openStdinControlSession();
  try {
    const request = await controls.request;
    const result = await runPrimaryEntrypoint(request, {
      streamingInput: controls.bind(cleanString(request?.prompt)),
    });
    process.stdout.write(`${JSON.stringify({ ok: true, result })}\n`);
  } catch (error) {
    process.stdout.write(`${JSON.stringify({
      ok: false,
      error: {
        code: cleanString(error?.code) || "primary_runtime_error",
        message: cleanString(error?.message) || "primary runtime failed",
        ...(error?.receipt ? { receipt: error.receipt } : {}),
      },
    })}\n`);
    process.exitCode = 1;
  } finally {
    controls.close();
  }
}

const directUrl = process.argv[1] ? pathToFileURL(process.argv[1]).href : "";
if (directUrl && import.meta.url === directUrl) await main();
