# Altiplano

Claude Code reads this file. Every other agent reads AGENTS.md.

@AGENTS.md

Claude-specific instructions belong under this line, everything else goes in AGENTS.md.

A note for whoever edits this file next: leave that directive bare, on its own line.
Claude Code scans for the pattern outside code fences and code spans.
Wrapping it in backticks turns it into inert text and the import fails without an error.
