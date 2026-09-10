# Repository instructions

- Follow `docs/WORKFLOW.md` for every render: keep `selection.csv` and
  `selection-uuids.txt` synchronized, require genuine originals exported with
  `osxphotos` and zero missing/error results, render and inspect a preview before
  the final output, and keep generated `sources/` and `output/` files untracked.
- Keep the full-quality video and archival GIF outputs, then add Farcaster GIFs with
  the byte-bounded `blue-skies farcaster-gif` workflow; never replace the masters or
  hard-code today's dimensions because selection growth changes compression.
- Memory measurements are authorized for repository validation. On macOS, run
  `/usr/bin/time -l ffmpeg ...` or `/usr/bin/time -l uv run blue-skies ...` with
  host access. The sandbox blocks the `kern.clockrate` query needed by `time`.
  Bryan approved persistent host execution for these two command prefixes in
  `.codex/rules/memory.rules`. Run each measured command directly so the rule can
  match it. Codex loads the rules at startup for this trusted repository; restart
  Codex after changing them. Use temporary output paths, preserve source files and
  masters, and wait for the command to finish.
  Report peak resident memory in bytes with the input duration and dimensions.
