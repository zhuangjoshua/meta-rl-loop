import { Box, Text } from '@takyon/ink'
import { memo, type ReactNode } from 'react'

import type { Theme } from '../theme.js'
import type { CommandCell, CommandMeter, CommandResult, Tone } from '../types.js'

import { Md } from './markdown.js'

// Single source for tone → theme color. Surfaces never use a raw hex.
function toneColor(t: Theme, tone?: Tone): string | undefined {
  switch (tone) {
    case 'accent':
      return t.color.accent
    case 'bad':
      return t.color.statusBad
    case 'muted':
      return t.color.muted
    case 'ok':
      return t.color.statusGood
    case 'warn':
      return t.color.statusWarn
    default:
      return undefined
  }
}

function Cell({ cell, t }: { cell: CommandCell; t: Theme }) {
  const color = toneColor(t, cell.tone) ?? (cell.mono ? t.color.shellDollar : t.color.text)

  return <Text color={color}>{cell.text}</Text>
}

// Build a markdown pipe-table and hand it to Md, which already does responsive column sizing
// and a vertical key:value fallback when the terminal is too narrow — so columns are never
// silently dropped (anti-hide). Per-cell tone is folded into the text as a leading glyph.
function tableMarkdown(columns: string[], rows: CommandCell[][]): string {
  const esc = (s: string) => s.replace(/\|/g, '\\|').replace(/\n/g, ' ')
  const glyph = (tone?: Tone) => (tone === 'ok' ? '● ' : tone === 'warn' ? '◐ ' : tone === 'bad' ? '○ ' : '')
  const header = `| ${columns.map(esc).join(' | ')} |`
  const sep = `| ${columns.map(() => '---').join(' | ')} |`
  const body = rows
    .map((r) => `| ${r.map((c) => `${glyph(c?.tone)}${esc(c?.text ?? '')}`).join(' | ')} |`)
    .join('\n')

  return `${header}\n${sep}\n${body}`
}

function Meter({ cols, m, t }: { cols?: number; m: CommandMeter; t: Theme }) {
  const width = Math.max(10, Math.min(28, (cols ?? 48) - 26))
  const pct = Math.max(0, Math.min(100, m.pct))
  const filled = Math.round((pct / 100) * width)

  return (
    <Text wrap="truncate-end">
      <Text color={t.color.muted}>{m.label.padEnd(18)}</Text>
      <Text color={toneColor(t, m.tone) ?? t.color.accent}>{'█'.repeat(filled)}</Text>
      <Text color={t.color.border}>{'░'.repeat(Math.max(0, width - filled))}</Text>
      <Text color={t.color.muted}> {Math.round(pct)}%</Text>
    </Text>
  )
}

function kvRows(pairs: [string, CommandCell][], t: Theme): ReactNode {
  const pad = Math.min(30, Math.max(0, ...pairs.map(([k]) => k.length)) + 2)

  return pairs.map(([k, v], i) => (
    <Text key={i} wrap="wrap">
      <Text color={t.color.muted}>{k.padEnd(pad)}</Text>
      <Cell cell={v} t={t} />
    </Text>
  ))
}

// 1:1 kind → renderer mapping. Each renderer returns null when its required
// fields are absent, so callers fall through to the isError+markdown fallback.
type KindRenderer = (r: CommandResult, cols: number | undefined, t: Theme) => ReactNode

const kindRenderers: Record<CommandResult['kind'], KindRenderer> = {
  diff: (r, cols, t) => (r.diff != null ? <Md cols={cols} t={t} text={`\`\`\`diff\n${r.diff}\n\`\`\``} /> : null),
  error: () => null,
  kv: (r, _cols, t) => (r.pairs ? kvRows(r.pairs, t) : null),
  list: (r, _cols, t) =>
    r.items
      ? r.items.map((it, i) => (
          <Text key={i} wrap="wrap">
            <Text color={t.color.muted}>• </Text>
            <Cell cell={it} t={t} />
          </Text>
        ))
      : null,
  log: (r, _cols, t) =>
    r.lines
      ? r.lines.map((ln, i) => (
          <Text color={toneColor(t, ln.tone) ?? t.color.text} key={i} wrap="wrap">
            {ln.text}
          </Text>
        ))
      : null,
  markdown: (r, cols, t) => (r.markdown != null ? <Md cols={cols} t={t} text={r.markdown} /> : null),
  status: (r, cols, t) =>
    r.sections ? (
      <Box flexDirection="column">
        {r.sections.map((sec, si) => (
          <Box flexDirection="column" key={si} marginTop={si ? 1 : 0}>
            {sec.title ? (
              <Text bold color={t.color.accent}>
                {sec.title}
              </Text>
            ) : null}
            {sec.pairs ? kvRows(sec.pairs, t) : null}
          </Box>
        ))}
        {r.meters?.length ? (
          <Box flexDirection="column" marginTop={r.sections.length ? 1 : 0}>
            {r.meters.map((m, mi) => (
              <Meter cols={cols} key={mi} m={m} t={t} />
            ))}
          </Box>
        ) : null}
      </Box>
    ) : null,
  table: (r, cols, t) =>
    r.columns && r.rows
      ? r.rows.length
        ? <Md cols={cols} t={t} text={tableMarkdown(r.columns, r.rows)} />
        : <Text color={t.color.muted}>(none)</Text>
      : null,
}

function CommandResultImpl({ cols, result: r, t }: { cols?: number; result: CommandResult; t: Theme }) {
  const isError = r.kind === 'error'
  const flush = r.kind === 'markdown' || r.kind === 'diff' || r.kind === 'table'

  let body: ReactNode = kindRenderers[r.kind](r, cols, t)

  if (body == null && isError && r.markdown != null) {
    body = <Md cols={cols} t={t} text={r.markdown} />
  }

  return (
    <Box
      borderColor={isError ? t.color.error : t.color.border}
      borderStyle="round"
      flexDirection="column"
      paddingX={2}
      paddingY={flush ? 0 : 1}
    >
      {r.title ? (
        <Text bold color={isError ? t.color.error : t.color.primary}>
          {isError ? '✗ ' : ''}
          {r.title}
        </Text>
      ) : null}
      <Box flexDirection="column" marginTop={r.title && !flush ? 1 : 0}>
        {body}
      </Box>
      {r.footer ? (
        <Box marginTop={1}>
          <Text color={t.color.muted}>{r.footer}</Text>
        </Box>
      ) : null}
    </Box>
  )
}

export const CommandResultView = memo(CommandResultImpl)
