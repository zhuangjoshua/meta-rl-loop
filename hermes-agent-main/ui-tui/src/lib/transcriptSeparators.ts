import type { Msg } from '../types.js'

/**
 * Index of the first user-role message in a transcript, or -1 when none
 * exists yet. Every user message *after* this one gets a separator dash
 * rendered above it so multi-turn transcripts segment visually by turn.
 *
 * Single source of truth shared by the renderer (appLayout.tsx) and the
 * virtual-height estimator (useMainApp.ts) so the two can't drift.
 */
export function firstUserIndex(items: readonly { role?: string }[]): number {
  return items.findIndex(m => m.role === 'user')
}

/**
 * Whether the row at ``index`` should render a turn-separator dash above it:
 * it's a user message, at least one earlier user message exists, and this
 * one comes after the first user message.
 */
export function hasSeparatorAt(msg: Pick<Msg, 'role'>, index: number, firstUserIdx: number): boolean {
  return msg.role === 'user' && firstUserIdx >= 0 && index > firstUserIdx
}
