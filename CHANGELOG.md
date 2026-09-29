# Changelog

All notable changes to `musubi-codex` are documented here. The format is based
on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-09-29

### Fixed

- Capture each completed Codex assistant answer with the ordered user input
  span that led to it, including turns that contain more than one final answer.
  An uncertain exchange is declined on its own without discarding later valid
  exchanges from the same turn.
- Stage Stop candidates before polling the transcript, retry bounded pending
  work across hooks, and keep infrastructure failures age bounded. Capture
  no longer depends on a single poll finding the whole exchange at once.

### Changed

- Require `musubi-harness>=1.7.0,<2` for the final-answer exchange contract.
- CI now uses the Sourceblender standard for concurrency, permissions, and
  pinned actions.

## [0.6.0] - 2026-09-26

### Changed

- A launcher-supplied seat identity now owns its transport and policy. With
  `MUSUBI_ACTOR`/`MUSUBI_PRESENCE`/`MUSUBI_ZONE` set, shared plugin-data
  `config.json`, `connection.json`, and `prompt_recall.json` are ignored.
  `MUSUBI_API_URL` and `MUSUBI_TOKEN` must both come from that seat's process
  environment. The bundled harness and HTTP client take precedence over
  legacy fleet-tools binaries. Prompt recall opts in with
  `MUSUBI_PROMPT_RECALL=true`; verified delivery opts in with
  `MUSUBI_DELIVERY_MODE=verified`. Missing settings stay shadow/off.
- File configuration remains available for single-seat installs without a
  launcher identity. Operators sharing one OS user should use launcher mode.

### Fixed

- SessionStart names a seat JWT whose subject, presence, or write scope does
  not fit the launcher identity. A known mismatch refuses remote recall and
  delivery before the credential reaches a subprocess; Musubi still validates
  the signed token on the server.

## [0.5.1] - 2026-09-26

### Fixed

- Prompt-aware recall uses Musubi's deep ranked retrieval path. The live
  service's fast path returned `BACKEND_UNAVAILABLE`; deep queries succeeded
  in the same scoped tests. The subprocess deadline is 3.5 seconds and the
  native hook bound is 6 seconds, while degraded responses still report
  unavailable rather than an empty memory set.

## [0.5.0] - 2026-09-26

### Added

- Opt-in `UserPromptSubmit` recall asks Musubi for up to three settled matches
  in the exact episodic namespace and supplies a bounded, quoted context block
  before the turn. Missing and degraded recall are distinct from a true empty
  result. Ranked results show relative recency scores without claiming a
  timestamp or the latest decision.
- Frozen prompt-recall eval runs in CI. An installed interactive Codex proof
  covered prompt injection and a second prompt after `/compact`.

### Changed

- Verified `Stop` delivery drains up to five queued rows within a bounded
  start budget when `musubi-harness` 1.2.0 is available. An older configured
  harness binary retains its one-row drain path.
- Setup instructions explain how to select the bundled HTTP client when an
  operator `memory-data` tool is also installed.

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
