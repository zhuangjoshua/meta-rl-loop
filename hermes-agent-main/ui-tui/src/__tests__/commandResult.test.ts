import { PassThrough } from 'stream'

import { renderSync } from '@takyon/ink'
import React from 'react'
import { describe, expect, it } from 'vitest'

import { CommandResultView } from '../components/commandResult.js'
import { stripAnsi } from '../lib/text.js'
import { DEFAULT_THEME } from '../theme.js'
import type { CommandResult } from '../types.js'

const ESC = String.fromCharCode(27)
const BEL = String.fromCharCode(7)
const CSI_RE = new RegExp(`${ESC}\\[[0-?]*[ -/]*[@-~]`, 'g')
const OSC_RE = new RegExp(`${ESC}\\][\\s\\S]*?(?:${BEL}|${ESC}\\\\)`, 'g')

const render = (r: CommandResult): string => {
  const stdout = new PassThrough()
  const stdin = new PassThrough()
  const stderr = new PassThrough()
  let output = ''

  Object.assign(stdout, { columns: 80, isTTY: false, rows: 24 })
  Object.assign(stdin, { isTTY: false })
  Object.assign(stderr, { isTTY: false })
  stdout.on('data', (chunk) => {
    output += chunk.toString()
  })

  const instance = renderSync(React.createElement(CommandResultView, { cols: 80, result: r, t: DEFAULT_THEME }), {
    patchConsole: false,
    stderr: stderr as NodeJS.WriteStream,
    stdin: stdin as NodeJS.ReadStream,
    stdout: stdout as NodeJS.WriteStream
  })

  instance.unmount()
  instance.cleanup()

  return output
    .replace(OSC_RE, '')
    .split('\n')
    .map((line) => stripAnsi(line).replace(CSI_RE, '').trimEnd())
    .join('\n')
}

describe('CommandResultView', () => {
  it('renders a kv result with title + keys, not raw JSON', () => {
    const out = render({
      kind: 'kv',
      title: 'RL status · acme',
      pairs: [
        ['episodes (open)', { text: '3' }],
        ['lessons total', { text: '5' }]
      ]
    })

    expect(out).toContain('RL status · acme')
    expect(out).toContain('episodes (open)')
    expect(out).toContain('3')
    expect(out).not.toContain('"kind"')
    expect(out).not.toContain("'kind'")
  })

  it('renders a table result through the markdown table engine', () => {
    const out = render({
      kind: 'table',
      title: 'lessons (2)',
      columns: ['status', 'claim'],
      rows: [
        [{ text: 'proven', tone: 'ok' }, { text: 'pain-first works' }],
        [{ text: 'candidate', tone: 'warn' }, { text: 'ship weekly digest' }]
      ]
    })

    expect(out).toContain('status')
    expect(out).toContain('pain-first works')
    expect(out).toContain('ship weekly digest')
  })

  it('renders an error result with the FULL message (anti-hide)', () => {
    const out = render({ kind: 'error', markdown: 'no lesson with id xyz-123', title: 'rl error' })

    expect(out).toContain('rl error')
    expect(out).toContain('no lesson with id xyz-123')
  })

  it('renders markdown policy text', () => {
    const out = render({ kind: 'markdown', markdown: 'Identity: B2C scheduling app', title: 'policy · acme' })

    expect(out).toContain('policy · acme')
    expect(out).toContain('B2C scheduling app')
  })
})
