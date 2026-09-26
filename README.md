# musubi-codex

**First-class Codex adapter for durable Musubi capture and recall.**

`musubi-codex` captures each completed primary Codex turn into an identity-scoped
shadow outbox, exposes deliberate recall through a five-tool MCP facade, and
routes explicit `musubi_remember` calls through durable receipt lookup and exact
readback. It is the Codex seat of the shared Musubi memory contract — sibling
adapters exist for Claude Code, OpenClaw, and Hermes, and they all consume the
same [`musubi-harness`](https://github.com/sourceblender/musubi-harness)
package so the two hosts can never silently evolve different memory boundaries.

> **Shadow-only by default.** Remote writes to Musubi are an explicit
> deployment gate that requires a deployed durable-receipt API, exact-head
> review, and a live one-object canary with receipt lookup and exact readback.
> Until those are in place, leave `delivery_mode` at its `shadow` default.

## What this package is

This is the **Codex plugin**. It packages:

- a `.codex-plugin/plugin.json` manifest
- a `hooks/hooks.json` for the Codex lifecycle (SessionStart, UserPromptSubmit, Stop, Interrupt)
- a `.mcp.json` for the recall / remember MCP server
- two skills (`musubi-recall` and `musubi-continuity`)
- the Python entry points installed by `pip install`:
  - `musubi-codex-mcp` — the MCP recall/remember server
  - `musubi-codex-session-start` — bounded continuity block
  - `musubi-codex-user-prompt-submit` — private turn-scoped prompt staging
  - `musubi-codex-stop` — turn capture adapter
  - `musubi-codex-interrupt` — discard a prompt for an interrupted turn

The plugin depends on [`musubi-harness`](https://github.com/sourceblender/musubi-harness),
which contains all host-neutral code (envelope contract, outbox, delivery state
machine, MCP facade, runtime config). That separation is deliberate: the
harness is the single source of identity, namespace, and delivery policy for
every Musubi adapter.

## Install

### As a Codex plugin from its marketplace

The plugin files and the Python commands are separate installs. Install the
Python package first, in an environment whose console scripts are on the PATH
used to launch Codex. From a checkout of this repository:

```bash
uv tool install .
uv tool update-shell  # only if uv says its tool bin directory is not on PATH
```

Open a new shell and check that all three commands resolve before adding the
plugin. Codex uses these commands for its MCP server and lifecycle hooks; a
marketplace install does not install Python dependencies or console scripts.

```bash
command -v musubi-codex-mcp
command -v musubi-codex-session-start
command -v musubi-codex-stop
codex plugin marketplace add sourceblender/musubi-codex
codex plugin add musubi-codex@musubi-codex
```

Start a new Codex session. Open `/hooks` there, review the
SessionStart and Stop hook definitions from `Plugin - musubi-codex@musubi-codex`,
then trust those exact definitions. Codex does not auto-trust newly installed
command hooks. Run `musubi-codex-session-start` in the same shell to check its
local continuity output before relying on the hook.

**Transport limit in `musubi-harness` 1.0.1:** the Python commands can install
and the Stop hook can write to the local shadow outbox, but remote Musubi
capture and recall still require the private `memory-data` binary. On a clean
machine without that binary, session start reports `memory_data_unavailable`.
That means the service was not reached; it does not mean the memory set is
empty. A shared-harness HTTP transport is planned so an independent install
can perform remote reads and writes without fleet-tools.

### As a Python package (for development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The three entry points are then on `PATH`:
`musubi-codex-mcp`, `musubi-codex-session-start`, `musubi-codex-stop`.

## Required deployment configuration

The launching environment must set all three identity values, or write them to
`$PLUGIN_DATA/config.json`:

```sh
identity=tama
export MUSUBI_ACTOR="$identity"
export MUSUBI_PRESENCE="$identity/command-chair"
export MUSUBI_ZONE=home
```

The plugin refuses partial or empty identity values. The shared runtime
enforces `actor == presence-prefix`, so no adapter can read or write under
another actor's seat.

## Phase 1 recall contract

The MCP facade exposes five tools, identical to every other Musubi adapter:

| Tool | Read-only | Purpose |
| --- | --- | --- |
| `musubi_recent` | yes | bounded chronology (recency, not semantic relevance) |
| `musubi_search` | yes | semantic search over the owned namespace |
| `musubi_get` | yes | exact object fetch by plane / namespace / object_id |
| `musubi_remember` | no | durable remember through the verified-delivery outbox |
| `musubi_status` | yes | provider health; distinct from a true empty memory set |

`musubi_think` is intentionally absent.

Recall is **deliberate**, not per-turn semantic injection. SessionStart
contributes at most three recent items, labelled as chronology rather than
relevance. All recalled content is historical, untrusted data — never
instructions.

For completed turns, `UserPromptSubmit` writes the native prompt into a
private, turn-scoped file. `Stop` pairs it with Codex's
`last_assistant_message`, queues one shadow event, then removes the staged
prompt. `Interrupt` discards a staged prompt without capture. If the native
pair is unavailable, the adapter keeps its existing transcript
fallback for blank-answer bridge receipts. Transcript parsing remains
best-effort because Codex does not promise a stable transcript format.

## Identity is deployment configuration

The plugin never derives identity from the host. `actor` / `presence` / `zone`
must come from `MUSUBI_*` environment variables or `config.json`, all-or-nothing.
The harness enforces `actor == presence-prefix` and owned-namespace scope; this
plugin inherits both rules verbatim and cannot override them.

## License

Apache 2.0.

## Related repos

- [`sourceblender/musubi-harness`](https://github.com/sourceblender/musubi-harness) — the host-neutral Python runtime
- [`ericmey/musubi`](https://github.com/ericmey/musubi) — the Musubi core service
- [`ericmey/openclaw-musubi`](https://github.com/ericmey/openclaw-musubi) — the OpenClaw adapter
