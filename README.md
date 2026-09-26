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
- a `hooks/hooks.json` for the Codex lifecycle (SessionStart + Stop)
- a `.mcp.json` for the recall / remember MCP server
- two skills (`musubi-recall` and `musubi-continuity`)
- the Python entry points installed by `pip install`:
  - `musubi-codex-mcp` — the MCP recall/remember server
  - `musubi-codex-session-start` — bounded continuity block
  - `musubi-codex-stop` — turn capture adapter

The plugin depends on [`musubi-harness`](https://github.com/sourceblender/musubi-harness),
which contains all host-neutral code (envelope contract, outbox, delivery state
machine, MCP facade, runtime config). That separation is deliberate: the
harness is the single source of identity, namespace, and delivery policy for
every Musubi adapter.

## Install

### As a Codex plugin from a marketplace

Once this repo is registered as a Codex marketplace, the install is one command:

```bash
codex plugin marketplace add sourceblender/musubi-codex
codex plugin install musubi-codex@musubi-codex
```

After installation, open `/hooks` in an interactive Codex session, review the
SessionStart and Stop hook definitions from `Plugin - musubi-codex@musubi-codex`,
then trust those exact definitions. Codex does not auto-trust newly installed
command hooks.

### As a Python package (for development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
pip install /path/to/musubi-harness   # or `pip install musubi-harness` once published
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
