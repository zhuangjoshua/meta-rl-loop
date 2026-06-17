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
  return cleanText(
    [tool.label || fallbackToolLabel(tool), tool.context, tool.preview, tool.summary, tool.error]
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

export function deriveLiveWorkstreamCard({
  running,
  businessName,
  statusItems,
  progressLines,
  tools,
}: {
  running: boolean;
  businessName: string;
  statusItems?: string[];
  progressLines?: string[];
  tools?: ProgressToolSignal[];
}): LiveWorkstreamCardData | null {
  const { blocked, currentKey, items, next } = deriveWorkstreamItems({
    statusItems,
    progressLines,
    tools,
  });
  if (!running && !currentKey && !blocked) return null;
  const current = currentKey ? workstreamDefinition(currentKey) : null;
  const completed = items.filter((item) => item.status === "complete");

  return {
    title: blocked
      ? `${businessName} needs attention`
      : completed.length === 0
        ? `Starting ${businessName}`
        : `${businessName} update`,
    summary: current?.summary || "I'm moving this through the next business workstream now.",
    items,
    current: current?.label,
    next,
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
