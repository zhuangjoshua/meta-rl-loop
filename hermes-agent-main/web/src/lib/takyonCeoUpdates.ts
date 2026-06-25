const ANSI_PATTERN = new RegExp(
  `${String.fromCharCode(27)}(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])`,
  "g",
);

const MEDIA_EXTENSIONS = "mp4|mov|webm|m4v|png|jpg|jpeg|webp|gif";
const TEXT_EXTENSIONS = "ts|tsx|js|jsx|py|md|json|css|html|yml|yaml|toml|txt|sql";
const PATH_EXTENSIONS = `${TEXT_EXTENSIONS}|${MEDIA_EXTENSIONS}`;

export type WorkstreamKey =
  | "research"
  | "offer"
  | "build"
  | "publish"
  | "test"
  | "launch";

export type WorkstreamStatus = "pending" | "running" | "complete" | "blocked";

export type ProgressToolSignal = {
  name?: string;
  label?: string;
  context?: string;
  preview?: string;
  summary?: string;
  error?: string;
  status?: string;
};

export type WorkstreamItem = {
  key: WorkstreamKey;
  label: string;
  completeLabel: string;
  status: WorkstreamStatus;
};

export type LiveWorkstreamCardData = {
  title: string;
  summary: string;
  items: WorkstreamItem[];
  current?: string;
  next?: string;
  blocked?: string;
};

export type AssistantReceiptData = {
  title: string;
  summary: string;
  bullets: string[];
  liveUrl?: string;
  checks: string[];
  files: string[];
  next?: string;
  rawDetails: string;
};

type WorkstreamDefinition = {
  key: WorkstreamKey;
  label: string;
  completeLabel: string;
  summary: string;
};

const WORKSTREAMS: WorkstreamDefinition[] = [
  {
    key: "research",
    label: "Researching the market",
    completeLabel: "Research complete",
    summary: "I'm validating the market and choosing the first useful angle.",
  },
  {
    key: "offer",
    label: "Designing the offer",
    completeLabel: "Offer defined",
    summary: "I'm turning the research into a clear wedge and product direction.",
  },
  {
    key: "build",
    label: "Building the product",
    completeLabel: "Product workflow built",
    summary: "I'm building the first usable workflow and tightening the customer surface.",
  },
  {
    key: "publish",
    label: "Publishing the site",
    completeLabel: "Site published",
    summary: "I'm putting a live version online so you can review it.",
  },
  {
    key: "test",
    label: "Testing the workflow",
    completeLabel: "Workflow verified",
    summary: "I'm checking the live flow before I hand back the result.",
  },
  {
    key: "launch",
    label: "Preparing launch assets",
    completeLabel: "Launch assets prepared",
    summary: "I'm packaging the first distribution and launch materials around the workflow.",
  },
];

const WORKSTREAM_INDEX = new Map(
  WORKSTREAMS.map((item, index) => [item.key, index] as const),
);

function cleanText(text: string): string {
  return text.replace(ANSI_PATTERN, "").replace(/\r/g, "");
}

function workstreamDefinition(key: WorkstreamKey): WorkstreamDefinition {
  return WORKSTREAMS[WORKSTREAM_INDEX.get(key) ?? 0]!;
}

function detectWorkstreamKey(rawText: string): WorkstreamKey | null {
  const text = cleanText(rawText).toLowerCase();
  if (!text) return null;
  if (/launch|creative|asset|campaign|distribution|outreach|ad copy|launch test/.test(text)) {
    return "launch";
  }
  if (/typecheck|test|verify|validation|qa|probe|stable|loads|works end to end|works end-to-end/.test(text)) {
    return "test";
  }
  if (/publish|deploy|vercel|live url|go live|domain|route verified|site live/.test(text)) {
    return "publish";
  }
  if (/build|product|workflow|surface|screen|app-home|actions\/|claude_agent_task|claude worker|editing files|summary action/.test(text)) {
    return "build";
  }
  if (/offer|wedge|position|pricing|surface contract|product angle|ghostwriting assistant|plan simultaneously/.test(text)) {
    return "offer";
  }
  if (/research|market|competitor|customer|icp|audience|notes-to-post|market angle/.test(text)) {
    return "research";
  }
  return null;
}

function detectSignalStatus(text: string, fallback: WorkstreamStatus = "running"): WorkstreamStatus {
  const lower = cleanText(text).toLowerCase();
  if (
    /\b(blocked|error|failed|failure|needs attention|can't|cannot|isn't|not provisioned|unavailable)\b/.test(
      lower,
    )
  ) {
    return "blocked";
  }
  if (/\b(done|complete|completed|passed|succeeded|verified|ready|live)\b/.test(lower)) {
    return "complete";
  }
  return fallback;
}

function detectToolWorkstreamStatus(tool: ProgressToolSignal): WorkstreamStatus {
  if (tool.status === "error") return "blocked";
  if (tool.status === "done") return "complete";
  return "running";
}

function fallbackToolLabel(tool: ProgressToolSignal): string {
  const text = `${tool.name || ""} ${tool.context || ""} ${tool.summary || ""}`.toLowerCase();
  if (tool.status === "error") return "Action needs attention";
  if (/write|patch|edit|file|agent|claude/.test(text)) return "Editing files";
  if (/build|npm|vite|compile|test|pytest/.test(text)) return "Checking build";
  if (/preview|site|surface|product/.test(text)) return "Checking product";
  if (/creative|video|image|ad/.test(text)) return "Creating ad asset";
  if (/research|icp|channel|competitor|pricing/.test(text)) return "Researching market";
  if (/cron|wake|schedule/.test(text)) return "Checking schedule";
  if (/shell|exec|command/.test(text)) return "Running command";
  return String(tool.name || "Action")
    .trim()
    .replace(/[._-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase()) || "Action";
}

function describeTool(tool: ProgressToolSignal): string {
  // tool.error is the one field that can carry a raw provider/runtime error
  // (`Error code: 400 - {'type': 'error', ...}`). Route it through the
  // "fail better" sanitizer so the live card's blocked detail is a calm,
  // user-facing line, never a Python dict / provider repr (BUG-002).
  const safeError = tool.error ? sanitizeTaskErrorText(tool.error) : "";
  return cleanText(
    [tool.label || fallbackToolLabel(tool), tool.context, tool.preview, tool.summary, safeError]
      .filter(Boolean)
      .join(" · "),
  ).trim();
}

function collectWorkstreamSignals({
  statusItems,
  progressLines,
  tools,
}: {
  statusItems?: string[];
  progressLines?: string[];
  tools?: ProgressToolSignal[];
}): Array<{ key: WorkstreamKey; status: WorkstreamStatus; detail: string }> {
  const signals: Array<{ key: WorkstreamKey; status: WorkstreamStatus; detail: string }> = [];

  for (const tool of (tools || []).slice().reverse()) {
    const detail = describeTool(tool);
    const key = detectWorkstreamKey(detail || tool.name || "");
    if (!key) continue;
    signals.push({ key, status: detectToolWorkstreamStatus(tool), detail: detail || tool.name || "" });
  }

  for (const line of [...(progressLines || [])].reverse()) {
    const detail = cleanText(line).trim();
    const key = detectWorkstreamKey(detail);
    if (!key) continue;
    signals.push({ key, status: detectSignalStatus(detail), detail });
  }

  for (const line of [...(statusItems || [])].reverse()) {
    const detail = cleanText(line).trim();
    const key = detectWorkstreamKey(detail);
    if (!key) continue;
    signals.push({ key, status: detectSignalStatus(detail), detail });
  }

  return signals;
}

export function deriveWorkstreamItems({
  statusItems,
  progressLines,
  tools,
}: {
  statusItems?: string[];
  progressLines?: string[];
  tools?: ProgressToolSignal[];
}): {
  blocked?: string;
  currentKey?: WorkstreamKey;
  items: WorkstreamItem[];
  next?: string;
} {
  const signals = collectWorkstreamSignals({ statusItems, progressLines, tools });
  const blockedSignal = signals.find((item) => item.status === "blocked");
  const runningSignal = signals.find((item) => item.status === "running");
  const currentSignal = blockedSignal || runningSignal;
  const completedKeys = new Set<WorkstreamKey>(
    signals
      .filter((item) => item.status === "complete")
      .map((item) => item.key),
  );

  const furthestCompleteIndex = [...completedKeys].reduce(
    (highest, key) => Math.max(highest, WORKSTREAM_INDEX.get(key) ?? -1),
    -1,
  );
  const currentIndex = currentSignal ? WORKSTREAM_INDEX.get(currentSignal.key) ?? -1 : -1;
  const inferredCompleteIndex = Math.max(furthestCompleteIndex, currentIndex - 1);

  const items = WORKSTREAMS.map((definition, index) => {
    let status: WorkstreamStatus = "pending";
    if (completedKeys.has(definition.key) || index <= inferredCompleteIndex) {
      status = "complete";
    }
    if (currentSignal?.key === definition.key) {
      status = currentSignal.status;
    }
    return {
      key: definition.key,
      label: definition.label,
      completeLabel: definition.completeLabel,
      status,
    };
  });

  const nextItem =
    currentIndex >= 0
      ? items.slice(currentIndex + 1).find((item) => item.status === "pending")
      : items.find((item) => item.status === "pending");

  return {
    blocked: blockedSignal?.detail,
    currentKey: currentSignal?.key,
    items,
    next: nextItem?.label,
  };
}

/**
 * True when the business has moved past its first bootstrap — it has a
 * published/publishing product, a built product surface, or any prior history.
 * Derived from the canonical workspace mirror (`overview.product` + outputs),
 * never from the agent's turn context. Callers pass the result as
 * `pastBootstrap` so the live card never replays the bootstrap "Starting…"
 * placeholder on a transient empty live-state (reconnect, restart, idle).
 */
export function businessHasShipped(
  workspace: { overview?: Record<string, unknown> | null; outputs?: unknown[] | null } | null | undefined,
): boolean {
  if (!workspace) return false;
  const overview = (workspace.overview || {}) as Record<string, unknown>;
  const product = (overview.product || {}) as Record<string, unknown>;
  const publicUrl = typeof product.public_url === "string" ? product.public_url.trim() : "";
  if (publicUrl) return true;
  const publishStatus = String(product.publish_status || "").trim().toLowerCase();
  if (publishStatus && !["", "missing", "none", "pending", "queued"].includes(publishStatus)) {
    return true;
  }
  const publishTarget = typeof product.publish_target === "string" ? product.publish_target.trim() : "";
  if (publishTarget) return true;
  const sourcePath = typeof product.source_path === "string" ? product.source_path.trim() : "";
  if (sourcePath) return true;
  if (product.preview_available === true) return true;
  const productStatus = String(product.status || "").trim().toLowerCase();
  if (productStatus && !["", "missing", "none"].includes(productStatus)) return true;
  const outputs = Array.isArray(workspace.outputs) ? workspace.outputs : [];
  return outputs.some((item) => {
    const output = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const path = typeof output.path === "string" ? output.path : "";
    return path.startsWith("product/");
  });
}

export function deriveLiveWorkstreamCard({
  running,
  businessName,
  statusItems,
  progressLines,
  tools,
  pastBootstrap = false,
}: {
  running: boolean;
  businessName: string;
  statusItems?: string[];
  progressLines?: string[];
  tools?: ProgressToolSignal[];
  // When true, the business is already past its first bootstrap, so the card
  // must NOT fall back to the bootstrap "Starting <business> / Researching the
  // market" placeholder. With no parseable workstream signal it stays a neutral
  // "<business> update" with no fabricated phase ladder — the last known real
  // update (or a stable idle state) shows instead of replaying bootstrap.
  pastBootstrap?: boolean;
}): LiveWorkstreamCardData | null {
  const { blocked, currentKey, items, next } = deriveWorkstreamItems({
    statusItems,
    progressLines,
    tools,
  });
  if (!running && !currentKey && !blocked) return null;
  const current = currentKey ? workstreamDefinition(currentKey) : null;
  const completed = items.filter((item) => item.status === "complete");
  // Only a genuine first bootstrap (no shipped product, no parsed progress yet)
  // may show the "Starting…" copy and the default phase ladder. A business that
  // has already shipped keeps a neutral update with no invented phases so a
  // momentary empty live-state cannot reset it to "Researching the market".
  const isFirstBootstrap = !pastBootstrap && completed.length === 0 && !currentKey;

  return {
    title: blocked
      ? `${businessName} needs attention`
      : isFirstBootstrap
        ? `Starting ${businessName}`
        : `${businessName} update`,
    summary:
      current?.summary
      || (isFirstBootstrap
        ? "I'm moving this through the next business workstream now."
        : "Picking this back up now — more in a moment."),
    items: pastBootstrap && !currentKey && completed.length === 0 ? [] : items,
    current: current?.label,
    next: pastBootstrap && !currentKey && completed.length === 0 ? undefined : next,
    blocked,
  };
}

function extractUrls(text: string): string[] {
  return [...cleanText(text).matchAll(/https?:\/\/[^\s)]+/g)].map((match) => match[0]);
}

function extractWorkflowBullets(text: string): string[] {
  const bullets: string[] = [];
  let capture = false;
  for (const rawLine of cleanText(text).split(/\n+/)) {
    const line = rawLine.trim();
    if (!line) {
      if (capture && bullets.length > 0) break;
      continue;
    }
    if (/^(built workflow|the mvp will let a user|product build started|validation status|what was built)\s*:?\s*$/i.test(line)) {
      capture = true;
      continue;
    }
    if (!capture) continue;
    const bullet = line.replace(/^[-*•]\s+/, "").replace(/^\d+\.\s+/, "").trim();
    if (!bullet) continue;
    if (/\/|\.(ts|tsx|js|jsx|py|md|json)\b/i.test(bullet)) continue;
    bullets.push(bullet);
    if (bullets.length >= 4) break;
  }
  return bullets;
}

function extractChecks(text: string, liveUrl?: string): string[] {
  const checks: string[] = [];
  const lower = cleanText(text).toLowerCase();
  if (/typecheck|npm run typecheck|types check|typechecked/.test(lower)) {
    checks.push("Typecheck passed");
  }
  if (/publish succeeded|published clean|publishing succeeded|deployed|is live|workflow live/.test(lower) || !!liveUrl) {
    checks.push("Publish succeeded");
  }
  if (/verified|validation status|stable|product loads|summary action works|live route verified|works end to end|works end-to-end/.test(lower)) {
    checks.push("Live flow verified");
  }
  return checks.filter((item, index) => checks.indexOf(item) === index);
}

function extractNextStep(text: string): string | undefined {
  const normalized = cleanText(text);
  const labeled =
    normalized.match(/Recommended next step:\s*([\s\S]+?)$/i)?.[1] ||
    normalized.match(/Next(?: step)?:\s*([\s\S]+?)$/i)?.[1];
  const sentence = (labeled || "").trim().split(/\n{2,}/)[0]?.trim();
  if (sentence) return sentence.replace(/^[-*•]\s*/, "");
  return undefined;
}

function extractPaths(text: string): string[] {
  const paths = new Set<string>();
  const patterns = [
    /^\*\*\* (?:Add|Update|Delete) File: (.+)$/gm,
    /^(?:\+\+\+|---) b\/(.+)$/gm,
    new RegExp(`\\b(?:[\\w.-]+\\/)+[\\w.+-]+\\.(?:${PATH_EXTENSIONS})\\b`, "g"),
    new RegExp(`\\b\\/(?:[\\w .+-]+\\/)+[\\w .+-]+\\.(?:${PATH_EXTENSIONS})\\b`, "g"),
  ];

  for (const pattern of patterns) {
    for (const match of text.matchAll(pattern)) {
      const candidate = (match[1] ?? match[0]).trim();
      if (candidate && !candidate.includes("node_modules")) {
        paths.add(cleanText(candidate.replace(/^["'`]|["'`]$/g, "")));
      }
    }
  }

  return [...paths].slice(0, 8);
}

// Internal toolchain artifacts the CEO writes incidentally while building —
// never surfaced as operator-facing deliverables (card "Dont show all raw
// documents"). Mirrors the backend _takyon_hide_operator_output filter.
const INTERNAL_ARTIFACT_DIR_SEGMENTS = [
  "node_modules/",
  "/.next/",
  ".next/cache/",
  "/.cache/",
  "/__pycache__/",
  "/.git/",
  "/dist/",
  "/build/",
  "/.turbo/",
  "/.vite/",
];
const INTERNAL_ARTIFACT_BASENAMES = new Set([
  "skill.md",
  "config.yaml",
  "config.yml",
  "package.json",
  "package-lock.json",
  "yarn.lock",
  "pnpm-lock.yaml",
  "uv.lock",
  "poetry.lock",
  "requirements.txt",
  "pyproject.toml",
  "tsconfig.json",
  "vite.config.ts",
  "vite.config.js",
  "agents.md",
  "claude.md",
]);
// Code module suffixes are internal source, not operator deliverables.
const INTERNAL_ARTIFACT_SUFFIXES = new Set(["ts", "tsx", "js", "jsx"]);

function isInternalArtifactPath(candidate: string): boolean {
  const normalized = `/${candidate.replace(/^\/+/, "")}`.toLowerCase();
  if (INTERNAL_ARTIFACT_DIR_SEGMENTS.some((seg) => normalized.includes(seg))) return true;
  const base = (candidate.split("/").pop() || "").toLowerCase();
  if (INTERNAL_ARTIFACT_BASENAMES.has(base)) return true;
  if (base.endsWith(".lock")) return true;
  const suffix = (base.split(".").pop() || "").toLowerCase();
  // Keep website source files (under product/site/) visible even if .js/.ts.
  if (INTERNAL_ARTIFACT_SUFFIXES.has(suffix) && !normalized.includes("/product/site/")) return true;
  return false;
}

export type LinearArtifact = {
  path: string;
  title: string;
  category: "research" | "product" | "launch" | "other";
};

function artifactCategory(path: string): LinearArtifact["category"] {
  const p = path.toLowerCase();
  if (p.startsWith("research/") || p.includes("/research/") || p.startsWith("brain/")) return "research";
  if (p.startsWith("product/") || p.includes("/product/") || p.includes("site/")) return "product";
  if (
    p.startsWith("distribution/")
    || p.startsWith("outreach/")
    || p.startsWith("campaigns/")
    || p.includes("/receipts/")
  ) {
    return "launch";
  }
  return "other";
}

/**
 * Surface bootstrap artifacts linearly: scan ordered build-stream lines and
 * return each business-meaningful artifact in first-seen (chronological,
 * append-only) order. Internal toolchain files are filtered out. Presentation
 * only — derived from the existing narration/terminal stream, no runtime change.
 */
export function deriveLinearArtifacts(lines: string[], limit = 24): LinearArtifact[] {
  const seen = new Set<string>();
  const artifacts: LinearArtifact[] = [];
  for (const line of lines) {
    for (const path of extractPaths(line)) {
      if (seen.has(path) || isInternalArtifactPath(path)) continue;
      seen.add(path);
      const name = path.split("/").pop() || path;
      artifacts.push({ path, title: name, category: artifactCategory(path) });
      if (artifacts.length >= limit) return artifacts;
    }
  }
  return artifacts;
}

function looksTechnicalAssistantReceipt(text: string): boolean {
  const normalized = cleanText(text);
  if (!normalized.trim()) return false;
  const pathCount = extractPaths(normalized).length;
  return (
    /what was built|files changed|typechecked|publish(?:ed|ing)|live url|validation status|checks|vercel|product\/site\//i.test(
      normalized,
    ) ||
    (pathCount > 0 &&
      /build|changed|typecheck|publish|verified|workflow live|live route/i.test(normalized))
  );
}

/**
 * Light, display-only clean of a CEO turn reply (mirrors the backend
 * `_takyon_clean_chat_text`). The chat IS the agent's turn: its reply is shown
 * lightly cleaned — control codes stripped, blank-line runs collapsed — while the
 * agent's actual words are PRESERVED. The legacy line-by-line plumbing ban-list
 * (which could delete every line and empty the bubble, then fall back to a generic
 * placeholder) has been REMOVED: abstraction is by generation, not by subtraction
 * (reactive-chat spec §32). This never drops content and never empties non-empty
 * input. Presentation-only — it never alters the agent's actual reply.
 */
export function sanitizeCustomerReply(content: string): string {
  const normalized = cleanText(content).trim();
  if (!normalized) return "";
  const kept: string[] = [];
  for (const rawLine of normalized.split(/\n/)) {
    const line = rawLine.trim();
    if (!line) {
      // Preserve paragraph breaks between kept content.
      if (kept.length && kept[kept.length - 1] !== "") kept.push("");
      continue;
    }
    kept.push(line);
  }
  return kept.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

// --- Raw provider-error "fail better" (BUG-002) ----------------------------
//
// A failed bootstrap/CEO task can carry the raw upstream provider error string
// in its detail/status/description fields. Rendered verbatim on a task card it
// reads like `Error: Error code: 400 - {'type': 'error', 'error': {'type':
// 'invalid_request_error', 'message': 'You have reached your specified
// workspace API usage limits...'}}` — a Python dict / provider repr that strands
// the customer with internal guts (violates card #4 "no thinking/log copy" and
// #11 "fail better"). This pure helper detects such raw errors and maps them to
// one calm, general, user-facing line. It is deliberately NOT keyed to a single
// message: it classifies by error SHAPE so any future quota / rate-limit / 4xx
// / 5xx / dict-blob error gets a clean line. Already-clean human text is left
// untouched (only rewritten when it clearly looks like a raw error).

// Calm, general customer-facing copy. Never a provider message, code, or field.
const TASK_ERROR_USAGE_LIMIT_COPY =
  "This step is briefly paused — we're at capacity and will pick it back up shortly.";
const TASK_ERROR_RATE_LIMIT_COPY =
  "Working through a busy moment — this will retry automatically.";
const TASK_ERROR_GENERIC_COPY = "That step hit a snag and will retry.";
// Network / socket failures are genuinely transient — retry is the right story.
const TASK_ERROR_TIMEOUT_COPY = "That step took too long to respond and will retry.";

// Markers that prove a string is a raw provider/runtime error rather than warm
// human copy. Matching ANY of these flips the string into "rewrite" mode.
const RAW_PROVIDER_ERROR_MARKERS: RegExp[] = [
  /invalid_request_error/i,
  /workspace api usage limit/i,
  /usage limit/i,
  /insufficient[_\s-]?(?:quota|credit)/i,
  /\bError code:\s*\d/i,
  /\brate[_\s-]?limit/i,
  /\{'type'\s*:\s*'error'/i,
  /"type"\s*:\s*"error"/i,
  // A JSON object / Python dict blob (balanced-ish braces with a quoted key):
  // `{'error': {...}}`, `{"message": "..."}`. Distinguishes a serialized error
  // payload from prose that merely contains a stray brace.
  /\{\s*['"][\w-]+['"]\s*:/,
  // Bare provider/runtime error preface ("Error: ...", "Exception: ...").
  /^\s*(?:error|exception|traceback)\b\s*[:(-]/i,
  // An HTTP status surfaced inline ("HTTP 429", "status 503").
  /\b(?:http\s*)?(?:status\s*)?\b[45]\d{2}\b\s*(?:-|—|:|error|too many|service)/i,
  // OAuth / connection / permission failure from a channel integration — a
  // blocked account ("Meta OAuth failed with invalid permissions"), not a
  // transient API error. Caught so it becomes a clean "reconnect <channel>" line.
  /\boauth\b/i,
  /\b40[13]\b/,
  /\bunauthorized\b/i,
  /\bforbidden\b/i,
  /\baccess denied\b/i,
  /\bpermission denied\b/i,
  /\binvalid[_\s-]?(?:permission|permissions|scope|grant|token|credential|credentials)\b/i,
  /\btoken (?:expired|invalid|revoked)\b/i,
  /\bnot connected\b/i,
  /\bconnection (?:not found|failed|refused|reset)\b/i,
  // Missing provider key / unconfigured capability — needs a one-time setup
  // ("GEMINI_API_KEY required", "gemini_image_unconfigured"), not a retry.
  /\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*_KEY\b/,
  /_unconfigured\b/i,
  /\bnot configured\b/i,
  /\bcredentials?\s+(?:required|missing|invalid|not\s+found)\b/i,
  // Network / timeout — transient, retryable.
  /\btimed out\b/i,
  /\btimeout\b/i,
  /\betimedout\b/i,
  /\beconn(?:reset|refused)\b/i,
  /\benotfound\b/i,
];

function looksLikeRawProviderError(text: string): boolean {
  return RAW_PROVIDER_ERROR_MARKERS.some((pattern) => pattern.test(text));
}

// --- Blocked-integration copy (the "sharp teammate" line, not a raw log) -----
//
// A connection/permission/missing-key failure is NOT a transient retry — it is
// a BLOCKER the operator has to clear (reconnect an account, finish a setup).
// These helpers turn the raw error into one calm line that KEEPS the important
// context: WHICH channel or capability is blocked (so the line stays concrete,
// never the vague "there was an issue with setup"). They are only consulted
// after the text is already classified as a raw error, so warm prose is never
// relabeled. Idempotent: their own output matches no raw-error marker.

// Warm name for the channel a raw OAuth/permission error came from ("" if none).
function detectBlockedChannel(lower: string): string {
  if (/\b(?:meta|facebook|instagram|fb ads|fb oauth)\b/.test(lower)) return "Meta";
  // X is matched only via named patterns ("x account", "x api", "twitter"…),
  // never a bare letter "x", so warm prose can't be relabeled.
  if (/\btwitter\b|\btweet|x\.com|x_v\d|\bx (?:ads|api|oauth|account|channel|post|posting|platform|scope)\b/.test(lower)) {
    return "X";
  }
  if (/\breddit\b/.test(lower)) return "Reddit";
  if (/\b(?:search console|google|gsc)\b/.test(lower)) return "Google";
  if (/\bstripe\b/.test(lower)) return "Stripe";
  return "";
}

// Warm name for the paid capability a missing-key/unconfigured error blocks.
function detectBlockedCapability(lower: string): string {
  if (/gemini|gpt[-_ ]?image|\bimage\b|\blogo\b|\bfal\b|\bugc\b|\bvideo\b/.test(lower)) {
    return "Image generation";
  }
  if (/tavily|web[-_ ]?search|web[-_ ]?extract|web research/.test(lower)) return "Web research";
  return "";
}

function connectionBlockedCopy(channel: string): string {
  return channel
    ? `${channel} isn't connected correctly yet — reconnect it to continue.`
    : "A connected account needs reconnecting before this step can continue.";
}

function providerUnconfiguredCopy(capability: string): string {
  return capability
    ? `${capability} isn't set up yet, so this step is on hold.`
    : "This step needs a connection that isn't set up yet.";
}

/**
 * Map a task's error/status text to clean, calm, user-facing copy when (and only
 * when) it looks like a raw provider/runtime error. Returns the input unchanged
 * for already-clean human messages. Pure — no I/O, no side effects.
 *
 * Examples:
 *   sanitizeTaskErrorText(
 *     "Error: Error code: 400 - {'type': 'error', 'error': {'type': " +
 *     "'invalid_request_error', 'message': 'You have reached your specified " +
 *     "workspace API usage limits...'}}"
 *   ) === "This step is briefly paused — we're at capacity and will pick it back up shortly."
 *
 *   sanitizeTaskErrorText("Error code: 429 - rate_limit_error")
 *     === "Working through a busy moment — this will retry automatically."
 *
 *   sanitizeTaskErrorText("Error code: 503 - {'type': 'error'}")
 *     === "That step hit a snag and will retry."
 *
 *   sanitizeTaskErrorText("Meta OAuth failed with invalid permissions")
 *     === "Meta isn't connected correctly yet — reconnect it to continue."
 *
 *   sanitizeTaskErrorText("GEMINI_API_KEY required")
 *     === "Image generation isn't set up yet, so this step is on hold."
 *
 *   sanitizeTaskErrorText("Researching the market") === "Researching the market"  // untouched
 */
export function sanitizeTaskErrorText(text: string): string {
  const raw = cleanText(String(text ?? "")).trim();
  if (!raw) return "";
  // Already-clean human copy: leave it exactly as written.
  if (!looksLikeRawProviderError(raw)) return raw;
  const lower = raw.toLowerCase();
  // Usage limit / quota / "workspace API usage" → capacity-paused copy.
  if (
    /workspace api usage limit/.test(lower)
    || /usage limit/.test(lower)
    || /\bquota\b/.test(lower)
    || /\bbilling\b/.test(lower)
    || /insufficient[_\s-]?(?:quota|credit)/.test(lower)
  ) {
    return TASK_ERROR_USAGE_LIMIT_COPY;
  }
  // Rate limit / 429 → busy-moment retry copy.
  if (/rate[_\s-]?limit/.test(lower) || /\b429\b/.test(lower) || /too many requests/.test(lower)) {
    return TASK_ERROR_RATE_LIMIT_COPY;
  }
  // Network / timeout → genuinely transient. Checked before the connection class
  // so a dropped/slow socket ("connection reset", "timed out") is not mistaken
  // for a credential problem.
  if (
    /\b(?:timed out|timeout|etimedout|econnreset|econnrefused|enotfound)\b/.test(lower)
    || /\bconnection (?:reset|refused)\b/.test(lower)
  ) {
    return TASK_ERROR_TIMEOUT_COPY;
  }
  // Channel not connected / OAuth / permission denied → a blocker the operator
  // must clear, never an auto-retry. Name the channel so it stays concrete.
  if (
    /\boauth\b/.test(lower)
    || /\b40[13]\b/.test(lower)
    || /\bunauthorized\b/.test(lower)
    || /\bforbidden\b/.test(lower)
    || /\baccess denied\b/.test(lower)
    || /\bpermission denied\b/.test(lower)
    || /\binvalid[_\s-]?(?:permission|permissions|scope|grant|token|credential|credentials)\b/.test(lower)
    || /\btoken (?:expired|invalid|revoked)\b/.test(lower)
    || /\bnot connected\b/.test(lower)
    || /\bconnection not found\b/.test(lower)
  ) {
    return connectionBlockedCopy(detectBlockedChannel(lower));
  }
  // Missing provider key / unconfigured capability → needs a one-time setup,
  // not a retry. Name the capability when the raw error reveals it.
  if (
    /\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)*_KEY\b/.test(raw)
    || /_unconfigured\b/.test(lower)
    || /\bnot configured\b/.test(lower)
    || /\bcredentials?\s+(?:required|missing|invalid|not\s+found)\b/.test(lower)
  ) {
    return providerUnconfiguredCopy(detectBlockedCapability(lower));
  }
  // Any other 4xx/5xx / provider dict / generic raw error → generic retry copy.
  return TASK_ERROR_GENERIC_COPY;
}

// One curated, customer-safe assistant message from the backend chat stream.
// Shape mirrors the gateway contract emitted by `_takyon_ceo_chat_stream`
// (tui_gateway/server.py): the text is already sanitized server-side; we
// re-run sanitizeCustomerReply as belt-and-suspenders so a slip can never
// surface raw planner/reasoning prose in the visible bubble.
export type ChatStreamItem = {
  id: string;
  role: "assistant";
  text: string;
  headline: string;
  summary: string;
  postedAt: string;
};

// A curated CEO chat message ready to render as an agent bubble: customer-safe
// text plus an optional wall-clock ms (parsed from the ISO posted_at) so it can
// be merged in order against the user's send-stamped messages.
export type ChatStreamMessage = {
  id: string;
  text: string;
  ts?: number;
};

type WorkspaceLike = {
  overview?: Record<string, unknown> | null;
  live_state?: Record<string, unknown> | null;
} | null | undefined;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function rawChatStreamArray(workspace: WorkspaceLike): unknown[] {
  if (!workspace) return [];
  const overview = asRecord(workspace.overview);
  const fromOverview = overview.chat_stream;
  if (Array.isArray(fromOverview)) return fromOverview;
  const liveState = asRecord(workspace.live_state);
  const fromLiveState = liveState.chat_stream;
  if (Array.isArray(fromLiveState)) return fromLiveState;
  return [];
}

/**
 * Parse the backend's `overview.chat_stream` (fallback `live_state.chat_stream`)
 * into normalized items — one bubble per completed CEO turn (the turn's own reply).
 * Each item's text is lightly cleaned for display (no ban-list, never emptied);
 * only a genuinely-empty record is skipped. This is the ONLY source the litebulb
 * chat reads for AGENT bubbles.
 */
export function parseChatStream(workspace: WorkspaceLike): ChatStreamItem[] {
  const items: ChatStreamItem[] = [];
  rawChatStreamArray(workspace).forEach((entry, index) => {
    const record = asRecord(entry);
    const safe = sanitizeCustomerReply(String(record.text ?? ""));
    if (!safe) return;
    const postedAt = String(record.posted_at ?? "").trim();
    const id = String(record.id ?? "").trim() || `chat-stream-${postedAt || index}`;
    items.push({
      id,
      role: "assistant",
      text: safe,
      headline: sanitizeCustomerReply(String(record.headline ?? "")),
      summary: sanitizeCustomerReply(String(record.summary ?? "")),
      postedAt,
    });
  });
  return items;
}

/**
 * The curated agent bubbles for the transcript: each chat_stream item as an
 * agent ChatStreamMessage with an optional wall-clock ms parsed from posted_at.
 * Ordered oldest→newest, matching the backend ordering.
 */
export function chatStreamAgentMessages(workspace: WorkspaceLike): ChatStreamMessage[] {
  return parseChatStream(workspace).map((item) => {
    const parsed = item.postedAt ? Date.parse(item.postedAt) : NaN;
    return {
      id: item.id,
      text: item.text,
      ts: Number.isFinite(parsed) ? parsed : undefined,
    };
  });
}

// One in-flight worker step for the build screen's live "Working on…" list. The
// backend already ships per-step worker progress in the boot payload
// (workspace.live_state.tasks, fallback overview.tasks, plus each milestone's
// nested runtime children). `detail` is re-sanitized at the render site so no
// tool/path/build noun reaches the customer-facing column.
export type LiveWorkerTask = {
  id: string;
  label: string;
  detail: string;
  status: string;
};

function asTaskArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((entry): entry is Record<string, unknown> => Boolean(entry) && typeof entry === "object")
    .map((entry) => entry as Record<string, unknown>);
}

function taskFields(record: Record<string, unknown>): LiveWorkerTask {
  const label = String(record.label ?? record.title ?? record.name ?? "").trim();
  const detail = String(record.detail ?? record.summary ?? record.context ?? "").trim();
  const status = String(record.status ?? "").trim().toLowerCase();
  const id = String(record.id ?? record.key ?? "").trim() || `${label || "step"}-${detail}`;
  return { id, label, detail, status };
}

/**
 * The live worker steps to surface on the build screen, flattened from the
 * canonical workspace mirror (`live_state.tasks`, fallback `overview.tasks`).
 * Returns the running/queued milestones plus — for the running milestone — its
 * nested runtime children (source 'runtime'/'task'), so the long worker phase is
 * legible step-by-step. Newest-relevant order is preserved; the caller caps and
 * sanitizes detail. Presentation-only — derived from the boot payload, never the
 * agent's raw turn context.
 */
export function liveWorkerTasks(workspace: WorkspaceLike): LiveWorkerTask[] {
  if (!workspace) return [];
  const liveState = asRecord(workspace.live_state);
  const overview = asRecord(workspace.overview);
  const rawTasks = asTaskArray(liveState.tasks).length
    ? asTaskArray(liveState.tasks)
    : asTaskArray(overview.tasks);
  const result: LiveWorkerTask[] = [];
  for (const record of rawTasks) {
    const status = String(record.status ?? "").trim().toLowerCase();
    if (status !== "running" && status !== "queued") continue;
    result.push(taskFields(record));
    if (status === "running") {
      // The running milestone carries the live per-step worker progress as nested children. Only the
      // DE-IDENTIFIED phase children (source="task", e.g. "Building and testing the product") belong
      // in the customer "Working on…" list. The raw source="runtime" children are unsanitized claude-
      // agent log lines — env-wrapped shell commands (`/usr/bin/env … npm run build`) and internal
      // labels like "CEO live trace" — which leak plumbing onto the build screen; they stay in the
      // collapsed "View build details" disclosure, not the customer-facing list.
      for (const child of asTaskArray(record.children)) {
        const source = String(child.source ?? "").trim().toLowerCase();
        if (source !== "task") continue;
        const childStatus = String(child.status ?? "").trim().toLowerCase();
        if (childStatus && childStatus !== "running" && childStatus !== "queued") continue;
        result.push(taskFields(child));
      }
    }
  }
  return result;
}

// --- Live work view (cockpit: chat turn + wake) ----------------------------
//
// A growing, append-only list of WHAT THE CEO IS DOING during a live turn — the
// same liveness the bootstrap build screen shows, but for the post-bootstrap
// cockpit during a chat turn AND a wake. Every rail (bootstrap / wake / chat
// turn) records the SAME per-tool runtime traces (dashboard.run.trace events ->
// overview.trace, fallback live_state.trace), each carrying a label, a detail,
// and a running/completed status, upserted one row per tool by entry_key. We
// read THAT canonical trace array (the same source livePhases reads — no new ad
// hoc event type) and turn it into one work-step line per tool, newest last.
//
// This is the WORK / DEBUG view, NOT a customer chat bubble: each line is still
// run through the de-identifier (sanitizeCustomerReply) so a raw tool/path/skill
// noun never reaches the screen, and the CEO's raw chain-of-thought (which lives
// only in the agent's messages, never in these traces) can never leak here.

export type LiveWorkStep = {
  id: string;
  // Warm, customer-safe label for the step (already de-identified upstream by the
  // gateway's _takyon_trace_tool_shape; re-cleaned here for defense).
  label: string;
  // Optional de-identified sub-line (the cleaned tool preview/summary). Empty
  // when it would echo the label or carry plumbing.
  detail: string;
  status: "running" | "completed";
};

type RawTraceRow = {
  entryKey: string;
  label: string;
  detail: string;
  status: string;
  updatedAt: string;
};

function traceRowArray(workspace: WorkspaceLike): RawTraceRow[] {
  if (!workspace) return [];
  const overview = asRecord(workspace.overview);
  const raw = Array.isArray(overview.trace)
    ? overview.trace
    : Array.isArray(asRecord(workspace.live_state).trace)
      ? (asRecord(workspace.live_state).trace as unknown[])
      : [];
  const rows: RawTraceRow[] = [];
  for (const entry of raw) {
    const record = asRecord(entry);
    rows.push({
      entryKey: String(record.entry_key ?? record.id ?? "").trim(),
      label: String(record.label ?? "").trim(),
      detail: String(record.detail ?? record.summary ?? "").trim(),
      status: String(record.status ?? "").trim().toLowerCase(),
      updatedAt: String(record.updated_at ?? "").trim(),
    });
  }
  return rows;
}

// Turn-level / runner plumbing labels that are NOT a discrete "step" the operator
// should watch grow (the turn row itself, raw runner names). The growing work
// view is the per-tool/agent step stream that sits UNDER the turn.
const WORK_STEP_PLUMBING_LABELS = new Set([
  "ceo turn",
  "ceo wake",
  "ceo wake loop",
  "ceo bootstrap",
  "ceo run",
  "ceo live trace",
  "background run",
  "background work",
]);

/**
 * The growing live work view for the cockpit during a chat turn or a wake: one
 * de-identified step line per tool/agent action the CEO is taking, in
 * chronological order (newest last), derived from the canonical runtime trace
 * array. A tool's start row and completed row share an entry_key, so the trace
 * array already collapses them to one evolving row (running -> completed) — this
 * keeps the view append-only (the line count grows, a row updates in place) and
 * never truncates mid-turn.
 *
 * Presentation-only — derived from the workspace mirror's trace events, never the
 * agent's raw turn context. The CEO's raw thinking is not in these traces, so it
 * cannot leak; each surfaced label/detail is still re-sanitized for defense.
 */

// Fail-closed clean for a work-step label/detail. sanitizeCustomerReply strips
// status-log/plumbing PROSE, but a trace detail can still carry a bare tool/skill
// IDENTIFIER shape (business_sync_ads, takyon:tool:foo, foo::bar) that prose
// sanitization does not target. Mirror the backend's
// _takyon_clean_worker_progress_detail guard: drop the line entirely when it
// carries such an identifier so no raw tool noun reaches the screen. Returns ""
// when the line should be dropped.
function cleanWorkStepText(value: string): string {
  const text = sanitizeCustomerReply(value);
  if (!text) return "";
  const lowered = text.toLowerCase();
  // A raw tool/skill identifier shape is plumbing — never surface it.
  if (/\b[a-z]+_[a-z_]+\b|takyon:|(?:^|\s)tool:|::/.test(lowered)) return "";
  return text;
}

export function liveWorkSteps(workspace: WorkspaceLike): LiveWorkStep[] {
  const steps: LiveWorkStep[] = [];
  for (const row of traceRowArray(workspace)) {
    // The work view shows in-flight + done steps only. A tool's evolving row is
    // either running or completed; treat anything else (queued/failed/blocked/
    // empty) as not-yet-a-visible-step and skip it — failures surface through the
    // chat/summary copy, not as a green-checked work line.
    const isCompleted = row.status === "completed" || row.status === "complete";
    const isRunning = row.status === "running";
    if (!isCompleted && !isRunning) continue;
    const status: LiveWorkStep["status"] = isCompleted ? "completed" : "running";
    const rawLabel = row.label;
    if (!rawLabel) continue;
    if (WORK_STEP_PLUMBING_LABELS.has(rawLabel.toLowerCase())) continue;
    // The label is the de-identified intent label (already cleaned upstream by
    // the gateway's _takyon_trace_tool_shape); keep prose, but if it still reads
    // as a raw tool identifier drop the whole step rather than surface plumbing.
    const label = cleanWorkStepText(rawLabel);
    if (!label) continue;
    // The detail is a best-effort sub-line; drop it (keep the labelled step) when
    // it would echo the label or carry a raw tool/path/skill identifier.
    let detail = cleanWorkStepText(row.detail);
    if (detail && detail.trim().toLowerCase() === label.trim().toLowerCase()) {
      detail = "";
    }
    const id = row.entryKey || `${label}:${row.updatedAt}`;
    steps.push({ id, label, detail, status });
  }
  return steps;
}

// --- Build-phase ladder ----------------------------------------------------
//
// The bootstrap turn runs a FIXED, ordered phase sequence
// (plugins/takyon/cli.py::_business_bootstrap_instruction): a fast landing
// build, then the logo, Search Console registration, the sign-in/subscription
// access shell, and the launch post. Today the build screen only shows opaque
// "Working on…" rows, so the customer can't tell that the landing is fast while
// the logo + Search Console are the long tail, or where sign-in/subscription
// get wired. This selector turns the durable per-tool runtime traces
// (overview.trace, fallback live_state.trace — each carrying tool_name, status,
// updated_at and the persisted per-tool duration_s) into a deterministic,
// timed 6-phase ladder. It is PRESENTATIONAL ONLY — phase status is derived
// strictly from real tool events (queued until the keying tool started, running
// while in flight, complete on its completed trace), never fabricated and never
// a deterministic business router. It fails toward queued/running, never a fake
// "complete".

export type LivePhaseStatus = "queued" | "running" | "complete";

export type LivePhase = {
  id: string;
  // Warm, customer-safe label — never a tool name or runtime jargon.
  label: string;
  status: LivePhaseStatus;
  // Seconds elapsed: the persisted tool duration once complete, otherwise the
  // live elapsed time since the keying tool started (when running). Undefined
  // when the phase has not started.
  durationS?: number;
};

// One trace row, narrowed to the timing-relevant fields the ladder reads.
type PhaseTrace = {
  toolName: string;
  status: string;
  durationS?: number;
  startedAtMs?: number;
};

function numberOrUndefined(value: unknown): number | undefined {
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function phaseTraceArray(workspace: WorkspaceLike): PhaseTrace[] {
  if (!workspace) return [];
  const overview = asRecord(workspace.overview);
  const raw = Array.isArray(overview.trace)
    ? overview.trace
    : Array.isArray(asRecord(workspace.live_state).trace)
      ? (asRecord(workspace.live_state).trace as unknown[])
      : [];
  const out: PhaseTrace[] = [];
  for (const entry of raw) {
    const record = asRecord(entry);
    const toolName = String(record.tool_name ?? "").trim().toLowerCase();
    if (!toolName) continue;
    out.push({
      toolName,
      status: String(record.status ?? "").trim().toLowerCase(),
      durationS: numberOrUndefined(record.duration_s),
      startedAtMs: numberOrUndefined(record.started_at),
    });
  }
  return out;
}

// The 6 canonical bootstrap phases, in order, each keyed to the tool whose
// completion marks the phase done. Sign-on AND Subscription/account are both
// wired by the SECOND business_claude_agent_task pass (the /app access shell +
// /app/profile account page), so they share that pass's second occurrence.
const PHASE_LABELS: { id: string; label: string }[] = [
  { id: "landing", label: "Building your landing page" },
  { id: "logo", label: "Adding your logo" },
  { id: "search_console", label: "Getting found on Google" },
  { id: "sign_on", label: "Wiring sign-in" },
  { id: "subscription", label: "Turning on subscriptions" },
  { id: "launch", label: "Putting your launch post out" },
];

/**
 * The deterministic, timed build-phase ladder for the Building screen, derived
 * from the durable per-tool runtime traces. Returns all 6 canonical phases in
 * order with a truthful status (queued/running/complete) and per-phase timing.
 * Presentation-only — driven by real tool events + persisted durations, not by
 * model prose and not a workflow router.
 */
export function livePhases(workspace: WorkspaceLike): LivePhase[] {
  const traces = phaseTraceArray(workspace);

  // Index the bootstrap tool events in chronological order. The two
  // business_claude_agent_task passes are distinguished by occurrence: the
  // first is the landing build (2a), the second is the app-shell pass (2b).
  const claudeTasks = traces.filter((t) => t.toolName === "business_claude_agent_task");
  const logo = traces.find((t) => t.toolName === "business_generate_logo");
  const searchConsole = traces.find((t) => t.toolName === "business_register_search_console");
  const landingTask = claudeTasks[0];
  const appShellTask = claudeTasks[1];

  const isComplete = (t?: PhaseTrace) =>
    Boolean(t) && (t!.status === "completed" || t!.status === "complete");
  // A keying tool with a trace row but not yet completed is in flight.
  const isRunning = (t?: PhaseTrace) => Boolean(t) && !isComplete(t);

  // Elapsed seconds: the persisted duration once complete, else the live
  // elapsed since the tool started (when we have a started_at), else undefined.
  const elapsed = (t?: PhaseTrace): number | undefined => {
    if (!t) return undefined;
    if (typeof t.durationS === "number") return Math.max(0, t.durationS);
    if (typeof t.startedAtMs === "number") {
      return Math.max(0, Math.round((Date.now() - t.startedAtMs) / 1000));
    }
    return undefined;
  };

  const phaseTrace: Record<string, PhaseTrace | undefined> = {
    landing: landingTask,
    logo,
    search_console: searchConsole,
    // Sign-on and Subscription/account are the same 2b pass; both light up
    // together with the second claude_agent_task.
    sign_on: appShellTask,
    subscription: appShellTask,
    // The launch post phase has no single durable tool trace keyed here yet
    // (the X publish records a job, not a tool.complete trace), so it stays
    // queued/running off the upstream phases rather than fabricating a time.
    launch: undefined,
  };

  return PHASE_LABELS.map(({ id, label }) => {
    const trace = phaseTrace[id];
    let status: LivePhaseStatus;
    if (id === "launch") {
      // Launch is the final phase: it shows complete only once everything
      // before it is complete (the X post runs after the app shell). It never
      // fabricates its own completion from a tool it has no trace for, so it
      // settles to complete only when the app-shell pass is done, else queued.
      status = isComplete(appShellTask) ? "complete" : "queued";
    } else if (isComplete(trace)) {
      status = "complete";
    } else if (isRunning(trace)) {
      status = "running";
    } else {
      status = "queued";
    }
    const durationS = id === "launch" ? undefined : elapsed(trace);
    return durationS === undefined
      ? { id, label, status }
      : { id, label, status, durationS };
  });
}

/**
 * Format a phase elapsed/duration as a compact M:SS clock ("0:42", "1:18").
 * Empty when undefined.
 */
export function formatPhaseDuration(durationS?: number): string {
  if (typeof durationS !== "number" || !Number.isFinite(durationS) || durationS < 0) {
    return "";
  }
  const total = Math.round(durationS);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/**
 * The durable end-of-turn summary the backend mirrors at
 * `overview.chat_summary` (fallback `live_state.chat_summary`), re-sanitized.
 * Empty when absent.
 */
export function workspaceChatSummary(workspace: WorkspaceLike): string {
  if (!workspace) return "";
  const overview = asRecord(workspace.overview);
  const fromOverview = String(overview.chat_summary ?? "").trim();
  if (fromOverview) return sanitizeCustomerReply(fromOverview);
  const liveState = asRecord(workspace.live_state);
  const fromLiveState = String(liveState.chat_summary ?? "").trim();
  return fromLiveState ? sanitizeCustomerReply(fromLiveState) : "";
}

/**
 * The backend's authoritative running flag for the chat, mirrored on
 * `live_state.chat_running` (true while the CEO turn is running). Used to drive
 * the standalone thinking indicator on a reload, falling back to the client's
 * own in-flight signal at the call site.
 */
export function workspaceChatRunning(workspace: WorkspaceLike): boolean {
  if (!workspace) return false;
  const liveState = asRecord(workspace.live_state);
  return liveState.chat_running === true;
}

export function deriveAssistantReceipt({
  content,
  businessName,
  liveUrl,
}: {
  content: string;
  businessName: string;
  liveUrl?: string;
}): AssistantReceiptData | null {
  if (!looksTechnicalAssistantReceipt(content)) return null;
  const rawDetails = cleanText(content).trim();
  if (!rawDetails) return null;
  const resolvedUrl = extractUrls(rawDetails)[0] || liveUrl || undefined;
  const messageSignals = deriveWorkstreamItems({
    statusItems: rawDetails.split(/\n+/).slice(0, 24),
  });
  const bullets =
    extractWorkflowBullets(rawDetails).length > 0
      ? extractWorkflowBullets(rawDetails)
      : messageSignals.items
          .filter((item) => item.status === "complete")
          .map((item) => item.completeLabel)
          .slice(-4);
  const checks = extractChecks(rawDetails, resolvedUrl);
  const next =
    extractNextStep(rawDetails) ||
    (resolvedUrl
      ? "Review the live flow and decide on the first acquisition test."
      : undefined);
  return {
    title: resolvedUrl ? `${businessName} is live` : `${businessName} update`,
    summary: resolvedUrl
      ? "The latest business workflow is ready for review."
      : "The latest build completed and is ready for review.",
    bullets,
    liveUrl: resolvedUrl,
    checks,
    files: extractPaths(rawDetails),
    next,
    rawDetails,
  };
}
