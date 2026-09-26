# Changelog

All notable changes to `musubi-codex` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.4.0] - 2026-09-26

### Added

- The bundled Musubi HTTP client receives its URL and seat JWT from an
  owner-only `connection.json` in Codex's plugin data directory. Ambient
  connection variables are ignored for that client; the existing operator
  `memory-data` transport keeps its configuration.
- Installed hooks publish their host-assigned plugin data directory in a
  short-lived, owner-only locator keyed by the installed plugin root. The MCP
  child resolves that exact directory per tool call or reports unavailable;
  it never guesses a different checkout's state. Tool discovery remains
  available when Codex starts MCP before its SessionStart hook. A bounded
  startup wait covers Codex's first-session hook/MCP race.

### Changed

- Require `musubi-harness>=1.1,<2` for the public HTTP transport and the
  local-only subprocess credential boundary.

## [0.3.0] - 2026-09-26

### Added

- Native `UserPromptSubmit` and `Stop` turn pairing for local shadow capture
  without relying on Codex's unstable transcript format.
- `Interrupt` cleanup for staged prompts. The existing transcript fallback
  remains for blank-answer bridge receipts.

### Changed

- Staged prompts are owner-only files, removed after a completed enqueue or a
  terminal turn without a final answer. An enqueue failure retains the prompt
  for an explicit retry.

## [0.2.0] - 2026-09-26

### Changed

- Dependency `musubi-harness` is now pinned to `>=1.0,<2` to track the
  public PyPI release `musubi-harness==1.0.1`. The 0.1.0 release targeted a
  never-published `0.1.x` series, so 0.2.0 is the first release against a
  real PyPI version.

## [0.1.0] - 2026-09-26

### Added

- Initial open-source release of the Codex adapter, previously shipped inside
  the private `fleet-tools/plugins/musubi-codex` directory.
- Codex plugin manifest at `.codex-plugin/plugin.json` (Agent Plugins schema,
  with `.codex-plugin/` compatibility overlay).
- `.mcp.json` registering the `musubi-codex` MCP server.
- `hooks/hooks.json` for Codex `SessionStart` (continuity) and `Stop` (capture)
  lifecycle events.
- Two skills: `skills/musubi-recall` and `skills/musubi-continuity`.
- Python package `musubi_codex` with three entry points:
  `musubi-codex-mcp`, `musubi-codex-session-start`, `musubi-codex-stop`.
- Depends on the standalone `musubi-harness` package; no requirement that
  `fleet-tools` be installed.
- Test contract covering transcript parsing, identity policy, delivery mode,
  and bridge-receipt validation.
- Apache 2.0 license.
