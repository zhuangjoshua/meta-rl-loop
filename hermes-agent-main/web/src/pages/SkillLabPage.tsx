import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  RotateCcw,
  Send,
  Sparkles,
  Wrench,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Markdown } from "@/components/Markdown";
import { ToolCall, type ToolEntry } from "@/components/ToolCall";
import { usePageHeader } from "@/contexts/usePageHeader";
import { GatewayClient } from "@/lib/gatewayClient";
import { cn } from "@/lib/utils";

type SkillCatalogEntry = {
  name: string;
  slug: string;
  description: string;
  category: string;
  owns: string;
  path: string;
};

type SkillLabMessage = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  streaming?: boolean;
  error?: boolean;
};

type SkillLabCreateResponse = {
  session_id?: string;
  takyon_skill_lab?: {
    enabled?: boolean;
    skills?: string[];
  };
};

type SkillLabCreateBusinessResponse = {
  business_slug?: string;
  business_name?: string;
  mode?: string;
  dev_mode?: boolean;
};

type SkillLabCatalogResponse = {
  skills?: SkillCatalogEntry[];
};

type SkillLabStatusResponse = {
  status?: string;
};

type SkillLabBusiness = {
  slug: string;
  name: string;
  mode: string;
};

function trimText(value: unknown) {
  return String(value || "").trim();
}

function buildSkillLabBusinessSeed(skill: SkillCatalogEntry | null) {
  const stamp = new Date()
    .toISOString()
    .replace(/[-:]/g, "")
    .replace(/\..*$/, "")
    .toLowerCase();
  const slug = trimText(skill?.slug || "skill-lab") || "skill-lab";
  const name = `Dev Skill Lab ${slug} ${stamp}`;
  const goal = skill
    ? `Development test lab for ${skill.name}. Use the normal Takyon business backend with CEO bootstrap disabled.`
    : "Development test lab. Use the normal Takyon business backend with CEO bootstrap disabled.";
  return { name, goal };
}

function liveBadgeTone(
  state: string,
): "default" | "secondary" | "destructive" | "outline" {
  switch (state) {
    case "open":
      return "default";
    case "connecting":
      return "secondary";
    case "error":
      return "destructive";
    default:
      return "outline";
  }
}

export default function SkillLabPage() {
  const gatewayRef = useRef<GatewayClient | null>(null);
  const sessionIdRef = useRef("");
  const sessionSkillRef = useRef("");
  const sessionBusinessRef = useRef("");
  const assistantMessageIdRef = useRef("");
  const transcriptRef = useRef<HTMLDivElement | null>(null);

  const [gatewayState, setGatewayState] = useState("idle");
  const [loadingCatalog, setLoadingCatalog] = useState(true);
  const [catalogError, setCatalogError] = useState("");
  const [catalog, setCatalog] = useState<SkillCatalogEntry[]>([]);
  const [selectedSkill, setSelectedSkill] = useState("");
  const [loadedSkills, setLoadedSkills] = useState<string[]>([]);
  const [devBusiness, setDevBusiness] = useState<SkillLabBusiness | null>(null);
  const [creatingBusiness, setCreatingBusiness] = useState(false);
  const [composer, setComposer] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [messages, setMessages] = useState<SkillLabMessage[]>([
    {
      id: "skill-lab-intro",
      role: "system",
      text:
        "Skill Lab Dev Mode creates a real Takyon business with CEO bootstrap disabled, then lets you chat through the normal backend and stream the real tool activity.",
    },
  ]);
  const [tools, setTools] = useState<ToolEntry[]>([]);
  const [statusLines, setStatusLines] = useState<string[]>([]);
  const { setAfterTitle, setEnd } = usePageHeader();

  const selectedSkillEntry = useMemo(
    () => catalog.find((item) => item.name === selectedSkill) || null,
    [catalog, selectedSkill],
  );

  const ensureGateway = useCallback(async () => {
    if (!gatewayRef.current) {
      gatewayRef.current = new GatewayClient();
    }
    const gateway = gatewayRef.current;
    await gateway.connect();
    return gateway;
  }, []);

  const appendStatus = useCallback((text: unknown) => {
    const line = trimText(text);
    if (!line) return;
    setStatusLines((current) => {
      if (current[current.length - 1] === line) return current;
      return [...current, line].slice(-16);
    });
  }, []);

  const ensureAssistantMessage = useCallback(() => {
    if (assistantMessageIdRef.current) return assistantMessageIdRef.current;
    const id = `assistant-${Date.now()}`;
    assistantMessageIdRef.current = id;
    setMessages((current) => [
      ...current,
      { id, role: "assistant", text: "", streaming: true },
    ]);
    return id;
  }, []);

  const appendAssistantDelta = useCallback(
    (text: unknown) => {
      const delta = String(text || "");
      if (!delta) return;
      const id = ensureAssistantMessage();
      setMessages((current) =>
        current.map((message) =>
          message.id === id
            ? { ...message, text: `${message.text}${delta}`, streaming: true }
            : message,
        ),
      );
    },
    [ensureAssistantMessage],
  );

  const completeAssistantMessage = useCallback(
    (text?: unknown, error = false) => {
      const finalText = trimText(text);
      const id =
        assistantMessageIdRef.current || (finalText ? ensureAssistantMessage() : "");
      if (!id) return;
      setMessages((current) =>
        current.map((message) =>
          message.id === id
            ? {
                ...message,
                text: finalText || message.text,
                streaming: false,
                error,
              }
            : message,
        ),
      );
      assistantMessageIdRef.current = "";
    },
    [ensureAssistantMessage],
  );

  const closeSession = useCallback(async () => {
    const gateway = gatewayRef.current;
    const sessionId = sessionIdRef.current;
    sessionIdRef.current = "";
    sessionSkillRef.current = "";
    sessionBusinessRef.current = "";
    assistantMessageIdRef.current = "";
    setLoadedSkills([]);
    setDevBusiness(null);
    setCreatingBusiness(false);
    if (!gateway || !sessionId) return;
    try {
      await gateway.request("session.close", { session_id: sessionId }, 15_000);
    } catch {
      // best-effort cleanup only
    }
  }, []);

  const resetLab = useCallback(
    async (note?: string) => {
      await closeSession();
      setMessages(() => {
        const base: SkillLabMessage[] = [
          {
            id: "skill-lab-intro",
            role: "system",
            text:
              "Skill Lab Dev Mode creates a real Takyon business with CEO bootstrap disabled, then lets you chat through the normal backend and stream the real tool activity.",
          },
        ];
        const next = trimText(note);
        if (next) {
          base.push({
            id: `system-${Date.now()}`,
            role: "system",
            text: next,
          });
        }
        return base;
      });
      setTools([]);
      setStatusLines([]);
      setDevBusiness(null);
      setCreatingBusiness(false);
      setSubmitting(false);
    },
    [closeSession],
  );

  const ensureSession = useCallback(async () => {
    const desiredSkill = trimText(selectedSkill);
    if (sessionIdRef.current && sessionSkillRef.current === desiredSkill) {
      return sessionIdRef.current;
    }
    if (sessionIdRef.current && sessionSkillRef.current !== desiredSkill) {
      await closeSession();
      setMessages((current) => [
        ...current,
        {
          id: `system-${Date.now()}`,
          role: "system",
          text: desiredSkill
            ? `Started a fresh Skill Lab Dev Mode session for ${desiredSkill}.`
            : "Started a fresh Skill Lab Dev Mode session.",
        },
      ]);
      setTools([]);
      setStatusLines([]);
    }
    const gateway = await ensureGateway();
    const result = await gateway.request<SkillLabCreateResponse>("session.create", {
      cols: 100,
      _takyon_skill_lab_skills: desiredSkill ? [desiredSkill] : [],
    });
    const sessionId = trimText(result?.session_id);
    if (!sessionId) {
      throw new Error("Skill Lab session did not return a session id.");
    }
    sessionIdRef.current = sessionId;
    sessionSkillRef.current = desiredSkill;
    setLoadedSkills(
      Array.isArray(result?.takyon_skill_lab?.skills)
        ? result.takyon_skill_lab.skills.map((item) => trimText(item)).filter(Boolean)
        : [],
    );
    return sessionId;
  }, [closeSession, ensureGateway, selectedSkill]);

  const ensureLabBusiness = useCallback(async () => {
    if (sessionBusinessRef.current && devBusiness) {
      return sessionBusinessRef.current;
    }
    const sessionId = await ensureSession();
    const gateway = await ensureGateway();
    const seed = buildSkillLabBusinessSeed(selectedSkillEntry);
    setCreatingBusiness(true);
    appendStatus("Creating a real dev business with CEO bootstrap disabled.");
    try {
      const result = await gateway.request<SkillLabCreateBusinessResponse>(
        "takyon.dashboard.create",
        {
          session_id: sessionId,
          business_name: seed.name,
          goal: seed.goal,
          mode: "live",
          bootstrap: false,
        },
        60_000,
      );
      const slug = trimText(result?.business_slug);
      if (!slug) {
        throw new Error("Skill Lab dev business creation did not return a business slug.");
      }
      const business = {
        slug,
        name: trimText(result?.business_name) || slug,
        mode: trimText(result?.mode) || "live",
      };
      sessionBusinessRef.current = slug;
      setDevBusiness(business);
      appendStatus(`Dev business ready: ${business.slug}`);
      setMessages((current) => [
        ...current,
        {
          id: `system-dev-business-${Date.now()}`,
          role: "system",
          text: `Dev Mode business ready: \`${business.slug}\`. CEO bootstrap is disabled for this lab session.`,
        },
      ]);
      return slug;
    } finally {
      setCreatingBusiness(false);
    }
  }, [
    appendStatus,
    devBusiness,
    ensureGateway,
    ensureSession,
    selectedSkillEntry,
  ]);

  useEffect(() => {
    const gateway = gatewayRef.current || new GatewayClient();
    gatewayRef.current = gateway;

    const offState = gateway.onState((state) => setGatewayState(state));
    const offStart = gateway.on("message.start", () => {
      setSubmitting(true);
      ensureAssistantMessage();
    });
    const offDelta = gateway.on("message.delta", (event) => {
      appendAssistantDelta((event.payload as { text?: string } | undefined)?.text);
    });
    const offComplete = gateway.on("message.complete", (event) => {
      completeAssistantMessage(
        (event.payload as { text?: string } | undefined)?.text,
      );
      setSubmitting(false);
    });
    const offThinking = gateway.onAny((event) => {
      if (!["thinking.delta", "reasoning.delta", "reasoning.available"].includes(event.type)) {
        return;
      }
      appendStatus((event.payload as { text?: string } | undefined)?.text);
    });
    const offStatus = gateway.on("status.update", (event) => {
      const payload = (event.payload as { text?: string } | undefined) || {};
      appendStatus(payload.text);
    });
    const offToolStart = gateway.on("tool.start", (event) => {
      const payload =
        (event.payload as {
          tool_id?: string;
          name?: string;
          context?: string;
        }) || {};
      const toolId = trimText(payload.tool_id || payload.name || `${Date.now()}`);
      const name = trimText(payload.name || "tool");
      const context = trimText(payload.context);
      setTools((current) => [
        {
          kind: "tool",
          id: `${toolId}:${Date.now()}`,
          tool_id: toolId,
          name,
          context,
          status: "running",
          startedAt: Date.now(),
        },
        ...current,
      ]);
    });
    const offToolProgress = gateway.on("tool.progress", (event) => {
      const payload =
        (event.payload as {
          tool_id?: string;
          text?: string;
          message?: string;
          preview?: string;
        }) || {};
      const toolId = trimText(payload.tool_id);
      if (!toolId) return;
      const preview = trimText(payload.preview || payload.text || payload.message);
      if (!preview) return;
      setTools((current) =>
        current.map((tool) =>
          tool.tool_id === toolId && tool.status === "running"
            ? { ...tool, preview }
            : tool,
        ),
      );
    });
    const offToolComplete = gateway.on("tool.complete", (event) => {
      const payload =
        (event.payload as {
          tool_id?: string;
          name?: string;
          summary?: string;
          error?: string;
          inline_diff?: string;
        }) || {};
      const toolId = trimText(payload.tool_id || payload.name);
      if (!toolId) return;
      setTools((current) =>
        current.map((tool) => {
          if (tool.tool_id !== toolId || tool.status !== "running") return tool;
          const error = trimText(payload.error);
          return {
            ...tool,
            summary: trimText(payload.summary),
            error,
            inline_diff: trimText(payload.inline_diff),
            status: error ? "error" : "done",
            completedAt: Date.now(),
          };
        }),
      );
    });
    const offError = gateway.on("error", (event) => {
      const message =
        trimText((event.payload as { message?: string } | undefined)?.message) ||
        "The live stream reported an error.";
      completeAssistantMessage(message, true);
      setMessages((current) => [
        ...current,
        {
          id: `error-${Date.now()}`,
          role: "system",
          text: message,
          error: true,
        },
      ]);
      setSubmitting(false);
    });

    return () => {
      offState();
      offStart();
      offDelta();
      offComplete();
      offThinking();
      offStatus();
      offToolStart();
      offToolProgress();
      offToolComplete();
      offError();
      void closeSession();
      gateway.close();
      gatewayRef.current = null;
    };
  }, [
    appendAssistantDelta,
    appendStatus,
    closeSession,
    completeAssistantMessage,
    ensureAssistantMessage,
  ]);

  useEffect(() => {
    void ensureGateway()
      .then((gateway) =>
        gateway.request<SkillLabCatalogResponse>("takyon.skill_lab.catalog", {}, 30_000),
      )
      .then((result) => {
        const skills = Array.isArray(result?.skills) ? result.skills : [];
        setCatalog(skills);
        setSelectedSkill((current) => current || trimText(skills[0]?.name));
        setCatalogError("");
      })
      .catch((error) => {
        setCatalog([]);
        setCatalogError(
          error instanceof Error ? error.message : "Unable to load Takyon skills.",
        );
      })
      .finally(() => setLoadingCatalog(false));
  }, [ensureGateway]);

  useEffect(() => {
    if (!transcriptRef.current) return;
    transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [messages, tools, statusLines]);

  useEffect(() => {
    if (sessionSkillRef.current && sessionSkillRef.current !== trimText(selectedSkill)) {
      setLoadedSkills([]);
      setDevBusiness(null);
      sessionBusinessRef.current = "";
    }
  }, [selectedSkill]);

  useEffect(() => {
    setAfterTitle(
      <Badge tone="outline" className="normal-case">
        Dev Mode
      </Badge>,
    );
    setEnd(
      <Badge
        tone={liveBadgeTone(gatewayState)}
        className="normal-case"
      >
        {gatewayState}
      </Badge>,
    );
    return () => {
      setAfterTitle(null);
      setEnd(null);
    };
  }, [gatewayState, setAfterTitle, setEnd]);

  const sendPrompt = useCallback(async () => {
    const text = trimText(composer);
    if (!text || submitting) return;
    setComposer("");
    setMessages((current) => [
      ...current,
      { id: `user-${Date.now()}`, role: "user", text },
    ]);
    setSubmitting(true);
    try {
      const sessionId = await ensureSession();
      await ensureLabBusiness();
      const gateway = await ensureGateway();
      await gateway.request<SkillLabStatusResponse>("prompt.submit", {
        session_id: sessionId,
        text,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Unable to send Skill Lab Dev Mode prompt.";
      completeAssistantMessage(message, true);
      setMessages((current) => [
        ...current,
        { id: `send-error-${Date.now()}`, role: "system", text: message, error: true },
      ]);
      setSubmitting(false);
    }
  }, [composer, completeAssistantMessage, ensureGateway, ensureLabBusiness, ensureSession, submitting]);

  return (
    <div className="mx-auto flex h-full min-h-0 w-full max-w-7xl flex-col gap-4 pb-8">
      <div className="grid min-h-0 gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="border-border/70 bg-background/80">
          <CardHeader className="space-y-2">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              <CardTitle className="text-base normal-case">Skill Lab Dev Mode</CardTitle>
            </div>
            <p className="text-sm normal-case text-muted-foreground">
              Creates a real Takyon business with CEO bootstrap disabled, then uses the normal Hermes chat/backend path for skill testing.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex flex-col gap-2">
              <span className="text-xs font-medium uppercase tracking-[0.18em] text-muted-foreground">
                active skill
              </span>
              <select
                className="h-10 rounded-md border border-border bg-background px-3 text-sm normal-case"
                disabled={loadingCatalog || !catalog.length}
                value={selectedSkill}
                onChange={(event) => setSelectedSkill(event.target.value)}
              >
                {catalog.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                  </option>
                ))}
              </select>
            </label>

            {loadingCatalog ? (
              <div className="flex items-center gap-2 text-sm normal-case text-muted-foreground">
                <Spinner />
                <span>Loading Takyon skills…</span>
              </div>
            ) : catalogError ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/[0.05] p-3 text-sm normal-case text-destructive">
                {catalogError}
              </div>
            ) : selectedSkillEntry ? (
              <div className="space-y-3 rounded-md border border-border/70 bg-muted/20 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="outline" className="normal-case">
                    {selectedSkillEntry.category || "takyon"}
                  </Badge>
                  <Badge tone="outline" className="normal-case">
                    skills.fourmanifold.com only
                  </Badge>
                  {loadedSkills.length > 0 && (
                    <Badge tone="secondary" className="normal-case">
                      preloaded
                    </Badge>
                  )}
                </div>
                <p className="text-sm normal-case text-foreground/90">
                  {selectedSkillEntry.description || "No description provided."}
                </p>
                {selectedSkillEntry.owns && (
                  <p className="text-xs normal-case text-muted-foreground">
                    Owns: {selectedSkillEntry.owns}
                  </p>
                )}
              </div>
            ) : null}

            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                outlined
                className="normal-case"
                onClick={() => void resetLab("Started a fresh Skill Lab Dev Mode session.")}
              >
                <RotateCcw className="mr-2 h-3.5 w-3.5" />
                new session
              </Button>
            </div>

            <div className="rounded-md border border-border/70 bg-muted/20 p-3 text-xs normal-case text-muted-foreground">
              Each lab session creates a real live Takyon dev business with bootstrap disabled and no automatic CEO wake scheduling.
            </div>

            {devBusiness ? (
              <div className="space-y-2 rounded-md border border-border/70 bg-background/70 p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone="secondary" className="normal-case">
                    dev business
                  </Badge>
                  <Badge tone="outline" className="normal-case">
                    {devBusiness.mode}
                  </Badge>
                </div>
                <p className="text-sm normal-case text-foreground/90">
                  {devBusiness.name}
                </p>
                <p className="text-xs normal-case text-muted-foreground">
                  Scope: {devBusiness.slug}
                </p>
              </div>
            ) : (
              <div className="rounded-md border border-dashed border-border/60 px-3 py-4 text-xs normal-case text-muted-foreground">
                Your first prompt will create a real dev business and then continue through the normal Takyon backend.
              </div>
            )}

            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-muted-foreground">
                <Activity className="h-3.5 w-3.5" />
                live status
              </div>
              <div className="space-y-2">
                {statusLines.length ? (
                  statusLines.map((line, index) => (
                    <div
                      key={`${index}:${line}`}
                      className="rounded-md border border-border/60 bg-muted/10 px-3 py-2 text-xs normal-case text-muted-foreground"
                    >
                      {line}
                    </div>
                  ))
                ) : (
                  <div className="rounded-md border border-dashed border-border/60 px-3 py-4 text-xs normal-case text-muted-foreground">
                    Activity lines will appear here while the selected skill works.
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card className="flex min-h-[70vh] min-w-0 flex-col border-border/70 bg-background/80">
          <CardHeader className="border-b border-border/60">
            <div className="flex items-center justify-between gap-3">
              <div className="min-w-0">
                <CardTitle className="text-base normal-case">
                  {selectedSkillEntry?.name || "Skill session"}
                </CardTitle>
                <p className="mt-1 text-sm normal-case text-muted-foreground">
                  Same streaming gateway, real business scope, CEO bootstrap disabled.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone="outline" className="normal-case">
                  {loadedSkills.length ? loadedSkills.join(", ") : "not yet started"}
                </Badge>
                {creatingBusiness && (
                  <Badge tone="secondary" className="normal-case">
                    creating dev business
                  </Badge>
                )}
              </div>
            </div>
          </CardHeader>

          <CardContent className="grid min-h-0 flex-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="flex min-h-0 flex-col gap-3">
              <div
                ref={transcriptRef}
                className="min-h-0 flex-1 overflow-y-auto rounded-md border border-border/60 bg-muted/10 p-4"
              >
                <div className="space-y-4">
                  {messages.map((message) => (
                    <div
                      key={message.id}
                      className={cn(
                        "max-w-[90%] rounded-xl px-4 py-3 text-sm normal-case shadow-sm",
                        message.role === "user"
                          ? "ml-auto border border-primary/30 bg-primary/[0.08]"
                          : message.role === "system"
                            ? "border border-border/60 bg-background/80"
                            : "border border-border/60 bg-background/90",
                        message.error && "border-destructive/50 bg-destructive/[0.05]",
                      )}
                    >
                      <div className="mb-2 text-[0.65rem] font-medium uppercase tracking-[0.18em] text-muted-foreground">
                        {message.role === "user"
                          ? "operator"
                          : message.role === "system"
                            ? "system"
                            : "agent"}
                      </div>
                      <Markdown
                        content={message.text}
                        streaming={Boolean(message.streaming)}
                      />
                    </div>
                  ))}
                </div>
              </div>

              <div className="flex gap-2">
                <Input
                  value={composer}
                  className="h-11 normal-case"
                  placeholder="Ask the selected skill to do something real in this dev business…"
                  onChange={(event) => setComposer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void sendPrompt();
                    }
                  }}
                />
                <Button
                  className="h-11 normal-case"
                  disabled={!trimText(composer) || submitting || loadingCatalog}
                  onClick={() => void sendPrompt()}
                >
                  {submitting ? (
                    <Spinner className="mr-2" />
                  ) : (
                    <Send className="mr-2 h-4 w-4" />
                  )}
                  send
                </Button>
              </div>
            </div>

            <div className="flex min-h-0 flex-col gap-3">
              <div className="flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-muted-foreground">
                <Wrench className="h-3.5 w-3.5" />
                tool activity
              </div>
              <div className="min-h-0 flex-1 space-y-2 overflow-y-auto rounded-md border border-border/60 bg-muted/10 p-3">
                {tools.length ? (
                  tools.map((tool) => <ToolCall key={tool.id} tool={tool} />)
                ) : (
                  <div className="rounded-md border border-dashed border-border/60 px-3 py-4 text-xs normal-case text-muted-foreground">
                    Tool calls will stream here as the skill works.
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
