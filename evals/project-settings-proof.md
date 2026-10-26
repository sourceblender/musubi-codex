# Project settings compatibility proof

The project-mode gate is `python evals/run_project_host_proof.py`. It installs
the candidate Python package and plugin in a disposable HOME and CODEX_HOME,
starts concurrent native Codex CLI sessions, and exercises the desktop-facing
App Server API. All model responses and Musubi requests use deterministic
loopback fixtures. No model account, production Musubi credential, or live
memory write is needed. The running operator installation is not changed.

The gate uses the documented one-invocation hook-trust bypass for the CLI's
self-authored test hooks. For App Server it lists the test hooks and records
their returned hashes in the temporary configuration through `config/batchWrite`.
It never changes hook trust in the user's Codex home.

## Acceptance

- Concurrent Yua/Tama projects resolve different credential files through the
  same installed plugin and shared Codex home.
- Model-initiated status calls bind through native hooks; the fixture never
  calls the binding tool on the model's behalf.
- An unbound direct MCP API call returns unavailable. After a user turn, direct
  calls on that chat use its established binding. Non-agent-loop clients can
  explicitly invoke the binding tool with their native context first.
- App Server resume retains Yua's configuration. Forking into Tama's project
  selects Tama's configuration without changing the parent.
- Five completed answers are captured in two separate project outboxes:
  three Yua rows and two Tama rows. Bounded startup recovery is exercised after
  the transcript's final flush.
- All Musubi requests are scoped to the expected synthetic credentials and
  only retrieval/status endpoints are contacted.

The native test passed on Codex CLI 0.164.0. It tests Codex execution and the
App Server protocol used by desktop clients; it does not automate desktop UI
clicks or certify a production remote-delivery deployment. Its JSON output
includes a digest of the candidate Python sources and MCP/hook manifests.

## Other gates

```sh
uv run --no-project --with '.[dev]' pytest -q
uv run --no-project --with '.[dev]' ruff check musubi_codex tests
uv run --no-project --with '.[dev]' ruff format --check musubi_codex tests
uv run --no-project --with '.[dev]' mypy musubi_codex
```

The existing native-hook, prompt-recall, and launcher-isolation proofs remain
required. Run them in the candidate's installed environment with ambient
`MUSUBI_*` and `FLEET_*` variables removed, so operator opt-ins cannot affect
the synthetic fixtures. The native-hook proof now selects its one command
handler independently of supplemental MCP tool handlers and uses a real
temporary working directory instead of the fixture's `/workspace/proof` label.

Unit regressions cover legacy file mode, complete/partial/conflicting launcher
identity, worktree discovery, malformed settings, secret-file ownership and
permissions, symlink refusal, non-executing dotenv parsing, token mismatch,
idempotent binding and rebinding refusal, configuration edits, credential
rotation, pending-record compatibility, and project-scoped delivery commands.
Minimum-dependency checks also pass with musubi-harness 1.7.0 on Python 3.12.

Use `MUSUBI_PROOF_DEBUG_DIR` only for private local debugging of this synthetic
gate. On failure it can preserve local hook metadata, synthetic state, and
client output. Those files are not release artifacts.
