# Codex prompt recall proof

This is a frozen adversarial gate for the opt-in native `UserPromptSubmit` hook.
It uses the Musubi ranked-retrieve wire shape, a disposable owner-only plugin
setting, and a mocked subprocess boundary. It makes no network request and
contains no credential. The fixture is
[here](fixtures/prompt-recall-v1.json); the runner is
[here](run_prompt_recall_proof.py).

The cases cover two conflicting decisions returned in relevance order, a
newline-based instruction injection, a degraded HTTP 200 with warnings,
a healthy empty response, missing namespace and state, provisional and
cross-scope rows, oversized content, and opt-out. The conflicting-decision
case requires both rows and their relative recency scores to be visible;
it refuses a false timestamp or "latest decision" claim because Musubi's
ranked response has no timestamp field. Every lookup must use the exact
`tama/codex/episodic` namespace, the settled filter, a limit of three,
and a 2.5-second process bound.

Against #10 `141ee50`, the gate was red at 14/28: degraded-empty became a
normal empty result, missing-scope and missing-state rows entered context,
query scope was the two-segment presence rather than the concrete episodic
namespace, and the date/recency claim lacked wire evidence.
[Red receipt](results/prompt-recall-red-141ee50.json).

At #10 `c92b23b`, the unchanged fixture passed 28/28 with Python 3.12.13,
plugin 0.4.0 built from that exact head, and `musubi-harness` 1.1.1 from
PyPI. [Initial green receipt](results/prompt-recall-green-c92b23b.json).
After #7 merged, replacement PR #11 at `38bd8b9` passed the same fixture
28/28. [Rebased green receipt](results/prompt-recall-green-38bd8b9.json).
An installed console script also passed 12/12 against a disposable HTTP
server, including an exact episodic query, settled filter, bounded context,
and no token in hook output.
[Installed executable receipt](results/prompt-recall-installed-38bd8b9.json).

The real Codex 0.157.1 TUI then ran the installed `UserPromptSubmit` hook in
an isolated profile. The first host attempt returned unavailable: Codex
prepended the house `memory-data` to `PATH`, so the bundled HTTP client and
the disposable server were never called. An owner-only `config.json` pinned
`memory_data_bin` to the isolated bundled executable. With that pin, Codex
injected both fixture IDs and relative recency scores, and the model answered
them without tools. Manual `/compact` completed. The next prompt got a fresh
SessionStart continuity block and the same two IDs from prompt recall; the
model answered both again. The recent-memory endpoint returned no matches in
this fixture, so this does not prove retention of a particular recent memory.
[Interactive host receipt](results/prompt-recall-interactive-f6a37cc.json).

PR #11 `f6a37cc` changes only README wording after the `38bd8b9` code head:
it documents this exact operator-tool precedence and the bundled-client pin.
These results are synthetic local HTTP and host-hook proofs, not a live Musubi
search or a claim that ranked results identify the latest decision.
