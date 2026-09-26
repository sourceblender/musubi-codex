# Codex deep prompt recall proof

This is the 0.5.1 follow-up to the [v1 prompt recall proof](prompt-recall-proof.md).
The fixture was frozen before the implementation changed: SHA-256
`7585841ee91c941c7eb13469913603a121bbadff971c22c2a157576c7214aacf`
over the exact UTF-8 bytes of
[`prompt-recall-deep-v2.json`](fixtures/prompt-recall-deep-v2.json).
The [v2 runner](run_prompt_recall_deep_proof.py) uses those bytes as JSON,
mocks only the subprocess boundary, and checks the literal command, returned
context, and failure distinctions. It requires `--mode deep`, the exact
episodic namespace, `--settled-only`, limit three, and a subprocess timeout
of at most 3.5 seconds. The nine adversarial responses retain the v1 scope,
state, warning, prompt-injection, and context-bound cases.

On v0.5.0 main `31efe42`, the frozen gate is red at 19/28: all nine failures
are the mode check because the command asks for `fast`.
[Red receipt](results/prompt-recall-deep-red-31efe42.json). The unchanged
fixture passes 28/28 on candidate `28ab74a`, which asks for `deep` with a
three-second client timeout and a 3.5-second subprocess timeout.
[Green receipt](results/prompt-recall-deep-green-28ab74a.json). CI runs this
v2 gate on both supported Python versions; the v1 runner and its receipts
remain as historical evidence.

A separate live, read-only proof used an existing Tama seat credential with
read permission. The credential is not limited to reads; the proof performed
no remote mutation.
The bundled client requested deep retrieval in an owned episodic namespace:
three settled rows returned in 0.811 seconds with no warnings. Running the
candidate `UserPromptSubmit` executable against the same service returned
three bounded context rows in 0.887 seconds, each with a relative recency
score. It staged one prompt locally and made zero remote mutations.
[Live receipt](results/prompt-recall-deep-live-read-28ab74a.json) contains
counts and booleans only, with no memory text, IDs, query text, namespace,
URL, or credential.

The live run proves the candidate executable's retrieval path at that moment.
It does not prove Codex TUI injection for 0.5.1; the 0.5.0 installed TUI
proof is separate. It also does not promise that the service cannot return a
future 503. A failure remains “unavailable,” never a false empty result.
