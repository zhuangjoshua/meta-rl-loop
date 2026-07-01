import type { Msg, TodoItem } from '../types.js'

export const countPendingTodos = (todos: readonly TodoItem[]) =>
  todos.filter(todo => todo.status === 'in_progress' || todo.status === 'pending').length

export const isTodoDone = (todos: readonly TodoItem[]) =>
  todos.length > 0 && todos.every(todo => todo.status === 'completed' || todo.status === 'cancelled')

export const isToolShelfMessage = (msg: Msg | undefined) =>
  Boolean(msg?.kind === 'trail' && !msg.text && !msg.thinking?.trim() && msg.tools?.length)

export const canHoldToolShelf = (msg: Msg | undefined) =>
  Boolean(msg?.kind === 'trail' && !msg.text && (msg.thinking?.trim() || msg.tools?.length))

export const mergeToolShelfInto = (target: Msg, source: Msg): Msg => ({
  ...target,
  tools: [...(target.tools ?? []), ...(source.tools ?? [])]
})

const isBarrierMessage = (msg: Msg | undefined) => {
  if (!msg) {
    return true
  }

  // Assistant text, user input, intro/panel rows all terminate the shelf.
  if (msg.kind === 'intro' || msg.kind === 'panel' || msg.kind === 'diff') {
    return true
  }

  if (msg.role && msg.role !== 'system') {
    return true
  }

  if (msg.text) {
    return true
  }

  return false
}

const isToolCarryingTrail = (msg: Msg | undefined) => Boolean(msg?.kind === 'trail' && !msg.text && msg.tools?.length)

export const appendToolShelfMessage = (prev: readonly Msg[], msg: Msg): Msg[] => {
  if (!isToolShelfMessage(msg)) {
    return [...prev, msg]
  }

  // Fast-path the common streaming case: inspect only the tail before
  // committing to the full O(n) backward scan. The loop below starts at the
  // last element, so these checks are semantically identical to it — they
  // just avoid touching the rest of the array when the answer is decided by
  // the final message alone.
  const tail = prev[prev.length - 1]

  if (isToolCarryingTrail(tail)) {
    const next = [...prev]

    next[prev.length - 1] = mergeToolShelfInto(tail!, msg)

    return next
  }

  if (isBarrierMessage(tail) && !canHoldToolShelf(tail)) {
    return [...prev, msg]
  }

  let fallbackHolder: number | null = null

  for (let index = prev.length - 1; index >= 0; index--) {
    const candidate = prev[index]

    if (isToolCarryingTrail(candidate)) {
      const next = [...prev]

      next[index] = mergeToolShelfInto(candidate!, msg)

      return next
    }

    if (fallbackHolder === null && canHoldToolShelf(candidate)) {
      fallbackHolder = index
    }

    if (isBarrierMessage(candidate)) {
      break
    }
  }

  if (fallbackHolder !== null) {
    const next = [...prev]

    next[fallbackHolder] = mergeToolShelfInto(prev[fallbackHolder]!, msg)

    return next
  }

  return [...prev, msg]
}
