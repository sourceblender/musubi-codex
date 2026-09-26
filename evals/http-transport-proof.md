# Clean-install HTTP transport proof (frozen protocol)

This proof covers the Codex plugin's connection to Musubi without the private
`memory-data` operator binary. It is separate from the native hook capture
proof: a shadow event alone does not establish remote delivery or recall.

## Frozen setup

- Install the Codex plugin's Python package from this repository and the
  published `musubi-harness` 1.1.x wheel into an isolated environment. The
  executable search path must contain neither fleet-tools nor `memory-data`.
- Use a new Codex home and the installed plugin's actual data directory. For
  the local gate, set `actor=proof`, `presence=proof/test`, `zone=home` in
  `config.json`. For the live canary, use a dedicated test token and an actor,
  presence, and namespace owned by that token. Never record the token or
  server address in the receipt.
- Freeze one synthetic prompt/final pair and one explicit durable memory with
  unique markers. Keep their exact text and expected hashes in a fixture
  before exercising the implementation.
- Keep the same fixture for the failing baseline and corrected build. A changed
  assertion starts a new proof version.

## Gate

1. **Install and boundary:** the installed MCP and hook commands resolve in
   the isolated environment, `musubi-memory-data` resolves from the published
   harness wheel, and no private operator binary resolves. Only processes
   that may contact Musubi receive the connection token; enqueue, local stage,
   and local remember do not. The token never appears in command arguments,
   hook output, MCP errors, or the receipt.
2. **Fail closed:** absent, owner-insecure, malformed, and symlinked
   `connection.json` files yield an unavailable connection, never an empty
   memory claim or an ambient-credential fallback. A redirect is refused
   without forwarding the bearer.
3. **Shadow default:** a completed Codex turn creates exactly one local shadow
   event with exact fixture text and performs no remote write before verified
   delivery is selected.
4. **Verified delivery:** after explicitly selecting verified delivery, one
   `musubi_remember` call produces a durable receipt. Lookup and exact readback
   return the same object ID, namespace, text bytes, and content hash. A
   repeat with the same idempotency key yields no second object.
5. **Continuity:** SessionStart reads at most the documented chronology bound
   from the owned presence scope, labels returned content as untrusted
   history, and distinguishes service unavailable from a valid empty result.

The local gate uses a disposable HTTP service to make request count, header
recipient, redirect refusal, and negative controls observable. The final
canary uses a real Musubi instance with a dedicated test credential and reads
back its own object. The local service does not substitute for that canary.

## Local red/green observation

The frozen fixture digest is `2a62b060aa9340a5d7ea51556382e709ae97874a10492c2fa53619b8b5fa12ef`.
With `musubi-harness` 1.1.0 built from shared PR #3, the current Codex main
`e5c154f` returned exit 1: the reachable ambient decoy got three requests,
including one after `connection.json` was removed. Status appeared healthy
under that decoy, while the file-token and no-fallback assertions failed. [Baseline
receipt](results/http-transport-baseline-e5c154f.json).

The same fixture against Codex #7 source head `c2da6ab` returned exit 0: the
fake service received two authorized requests with the file token despite
planted ambient decoys; no request reached the redirect target. [Candidate
receipt](results/http-transport-candidate-c2da6ab.json). Both packages were
built from source into fresh Python 3.12 virtual environments. This is a
local boundary proof, **not** the published-wheel or real-service canary.

An installed Codex CLI 0.157.1 probe found a separate deployment gap: legacy
plugin hooks received the host-assigned `PLUGIN_DATA`, but the bundled MCP
process did not. The Codex team is holding #7 until one installed session
proves that the two processes resolve the same data directory. The local gate
above sets `PLUGIN_DATA` explicitly and cannot close that host integration
claim.

The same frozen local gate also passed with `musubi-harness==1.1.0` installed
from PyPI into a fresh Python 3.12.13 environment, and Codex #7 at `c2da6ab`
installed as a built package from its checkout. The search path excluded the
private operator. All 13 assertions passed: two configured requests used the
file token, removing the file made no further request, and the redirect target
received none. [Published-wheel receipt](results/http-transport-pypi-harness-1.1.0-codex-c2da6ab.json).
This closes the published harness wheel gate only; the installed Codex host
root and real-service readback remain open.

The first installed-host trial of #7 at `06e4759` failed the cold-start gate.
In a fresh `CODEX_HOME` with Codex CLI 0.157.1, the plugin 0.4.0 was installed
from its local marketplace and `musubi-harness` 1.1.0 from PyPI. After the
first session, one data-root locator existed, but the session had zero MCP tool
call events and the agent reported that `musubi_status` was unavailable. An
identical second session in the same home emitted one `musubi_status` tool call;
with no connection configured it correctly returned `unavailable`.
[Cold-start receipt](results/locator-cold-start-codex-06e4759.json). The
paired observation suggests MCP initialized before SessionStart published the
locator, but the event stream does not expose startup ordering directly. A
first-session tool call remains unproved for the Codex code lane.

## Receipt

Record exact plugin and harness versions/commits, Codex CLI version, hook
trust mode, the frozen fixture digest, gate verdicts, request counts, object
ID, namespace, and SHA-256 of written and read-back text. Record only a
redacted endpoint label and a token-presence boolean. Preserve failed
baseline and passing receipts. A live proof is incomplete until the real
readback and both teams' exact-head reviews are recorded.
