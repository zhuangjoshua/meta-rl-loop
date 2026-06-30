export interface ActiveTool {
  context?: string
  id: string
  name: string
  startedAt?: number
}

export interface TodoItem {
  content: string
  id: string
  status: 'cancelled' | 'completed' | 'in_progress' | 'pending'
}

export interface ActivityItem {
  id: number
  text: string
  tone: 'error' | 'info' | 'warn'
}

export type SubagentStatus = 'completed' | 'error' | 'failed' | 'interrupted' | 'queued' | 'running' | 'timeout'

export interface SubagentProgress {
  apiCalls?: number
  costUsd?: number
  depth: number
  durationSeconds?: number
  filesRead?: string[]
  filesWritten?: string[]
  goal: string
  id: string
  index: number
  inputTokens?: number
  iteration?: number
  model?: string
  notes: string[]
  outputTail?: SubagentOutputEntry[]
  outputTokens?: number
  parentId: null | string
  reasoningTokens?: number
  startedAt?: number
  status: SubagentStatus
  summary?: string
  taskCount: number
  thinking: string[]
  toolCount: number
  tools: string[]
  toolsets?: string[]
}

export interface SubagentOutputEntry {
  isError: boolean
  preview: string
  tool: string
}

export interface SubagentNode {
  aggregate: SubagentAggregate
  children: SubagentNode[]
  item: SubagentProgress
}

export interface SubagentAggregate {
  activeCount: number
  costUsd: number
  descendantCount: number
  filesTouched: number
  hotness: number
  inputTokens: number
  maxDepthFromHere: number
  outputTokens: number
  totalDuration: number
  totalTools: number
}

export interface DelegationStatus {
  active: {
    depth?: number
    goal?: string
    model?: null | string
    parent_id?: null | string
    started_at?: number
    status?: string
    subagent_id?: string
    tool_count?: number
  }[]
  max_concurrent_children?: number
  max_spawn_depth?: number
  paused: boolean
}

export interface ApprovalReq {
  command: string
  description: string
}

export interface ConfirmReq {
  cancelLabel?: string
  confirmLabel?: string
  danger?: boolean
  detail?: string
  onConfirm: () => void
  title: string
}

export interface ClarifyReq {
  choices: string[] | null
  question: string
  requestId: string
}

export interface Msg {
  info?: SessionInfo
  kind?: 'diff' | 'intro' | 'panel' | 'result' | 'slash' | 'trail'
  panelData?: PanelData
  result?: CommandResult
  role: Role
  text: string
  thinking?: string
  thinkingTokens?: number
  toolTokens?: number
  tools?: string[]
  todos?: TodoItem[]
  todoIncomplete?: boolean
  todoCollapsedByDefault?: boolean
}

export type Role = 'assistant' | 'system' | 'tool' | 'user'
export type DetailsMode = 'hidden' | 'collapsed' | 'expanded'
export type ThinkingMode = 'collapsed' | 'truncated' | 'full'

// Per-section overrides for the agent details accordion.  Resolution order
// at lookup time is: explicit `display.sections.<name>` → built-in
// SECTION_DEFAULTS → global `details_mode`.  Today the built-in defaults
// expand `thinking`/`tools` and hide `activity`; `subagents` falls through
// to the global mode.  Any explicit value still wins for that one section.
export type SectionName = 'thinking' | 'tools' | 'subagents' | 'activity'
export type SectionVisibility = Partial<Record<SectionName, DetailsMode>>

export interface McpServerStatus {
  connected: boolean
  name: string
  tools: number
  transport: string
}

export interface SessionInfo {
  cwd?: string
  fast?: boolean
  lazy?: boolean
  mcp_servers?: McpServerStatus[]
  model: string
  profile_name?: string
  reasoning_effort?: string
  release_date?: string
  service_tier?: string
  skills: Record<string, string[]>
  system_prompt?: string
  tools: Record<string, string[]>
  update_behind?: number | null
  update_command?: string
  usage?: Usage
  version?: string
}

export interface Usage {
  calls: number
  compressions?: number
  context_max?: number
  context_percent?: number
  context_used?: number
  cost_status?: string
  cost_usd?: number
  input: number
  output: number
  reasoning?: number
  total: number
}

export interface SudoReq {
  requestId: string
}

export interface SecretReq {
  envVar: string
  prompt: string
  requestId: string
}

export interface PanelData {
  sections: PanelSection[]
  title: string
}

export interface PanelSection {
  items?: string[]
  rows?: [string, string][]
  text?: string
  title?: string
}

// ── Structured command results (the keystone: commands return shape, not a flattened string) ──
// Semantic tone → theme status colors (resolved in components/commandResult.tsx). Never a raw hex.
export type Tone = 'accent' | 'bad' | 'muted' | 'ok' | 'warn'

export interface CommandCell {
  href?: string
  mono?: boolean
  text: string
  tone?: Tone
}

export interface CommandResultSection {
  pairs?: [string, CommandCell][]
  title?: string
}

export interface CommandMeter {
  label: string
  pct: number
  tone?: Tone
}

// One discriminated shape the TUI renders richly. `kind` selects the layout; the matching
// payload field carries the data. Producers (Python gateway) build this from canonical state;
// the renderer never truncates the substance (anti-hide).
export interface CommandResult {
  columns?: string[]
  diff?: string
  footer?: string
  items?: CommandCell[]
  kind: 'diff' | 'error' | 'kv' | 'list' | 'log' | 'markdown' | 'status' | 'table'
  lines?: { text: string; tone?: Tone }[]
  markdown?: string
  meters?: CommandMeter[]
  pairs?: [string, CommandCell][]
  rows?: CommandCell[][]
  sections?: CommandResultSection[]
  title?: string
}

export interface SlashCatalog {
  canon: Record<string, string>
  categories: SlashCategory[]
  pairs: [string, string][]
  skillCount: number
  sub: Record<string, string[]>
}

export interface SlashCategory {
  name: string
  pairs: [string, string][]
}
