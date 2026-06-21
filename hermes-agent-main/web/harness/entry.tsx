import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Product } from "@/litebulb/product/Product";
import { Building } from "@/litebulb/product/Building";
import type { ChatMessage, LitebulbBusiness } from "@/litebulb/takyon/useTakyonLitebulb";
import type { TakyonBusinessWorkspaceResponse } from "@/lib/api";
import "@/litebulb/styles/globals.css";
import "@/litebulb/base44/base44.css";
import "@/litebulb/composer-ui/styles/tokens.scss";
import "@/litebulb/styles/litebulb.css";

// Mock business + noop callbacks so the real Product component renders with its
// real AgentChat panel. We only assert against the chat transcript.
const business: LitebulbBusiness = {
  slug: "acme",
  name: "Acme",
  goal: "Sell widgets",
  mode: "live",
  status: "active",
  tagline: "Sell widgets",
  meta: "Live mode",
  productUrl: "https://acme.coscale.app",
  logoPath: "",
};

const noop = () => undefined;
const asyncNoop = async () => null;
const asyncVoid = async () => undefined;

// A curated chat_stream item exactly as the backend emits it
// (_takyon_ceo_chat_stream): { id, role, text, headline, summary, posted_at }.
function streamItem(
  id: string,
  text: string,
  postedAt: string,
  headline = "",
  summary = "",
) {
  return { id, role: "assistant", text, headline, summary, posted_at: postedAt };
}

// A banned plumbing token (matches CUSTOMER_PLUMBING_PATTERNS) — should be DROPPED.
const BANNED_ONLY = "business_generate_logo";
const BANNED_LINE_TEXT = `Running business_generate_logo via the claude_agent_task worker`;

type Scenario = {
  chatStream: Array<ReturnType<typeof streamItem>>;
  chatSummary?: string;
  chatRunning: boolean;
  messages: ChatMessage[];
};

const SCENARIOS: Record<string, Scenario> = {
  // (1) bootstrap — 3 narration messages, NO user messages, chat_running true.
  bootstrap: {
    chatStream: [
      streamItem("a1", "I'm validating the market for Acme.", "2026-06-17T10:00:00Z"),
      streamItem("a2", "I picked a clear wedge: fast widget reordering.", "2026-06-17T10:01:00Z"),
      streamItem("a3", "Now I'm putting a first version online for you.", "2026-06-17T10:02:00Z"),
    ],
    chatRunning: true,
    messages: [],
  },
  // (2) idle — same stream but chat_running false → dots GONE.
  idle: {
    chatStream: [
      streamItem("a1", "I'm validating the market for Acme.", "2026-06-17T10:00:00Z"),
      streamItem("a2", "I picked a clear wedge: fast widget reordering.", "2026-06-17T10:01:00Z"),
      streamItem("a3", "Now I'm putting a first version online for you.", "2026-06-17T10:02:00Z"),
    ],
    chatSummary: "Acme is live and ready for your review.",
    chatRunning: false,
    messages: [],
  },
  // (3) follow-up — 1 user message + chat_stream narration AFTER it.
  followup: {
    chatStream: [
      streamItem("b1", "Got it — making the hero headline punchier now.", "2026-06-17T11:00:30Z"),
      streamItem("b2", "Done. The new headline is live on the homepage.", "2026-06-17T11:01:00Z"),
    ],
    chatRunning: false,
    messages: [
      {
        id: "u1",
        who: "user",
        text: "Make the homepage headline punchier",
        ts: Date.parse("2026-06-17T11:00:00Z"),
      },
    ],
  },
  // (4) a chat_stream message that is ENTIRELY a banned plumbing token → dropped.
  banned: {
    chatStream: [
      streamItem("c1", "Kicking off the build for Acme.", "2026-06-17T12:00:00Z"),
      streamItem("c2", BANNED_LINE_TEXT, "2026-06-17T12:00:30Z"),
      streamItem("c3", "The first screen is ready to preview.", "2026-06-17T12:01:00Z"),
    ],
    chatRunning: false,
    messages: [],
  },
};

function workspaceFor(scenario: Scenario): TakyonBusinessWorkspaceResponse {
  return {
    business_slug: "acme",
    current: {},
    overview: {
      product: {},
      chat_stream: scenario.chatStream,
      chat_summary: scenario.chatSummary || "",
    },
    outputs: [],
    deliverables: [],
    background_run: null,
    live_state: {
      status: scenario.chatRunning ? "running" : "idle",
      chat_running: scenario.chatRunning,
      chat_stream: scenario.chatStream,
      chat_summary: scenario.chatSummary || "",
    },
  };
}

const params = new URLSearchParams(window.location.search);
const name = params.get("scenario") || "bootstrap";
const surface = params.get("surface") || "product";
const scenario = SCENARIOS[name] || SCENARIOS.bootstrap;
const workspace = workspaceFor(scenario);

// Expose the banned token so the test can assert it never appears in the DOM.
(window as unknown as { __BANNED__: string }).__BANNED__ = BANNED_ONLY;
(window as unknown as { __HARNESS_READY__: boolean }).__HARNESS_READY__ = false;

const root = createRoot(document.getElementById("root")!);

if (surface === "building") {
  root.render(
    <StrictMode>
      <Building
        idea="Sell widgets"
        state={{
          status: scenario.chatRunning ? "creating" : "ready",
          goal: "Sell widgets",
          businessSlug: "acme",
          businessName: "Acme",
          // Raw narration that includes plumbing — must stay OUT of the
          // conversational stream when the curated chat_stream is present.
          narration: [BANNED_LINE_TEXT, "Internal: writing actions/summary.ts"],
          terminal: ["Booting Coscale CEO…"],
          error: "",
          errorCode: 0,
        }}
        workspace={workspace}
        onDone={noop}
      />
    </StrictMode>,
  );
} else {
  root.render(
    <StrictMode>
      <Product
        business={business}
        workspace={workspace}
        creativeCredits={null}
        traction={null}
        tractionRange="M"
        theme="light"
        chatMessages={scenario.messages}
        sending={false}
        sessionRunning={scenario.chatRunning}
        onTheme={noop}
        onNav={noop}
        onLogout={noop}
        onOpenSettings={noop}
        onSendPrompt={noop}
        onStopPrompt={noop}
        onSaveChannelCreditBudgets={asyncNoop}
        onBuyCreativeCredits={asyncVoid}
        onTractionRangeChange={noop}
      />
    </StrictMode>,
  );
}

// Signal readiness on the next frame after render.
requestAnimationFrame(() => {
  requestAnimationFrame(() => {
    (window as unknown as { __HARNESS_READY__: boolean }).__HARNESS_READY__ = true;
  });
});
