#!/usr/bin/env node
import path from "node:path";
import fs from "node:fs/promises";
import { createHash, randomUUID } from "node:crypto";
import { spawn } from "node:child_process";
import net from "node:net";
import { fileURLToPath } from "node:url";

const PROGRESS_PREFIX = "TAKYON_SDK_EVENT ";
const SANDBOX_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin";
const TASTE_SKILL_NAME = "design-taste-frontend";
const TASTE_SKILL_SHA256 = "aa194351b246b8b4799099d4ed7b033d29eab6e6e3d58d8d2172978be7b3ec89";
const TASTE_PROMPT_DISTINCTIVE_MARKERS = Object.freeze([
  "The audience picks the aesthetic, not your taste.",
  "A pure-text page is not minimalism. It is incomplete work.",
  "The agent's default mental model that \"creative brief = serif\"",
]);
const RUNTIME_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLAUDE_CONFIG_DIR = path.join(RUNTIME_ROOT, ".claude");
const TASTE_SKILL_CANONICAL_DIR = path.join(RUNTIME_ROOT, "skills", "creative", "taste-frontend");
const TASTE_SKILL_NATIVE_DIR = path.join(CLAUDE_CONFIG_DIR, "skills", TASTE_SKILL_NAME);
let workerStderr = "";
let workerSkillReceipt = null;
let workerSkillStartedAt = 0;
let workerTastePublicationState = null;
let workerModel = null;
let workerUsage = null;
let workerTotalCostUsd = null;

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function isProductSiteWorkspace(workspace) {
  const normalized = normalizeRelative(workspace || ".");
  return normalized === "product/site" || normalized.startsWith("product/site/");
}

function isTasteSkillApplicable(input) {
  return isProductSiteWorkspace(input?.workspace);
}

function hasCanonicalTasteFrontmatter(value) {
  const head = Buffer.isBuffer(value) ? value.subarray(0, 4096).toString("utf8") : String(value || "").slice(0, 4096);
  return /^---\s*$[\s\S]*?^name:\s*design-taste-frontend\s*$[\s\S]*?^---\s*$/m.test(head);
}

function appendTasteSkillAdvisory(advisories, code, detail) {
  if (!advisories.some((item) => item?.code === code)) {
    advisories.push({ code, detail });
  }
}

async function validateNativeTasteSkill({ nativeDir, canonicalDir } = {}) {
  const resolvedNativeDir = path.resolve(String(nativeDir || TASTE_SKILL_NATIVE_DIR));
  const resolvedCanonicalDir = path.resolve(String(canonicalDir || TASTE_SKILL_CANONICAL_DIR));
  const nativePath = path.join(resolvedNativeDir, "SKILL.md");
  const canonicalPath = path.join(resolvedCanonicalDir, "SKILL.md");
  const advisories = [];
  let link = null;
  try {
    link = await fs.lstat(resolvedNativeDir);
  } catch (error) {
    appendTasteSkillAdvisory(
      advisories,
      "install_unconfirmed",
      `shared native Taste skill is not installed at ${resolvedNativeDir}: ${error?.code || "unreadable"}`
    );
  }
  const linkIsSymbolic = Boolean(link?.isSymbolicLink());
  if (link && !linkIsSymbolic) {
    appendTasteSkillAdvisory(
      advisories,
      "install_unconfirmed",
      `shared native Taste skill is not a symlink at ${resolvedNativeDir}`
    );
  }

  let canonicalRealPath = "";
  let canonicalContent = null;
  try {
    [canonicalRealPath, canonicalContent] = await Promise.all([
      fs.realpath(canonicalPath),
      fs.readFile(canonicalPath),
    ]);
  } catch (error) {
    appendTasteSkillAdvisory(
      advisories,
      "digest_unconfirmed",
      `canonical native Taste skill is unavailable at ${canonicalPath}: ${error?.code || "unreadable"}`
    );
  }

  let nativeRealPath = "";
  let nativeContent = null;
  if (linkIsSymbolic) {
    try {
      [nativeRealPath, nativeContent] = await Promise.all([
        fs.realpath(nativePath),
        fs.readFile(nativePath),
      ]);
    } catch (error) {
      appendTasteSkillAdvisory(
        advisories,
        "install_unconfirmed",
        `shared native Taste skill content is unavailable at ${nativePath}: ${error?.code || "unreadable"}`
      );
    }
  }

  const canonicalTarget = Boolean(
    nativeRealPath && canonicalRealPath && nativeRealPath === canonicalRealPath
  );
  if (!canonicalTarget) {
    appendTasteSkillAdvisory(
      advisories,
      "symlink_target_unconfirmed",
      "shared native Taste skill symlink does not resolve to the canonical runtime skill"
    );
  }
  const nativeDigest = nativeContent ? sha256(nativeContent) : "";
  const canonicalDigest = canonicalContent ? sha256(canonicalContent) : "";
  const nativeFrontmatterValid = Boolean(
    nativeContent && hasCanonicalTasteFrontmatter(nativeContent)
  );
  const canonicalFrontmatterValid = Boolean(
    canonicalContent && hasCanonicalTasteFrontmatter(canonicalContent)
  );
  if (
    nativeDigest !== TASTE_SKILL_SHA256
    || canonicalDigest !== TASTE_SKILL_SHA256
    || !nativeFrontmatterValid
    || !canonicalFrontmatterValid
  ) {
    appendTasteSkillAdvisory(
      advisories,
      "digest_unconfirmed",
      `shared native Taste skill did not match canonical digest/frontmatter ${TASTE_SKILL_SHA256}`
    );
  }
  const installed = Boolean(
    linkIsSymbolic
    && canonicalTarget
    && nativeDigest === TASTE_SKILL_SHA256
    && canonicalDigest === TASTE_SKILL_SHA256
    && nativeFrontmatterValid
    && canonicalFrontmatterValid
  );
  return {
    configDir: path.resolve(resolvedNativeDir, "..", ".."),
    receipt: {
      name: TASTE_SKILL_NAME,
      installed,
      expected_sha256: TASTE_SKILL_SHA256,
      installed_sha256: nativeDigest || null,
      canonical_sha256: canonicalDigest || null,
      source: "shared-runtime-symlink",
      native_scope: "user",
      canonical_target: canonicalTarget,
      advisory: advisories.length > 0,
      advisories,
    },
  };
}

function nativeTasteSkillReceiptAdvisories(receipt) {
  const record = receipt && typeof receipt === "object" ? receipt : {};
  const advisories = Array.isArray(record.advisories)
    ? record.advisories.filter((item) => item && typeof item === "object").map((item) => ({ ...item }))
    : [];
  if (record.required !== true) return advisories;
  if (record.installed !== true) {
    appendTasteSkillAdvisory(
      advisories,
      "install_unconfirmed",
      `Claude worker did not confirm installation of native skill ${TASTE_SKILL_NAME}.`
    );
  }
  if (record.canonical_target !== true) {
    appendTasteSkillAdvisory(
      advisories,
      "symlink_target_unconfirmed",
      `Claude worker did not confirm the canonical symlink target for ${TASTE_SKILL_NAME}.`
    );
  }
  if (String(record.installed_sha256 || "").toLowerCase() !== TASTE_SKILL_SHA256) {
    appendTasteSkillAdvisory(
      advisories,
      "digest_unconfirmed",
      `Claude worker did not confirm the canonical digest for ${TASTE_SKILL_NAME}.`
    );
  }
  if (record.discovery_event !== true || record.discovered !== true) {
    appendTasteSkillAdvisory(
      advisories,
      "sdk_discovery_unconfirmed",
      `Claude SDK did not confirm discovery of native skill ${TASTE_SKILL_NAME}.`
    );
  }
  if (record.inclusion_event !== true || record.included !== true) {
    appendTasteSkillAdvisory(
      advisories,
      "sdk_inclusion_unconfirmed",
      `Claude SDK did not confirm inclusion of native skill ${TASTE_SKILL_NAME} from user settings.`
    );
  }
  if (record.native_use !== true) {
    appendTasteSkillAdvisory(
      advisories,
      "sdk_invocation_unconfirmed",
      `Claude SDK did not confirm invocation of native skill ${TASTE_SKILL_NAME}.`
    );
  }
  if (
    record.prompt_body_absent !== true
    || record.prompt_distinctive_markers_absent !== true
  ) {
    appendTasteSkillAdvisory(
      advisories,
      "prompt_separation_unconfirmed",
      "Claude SDK receipt did not confirm that native Taste content stayed out of the worker prompt."
    );
  }
  return advisories;
}

function nativeTasteSkillUseFromSdkMessage(message) {
  return nativeTasteSkillToolUsesFromSdkMessage(message).length > 0;
}

function nativeTasteSkillToolUsesFromSdkMessage(message) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "assistant") return [];
  const content = Array.isArray(record.message?.content) ? record.message.content : [];
  return content.flatMap((block) => {
    if (!block || block.type !== "tool_use" || block.name !== "Skill") return [];
    const input = block.input && typeof block.input === "object" ? block.input : {};
    const requested = String(input.skill || input.name || input.command || "").trim();
    if (requested !== TASTE_SKILL_NAME && !requested.endsWith(`:${TASTE_SKILL_NAME}`)) return [];
    return [{ id: String(block.id || "").trim(), requested }];
  });
}

function nativeTasteSkillResultsFromSdkMessage(message, pendingIds) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "user") return [];
  const content = Array.isArray(record.message?.content) ? record.message.content : [];
  return content.flatMap((block) => {
    if (!block || block.type !== "tool_result") return [];
    const id = String(block.tool_use_id || "").trim();
    if (!id || !pendingIds.has(id)) return [];
    const rendered = typeof block.content === "string"
      ? block.content
      : Array.isArray(block.content)
        ? block.content.map((item) => String(item?.text || "")).join("\n")
        : "";
    const failed = block.is_error === true || /<tool_use_error>|no such tool available/i.test(rendered);
    return [{ id, success: !failed, error: failed ? compactText(rendered, 160) : "" }];
  });
}

function completedReadToolResultsFromSdkMessage(message, pendingReads) {
  const record = message && typeof message === "object" ? message : null;
  if (!record || record.type !== "user") return [];
  const content = Array.isArray(record.message?.content) ? record.message.content : [];
  return content.flatMap((block) => {
    if (!block || block.type !== "tool_result") return [];
    const id = String(block.tool_use_id || "").trim();
    const filePath = pendingReads.get(id);
    if (!id || !filePath) return [];
    const rendered = typeof block.content === "string"
      ? block.content
      : Array.isArray(block.content)
        ? block.content.map((item) => String(item?.text || "")).join("\n")
        : "";
    const failed = block.is_error === true || /<tool_use_error>|no such tool available/i.test(rendered);
    return [{ id, filePath, success: !failed }];
  });
}

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
const TASTE_PREFLIGHT_MAX_CALLS = 1;
const TASTE_PREFLIGHT_CHROMIUM = "/usr/bin/chromium";
const TASTE_PREFLIGHT_PREVIEW_TIMEOUT_MS = 20_000;
const TASTE_PREFLIGHT_CHROMIUM_TIMEOUT_MS = 45_000;
const TASTE_PREFLIGHT_VIEWPORTS = Object.freeze([
  Object.freeze({ name: "desktop", width: 1440, height: 900 }),
  Object.freeze({ name: "mobile", width: 390, height: 844 }),
  Object.freeze({ name: "hero-1280", width: 1280, height: 800 }),
]);
const TASTE_OFFICIAL_GATE_IDS = Object.freeze([
  "zero_visible_dashes",
  "canonical_preflight_evidence",
  "section_layout_diversity",
  "image_plan_and_asset_integrity",
  "hero_first_viewport",
  "single_visual_system",
]);
const TASTE_PUBLICATION_GATE_PATH = path.join(
  RUNTIME_ROOT,
  "plugins",
  "takyon",
  "taste_publication_gate.py"
);

function tastePreflightDir(cwd) {
  return path.join(path.resolve(cwd), ".takyon-preflight");
}

async function loadTastePublicationContract(sourcePath = TASTE_PUBLICATION_GATE_PATH) {
  const source = await fs.readFile(sourcePath, "utf8");
  const probeMatch = source.match(/TASTE_RENDER_INSPECTION_JS\s*=\s*r?"""([\s\S]*?)"""/);
  if (!probeMatch?.[1]) {
    throw new Error("Taste publication gate does not expose TASTE_RENDER_INSPECTION_JS");
  }
  const idsMatch = source.match(/CANONICAL_PREFLIGHT_IDS\s*=\s*\(([\s\S]*?)\n\)/);
  if (!idsMatch?.[1]) {
    throw new Error("Taste publication gate does not expose CANONICAL_PREFLIGHT_IDS");
  }
  const canonicalPreflightIds = [...idsMatch[1].matchAll(/["']([a-z0-9_]+)["']/g)]
    .map((match) => match[1]);
  if (canonicalPreflightIds.length < 1 || new Set(canonicalPreflightIds).size !== canonicalPreflightIds.length) {
    throw new Error("Taste publication gate canonical preflight IDs are empty or duplicated");
  }
  return {
    inspectionExpression: probeMatch[1],
    inspectionSha256: sha256(probeMatch[1]),
    canonicalPreflightIds,
    sourcePath,
  };
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

async function pngEvidence(filePath, expectedWidth = null, expectedHeight = null) {
  const header = await fs.readFile(filePath);
  const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (header.length < 24 || !header.subarray(0, 8).equals(pngSignature)) {
    throw new Error(`Chromium did not create a valid PNG at ${filePath}`);
  }
  const width = header.readUInt32BE(16);
  const height = header.readUInt32BE(20);
  if (
    expectedWidth !== null
    && expectedHeight !== null
    && (width !== expectedWidth || height !== expectedHeight)
  ) {
    throw new Error(
      `Chromium rendered ${width}x${height} at ${filePath}; expected ` +
      `${expectedWidth}x${expectedHeight}`
    );
  }
  return {
    width,
    height,
    sha256: sha256(header),
    bytes: header.length,
  };
}

class CdpPipeClient {
  constructor(child, timeoutMs) {
    this.child = child;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
    this.events = [];
    this.waiters = [];
    this.buffer = Buffer.alloc(0);
    const responsePipe = child.stdio[4];
    responsePipe.on("data", (chunk) => this.accept(chunk));
    responsePipe.on("error", (error) => this.rejectAll(error));
    child.once("error", (error) => this.rejectAll(error));
    child.once("close", (code, signal) => {
      this.rejectAll(new Error(`Chromium CDP pipe closed (${code ?? signal ?? "unknown"})`));
    });
  }

  accept(chunk) {
    this.buffer = Buffer.concat([this.buffer, Buffer.from(chunk)]);
    while (true) {
      const separator = this.buffer.indexOf(0);
      if (separator < 0) return;
      const payload = this.buffer.subarray(0, separator).toString("utf8");
      this.buffer = this.buffer.subarray(separator + 1);
      if (!payload.trim()) continue;
      let message;
      try {
        message = JSON.parse(payload);
      } catch (error) {
        this.rejectAll(new Error(`Chromium CDP returned invalid JSON: ${error?.message || error}`));
        continue;
      }
      if (Number.isInteger(message.id)) {
        const pending = this.pending.get(message.id);
        if (!pending) continue;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) {
          pending.reject(new Error(`CDP ${pending.method} failed: ${JSON.stringify(message.error)}`));
        } else {
          pending.resolve(message.result || {});
        }
        continue;
      }
      this.events.push(message);
      for (const waiter of [...this.waiters]) {
        if (!waiter.matches(message)) continue;
        this.waiters.splice(this.waiters.indexOf(waiter), 1);
        clearTimeout(waiter.timer);
        waiter.resolve(message.params || {});
      }
    }
  }

  rejectAll(error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
    for (const waiter of this.waiters) {
      clearTimeout(waiter.timer);
      waiter.reject(error);
    }
    this.waiters.length = 0;
  }

  send(method, params = {}, sessionId = "") {
    const id = this.nextId++;
    const message = { id, method, params, ...(sessionId ? { sessionId } : {}) };
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`CDP ${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.pending.set(id, { method, resolve, reject, timer });
      this.child.stdio[3].write(`${JSON.stringify(message)}\0`, "utf8", (error) => {
        if (!error) return;
        clearTimeout(timer);
        this.pending.delete(id);
        reject(error);
      });
    });
  }

  waitFor(method, sessionId = "") {
    const bufferedIndex = this.events.findIndex(
      (event) => event.method === method && (!sessionId || event.sessionId === sessionId)
    );
    if (bufferedIndex >= 0) {
      const [event] = this.events.splice(bufferedIndex, 1);
      return Promise.resolve(event.params || {});
    }
    return new Promise((resolve, reject) => {
      const waiter = {
        matches: (event) => event.method === method && (!sessionId || event.sessionId === sessionId),
        resolve,
        reject,
        timer: null,
      };
      waiter.timer = setTimeout(() => {
        this.waiters.splice(this.waiters.indexOf(waiter), 1);
        reject(new Error(`CDP event ${method} timed out after ${this.timeoutMs}ms`));
      }, this.timeoutMs);
      this.waiters.push(waiter);
    });
  }
}

async function captureTasteViewport({
  cwd,
  url,
  viewport,
  outputPath,
  profileDir,
  inspectionExpression,
  chromiumPath = TASTE_PREFLIGHT_CHROMIUM,
  chromiumEnv = null,
}) {
  const child = spawn(
    chromiumPath,
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
      "--remote-debugging-pipe",
      `--user-data-dir=${profileDir}`,
      `--window-size=${viewport.width},${viewport.height}`,
      "about:blank",
    ],
    {
      cwd,
      // Chromium renders product-controlled code; it gets no SDK/broker capability environment.
      env: chromiumEnv || preflightChildEnv(),
      detached: true,
      // Chromium's --remote-debugging-pipe contract reads JSON+NUL on fd 3 and writes it on fd 4.
      stdio: ["ignore", "pipe", "pipe", "pipe", "pipe"],
    }
  );
  const readStdout = collectBoundedOutput(child.stdout);
  const readStderr = collectBoundedOutput(child.stderr);
  const client = new CdpPipeClient(child, TASTE_PREFLIGHT_CHROMIUM_TIMEOUT_MS);
  try {
    const { targetId } = await client.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await client.send("Target.attachToTarget", { targetId, flatten: true });
    await client.send("Page.enable", {}, sessionId);
    await client.send("Runtime.enable", {}, sessionId);
    await client.send(
      "Emulation.setDeviceMetricsOverride",
      {
        width: viewport.width,
        height: viewport.height,
        deviceScaleFactor: 1,
        mobile: viewport.width <= 480,
        screenWidth: viewport.width,
        screenHeight: viewport.height,
      },
      sessionId
    );
    const loaded = client.waitFor("Page.loadEventFired", sessionId);
    const navigation = await client.send("Page.navigate", { url }, sessionId);
    if (navigation.errorText) {
      throw new Error(`Chromium could not navigate to the Taste preview: ${navigation.errorText}`);
    }
    await loaded;
    await client.send(
      "Runtime.evaluate",
      {
        expression:
          "Promise.all([document.fonts?.ready, new Promise((resolve) => " +
          "requestAnimationFrame(() => requestAnimationFrame(resolve)))])",
        awaitPromise: true,
        returnByValue: true,
      },
      sessionId
    );
    const evaluated = await client.send(
      "Runtime.evaluate",
      {
        expression: `(${inspectionExpression})()`,
        awaitPromise: true,
        returnByValue: true,
      },
      sessionId
    );
    if (evaluated.exceptionDetails || !evaluated.result || evaluated.result.type !== "object") {
      throw new Error(
        `Taste browser inspection failed: ${JSON.stringify(evaluated.exceptionDetails || evaluated.result || {})}`
      );
    }
    const probe = evaluated.result.value;
    if (!probe || typeof probe !== "object") {
      throw new Error("Taste browser inspection returned no facts");
    }
    const screenshot = await client.send(
      "Page.captureScreenshot",
      { format: "png", fromSurface: true, captureBeyondViewport: false },
      sessionId
    );
    const png = Buffer.from(String(screenshot.data || ""), "base64");
    if (!png.length) throw new Error("Chromium CDP returned an empty screenshot");
    await fs.writeFile(outputPath, png, { mode: 0o600 });
    const evidence = await pngEvidence(outputPath, viewport.width, viewport.height);
    return { ...evidence, probe };
  } catch (error) {
    const detail = compactText(`${readStdout()}\n${readStderr()}`, 1600);
    throw new Error(`${error?.message || error}${detail ? `; Chromium: ${detail}` : ""}`);
  } finally {
    client.rejectAll(new Error("Chromium CDP session closed"));
    await stopProcessTree(child);
  }
}

async function renderTasteLandingPreflight(
  cwd,
  { publicationContract = null, chromiumPath = TASTE_PREFLIGHT_CHROMIUM } = {}
) {
  const distIndex = path.join(cwd, "dist", "index.html");
  const preflightDir = tastePreflightDir(cwd);
  try {
    await fs.access(distIndex);
    await fs.access(chromiumPath);
  } catch (error) {
    if (error?.path === distIndex) {
      throw new Error("Taste render preflight requires a built dist/index.html; run npm run build first");
    }
    throw new Error(`Taste render preflight requires Chromium at ${chromiumPath}`);
  }
  const contract = publicationContract || await loadTastePublicationContract();

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
      const evidence = await captureTasteViewport({
        cwd,
        url: preview.url,
        viewport,
        outputPath,
        profileDir,
        inspectionExpression: contract.inspectionExpression,
        chromiumPath,
      });
      screenshots.push({
        name: viewport.name,
        path: outputPath,
        screenshot_path: outputPath,
        screenshot_sha256: evidence.sha256,
        bytes: evidence.bytes,
        width: evidence.width,
        height: evidence.height,
        inspected: true,
        probe: evidence.probe,
      });
    }
    return {
      success: true,
      route: "/",
      screenshots,
      inspection_contract: {
        source_path: contract.sourcePath,
        constant: "TASTE_RENDER_INSPECTION_JS",
        sha256: contract.inspectionSha256,
      },
      instruction:
        "Read all three returned PNG paths with the Read tool and inspect the actual first viewports.",
    };
  } finally {
    await stopProcessTree(preview?.child);
    await fs.rm(profileRoot, { recursive: true, force: true });
  }
}

function createTastePublicationState(cwd, publicationContract) {
  return {
    cwd: path.resolve(cwd),
    publicationContract,
    generatedAssets: new Map(),
    renderEvidence: null,
    requiredReadPaths: new Set(),
    authorizedReadPaths: new Set(),
    completedReadPaths: new Set(),
    readAuthorizations: [],
    audit: null,
  };
}

function registerTasteRequiredRead(state, filePath) {
  const resolved = path.resolve(filePath);
  if (!isSubpath(state.cwd, resolved)) {
    throw new Error(`Taste evidence path escapes the product workspace: ${resolved}`);
  }
  state.requiredReadPaths.add(resolved);
  return resolved;
}

async function refreshTasteGeneratedAssets(state) {
  const receiptsDir = path.join(state.cwd, ".takyon", "site-images");
  let entries = [];
  try {
    entries = await fs.readdir(receiptsDir, { withFileTypes: true });
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith(".json")) continue;
    let receipt;
    try {
      receipt = JSON.parse(await fs.readFile(path.join(receiptsDir, entry.name), "utf8"));
    } catch (error) {
      throw new Error(`generated-image receipt is invalid (${entry.name}): ${error?.message || error}`);
    }
    const publicPath = String(receipt?.public_path || "").trim();
    if (!receipt?.success || !/^\/generated\/[a-z0-9]+(?:-[a-z0-9]+)*\.png$/.test(publicPath)) {
      throw new Error(`generated-image receipt is unsuccessful or malformed: ${entry.name}`);
    }
    const imagePath = path.join(state.cwd, "public", publicPath.slice(1));
    const evidence = await pngEvidence(imagePath);
    state.generatedAssets.set(publicPath, {
      public_path: publicPath,
      path: registerTasteRequiredRead(state, imagePath),
      image_sha256: evidence.sha256,
      width: evidence.width,
      height: evidence.height,
      bytes: evidence.bytes,
    });
  }
  return [...state.generatedAssets.values()].sort((left, right) =>
    left.public_path.localeCompare(right.public_path)
  );
}

function evidenceArrayToMap(items, expectedIds, label) {
  const result = {};
  for (const item of Array.isArray(items) ? items : []) {
    const id = String(item?.id || "").trim();
    if (!id || Object.hasOwn(result, id)) {
      throw new Error(`${label} contains a missing or duplicate id`);
    }
    result[id] = {
      passed: item?.passed === true,
      evidence: String(item?.evidence || "").trim(),
      source: String(item?.source || "").trim(),
    };
  }
  const expected = new Set(expectedIds);
  const missing = expectedIds.filter((id) => !Object.hasOwn(result, id));
  const extra = Object.keys(result).filter((id) => !expected.has(id));
  if (missing.length || extra.length) {
    throw new Error(
      `${label} is incomplete (missing: ${missing.join(", ") || "none"}; ` +
      `unexpected: ${extra.join(", ") || "none"})`
    );
  }
  for (const [id, item] of Object.entries(result)) {
    if (!item.passed) throw new Error(`${label} reports a failed gate: ${id}`);
    if (!item.evidence || !item.source) {
      throw new Error(`${label} lacks code/copy evidence for ${id}`);
    }
  }
  return result;
}

function rgbHue(value) {
  const match = String(value || "").match(/^rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$/i);
  if (!match) return null;
  const channels = match.slice(1).map((channel) => Number(channel) / 255);
  const high = Math.max(...channels);
  const low = Math.min(...channels);
  if (high === low) return null;
  const delta = high - low;
  let hue;
  if (high === channels[0]) hue = ((channels[1] - channels[2]) / delta) % 6;
  else if (high === channels[1]) hue = (channels[2] - channels[0]) / delta + 2;
  else hue = (channels[0] - channels[1]) / delta + 4;
  return ((hue * 60) + 360) % 360;
}

function hueClusterCount(colors, tolerance = 28) {
  const clusters = [];
  for (const color of colors) {
    const hue = rgbHue(color);
    if (hue === null) continue;
    if (clusters.some((center) => Math.min(Math.abs(hue - center), 360 - Math.abs(hue - center)) <= tolerance)) {
      continue;
    }
    clusters.push(hue);
  }
  return clusters.length;
}

function officialGateFailures(renderEvidence, designSource = "") {
  const byName = Object.fromEntries(
    (renderEvidence?.screenshots || []).map((entry) => [String(entry.name || ""), entry])
  );
  const desktop = byName.desktop?.probe || {};
  const hero = byName["hero-1280"]?.probe || {};
  const failures = [];
  const allBodyText = Object.values(byName).map((entry) => String(entry?.probe?.body_text || "")).join("\n");
  if (/[—–]/.test(allBodyText)) failures.push("zero_visible_dashes");

  const families = (desktop.section_layouts || []).map((entry) => String(entry?.family || "")).filter(Boolean);
  if (new Set(families).size < Math.min(families.length, 4)) {
    failures.push("section_layout_diversity");
  }

  const sectionSources = (desktop.section_layouts || [])
    .flatMap((entry) => Array.isArray(entry?.image_srcs) ? entry.image_srcs : [])
    .map((source) => String(source || ""));
  const usesUnsplash = sectionSources.some((source) => /(?:^|\.)unsplash\.com\//i.test(source));
  const unsplashExplicitlyAllowed = /\bunsplash\b.{0,80}\b(?:explicitly\s+)?(?:allowed|approved|provided)\b/i
    .test(designSource);
  if (
    (usesUnsplash && !unsplashExplicitlyAllowed)
    || new Set(sectionSources).size !== sectionSources.length
  ) {
    failures.push("image_plan_and_asset_integrity");
  }

  const heroWords = String(hero.hero_subtext || "").trim().match(/\b[\w'-]+\b/g) || [];
  if (
    Number(hero.viewport_width || 0) !== 1280
    || Number(hero.viewport_height || 0) !== 800
    || Number(hero.h1_line_count || 0) > 2
    || heroWords.length > 20
    || hero.primary_cta_visible !== true
  ) {
    failures.push("hero_first_viewport");
  }

  const radii = desktop.shape_radii && typeof desktop.shape_radii === "object"
    ? Object.values(desktop.shape_radii)
    : [];
  if (
    (
      new Set(desktop.theme_modes || []).size > 1
      && !/\b(?:theme switch|color block story)\b/i.test(designSource)
    )
    || hueClusterCount(desktop.accent_colors || []) > 1
    || (
      radii.some((values) => Array.isArray(values) && new Set(values).size > 1)
      && !/\b(?:radius|corner)\s+(?:rule|system)\b/i.test(designSource)
    )
  ) {
    failures.push("single_visual_system");
  }
  return [...new Set(failures)];
}

async function submitTastePublicationAudit(state, args) {
  if (!state.renderEvidence?.success) {
    throw new Error("Taste publication audit requires a successful rendered preflight");
  }
  const assets = await refreshTasteGeneratedAssets(state);
  if (assets.length < 2 || new Set(assets.map((asset) => asset.image_sha256)).size !== assets.length) {
    throw new Error("Taste publication audit requires at least two distinct generated landing assets");
  }
  const missingReads = [...state.requiredReadPaths].filter(
    (filePath) => !state.completedReadPaths.has(filePath)
  );
  if (missingReads.length) {
    throw new Error(`Taste publication audit requires successful Read evidence for: ${missingReads.join(", ")}`);
  }
  const officialGates = evidenceArrayToMap(
    args?.official_gates,
    TASTE_OFFICIAL_GATE_IDS,
    "official Taste gates"
  );
  const preflightEvidence = evidenceArrayToMap(
    args?.preflight_evidence,
    state.publicationContract.canonicalPreflightIds,
    "canonical Taste preflight"
  );
  let designSource = "";
  try {
    designSource = await fs.readFile(path.join(state.cwd, "DESIGN.md"), "utf8");
  } catch {
    // The Python publication gate owns the durable DESIGN.md requirement and produces the blocker.
  }
  const mechanicalFailures = officialGateFailures(state.renderEvidence, designSource);
  if (mechanicalFailures.length) {
    throw new Error(`trusted browser facts fail official Taste gates: ${mechanicalFailures.join(", ")}`);
  }
  const renderedImagePaths = new Set(
    (state.renderEvidence.screenshots || []).flatMap((render) =>
      Array.isArray(render?.probe?.image_srcs) ? render.probe.image_srcs : []
    ).map((source) => {
      try {
        return new URL(String(source || ""), "http://127.0.0.1").pathname;
      } catch {
        return String(source || "").split("?")[0];
      }
    })
  );
  const unusedAssets = assets.filter((asset) => !renderedImagePaths.has(asset.public_path));
  if (unusedAssets.length) {
    throw new Error(
      `Taste publication audit found generated assets absent from the render: ` +
      unusedAssets.map((asset) => asset.public_path).join(", ")
    );
  }

  const submittedAssets = new Map();
  for (const item of Array.isArray(args?.asset_inspections) ? args.asset_inspections : []) {
    const publicPath = String(item?.public_path || "").trim();
    if (!publicPath || submittedAssets.has(publicPath)) {
      throw new Error("asset inspections contain a missing or duplicate public_path");
    }
    submittedAssets.set(publicPath, item);
  }
  const missingAssets = assets.filter((asset) => !submittedAssets.has(asset.public_path));
  const extraAssets = [...submittedAssets.keys()].filter(
    (publicPath) => !state.generatedAssets.has(publicPath)
  );
  if (missingAssets.length || extraAssets.length) {
    throw new Error(
      `asset visual inspections are incomplete (missing: ` +
      `${missingAssets.map((asset) => asset.public_path).join(", ") || "none"}; ` +
      `unexpected: ${extraAssets.join(", ") || "none"})`
    );
  }
  const assetInspections = {};
  for (const asset of assets) {
    const submitted = submittedAssets.get(asset.public_path);
    if (
      String(submitted.image_sha256 || "") !== asset.image_sha256
      || Number(submitted.inspected_width || 0) !== asset.width
      || Number(submitted.inspected_height || 0) !== asset.height
    ) {
      throw new Error(`asset visual inspection is stale or not full-resolution: ${asset.public_path}`);
    }
    const detectedText = (submitted.detected_text || []).map((value) => String(value || "").trim()).filter(Boolean);
    const fakeUiDetected = submitted.fake_ui_detected === true;
    if (detectedText.length || fakeUiDetected) {
      throw new Error(`asset visual inspection reports a text artifact or fake UI: ${asset.public_path}`);
    }
    const source = String(submitted.source || "").trim();
    if (!source || !source.includes(asset.path)) {
      throw new Error(`asset visual inspection lacks its exact successful Read path: ${asset.public_path}`);
    }
    assetInspections[asset.public_path] = {
      public_path: asset.public_path,
      image_sha256: asset.image_sha256,
      inspected: true,
      inspected_width: asset.width,
      inspected_height: asset.height,
      detected_text: [],
      fake_ui_detected: false,
      artifact_labels: (submitted.artifact_labels || []).map((value) => String(value || "").trim()).filter(Boolean),
      source,
    };
  }

  const renders = Object.fromEntries(
    state.renderEvidence.screenshots.map((entry) => [entry.name, {
      width: entry.width,
      height: entry.height,
      screenshot_path: entry.screenshot_path,
      screenshot_sha256: entry.screenshot_sha256,
      inspected: true,
      probe: entry.probe,
    }])
  );
  state.audit = {
    version: 1,
    submitted: true,
    passed: true,
    inspection_contract: state.renderEvidence.inspection_contract,
    official_gates: officialGates,
    preflight_evidence: preflightEvidence,
    render_inspections: {
      desktop: renders.desktop,
      mobile: renders.mobile,
      hero_1280: renders["hero-1280"],
    },
    asset_inspections: assetInspections,
    read_evidence: {
      required_paths: [...state.requiredReadPaths].sort(),
      authorized_paths: [...state.authorizedReadPaths].sort(),
      completed_paths: [...state.completedReadPaths].sort(),
      authorizations: [...state.readAuthorizations],
    },
  };
  return state.audit;
}

function tastePublicationEvidenceFromState(state) {
  if (!state) return null;
  if (state.audit) return state.audit;
  const renders = Object.fromEntries(
    (state.renderEvidence?.screenshots || []).map((entry) => [entry.name, {
      width: entry.width,
      height: entry.height,
      screenshot_path: entry.screenshot_path,
      screenshot_sha256: entry.screenshot_sha256,
      inspected: entry.inspected === true,
      probe: entry.probe,
    }])
  );
  return {
    version: 1,
    submitted: false,
    passed: false,
    inspection_contract: state.renderEvidence?.inspection_contract || null,
    render_inspections: {
      desktop: renders.desktop || null,
      mobile: renders.mobile || null,
      hero_1280: renders["hero-1280"] || null,
    },
    asset_inspections: {},
    preflight_evidence: {},
    official_gates: {},
    read_evidence: {
      required_paths: [...state.requiredReadPaths].sort(),
      authorized_paths: [...state.authorizedReadPaths].sort(),
      completed_paths: [...state.completedReadPaths].sort(),
      authorizations: [...state.readAuthorizations],
    },
  };
}

function createSiteImageMcpServer({
  createSdkMcpServer,
  tool,
  z,
  bridgeDir,
  cwd,
  publicationState = null,
  publicationContract = null,
}) {
  if (!bridgeDir) return null;
  const state = publicationState || createTastePublicationState(cwd, publicationContract);
  return createSdkMcpServer({
    name: "takyon_site_image",
    version: "1.0.0",
    alwaysLoad: true,
    instructions:
      "A Safebox-gated, cost-capped image generator is available as an optional creative tool. " +
      "Let the native Taste skill decide whether original imagery improves this business and, if so, " +
      "choose the art direction, prompt, purpose, aspect ratio, and placement. Do not add filler imagery.",
    tools: [
      tool(
        "business_generate_site_image",
        "Generate one real, business-owned landing image through Takyon's Safebox-gated creative rail. " +
          "The native Taste skill chooses the prompt, purpose, aspect ratio, and page role. Returns a " +
          "local /generated/... path.",
        {
          slug: z.string().regex(/^[a-z0-9]+(?:-[a-z0-9]+)*$/).max(80),
          prompt: z.string().min(1).max(12000),
          aspect_ratio: z.enum(["16:9", "4:3", "1:1", "3:4", "9:16"]),
          purpose: z.string().min(1).max(200),
        },
        async (args) => {
          try {
            const result = await invokeSiteImageBridge(bridgeDir, args);
            const publicPath = String(result?.public_path || "").trim();
            if (!/^\/generated\/[a-z0-9]+(?:-[a-z0-9]+)*\.png$/.test(publicPath)) {
              throw new Error("site-image bridge returned an invalid public_path");
            }
            const imagePath = path.join(state.cwd, "public", publicPath.slice(1));
            const evidence = await pngEvidence(imagePath);
            const asset = {
              public_path: publicPath,
              path: imagePath,
              image_sha256: evidence.sha256,
              width: evidence.width,
              height: evidence.height,
              bytes: evidence.bytes,
            };
            state.generatedAssets.set(publicPath, asset);
            return {
              content: [{
                type: "text",
                text: JSON.stringify({
                  ...result,
                  read_path: asset.path,
                  image_sha256: asset.image_sha256,
                  width: asset.width,
                  height: asset.height,
                  instruction: "Use the generated image only if it improves the product design.",
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
  claudeConfigDir,
  inDockerWorker,
  cwd,
  failOnApiRetry,
}) {
  const env = {
    PATH: String(process.env.PATH || SANDBOX_PATH).trim() || SANDBOX_PATH,
    HOME: String(process.env.HOME || "/tmp").trim() || "/tmp",
    ANTHROPIC_API_KEY: anthropicApiKey,
    ANTHROPIC_TOKEN: anthropicToken,
    CLAUDE_AGENT_SDK_CLIENT_APP: "takyon-business-agent",
    CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: disableExperimentalBetas,
    // Claude Code otherwise performs its own retry loop before the Agent SDK can emit and let the
    // parent reject an api_retry frame.  Zero retries makes the first retryable response terminal,
    // so Takyon can fail the worker after exactly one provider request.
    ...(failOnApiRetry ? { CLAUDE_CODE_MAX_RETRIES: "0" } : {}),
    ...(failOnApiRetry
      ? { ANTHROPIC_CUSTOM_HEADERS: "x-takyon-fail-on-api-retry: 1" }
      : {}),
    ...(claudeConfigDir ? { CLAUDE_CONFIG_DIR: claudeConfigDir } : {}),
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

function failFastApiFailureFromTerminalError(error) {
  const message = String(error?.message || error || "");
  const match = message.match(/\bAPI Error:\s*(408|409|429|5\d\d)\b/i);
  if (!match) return "";
  const status = Number.parseInt(match[1], 10);
  const reason = status === 408
    ? "request_timeout"
    : status === 409
      ? "request_conflict"
      : status === 429
        ? "rate_limit_error"
        : status === 529
          ? "overloaded_error"
          : "server_error";
  return compactText(
    `Claude API retry refused by fail-fast policy on attempt 1/1: ${reason} (HTTP ${status})`,
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
  const tasteRule = isProductSiteWorkspace(normalizedWorkspace)
    ? "The runtime requests the native design-taste-frontend skill for this session. Use it when available. Installation or Skill-tool telemetry is advisory: if Taste is unavailable or its invocation fails, continue the product task with your best design judgment and do not report BLOCKED solely because of Taste."
    : "";
  const buildGate = input.allowBash
    ? "Customer-facing product build gate (HARD): before you finish you MUST run `npm run build` and `npm run typecheck` and confirm BOTH exit green. Diagnosing the error is not done; only a green build is done. If you cannot land both green this pass, do NOT report success — your FINAL line MUST start with BLOCKED: followed by the exact remaining build/typecheck error and the file(s) involved."
    : "";
  return [
    "You are a Claude Agent SDK worker called by Takyon for one bounded business-scoped task.",
    "",
    `You may inspect and edit files only inside the provided current workspace. ${bashRule}`,
    "The provided current workspace is already your working directory. Write paths relative to it; do not prefix paths with the workspace name again.",
    "",
    `Complete the requested task while preserving unrelated business files. ${noteRule}`,
    ...(tasteRule ? [tasteRule] : []),
    "",
    "Inspect the relevant files, reason about the task, and use the available tools as needed. Keep private reasoning private; expose only concise tool progress and the final result.",
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
  workerSkillStartedAt = Date.now();
  workerTastePublicationState = null;
  workerStderr = "";
  workerModel = null;
  workerUsage = null;
  workerTotalCostUsd = null;
  const raw = await readStdin();
  const input = JSON.parse(raw || "{}");
  const tasteSkillRequired = isTasteSkillApplicable(input);
  workerSkillReceipt = {
    name: TASTE_SKILL_NAME,
    installed: false,
    expected_sha256: TASTE_SKILL_SHA256,
    required: tasteSkillRequired,
    requested: true,
    discovered: false,
    discovery_event: false,
    included: null,
    inclusion_event: false,
    native_use: false,
    native_use_attempts: 0,
    native_use_events: 0,
    advisory: false,
    advisories: [],
    model: null,
    usage: null,
  };
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
  workerSkillReceipt.model = model;
  workerSkillReceipt.requested_model = model;
  workerModel = model;
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
  const configuredClaudeConfigDir = path.resolve(String(
    input.claudeConfigDir || process.env.CLAUDE_CONFIG_DIR || CLAUDE_CONFIG_DIR
  ));
  const nativeTasteSkill = await validateNativeTasteSkill({
    nativeDir: path.join(configuredClaudeConfigDir, "skills", TASTE_SKILL_NAME),
    canonicalDir: TASTE_SKILL_CANONICAL_DIR,
  });
  Object.assign(workerSkillReceipt, nativeTasteSkill.receipt);
  const requestedSkills = nativeTasteSkill.receipt.installed
    ? [TASTE_SKILL_NAME]
    : [];
  for (const advisory of nativeTasteSkill.receipt.advisories || []) {
    emitProgress({
      kind: "claude_agent_skill",
      status: "output",
      detail: `Native Taste advisory: ${String(advisory?.detail || advisory?.code || "unverified")}`,
    });
  }
  if (workerSkillReceipt.installed) {
    emitProgress({
      kind: "claude_agent_skill",
      status: "output",
      detail: `Installed canonical ${TASTE_SKILL_NAME} as a native Claude Code skill.`,
    });
  }
  const publicationContract = null;
  workerTastePublicationState = tasteSkillRequired
    ? createTastePublicationState(cwd, publicationContract)
    : null;
  const siteImageMcpServer = tasteSkillRequired
    ? createSiteImageMcpServer({
        createSdkMcpServer,
        tool,
        z,
        bridgeDir: siteImageBridgeDir,
        cwd,
        publicationState: workerTastePublicationState,
        publicationContract,
      })
    : null;
  let text = "";
  let totalCostUsd = null;
  let finalUsage = null;
  const actualModels = new Set();
  const pendingTasteSkillUses = new Set();
  const pendingReadUses = new Map();
  const workerPrompt = buildPrompt(input);
  let canonicalTasteBody = "";
  try {
    canonicalTasteBody = await fs.readFile(
      path.join(TASTE_SKILL_CANONICAL_DIR, "SKILL.md"),
      "utf8"
    );
  } catch (error) {
    appendTasteSkillAdvisory(
      workerSkillReceipt.advisories,
      "digest_unconfirmed",
      `canonical native Taste prompt body is unavailable: ${error?.code || "unreadable"}`
    );
  }
  workerSkillReceipt.prompt_body_absent = canonicalTasteBody
    ? !workerPrompt.includes(canonicalTasteBody)
    : null;
  workerSkillReceipt.prompt_distinctive_markers_absent = TASTE_PROMPT_DISTINCTIVE_MARKERS.every(
    (marker) => !workerPrompt.includes(marker)
  );
  workerSkillReceipt.prompt_body_injected = false;
  workerSkillReceipt.prompt_sha256 = sha256(workerPrompt);
  workerSkillReceipt.prompt_bytes = Buffer.byteLength(workerPrompt, "utf8");
  let parentAbortRequested = false;
  const requestParentAbort = () => {
    parentAbortRequested = true;
    abortController.abort();
  };
  process.once("SIGTERM", requestParentAbort);
  process.once("SIGINT", requestParentAbort);
  const captureSkillInclusion = async (sdkQuery) => {
    try {
      const contextUsage = await sdkQuery.getContextUsage();
      const skillUsage = contextUsage && typeof contextUsage === "object" ? contextUsage.skills : null;
      workerSkillReceipt.inclusion_event = true;
      if (!skillUsage || typeof skillUsage !== "object") {
        workerSkillReceipt.included = false;
        workerSkillReceipt.inclusion_error = "SDK context usage omitted skills";
        return;
      }
      const frontmatter = Array.isArray(skillUsage.skillFrontmatter)
        ? skillUsage.skillFrontmatter
        : [];
      const matched = frontmatter.find((item) => {
        const name = String(item?.name || "").trim();
        return name === TASTE_SKILL_NAME || name.endsWith(`:${TASTE_SKILL_NAME}`);
      });
      const matchedSource = matched ? String(matched.source || "") : "";
      workerSkillReceipt.included = Boolean(matched && matchedSource === "userSettings");
      workerSkillReceipt.included_skills = frontmatter.map((item) => ({
        name: String(item?.name || ""),
        source: String(item?.source || ""),
        tokens: Number(item?.tokens || 0),
      }));
      workerSkillReceipt.included_skill_count = Number(skillUsage.includedSkills || 0);
      workerSkillReceipt.total_skills = Number(skillUsage.totalSkills || 0);
      workerSkillReceipt.included_source = matchedSource || null;
      workerSkillReceipt.included_tokens = matched ? Number(matched.tokens || 0) : 0;
      if (matched && matchedSource !== "userSettings") {
        workerSkillReceipt.inclusion_error =
          `native Taste resolved from forbidden source ${matchedSource || "unknown"}`;
      }
    } catch (error) {
      workerSkillReceipt.inclusion_event = false;
      workerSkillReceipt.included = null;
      workerSkillReceipt.inclusion_error = compactText(error?.message || String(error), 160);
    }
  };
  try {
    const sdkQuery = query({
          prompt: workerPrompt,
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
              claudeConfigDir: nativeTasteSkill.configDir,
              inDockerWorker,
              cwd,
              failOnApiRetry,
            }),
            model,
            // Supplying a title disables Claude Code's separate paid title-generation request.
            title: `Takyon ${String(input.business || "business")} worker`,
            skills: requestedSkills,
            settingSources: ["user"],
            includePartialMessages: true,
            thinking: { type: "adaptive", display: "summarized" },
            effort: ["low", "medium", "high"].includes(effort) ? effort : "high",
            tools: [
              "Read", "Write", "Edit", "MultiEdit", "Grep", "Glob", "Skill",
              ...(allowBash ? ["Bash"] : []),
            ],
            ...(siteImageMcpServer
              ? {
                  allowedTools: [
                    "mcp__takyon_site_image__business_generate_site_image",
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
              if (toolName === "Read") {
                const readPathValue = pathValues(updatedInput)[0];
                if (readPathValue) {
                  const resolvedReadPath = path.resolve(cwd, readPathValue);
                  pendingReadUses.set(options.toolUseID, resolvedReadPath);
                  if (workerTastePublicationState?.requiredReadPaths.has(resolvedReadPath)) {
                    workerTastePublicationState.authorizedReadPaths.add(resolvedReadPath);
                    workerTastePublicationState.readAuthorizations.push({
                      tool_use_id: options.toolUseID,
                      path: resolvedReadPath,
                    });
                  }
                }
              }
              return { behavior: "allow", updatedInput, toolUseID: options.toolUseID };
            }
          }
    });
    for await (const message of sdkQuery) {
      emitProgress(progressEventFromSdkMessage(message));
      const reportedModel = String(message?.message?.model || message?.model || "").trim();
      if (reportedModel) actualModels.add(reportedModel);
      if (message && typeof message === "object" && message.type === "system" && message.subtype === "init") {
        const discoveredSkills = Array.isArray(message.skills)
          ? message.skills.map((name) => String(name || "").trim()).filter(Boolean)
          : [];
        workerSkillReceipt.discovery_event = true;
        workerSkillReceipt.discovered = discoveredSkills.includes(TASTE_SKILL_NAME);
        workerSkillReceipt.discovered_skills = discoveredSkills;
        workerSkillReceipt.taste_discovered_name = discoveredSkills.find(
          (name) => name === TASTE_SKILL_NAME || name.endsWith(`:${TASTE_SKILL_NAME}`)
        ) || null;
        if (workerSkillReceipt.discovered) {
          emitProgress({
            kind: "claude_agent_skill",
            status: "output",
            detail: `Claude Code discovered native skill ${TASTE_SKILL_NAME}.`,
          });
        }
      }
      const tasteToolUses = nativeTasteSkillToolUsesFromSdkMessage(message);
      if (tasteToolUses.length > 0) {
        for (const use of tasteToolUses) {
          if (use.id) pendingTasteSkillUses.add(use.id);
        }
        workerSkillReceipt.native_use_attempts += tasteToolUses.length;
      }
      const tasteToolResults = nativeTasteSkillResultsFromSdkMessage(message, pendingTasteSkillUses);
      if (tasteToolResults.length > 0) {
        for (const result of tasteToolResults) {
          pendingTasteSkillUses.delete(result.id);
          if (result.success) {
            workerSkillReceipt.native_use = true;
            workerSkillReceipt.native_use_events += 1;
          } else {
            workerSkillReceipt.native_use_error = result.error || "native Skill tool failed";
          }
        }
        if (workerSkillReceipt.native_use_events === 1) {
          emitProgress({
            kind: "claude_agent_skill",
            status: "output",
            detail: `Claude Code invoked native skill ${TASTE_SKILL_NAME}.`,
          });
        }
        if (workerSkillReceipt.native_use && !workerSkillReceipt.inclusion_event) {
          await captureSkillInclusion(sdkQuery);
        }
      }
      const completedReads = completedReadToolResultsFromSdkMessage(message, pendingReadUses);
      for (const result of completedReads) {
        pendingReadUses.delete(result.id);
        if (result.success && workerTastePublicationState?.requiredReadPaths.has(result.filePath)) {
          workerTastePublicationState.completedReadPaths.add(result.filePath);
        }
      }
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
          workerTotalCostUsd = totalCostUsd;
        }
        if (message.usage && typeof message.usage === "object") {
          finalUsage = message.usage;
          workerUsage = finalUsage;
        }
      }
    }
    if (parentAbortRequested) {
      throw new Error("Claude Agent SDK task cancelled by parent supervisor");
    }
  } catch (error) {
    const failFastFailure = failOnApiRetry
      ? failFastApiFailureFromTerminalError(error)
      : "";
    if (failFastFailure) throw new Error(failFastFailure);
    throw error;
  } finally {
    process.removeListener("SIGTERM", requestParentAbort);
    process.removeListener("SIGINT", requestParentAbort);
    // The parent removes verification scratch after collecting advisory telemetry. Deleting it here
    // would race that cleanup and can obscure diagnostics.
    if (workerSkillReceipt) {
      workerSkillReceipt.duration_ms = Math.max(0, Date.now() - workerSkillStartedAt);
      workerSkillReceipt.usage = finalUsage;
      workerSkillReceipt.actual_models = [...actualModels];
      workerSkillReceipt.actual_model = actualModels.size === 1 ? [...actualModels][0] : null;
      workerSkillReceipt.advisories = nativeTasteSkillReceiptAdvisories(workerSkillReceipt);
      workerSkillReceipt.advisory = workerSkillReceipt.advisories.length > 0;
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
    skill_receipt: workerSkillReceipt,
    taste_publication_evidence: tastePublicationEvidenceFromState(workerTastePublicationState),
    worker_stderr: redact(workerStderr).trim() || null,
  }));
}

export {
  apiRetryFailureFromSdkMessage,
  buildPrompt,
  captureTasteViewport,
  completedReadToolResultsFromSdkMessage,
  createTastePublicationState,
  createSiteImageMcpServer,
  loadTastePublicationContract,
  nativeTasteSkillReceiptAdvisories,
  nativeTasteSkillUseFromSdkMessage,
  progressEventFromSdkMessage,
  renderTasteLandingPreflight,
  submitTastePublicationAudit,
  tastePublicationEvidenceFromState,
  validateNativeTasteSkill,
};

const isMainModule = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) main().catch((error) => {
  if (workerSkillReceipt) {
    workerSkillReceipt.duration_ms = Math.max(0, Date.now() - workerSkillStartedAt);
    workerSkillReceipt.usage = workerUsage;
    workerSkillReceipt.model ??= workerModel;
    workerSkillReceipt.requested_model ??= workerModel;
  }
  emitProgress({
    kind: "claude_agent_sdk",
    status: "failed",
    detail: compactText(error?.message || String(error)),
    line: compactText(error?.message || String(error)),
  });
  process.stdout.write(JSON.stringify({
    success: false,
    source: "claude-agent-sdk",
    model: workerModel,
    actual_model: workerSkillReceipt?.actual_model || null,
    total_cost_usd: typeof workerTotalCostUsd === "number" ? workerTotalCostUsd : null,
    actual_cost_cents: typeof workerTotalCostUsd === "number"
      ? Math.max(0, Math.round(workerTotalCostUsd * 100))
      : null,
    usage: workerUsage,
    error: redact(error?.stack || error?.message || String(error)),
    skill_receipt: workerSkillReceipt,
    taste_publication_evidence: tastePublicationEvidenceFromState(workerTastePublicationState),
    worker_stderr: redact(workerStderr).trim() || null,
  }));
  process.exitCode = 1;
});
