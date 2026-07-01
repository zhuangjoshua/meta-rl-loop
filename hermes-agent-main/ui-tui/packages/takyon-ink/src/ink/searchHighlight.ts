import { cellAtIndex, CellWidth, type Screen, setCellStyleId, type StylePool } from './screen.js'

/**
 * Highlight all visible occurrences of `query` in the screen buffer by
 * inverting cell styles (SGR 7). Post-render, same damage-tracking machinery
 * as applySelectionOverlay — the diff picks up highlighted cells as ordinary
 * changes, LogUpdate stays a pure diff engine.
 *
 * Case-insensitive. Handles wide characters (CJK, emoji) by building a
 * col-of-char map per row — the Nth character isn't at col N when wide chars
 * are present (each occupies 2 cells: head + SpacerTail).
 *
 * This ONLY inverts — there is no "current match" logic here. The yellow
 * current-match overlay is handled separately by applyPositionedHighlight
 * (render-to-screen.ts), which writes on top using positions scanned from
 * the target message's DOM subtree.
 *
 * Returns true if any match was highlighted (damage gate — caller forces
 * full-frame damage when true).
 */
export function applySearchHighlight(screen: Screen, query: string, stylePool: StylePool): boolean {
  if (!query) {
    return false
  }

  const lq = query.toLowerCase()
  const qlen = lq.length
  const w = screen.width
  const noSelect = screen.noSelect
  const height = screen.height

  let applied = false

  for (let row = 0; row < height; row++) {
    const rowOff = row * w
    // Build row text (already lowercased) + code-unit→cell-index map.
    // Three skip conditions, all aligned with setCellStyleId /
    // extractRowText (selection.ts):
    //   - SpacerTail: 2nd cell of a wide char, no char of its own
    //   - SpacerHead: end-of-line padding when a wide char wraps
    //   - noSelect: gutters (⎿, line numbers) — same exclusion as
    //     applySelectionOverlay. "Highlight what you see" still holds for
    //     content; gutters aren't search targets.
    // Lowercasing per-char (not on the joined string at the end) means
    // codeUnitToCell maps positions in the LOWERCASED text — U+0130
    // (Turkish İ) lowercases to 2 code units, so lowering the joined
    // string would desync indexOf positions from the map.
    // Cheap first pass: build only the lowercased row text (no colOf /
    // codeUnitToCell arrays) so we can indexOf and bail out early on the
    // majority of rows that hold no match. The full cell-index maps are
    // only constructed when a match is confirmed below.
    let text = ''

    for (let col = 0; col < w; col++) {
      const idx = rowOff + col
      const cell = cellAtIndex(screen, idx)

      if (cell.width === CellWidth.SpacerTail || cell.width === CellWidth.SpacerHead || noSelect[idx] === 1) {
        continue
      }

      text += cell.char.toLowerCase()
    }

    let pos = text.indexOf(lq)

    if (pos < 0) {
      continue
    }

    // Match confirmed on this row — now build the cell-index maps needed
    // to translate lowercased-text positions back to screen cells.
    const colOf: number[] = []
    const codeUnitToCell: number[] = []

    for (let col = 0; col < w; col++) {
      const idx = rowOff + col
      const cell = cellAtIndex(screen, idx)

      if (cell.width === CellWidth.SpacerTail || cell.width === CellWidth.SpacerHead || noSelect[idx] === 1) {
        continue
      }

      const lc = cell.char.toLowerCase()
      const cellIdx = colOf.length

      for (let i = 0; i < lc.length; i++) {
        codeUnitToCell.push(cellIdx)
      }

      colOf.push(col)
    }

    while (pos >= 0) {
      applied = true
      const startCi = codeUnitToCell[pos]!
      const endCi = codeUnitToCell[pos + qlen - 1]!

      for (let ci = startCi; ci <= endCi; ci++) {
        const col = colOf[ci]!
        const cell = cellAtIndex(screen, rowOff + col)
        setCellStyleId(screen, col, row, stylePool.withInverse(cell.styleId))
      }

      // Non-overlapping advance (less/vim/grep/Ctrl+F). pos+1 would find
      // 'aa' at 0 AND 1 in 'aaa' → double-invert cell 1.
      pos = text.indexOf(lq, pos + qlen)
    }
  }

  return applied
}
