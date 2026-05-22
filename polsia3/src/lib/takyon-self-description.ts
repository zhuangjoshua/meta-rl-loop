import fs from "node:fs/promises";
import path from "node:path";
import { takyonControlScopeTypes, takyonControlStates } from "./takyon-control";
import { listToolCapabilities, type ToolCapability } from "./tool-availability";
import { takyonWorkflowRegistry } from "./takyon-registry";
import { workerDispatchableWorkflowIds } from "./workflow-jobs";
import { businessWorkspaceContext, focusedBusinessWorkspaceExcerpts } from "./business-workspace";
import { listTakyonHarnessCommands, readTakyonHarnessSettings } from "./takyon-harness";

type SkillManifest = {
  namespace?: string;
  owner?: string;
  runtime?: string;
  skills?: Array<{
    id?: string;
    file?: string;
    workflow_id?: string | null;
  }>;
};

async function readSkillManifests() {
  const skillsRoot = path.join(process.cwd(), "skills");
  const entries = await fs.readdir(skillsRoot, { withFileTypes: true }).catch(() => []);
  const manifests: Array<{
    namespace: string;
    owner: string | null;
    runtime: string | null;
    skills: Array<{ id: string; file: string | null; workflow_id: string | null }>;
  }> = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const manifestPath = path.join(skillsRoot, entry.name, "manifest.json");
    const raw = await fs.readFile(manifestPath, "utf8").catch(() => null);
    if (!raw) continue;
    try {
      const parsed = JSON.parse(raw) as SkillManifest;
      manifests.push({
        namespace: parsed.namespace || entry.name,
        owner: parsed.owner ?? null,
        runtime: parsed.runtime ?? null,
        skills: (parsed.skills ?? []).map((skill) => ({
          id: skill.id || "",
          file: skill.file ?? null,
          workflow_id: skill.workflow_id ?? null
        })).filter((skill) => skill.id)
      });
    } catch {
      manifests.push({
        namespace: entry.name,
        owner: null,
        runtime: null,
        skills: []
      });
    }
  }

  return manifests;
}

function capabilitySummary(capabilities: ToolCapability[]) {
  return capabilities.map((capability) => ({
    key: capability.key,
    label: capability.label,
    category: capability.category,
    can_run: capability.canRun,
    reason: capability.reason,
    missing: capability.missing,
    setup: capability.setup,
    reports: capability.reports,
    docs_url: capability.docsUrl ?? null
  }));
}

export async function buildTakyonSelfDescription(input: {
  profileId: string;
  businessId?: string | null;
  terminalHelp: string;
  operatorText?: string | null;
}) {
  const [capabilities, skillManifests, workspace, harnessCommands, harnessSettings] = await Promise.all([
    listToolCapabilities({ businessId: input.businessId ?? null, profileId: input.profileId }),
    readSkillManifests(),
    input.businessId ? businessWorkspaceContext({ businessId: input.businessId, profileId: input.profileId }) : null,
    listTakyonHarnessCommands(),
    readTakyonHarnessSettings()
  ]);
  const focusedWorkspace = workspace && input.operatorText
    ? await focusedBusinessWorkspaceExcerpts({
        businessId: input.businessId!,
        text: input.operatorText,
        workspace,
        maxFiles: 8,
        maxBytes: 4_000
      })
    : null;

  return {
    terminal_mode: {
      control_input: "Slash-prefixed input is deterministic CLI control.",
      operator_input: "Plain text is operator conversation or instruction to the Takyon CEO agent.",
      command_help: input.terminalHelp
    },
    workflows: {
      dispatchable_workflow_ids: workerDispatchableWorkflowIds,
      registry: takyonWorkflowRegistry.map((workflow) => ({
        workflow_id: workflow.workflowId,
        lane: workflow.lane,
        priority: workflow.priority,
        priority_band: workflow.priority >= 100 ? "ceo" : workflow.priority >= 90 ? "build" : workflow.priority >= 60 ? "growth" : "observe",
        stages: workflow.stages,
        dependencies: workflow.dependencies,
        capabilities: { all: workflow.capabilityAll ?? [], any: workflow.capabilityAny ?? [] },
        repeatable: Boolean(workflow.repeatable),
        description: workflow.description
      }))
    },
    skills: skillManifests,
    harness: {
      root: harnessSettings.root,
      ui: harnessSettings.ui,
      commands: harnessCommands.map((command) => ({
        name: command.name,
        description: command.description,
        requires_business: command.requiresBusiness,
        priority_band: command.priorityBand,
        allowed_tools: command.allowedTools,
        path: command.path
      })),
      policy: harnessSettings.workspace
    },
    capabilities: capabilitySummary(capabilities),
    business_workspace: workspace
      ? {
          root: workspace.root,
          file_count: workspace.files.length,
          top_level_map: workspace.topLevelMap,
          boot_files: workspace.bootFiles,
          focused_file_excerpts: focusedWorkspace
            ? focusedWorkspace.excerpts.map((file) => ({
                path: file.path,
                bytes: file.bytes,
                truncated: file.truncated,
                error: "error" in file ? file.error : null,
                content: file.content.slice(0, 2200)
              }))
            : [],
          read_strategy: workspace.readStrategy,
          rule: "The CEO should inspect relevant workspace facts/receipts before deciding. Missing evidence means unknown. Prompt truncation never means a workspace path is absent."
        }
      : null,
    controls: {
      states: takyonControlStates,
      scope_types: takyonControlScopeTypes,
      meaning: "Paused or killed controls block work at global, business, campaign, workflow job, agent run, or provider scope."
    },
    execution_boundary: {
      ceo_agent: "Interprets operator intent, explains state, and wakes/autopilots work through the local Mac CEO runtime by default.",
      deterministic_runner: "Executes queued workflows, capability checks, budgets, provider calls, receipts, cron dispatch, cleanup, and kill switches.",
      code_builder: "Claude Code SDK is reserved for generated app/product file editing lanes, not terminal chat."
    },
    runtime_visibility: {
      terminal_command: "/runtimes",
      local_vps_command: "./takyon vps",
      cron_command: "./takyon cron status",
      note: "ARGON_RUNTIME_URL is optional remote infrastructure, not required for local terminal use."
    }
  };
}

export type TakyonSelfDescription = Awaited<ReturnType<typeof buildTakyonSelfDescription>>;
