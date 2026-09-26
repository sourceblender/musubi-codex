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

## Receipt

Record exact plugin and harness versions/commits, Codex CLI version, hook
trust mode, the frozen fixture digest, gate verdicts, request counts, object
ID, namespace, and SHA-256 of written and read-back text. Record only a
redacted endpoint label and a token-presence boolean. Preserve failed
baseline and passing receipts. A live proof is incomplete until the real
readback and both teams' exact-head reviews are recorded.
