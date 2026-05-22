# Takyon Mascot

The startup character is loaded from `harness/takyon/mascot.ansi` when present.

`mascot.ansi` stores ANSI escape codes as readable `\x1b[...]` text. Takyon decodes those at runtime, so the file can stay editable in normal editors without raw control characters.

`harness/takyon/mascot.txt` is the fallback plain pixel map.

The fallback is a tiny pixel map, not line art:

- `#` or `@` becomes an electric-blue filled block.
- `.` becomes a cyan filled block.
- `=` and `>` become cyan motion marks.
- spaces stay transparent.

To make a Claude-Code-style terminal character:

- Keep it 6-8 lines tall and roughly 12-16 cells wide.
- Design the silhouette as a small pixel sprite; image-to-ASCII usually looks noisy at this size.
- Use only the source characters above so the renderer can fill it cleanly.
- Give it one memorable signal: eyes, a face, a bolt, or a right-facing motion cue.
- Test with `TAKYON_COLOR=1 ./takyon shell`, edit `mascot.ansi` or `mascot.txt`, and rerun.

The wordmark is separate in `scripts/takyon.ts`; the mascot should be a small character, not another logo.
