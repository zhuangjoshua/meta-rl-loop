import type { OverlayState } from './overlayStore.js'

/**
 * Pure pager keymap extracted from useInputHandlers' useInput callback.
 *
 * The pager sub-mode is an independent keymap (arrow/j/k line moves, PageUp/b
 * page-back, g/G top/bottom, Enter/Space/PageDown page-forward-or-close, and
 * Esc/Ctrl+C/q to dismiss).  Keeping it here isolates that concern from the
 * main input dispatcher and makes it independently testable.
 *
 * `handlePagerKey` returns a `patchOverlayState` updater to apply, or
 * `PAGER_UNHANDLED` when the key produced no state change (mirroring the
 * original inlined code, which `return`ed without touching state for
 * unrecognized keys while the pager was up).
 */

export const PAGER_UNHANDLED = Symbol('pager-unhandled')

export type PagerUpdater = (prev: OverlayState) => OverlayState

export type PagerKeyResult = PagerUpdater | typeof PAGER_UNHANDLED

type PagerKey = {
  ctrl: boolean
  downArrow: boolean
  escape: boolean
  pageDown: boolean
  pageUp: boolean
  return: boolean
  upArrow: boolean
}

const isCtrlC = (key: PagerKey, ch: string) => key.ctrl && ch.toLowerCase() === 'c'

export function handlePagerKey(ch: string, key: PagerKey, pagerPageSize: number): PagerKeyResult {
  if (key.escape || isCtrlC(key, ch) || ch === 'q') {
    return prev => ({ ...prev, pager: null })
  }

  const move =
    (delta: number | 'top' | 'bottom'): PagerUpdater =>
    prev => {
      if (!prev.pager) {
        return prev
      }

      const { lines, offset } = prev.pager
      const max = Math.max(0, lines.length - pagerPageSize)
      const step = delta === 'top' ? -lines.length : delta === 'bottom' ? lines.length : delta
      const next = Math.max(0, Math.min(offset + step, max))

      return next === offset ? prev : { ...prev, pager: { ...prev.pager, offset: next } }
    }

  if (key.upArrow || ch === 'k') {
    return move(-1)
  }

  if (key.downArrow || ch === 'j') {
    return move(1)
  }

  if (key.pageUp || ch === 'b') {
    return move(-pagerPageSize)
  }

  if (ch === 'g') {
    return move('top')
  }

  if (ch === 'G') {
    return move('bottom')
  }

  if (key.return || ch === ' ' || key.pageDown) {
    return prev => {
      if (!prev.pager) {
        return prev
      }

      const { lines, offset } = prev.pager
      const max = Math.max(0, lines.length - pagerPageSize)

      // Auto-close only when already at the last page — otherwise clamp
      // to `max` so the offset matches what the line/page-back handlers
      // can reach (prevents a snap-back jump on the next ↑/↓/PgUp).
      return offset >= max
        ? { ...prev, pager: null }
        : { ...prev, pager: { ...prev.pager, offset: Math.min(offset + pagerPageSize, max) } }
    }
  }

  return PAGER_UNHANDLED
}
