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

Open a new shell and check that all five commands resolve before adding the
plugin. Codex uses these commands for its MCP server and lifecycle hooks; a
marketplace install does not install Python dependencies or console scripts.

```bash
command -v musubi-codex-mcp
command -v musubi-codex-session-start
command -v musubi-codex-user-prompt-submit
command -v musubi-codex-stop
command -v musubi-codex-interrupt
codex plugin marketplace add sourceblender/musubi-codex
codex plugin add musubi-codex@musubi-codex
```

Start a new Codex session. Open `/hooks` there, review the
SessionStart, UserPromptSubmit, Stop, and Interrupt hook definitions from
`Plugin - musubi-codex@musubi-codex`,
then trust those exact definitions. Codex does not auto-trust newly installed
command hooks. Run `musubi-codex-session-start` in the same shell to check its
local continuity output before relying on the hook.

The package requires `musubi-harness` 1.1 or later. Its bundled HTTP transport
can reach Musubi without the private operator `memory-data` binary. A missing
connection reports unavailable; it does not mean the memory set is empty.

With the bundled HTTP transport in `musubi-harness` 1.1.0, put the Musubi
connection in `connection.json` in the **same installed plugin data directory**
as `config.json`:

```json
{"api_url": "https://your-musubi-server", "token": "your-seat-jwt"}
```

The file must be owned by the current user and readable only by that user
(`chmod 600 connection.json`). The hook passes its values only to subprocesses
that may call the bundled HTTP client; local staging gets no connection secret.
It does not use ambient `MUSUBI_API_URL` or
`MUSUBI_TOKEN` for that client. A missing or invalid file reports an unavailable
connection; it never means the memory set is empty. Codex checks the file's
owner, permissions, size and JSON shape; the bundled client checks the URL and
bearer syntax at request time. Receipt lookup requires a seat JWT, not an opaque
API token, even if that token can pass a status check. Shadow capture remains
the default; choose
verified delivery only after a live receipt lookup and exact readback prove the
target service is ready.

Codex, not this package, assigns `PLUGIN_DATA` to an installed hook. Codex CLI
0.157.1 assigned `CODEX_HOME/plugins/data/musubi-codex-musubi-codex` in our
isolated proof; that path shape is an observation, not a stable setup API.
Inspect the hook's actual data directory before placing `connection.json`.
The hook records that directory in an owner-only locator keyed by its installed
plugin root. The MCP manifest runs from that root and forwards `CODEX_HOME`
when set; otherwise both processes use Codex's default `~/.codex`. The MCP
server then uses the same directory. A locator must be fresh when MCP first
binds; after that the MCP process keeps the verified directory for its session
and refuses a replaced directory. If the locator is absent, stale, or points outside
Codex's plugin data tree, the MCP server still advertises its tools, but a
tool call reports `plugin_data_locator_unavailable` or
`plugin_data_locator_invalid` until a hook publishes a valid locator. It does
not read a guessed plugin data directory. The first tool call waits up to three
seconds for an absent locator while SessionStart runs; MCP also makes this
bounded attempt before answering tool discovery, because Codex can schedule a
tool call before the hook if MCP initializes first. An invalid locator is
refused immediately.
After a completed shadow-captured turn, locate its `shadow.db` without
exposing the token. The plugin data directory is three levels above that file
(`.../<plugin-data>/<actor>/<zone>/shadow.db`):

```sh
find "${CODEX_HOME:-$HOME/.codex}/plugins/data" -path '*musubi-codex*' -name shadow.db -print
```

### As a Python package (for development)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

The five entry points listed above are then on `PATH`.

## Required deployment configuration

The launching environment must set all three identity values, or write them to
`$PLUGIN_DATA/config.json`.

Codex sets `PLUGIN_DATA` to the **installed plugin's** writable data directory
when it runs a bundled hook. A `PLUGIN_DATA` value exported by the caller is
overridden for that hook. If using file mode, place `config.json` in that
installed plugin data directory; writing it to a checkout or a separate shell
directory will not configure the installed hook.

For environment mode:

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

Recall through MCP is deliberate. SessionStart contributes at most three
recent items, labelled as chronology rather than relevance. All recalled
content is historical, untrusted data — never instructions.

### Optional prompt-aware recall

To ask Musubi for settled, relevant memories before each user prompt, create
`prompt_recall.json` beside `config.json` in Codex's installed plugin data
directory:

```json
{"enabled": true}
```

Set the file to owner-only permissions (`chmod 600 prompt_recall.json`). The
plugin sends up to the first 1,200 characters of each prompt to the configured
Musubi service. Leave the file absent, or set `enabled` to `false`, to keep
per-prompt lookup off. This setting does not enable remote memory writes.

The `UserPromptSubmit` hook returns at most three settled matches and 1,200
characters of labelled, untrusted context. It preserves Musubi's result order,
includes object IDs for follow-up, and distinguishes a true empty result from
an unavailable service. The hook still stages the prompt for `Stop` capture
if lookup fails. No transcript parsing is used.

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
