const TAGS = ['think', 'reasoning', 'thinking', 'thought', 'REASONING_SCRATCHPAD'] as const

// Precompile the paired/unclosed regexes once per tag instead of rebuilding
// them on every splitReasoning call. splitReasoning runs on the streaming
// hot path, so this avoids 10 regex compilations per assistant chunk.
const TAG_PATTERNS = TAGS.map((tag) => ({
  paired: new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>\\s*`, 'gi'),
  // Anchor to start-of-input so a literal `<think>` mid-prose (model quoting
  // the word, code blocks containing the tag, etc.) doesn't eat every
  // paragraph after it. Real unclosed reasoning blocks always lead the
  // message — that's how reasoning models stream. See test
  // "does not strip trailing prose after a stray mid-text <think> mention".
  unclosed: new RegExp(`^\\s*<${tag}>([\\s\\S]*)$`, 'i')
}))

export interface SplitReasoning {
  reasoning: string
  text: string
}

export function splitReasoning(input: string): SplitReasoning {
  let text = input
  const reasoning: string[] = []

  for (const { paired, unclosed } of TAG_PATTERNS) {
    // Reset stateful lastIndex from any prior call before reusing the global regex.
    paired.lastIndex = 0
    text = text.replace(paired, (_m, inner: string) => {
      const trimmed = inner.trim()

      if (trimmed) {
        reasoning.push(trimmed)
      }

      return ''
    })

    text = text.replace(unclosed, (_m, inner: string) => {
      const trimmed = inner.trim()

      if (trimmed) {
        reasoning.push(trimmed)
      }

      return ''
    })
  }

  return {
    reasoning: reasoning.join('\n\n').trim(),
    text: text.trim()
  }
}

export const hasReasoningTag = (input: string) => {
  for (const tag of TAGS) {
    if (input.includes(`<${tag}>`)) {
      return true
    }
  }

  return false
}
