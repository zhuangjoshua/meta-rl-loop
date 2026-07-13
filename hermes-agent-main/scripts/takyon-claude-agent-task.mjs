#!/usr/bin/env node
import path from "node:path";
import fs from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import net from "node:net";
import { fileURLToPath } from "node:url";

const PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
const SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
let workerStderr = "";

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.on("data", (chunk) => {
      chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
    });
    process.stdin.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    process.stdin.on("error", reject);
  });
}

function isSubpath(root, maybePath) {
  const absolute = path.resolve(root, String(maybePath || "."));
  const relative = path.relative(root, absolute);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function pathValues(input) {
  if (!input || typeof input !== "object") return [];
  return ["file_path", "path", "notebook_path"]
    .map((key) => input[key])
    .filter((value) => typeof value === "string" && value.length > 0);
}

function normalizeRelative(value) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\.\/+/, "")
    .replace(/^\/+/, "")
    .replace(/\/+$/, "");
}

function sandboxedBashCommand(command) {
  const script = String(command || "");
  return `/usr/bin/env -i PATH=${SANDBOX_PATH} HOME=/tmp /bin/bash -lc ${JSON.stringify(script)}`;
}

function isManualTastePreflightCommand(command) {
  const script = String(command || "");
  return (
    /\bagent-browser\b/i.test(script) ||
    /(?:^|[\s/])chromium(?:-browser)?\b/i.test(script) ||
    /\b(?:npm|pnpm|yarn)\s+(?:run\s+)?preview\b/i.test(script) ||
    /\b(?:npx\s+)?vite\s+preview\b/i.test(script)
  );
}

function preflightChildEnv({ browserNone = false } = {}) {
  const env = {
    PATH: SANDBOX_PATH,
    HOME: "/tmp",
    ...(browserNone ? { BROWSER: "none" } : {}),
  };
  for (const key of ["LANG", "LC_ALL"]) {
    const value = String(process.env[key] || "").trim();
    if (value) env[key] = value;
  }
  return env;
}

const SITE_IMAGE_BRIDGE_TIMEOUT_MS = 240_000;
const TASTE_PREFLIGHT_MAX_CALLS = 2;
const TASTE_PREFLIGHT_CHROMIUM = "/usr/bin/chromium";
const TASTE_PREFLIGHT_PREVIEW_TIMEOUT_MS = 20_000;
const TASTE_PREFLIGHT_CHROMIUM_TIMEOUT_MS = 45_000;
const TASTE_PREFLIGHT_VIEWPORTS = Object.freeze([
  Object.freeze({ name: "desktop", width: 1440, height: 900 }),
  Object.freeze({ name: "mobile", width: 390, height: 844 }),
]);

function tastePreflightDir(cwd) {
  return path.join(path.resolve(cwd), ".takyon-preflight");
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function invokeSiteImageBridge(bridgeDir, args) {
  const requestId = randomUUID();
  const requestsDir = path.join(bridgeDir, "requests");
  const responsesDir = path.join(bridgeDir, "responses");
  const requestPath = path.join(requestsDir, `${requestId}.json`);
  const temporaryPath = path.join(requestsDir, `.${requestId}.tmp`);
  const responsePath = path.join(responsesDir, `${requestId}.json`);
  await fs.writeFile(temporaryPath, `${JSON.stringify({ args })}\n`, { encoding: "utf8", mode: 0o600 });
  await fs.rename(temporaryPath, requestPath);
  const deadline = Date.now() + SITE_IMAGE_BRIDGE_TIMEOUT_MS;
  try {
    while (Date.now() < deadline) {
      try {
        const response = JSON.parse(await fs.readFile(responsePath, "utf8"));
        if (!response || typeof response !== "object") {
          throw new Error("site-image bridge returned an invalid response");
        }
        if (!response.success) {
          throw new Error(String(response.error || "site-image generation failed"));
        }
        return response;
      } catch (error) {
        if (error?.code !== "ENOENT") throw error;
      }
      await delay(75);
    }
    throw new Error("site-image generation timed out after 240 seconds");
  } finally {
    await Promise.allSettled([fs.unlink(requestPath), fs.unlink(responsePath)]);
  }
}

function childHasExited(child) {
  return !child || child.exitCode !== null || child.signalCode !== null;
}

function signalProcessTree(child, signal) {
  if (!child?.pid) return;
  try {
    // Every preflight child is detached into its own process group. Signalling the group also
    // reaches Vite or Chromium helper processes instead of leaking daemons.
    process.kill(-child.pid, signal);
  } catch (error) {
    if (error?.code !== "ESRCH" && !childHasExited(child)) {
      try {
        child.kill(signal);
      } catch {
        // The process may have exited between the state check and signal delivery.
      }
    }
  }
}

function waitForChildExit(child, timeoutMs) {
  if (childHasExited(child)) return Promise.resolve(true);
  return new Promise((resolve) => {
    let timer = null;
    const onClose = () => {
      if (timer) clearTimeout(timer);
      resolve(true);
    };
    timer = setTimeout(() => {
      child.removeListener("close", onClose);
      resolve(childHasExited(child));
    }, timeoutMs);
    child.once("close", onClose);
  });
}

async function stopProcessTree(child) {
  if (!child?.pid) return;
  signalProcessTree(child, "SIGTERM");
  await waitForChildExit(child, 2_000);
  // The leader can exit before a descendant. Always address the original process group once more.
  signalProcessTree(child, "SIGKILL");
  if (!childHasExited(child)) await waitForChildExit(child, 2_000);
}

function collectBoundedOutput(stream, limit = 8_000) {
  let output = "";
  stream?.setEncoding?.("utf8");
  stream?.on?.("data", (chunk) => {
    if (output.length >= limit) return;
    output += String(chunk || "").slice(0, limit - output.length);
  });
  return () => output;
}

function reserveLoopbackPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0, exclusive: true }, () => {
      const address = server.address();
      const port = address && typeof address === "object" ? address.port : 0;
      server.close((error) => {
        if (error) reject(error);
        else if (!port) reject(new Error("could not reserve a loopback preview port"));
        else resolve(port);
      });
    });
  });
}

async function fetchWithTimeout(url, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      method: "GET",
      redirect: "manual",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

async function waitForVitePreview({ child, url, readOutput }) {
  const deadline = Date.now() + TASTE_PREFLIGHT_PREVIEW_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (childHasExited(child)) {
      throw new Error(`Vite preview exited before it was ready: ${compactText(readOutput(), 1200)}`);
    }
    try {
      const response = await fetchWithTimeout(url, 1_000);
      if (response.ok) return;
    } catch {
      // Vite has not bound the loopback socket yet.
    }
    await delay(100);
  }
  throw new Error(
    `Vite preview did not become ready within ${TASTE_PREFLIGHT_PREVIEW_TIMEOUT_MS}ms: ` +
    compactText(readOutput(), 1200)
  );
}

async function startVitePreview(cwd) {
  const port = await reserveLoopbackPort();
  const url = `http://127.0.0.1:${port}/`;
  const vite = path.join(cwd, "node_modules", ".bin", "vite");
  const child = spawn(
    vite,
    ["preview", "--host", "127.0.0.1", "--port", String(port), "--strictPort"],
    {
      cwd,
      // Product-owned Vite config executes in this child. Never inherit the parent SDK's short-TTL
      // operator capability or provider-broker configuration into product-controlled code.
      env: preflightChildEnv({ browserNone: true }),
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    }
  );
  const readStdout = collectBoundedOutput(child.stdout);
  const readStderr = collectBoundedOutput(child.stderr);
  const readOutput = () => `${readStdout()}\n${readStderr()}`.trim();
  try {
    await Promise.race([
      waitForVitePreview({ child, url, readOutput }),
      new Promise((_, reject) => child.once("error", reject)),
    ]);
    return { child, url };
  } catch (error) {
    await stopProcessTree(child);
    throw error;
  }
}

async function runBoundedChild(command, args, { cwd, timeoutMs }) {
  const child = spawn(command, args, {
    cwd,
    // Chromium renders product-controlled code; it gets no SDK/broker capability environment.
    env: preflightChildEnv(),
    detached: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const readStdout = collectBoundedOutput(child.stdout);
  const readStderr = collectBoundedOutput(child.stderr);
  let timer = null;
  try {
    const result = await new Promise((resolve, reject) => {
      timer = setTimeout(() => {
        signalProcessTree(child, "SIGKILL");
        reject(new Error(`${path.basename(command)} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      child.once("error", reject);
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    if (result.code !== 0) {
      throw new Error(
        `${path.basename(command)} exited ${result.code ?? result.signal}: ` +
        compactText(`${readStdout()}\n${readStderr()}`, 1600)
      );
    }
  } finally {
    if (timer) clearTimeout(timer);
    await stopProcessTree(child);
  }
}

async function assertPngViewport(filePath, expectedWidth, expectedHeight) {
  const header = await fs.readFile(filePath);
  const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (header.length < 24 || !header.subarray(0, 8).equals(pngSignature)) {
    throw new Error(`Chromium did not create a valid PNG at ${filePath}`);
  }
  const width = header.readUInt32BE(16);
  const height = header.readUInt32BE(20);
  if (width !== expectedWidth || height !== expectedHeight) {
    throw new Error(
      `Chromium rendered ${width}x${height} at ${filePath}; expected ` +
      `${expectedWidth}x${expectedHeight}`
    );
  }
}

async function captureTasteViewport({ cwd, url, viewport, outputPath, profileDir }) {
  await runBoundedChild(
    TASTE_PREFLIGHT_CHROMIUM,
    [
      "--headless=new",
      "--no-sandbox",
      "--disable-dev-shm-usage",
      "--disable-background-networking",
      "--disable-background-timer-throttling",
      "--disable-default-apps",
      "--disable-extensions",
      "--disable-gpu",
      "--disable-renderer-backgrounding",
      "--disable-sync",
      "--force-color-profile=srgb",
      "--force-device-scale-factor=1",
      "--hide-scrollbars",
      "--metrics-recording-only",
      "--no-default-browser-check",
      "--no-first-run",
      "--run-all-compositor-stages-before-draw",
      "--virtual-time-budget=2500",
      `--user-data-dir=${profileDir}`,
      `--window-size=${viewport.width},${viewport.height}`,
      `--screenshot=${outputPath}`,
      url,
    ],
    { cwd, timeoutMs: TASTE_PREFLIGHT_CHROMIUM_TIMEOUT_MS }
  );
  await fs.chmod(outputPath, 0o600);
  await assertPngViewport(outputPath, viewport.width, viewport.height);
}

async function renderTasteLandingPreflight(cwd) {
  const distIndex = path.join(cwd, "dist", "index.html");
  const preflightDir = tastePreflightDir(cwd);
  try {
    await fs.access(distIndex);
    await fs.access(TASTE_PREFLIGHT_CHROMIUM);
  } catch (error) {
    if (error?.path === distIndex) {
      throw new Error("Taste render preflight requires a built dist/index.html; run npm run build first");
    }
    throw new Error(`Taste render preflight requires Chromium at ${TASTE_PREFLIGHT_CHROMIUM}`);
  }

  await fs.rm(preflightDir, { recursive: true, force: true });
  await fs.mkdir(preflightDir, { recursive: true, mode: 0o700 });
  const profileRoot = path.join("/tmp", `takyon-taste-preflight-${randomUUID()}`);
  let preview = null;
  try {
    preview = await startVitePreview(cwd);
    const screenshots = [];
    for (const viewport of TASTE_PREFLIGHT_VIEWPORTS) {
      const outputPath = path.join(preflightDir, `landing-${viewport.name}.png`);
      const profileDir = path.join(profileRoot, viewport.name);
      await fs.mkdir(profileDir, { recursive: true, mode: 0o700 });
      await captureTasteViewport({ cwd, url: preview.url, viewport, outputPath, profileDir });
      screenshots.push({
        name: viewport.name,
        width: viewport.width,
        height: viewport.height,
        path: outputPath,
      });
    }
    return {
      success: true,
      route: "/",
      screenshots,
      instruction: "Read both returned PNG paths with the Read tool and inspect the actual first viewports.",
    };
  } finally {
    await stopProcessTree(preview?.child);
    await fs.rm(profileRoot, { recursive: true, force: true });
  }
}

function createSiteImageMcpServer({ createSdkMcpServer, tool, z, bridgeDir, cwd }) {
  if (!bridgeDir) return null;
  let preflightCalls = 0;
  return createSdkMcpServer({
    name: "takyon_site_image",
    version: "1.0.0",
    alwaysLoad: true,
    instructions:
      "Taste landing image generation is mandatory. After the Design Read, generate exactly two " +
      "distinct page-role assets: one hero and one supporting image. Use every returned public_path " +
      "in an <img data-takyon-landing-asset=\"hero|supporting\">. The image tool is capped and " +
      "money-gated. After build and typecheck pass, call business_render_landing_preflight instead " +
      "of starting agent-browser or a preview daemon yourself. Read both returned screenshots, fix " +
      "the source if needed, rebuild, and use the one remaining preflight call for the final render.",
    tools: [
      tool(
        "business_generate_site_image",
        "Generate one real, business-owned landing image through Takyon's Safebox-gated creative rail. " +
          "Art-direct one exact page role. No baked-in text, UI labels, logos, watermarks, browser chrome, " +
          "fake product controls, generic filler, or stock hotlinks. Returns only a local /generated/... path.",
        {
          slug: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/).max(80),
          prompt: z.string().min(1).max(12000),
          aspect_ratio: z.enum(["16:9", "4:3", "1:1", "3:4", "9:16"]),
          purpose: z.string().min(1).max(200),
        },
        async (args) => {
          try {
            const result = await invokeSiteImageBridge(bridgeDir, args);
            return { content: [{ type: "text", text: JSON.stringify(result) }] };
          } catch (error) {
            return {
              isError: true,
              content: [{ type: "text", text: String(error?.message || error) }],
            };
          }
        },
        { alwaysLoad: true }
      ),
      tool(
        "business_render_landing_preflight",
        "Deterministically render the built landing at desktop 1440x900 and mobile 390x844. " +
          "Starts and stops one loopback-only Vite preview, invokes /usr/bin/chromium directly, " +
          "and writes /workspace/.takyon-preflight/landing-{desktop,mobile}.png. Maximum two calls " +
          "for this Taste session. Call only after npm run build and npm run typecheck pass; then " +
          "Read both returned PNGs and inspect them visually.",
        {},
        async () => {
          preflightCalls += 1;
          if (preflightCalls > TASTE_PREFLIGHT_MAX_CALLS) {
            return {
              isError: true,
              content: [{
                type: "text",
                text: `Taste landing render preflight is capped at ${TASTE_PREFLIGHT_MAX_CALLS} calls`,
              }],
            };
          }
          try {
            const result = await renderTasteLandingPreflight(cwd);
            return {
              content: [{
                type: "text",
                text: JSON.stringify({
                  ...result,
                  call: preflightCalls,
                  remaining_calls: TASTE_PREFLIGHT_MAX_CALLS - preflightCalls,
                }),
              }],
            };
          } catch (error) {
            return {
              isError: true,
              content: [{ type: "text", text: String(error?.message || error) }],
            };
          }
        },
        { alwaysLoad: true }
      ),
    ],
  });
}

function buildClaudeSessionEnv({
  anthropicApiKey,
  anthropicToken,
  disableExperimentalBetas,
  anthropicBaseUrl,
  anthropicModel,
  anthropicDefaultOpusModel,
  anthropicDefaultSonnetModel,
  anthropicDefaultHaikuModel,
  claudeCodeSubagentModel,
  inDockerWorker,
  cwd,
}) {
  const env = {
    PATH: String(process.env.PATH || SANDBOX_PATH).trim() || SANDBOX_PATH,
    HOME: String(process.env.HOME || "/tmp").trim() || "/tmp",
    ANTHROPIC_API_KEY: anthropicApiKey,
    ANTHROPIC_TOKEN: anthropicToken,
    CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-business-agent",
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: disableExperimentalBetas,
    ...(anthropicBaseUrl ? { ANTHROPIC_BASE_URL: anthropicBaseUrl } : {}),
    ...(anthropicModel ? { ANTHROPIC_MODEL: anthropicModel } : {}),
    ...(anthropicDefaultOpusModel ? { ANTHROPIC_DEFAULT_OPUS_MODEL: anthropicDefaultOpusModel } : {}),
    ...(anthropicDefaultSonnetModel ? { ANTHROPIC_DEFAULT_SONNET_MODEL: anthropicDefaultSonnetModel } : {}),
    ...(anthropicDefaultHaikuModel ? { ANTHROPIC_DEFAULT_HAIKU_MODEL: anthropicDefaultHaikuModel } : {}),
    ...(claudeCodeSubagentModel ? { CLAUDE_CODE_SUBAGENT_MODEL: claudeCodeSubagentModel } : {})
  };
  for (const key of ["LANG", "LC_ALL", "SHELL", "TERM", "TMPDIR", "TMP", "TEMP", "USER"]) {
    const value = String(process.env[key] || "").trim();
    if (value) env[key] = value;
  }
  if (inDockerWorker) {
    env.TERMINAL_ENV = "local";
    env.TERMINAL_CWD = cwd;
    env.TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE = "0";
  }
  return env;
}

function rewriteWorkspacePrefixedPaths(input, workspace) {
  if (!input || typeof input !== "object") return input;
  const workspacePrefix = normalizeRelative(workspace);
  if (!workspacePrefix || workspacePrefix === ".") return input;
  let changed = false;
  const updated = { ...input };
  for (const key of ["file_path", "path", "notebook_path"]) {
    const value = updated[key];
    if (typeof value !== "string" || path.isAbsolute(value)) continue;
    const clean = normalizeRelative(value);
    if (clean === workspacePrefix) {
      updated[key] = ".";
      changed = true;
    } else if (clean.startsWith(`${workspacePrefix}/`)) {
      updated[key] = clean.slice(workspacePrefix.length + 1) || ".";
      changed = true;
    }
  }
  return changed ? updated : input;
}

function textFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record) return "";
  if (record.type === "assistant") {
    const inner = record.message && typeof record.message === "object" ? record.message : null;
    const content = Array.isArray(inner?.content) ? inner.content : [];
    return content
      .map((part) => (part?.type === "text" && typeof part.text === "string" ? part.text : ""))
      .filter(Boolean)
      .join("\n");
  }
  if (record.type === "result" && typeof record.result === "string") return record.result;
  return "";
}

function redact(text) {
  return String(text || "")
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "sk-[redacted]")
    .replace(/EAA[A-Za-z0-9_-]{20,}/g, "EAA[redacted]")
    .replace(/\b(api[_-]?key|access[_-]?token|secret)=([^&\s]+)/gi, "$1=[redacted]");
}

function compactText(text, maxLen = 240) {
  const cleaned = redact(String(text || "").replace(/\s+/g, " ").trim());
  if (!cleaned) return "";
  return cleaned.length > maxLen ? `${cleaned.slice(0, maxLen - 1)}…` : cleaned;
}

function humanizeLabel(value) {
  const cleaned = String(value || "")
    .replace(/[._-]+/g, " ")
    .trim();
  if (!cleaned) return "Claude task";
  return cleaned.replace(/\b\w/g, (match) => match.toUpperCase());
}

const thinkingBlockState = new Set();
let emittedPlanningMilestone = false;

function planningProgressEvent() {
  const detail = "Claude is inspecting the product and preparing the implementation.";
  return {
    kind: "claude_agent_sdk",
    status: "trace",
    detail,
    line: detail,
    trace: {
      kind: "reasoning",
      entry_key: "claude-planning",
      label: "Planning",
      detail,
      status: "running",
      summary: detail,
    },
  };
}

function assistantThinkingText(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "assistant") return "";
  const inner = record.message && typeof record.message === "object" ? record.message : null;
  const content = Array.isArray(inner?.content) ? inner.content : [];
  return content
    .map((part) => (part?.type === "thinking" && typeof part.thinking === "string" ? part.thinking : ""))
    .filter(Boolean)
    .join("\n");
}

function thinkingProgressEventFromStream(message) {
  const record = message && typeof message === "object" ? message : null;
  const event = record && record.type === "stream_event" && record.event && typeof record.event === "object"
    ? record.event
    : null;
  if (!event) return null;
  if (event.type === "content_block_start") {
    const block = event.content_block && typeof event.content_block === "object" ? event.content_block : null;
    if (block?.type !== "thinking") return null;
    thinkingBlockState.add(Number(event.index));
    if (emittedPlanningMilestone) return null;
    emittedPlanningMilestone = true;
    return planningProgressEvent();
  }
  if (event.type === "content_block_delta") {
    const delta = event.delta && typeof event.delta === "object" ? event.delta : null;
    if (!delta || delta.type !== "thinking_delta") return null;
    // Thinking deltas are private model reasoning, not user-facing progress. Streaming their
    // arbitrary token boundaries caused split words, cumulative replay, and hundreds of lines
    // of design deliberation. Tool/task events below remain the authoritative progress lane.
    return null;
  }
  if (event.type === "content_block_stop") {
    thinkingBlockState.delete(Number(event.index));
    return null;
  }
  return null;
}

function progressEventFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record) return null;
  const thinkingProgress = thinkingProgressEventFromStream(record);
  if (thinkingProgress) return thinkingProgress;
  const assistantThinking = assistantThinkingText(record);
  if (assistantThinking) {
    // The completed assistant envelope repeats thinking blocks after their stream events. Never
    // expose that private reasoning or replay it as a second, larger progress message.
    return null;
  }
  if (record.type === "system" && record.subtype === "task_started") {
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(record.description || record.prompt || record.workflow_name || record.task_type || "Claude task started.");
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(record.description || record.workflow_name || record.subagent_type || record.task_type || "Claude task", 80) || "Claude task",
        detail,
        status: "running",
        skill_name: compactText(record.workflow_name || record.subagent_type || "", 80),
      },
    };
  }
  if (record.type === "system" && record.subtype === "task_progress") {
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(record.summary || record.description || record.last_tool_name || "Claude task is running.");
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(record.description || record.subagent_type || "Claude task", 80) || "Claude task",
        detail,
        status: "running",
        tool_name: compactText(record.last_tool_name || "", 80),
        summary: detail,
      },
    };
  }
  if (record.type === "system" && record.subtype === "task_updated") {
    const patch = record.patch && typeof record.patch === "object" ? record.patch : {};
    const rawStatus = String(patch.status || "running").trim().toLowerCase();
    const traceStatus = rawStatus === "completed" ? "completed" : rawStatus === "failed" || rawStatus === "killed" ? "failed" : "running";
    const entryKey = `claude-task:${String(record.task_id || record.uuid || "task").trim()}`;
    const detail = compactText(patch.error || patch.description || `Claude task ${rawStatus || "updated"}.`);
    return {
      kind: "claude_agent_sdk",
      status: "trace",
      detail,
      line: detail,
      trace: {
        kind: "task",
        entry_key: entryKey,
        label: compactText(patch.description || "Claude task", 80) || "Claude task",
        detail,
        status: traceStatus,
        summary: detail,
      },
    };
  }
  if (record.type === "tool_progress") {
    const roundedSeconds = Math.max(0, Math.round(Number(record.elapsed_time_seconds || 0)));
    if (roundedSeconds > 0 && roundedSeconds % 5 !== 0) return null;
    const detail = compactText(`${humanizeLabel(record.tool_name)} running${roundedSeconds > 0 ? ` · ${roundedSeconds}s` : ""}`);
    return {
      kind: "claude_agent_sdk",
      status: "output",
      detail,
      line: detail,
      trace: {
        kind: "tool",
        entry_key: `claude-tool:${String(record.tool_use_id || record.uuid || record.tool_name || "tool").trim()}`,
        label: humanizeLabel(record.tool_name || "Tool"),
        detail,
        status: "running",
        tool_name: compactText(record.tool_name || "", 80),
      },
    };
  }
  if (record.type === "tool_use_summary") {
    const detail = compactText(record.summary || "Claude tool completed.");
    return detail ? { kind: "claude_agent_sdk", status: "output", detail, line: detail } : null;
  }
  if (record.type === "system" && record.subtype === "api_retry") {
    const status = Number.isInteger(record.error_status)
      ? `HTTP ${record.error_status}`
      : "connection error";
    const reason = compactText(record.error || "unknown", 80) || "unknown";
    const detail = compactText(
      `Claude API retry ${Number(record.attempt || 0)}/${Number(record.max_retries || 0)} ` +
      `in ${Number(record.retry_delay_ms || 0)}ms: ${reason} (${status}).`
    );
    return detail ? { kind: "claude_agent_sdk", status: "output", detail, line: detail } : null;
  }
  return null;
}

function apiRetryFailureFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "system" || record.subtype !== "api_retry") return "";
  const status = Number.isInteger(record.error_status)
    ? `HTTP ${record.error_status}`
    : "connection error";
  const reason = compactText(record.error || "unknown", 80) || "unknown";
  return compactText(
    `Claude API retry refused by fail-fast policy on attempt ${Number(record.attempt || 0)}/` +
    `${Number(record.max_retries || 0)}: ${reason} (${status})`,
    320
  );
}

let lastProgressSignature = "";

function emitProgress(event) {
  if (!event || typeof event !== "object") return;
  const payload = {
    kind: compactText(event.kind || "claude_agent_sdk", 80) || "claude_agent_sdk",
    status: compactText(event.status || "output", 24) || "output",
    detail: compactText(event.detail || event.line || "", 240),
    line: compactText(event.line || event.detail || "", 240),
  };
  if (event.trace && typeof event.trace === "object") {
    payload.trace = Object.fromEntries(
      Object.entries(event.trace)
        .map(([key, value]) => [key, compactText(value, key === "entry_key" ? 120 : 160)])
        .filter(([, value]) => value)
    );
  }
  if (!payload.detail && !payload.line && !payload.trace) return;
  const serialized = JSON.stringify(payload);
  if (serialized === lastProgressSignature) return;
  lastProgressSignature = serialized;
  process.stderr.write(`${PROGRESS_PREFIX}${serialized}\n`);
}

function buildPrompt(input) {
  const normalizedWorkspace = normalizeRelative(input.workspace || ".");
  const bashRule = input.allowBash
    ? "You may use Bash only for local build/test/install/cleanup inside the current workspace. Do not use it for provider calls, deployment, posting, payment changes, or filesystem access outside this workspace."
    : "Do not attempt shell commands, network calls, vendor side effects, credential reads, deployment, posting, payment changes, or filesystem access outside this workspace.";
  const noteRule = normalizedWorkspace === "product/site" || normalizedWorkspace.startsWith("product/site/")
    ? "For product/site work, reflect durable truth in the source itself and your final summary. Do not create helper markdown, request files, verification notes, or scratch docs unless the instruction explicitly asks for them."
    : "If durable business truth changes, write a concise note into an appropriate file in this workspace or a child path.";
  const buildGate = input.allowBash
    ? "Customer-facing product build gate (HARD): before you finish you MUST run `npm run build` and `npm run typecheck` and confirm BOTH exit green. Diagnosing the error is not done; only a green build is done. If you cannot land both green this pass, do NOT report success — your FINAL line MUST start with BLOCKED: followed by the exact remaining build/typecheck error and the file(s) involved."
    : "";
  return [
    "You are a Claude Agent SDK worker called by Takyon for one bounded business-scoped task.",
    "",
    `You may inspect and edit files only inside the provided current workspace. ${bashRule}`,
    "The provided current workspace is already your working directory. Write paths relative to it; do not prefix paths with the workspace name again.",
    "",
    `Make the smallest useful changes that satisfy the task. Preserve existing business files unless the instruction asks to update them. ${noteRule}`,
    "",
    "Execution posture (HARD): begin with targeted file inspection, then implement immediately. Do not narrate design exploration, produce an implementation plan, or spend a turn deliberating before using tools. Keep private reasoning private; expose only concise tool progress and the final result.",
    "",
    "Do not claim external execution happened. If the task needs a vendor/API/payment/deploy/posting action you cannot perform, report the blocker in the final summary instead of pretending it ran.",
    "Do not create request/spec/verification markdown files unless the instruction explicitly asks for them.",
    ...(buildGate ? ["", buildGate] : []),
    "",
    `Business: ${input.business}`,
    `Workspace: ${input.workspace || "."}`,
    "",
    "Task:",
    String(input.instruction || "").trim(),
    "",
    "Finish with a short summary of files changed and any blocker. If blocked, start the final line with BLOCKED:."
  ].join("\n");
}

async function main() {
  thinkingBlockState.clear();
  emittedPlanningMilestone = false;
  lastProgressSignature = "";
  const raw = await readStdin();
  const input = JSON.parse(raw || "{}");
  const cwd = path.resolve(String(input.cwd || "."));
  const root = path.resolve(String(input.root || cwd));
  if (!isSubpath(root, cwd)) {
    throw new Error("cwd must be inside the business root");
  }
  const anthropicApiKey = String(process.env.ANTHROPIC_API_KEY || "").trim();
  const anthropicToken = String(
    process.env.ANTHROPIC_TOKEN || process.env.CLAUDE_CODE_OAUTH_TOKEN || ""
  ).trim();
  // Broker lockdown: the operator host points the SDK at the safebox broker (ANTHROPIC_BASE_URL) and
  // injects a short-TTL capability token as ANTHROPIC_API_KEY — the raw provider key never enters this
  // container. In that mode ANTHROPIC_API_KEY (the capability token) is the auth and the base URL is
  // present; outside lockdown a raw key/token is still required.
  const anthropicBaseUrl = String(process.env.ANTHROPIC_BASE_URL || "").trim();
  const anthropicModel = String(process.env.ANTHROPIC_MODEL || "").trim();
  const anthropicDefaultOpusModel = String(process.env.ANTHROPIC_DEFAULT_OPUS_MODEL || "").trim();
  const anthropicDefaultSonnetModel = String(process.env.ANTHROPIC_DEFAULT_SONNET_MODEL || "").trim();
  const anthropicDefaultHaikuModel = String(process.env.ANTHROPIC_DEFAULT_HAIKU_MODEL || "").trim();
  const claudeCodeSubagentModel = String(process.env.CLAUDE_CODE_SUBAGENT_MODEL || "").trim();
  const disableExperimentalBetas = String(
    process.env.CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS || "1"
  ).trim() || "1";
  if (!anthropicApiKey && !anthropicToken) {
    throw new Error(
      "ANTHROPIC_API_KEY, ANTHROPIC_TOKEN, or CLAUDE_CODE_OAUTH_TOKEN is required"
    );
  }

  const requestedModel = String(input.model || "").trim();
  const pinnedModel = String(process.env.TAKYON_CLAUDE_AGENT_MODEL || "").trim();
  if (requestedModel && pinnedModel && requestedModel !== pinnedModel) {
    throw new Error(
      `coding worker model override refused: requested ${JSON.stringify(requestedModel)}, ` +
      `pinned ${JSON.stringify(pinnedModel)} by TAKYON_CLAUDE_AGENT_MODEL`
    );
  }
  const model = requestedModel || pinnedModel;
  if (!model) {
    throw new Error(
      "coding worker model is not configured; no fallback model is available"
    );
  }
  for (const [name, value] of Object.entries({
    ANTHROPIC_MODEL: anthropicModel,
    ANTHROPIC_DEFAULT_OPUS_MODEL: anthropicDefaultOpusModel,
    ANTHROPIC_DEFAULT_SONNET_MODEL: anthropicDefaultSonnetModel,
    ANTHROPIC_DEFAULT_HAIKU_MODEL: anthropicDefaultHaikuModel,
    CLAUDE_CODE_SUBAGENT_MODEL: claudeCodeSubagentModel,
  })) {
    if (value && value !== model) {
      throw new Error(
        `coding worker model alias ${name}=${JSON.stringify(value)} conflicts with pinned model ` +
        JSON.stringify(model)
      );
    }
  }

  const { query, createSdkMcpServer, tool } = await import("@anthropic-ai/claude-agent-sdk");
  const { z } = await import("zod");
  const abortController = new AbortController();
  const maxTurns = Number.parseInt(String(input.maxTurns || ""), 10) || 12;
  const maxBudgetUsd = Number.parseFloat(String(input.maxBudgetUsd || "")) || 2;
  const effort = String(input.effort || process.env.TAKYON_CLAUDE_AGENT_EFFORT || "high").trim().toLowerCase();
  const allowBash = Boolean(input.allowBash);
  const pathToClaudeCodeExecutable = String(process.env.TAKYON_CLAUDE_CODE_EXECUTABLE || "").trim();
  const inDockerWorker = String(process.env.TAKYON_CLAUDE_AGENT_IN_DOCKER || "").trim() === "1";
  const siteImageBridgeDir = String(input.siteImageBridgeDir || "").trim();
  const failOnApiRetry = input.failOnApiRetry === true;
  const siteImageMcpServer = createSiteImageMcpServer({
    createSdkMcpServer,
    tool,
    z,
    bridgeDir: siteImageBridgeDir,
    cwd,
  });

  let text = "";
  let totalCostUsd = null;
  let finalUsage = null;
  workerStderr = "";
  let parentAbortRequested = false;
  const requestParentAbort = () => {
    parentAbortRequested = true;
    abortController.abort();
  };
  process.once("SIGTERM", requestParentAbort);
  process.once("SIGINT", requestParentAbort);
  try {
    for await (const message of query({
          prompt: buildPrompt(input),
          options: {
            abortController,
            cwd,
            env: buildClaudeSessionEnv({
              anthropicApiKey,
              anthropicToken,
              disableExperimentalBetas,
              anthropicBaseUrl,
              anthropicModel: model,
              anthropicDefaultOpusModel: model,
              anthropicDefaultSonnetModel: model,
              anthropicDefaultHaikuModel: model,
              claudeCodeSubagentModel: model,
              inDockerWorker,
              cwd,
            }),
            model,
            includePartialMessages: true,
            thinking: { type: "adaptive", display: "summarized" },
            effort: ["low", "medium", "high"].includes(effort) ? effort : "high",
            tools: [
              "Read", "Write", "Edit", "MultiEdit", "Grep", "Glob",
              ...(allowBash ? ["Bash"] : []),
            ],
            ...(siteImageMcpServer
              ? {
                  allowedTools: [
                    "mcp__takyon_site_image__business_generate_site_image",
                    "mcp__takyon_site_image__business_render_landing_preflight",
                  ],
                }
              : {}),
            ...(siteImageMcpServer
              ? { mcpServers: { takyon_site_image: siteImageMcpServer } }
              : {}),
            disallowedTools: allowBash ? [] : ["Bash"],
            permissionMode: "acceptEdits",
            persistSession: false,
            maxTurns,
            maxBudgetUsd,
            ...(pathToClaudeCodeExecutable ? { pathToClaudeCodeExecutable } : {}),
            stderr: (chunk) => {
              if (typeof chunk !== "string" || !chunk) return;
              if (workerStderr.length >= 12000) return;
              workerStderr += chunk.slice(0, 12000 - workerStderr.length);
            },
            canUseTool: async (toolName, toolInput, options) => {
              if (toolName === "Bash") {
                if (!allowBash) {
                  return {
                    behavior: "deny",
                    message: "Bash is disabled for Takyon Claude SDK business tasks.",
                    toolUseID: options.toolUseID
                  };
                }
                const updatedInput = { ...(toolInput || {}) };
                const rawCommand = typeof updatedInput.command === "string"
                  ? updatedInput.command
                  : typeof updatedInput.cmd === "string"
                    ? updatedInput.cmd
                    : "";
                if (siteImageMcpServer && isManualTastePreflightCommand(rawCommand)) {
                  return {
                    behavior: "deny",
                    message:
                      "Taste landing preview/browser commands are disabled. Run build and typecheck, " +
                      "then call business_render_landing_preflight and Read both returned PNGs.",
                    toolUseID: options.toolUseID
                  };
                }
                if (typeof updatedInput.command === "string") {
                  updatedInput.command = sandboxedBashCommand(updatedInput.command);
                } else if (typeof updatedInput.cmd === "string") {
                  updatedInput.cmd = sandboxedBashCommand(updatedInput.cmd);
                }
                return { behavior: "allow", updatedInput, toolUseID: options.toolUseID };
              }
              const updatedInput = rewriteWorkspacePrefixedPaths(toolInput, input.workspace || ".");
              const outside = pathValues(updatedInput).find((value) => !isSubpath(root, path.resolve(cwd, value)));
              if (outside) {
                return {
                  behavior: "deny",
                  message: "Takyon Claude SDK tasks may only access files inside the requested workspace.",
                  toolUseID: options.toolUseID
                };
              }
              return { behavior: "allow", updatedInput, toolUseID: options.toolUseID };
            }
          }
    })) {
      emitProgress(progressEventFromSdkMessage(message));
      const apiRetryFailure = failOnApiRetry ? apiRetryFailureFromSdkMessage(message) : "";
      if (apiRetryFailure) {
        abortController.abort();
        throw new Error(apiRetryFailure);
      }
      const chunk = textFromSdkMessage(message);
      if (chunk) text += `${chunk}\n`;
      if (message && typeof message === "object" && message.type === "result") {
        if (typeof message.total_cost_usd === "number" && Number.isFinite(message.total_cost_usd)) {
          totalCostUsd = message.total_cost_usd;
        }
        if (message.usage && typeof message.usage === "object") {
          finalUsage = message.usage;
        }
      }
    }
    if (parentAbortRequested) {
      throw new Error("Claude Agent SDK task cancelled by parent supervisor");
    }
  } finally {
    process.removeListener("SIGTERM", requestParentAbort);
    process.removeListener("SIGINT", requestParentAbort);
    // Screenshots must remain available while this SDK session can Read them, but they are
    // verification scratch, never business source. Remove them before stdout lets the parent sync
    // the mounted workspace; the parent repeats this cleanup after forced-timeout container exits.
    if (siteImageMcpServer) {
      await fs.rm(tastePreflightDir(cwd), { recursive: true, force: true });
    }
  }

  process.stdout.write(JSON.stringify({
    success: true,
    source: "claude-agent-sdk",
    model,
    summary: redact(text).trim(),
    total_cost_usd: typeof totalCostUsd === "number" ? totalCostUsd : null,
    actual_cost_cents: typeof totalCostUsd === "number" ? Math.max(0, Math.round(totalCostUsd * 100)) : null,
    usage: finalUsage,
    worker_stderr: redact(workerStderr).trim() || null,
  }));
}

export {
  apiRetryFailureFromSdkMessage,
  buildPrompt,
  progressEventFromSdkMessage,
  renderTasteLandingPreflight,
};

const isMainModule = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) main().catch((error) => {
  emitProgress({
    kind: "claude_agent_sdk",
    status: "failed",
    detail: compactText(error?.message || String(error)),
    line: compactText(error?.message || String(error)),
  });
  process.stdout.write(JSON.stringify({
    success: false,
    source: "claude-agent-sdk",
    error: redact(error?.stack || error?.message || String(error)),
    worker_stderr: redact(workerStderr).trim() || null,
  }));
  process.exitCode = 1;
});
