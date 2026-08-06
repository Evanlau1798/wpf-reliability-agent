# Reuse Disclosure

This repository was created as a new, independent project during the All Things Agentic Hackathon 2026. It is not a fork and does not import the history or complete solution of `wpf-devtools-mcp`.

## Upstream Source

- Repository: `https://github.com/Evanlau1798/wpf-devtools-mcp`
- Pinned upstream source revision: `900ac97cf9b69b4a3c1f4899b08c9b1e78212af3`
- License: Apache License 2.0

## Planned Adaptations

- Binding trace capture: adapt only the instance-level diagnostic behavior needed by the in-process sensor.
- Binding parsing and correlation: rewrite against the new versioned contracts as pure functions.
- Validation filtering: preserve selected regression behavior and document adapted tests.
- Bounded UI traversal: use the limits and truncation approach as a design reference; do not copy the full analyzer.
- State comparison: adapt the useful comparison behavior into a pure before/after function.

Each actual reuse is recorded in `reuse-manifest.json` with the exact source path, destination path, reuse type, and adaptation summary before the adapted code is committed.

## Explicitly Excluded

The project does not reuse the complete MCP server, InspectorHost or Inspector SDK transport, Injector, Bootstrapper, Composer, installer, release, or signing infrastructure.
