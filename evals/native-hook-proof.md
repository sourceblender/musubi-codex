# Native hook capture proof (draft contract)

This proof targets the Codex adapter's primary-turn capture. It keeps the
synthetic input fixed while the implementation changes. The normal path must
use documented hook fields rather than parse `transcript_path`, whose format
OpenAI describes as unstable. The existing transcript receipt path, if retained
for blank final messages, is a separate compatibility claim.

Official inputs: [Codex Hooks](https://learn.chatgpt.com/docs/hooks) documents
`UserPromptSubmit.prompt` and `turn_id`, `Stop.last_assistant_message` and
`turn_id`, `Interrupt.turn_id`, hook trust, and the unstable transcript format.

## Frozen case

[`fixtures/native-hook-stream-v1.json`](fixtures/native-hook-stream-v1.json)
contains one main-thread turn, a wrong-turn Stop, a duplicate Stop, an
interrupted turn, and a turn with no final message. The primary turn has
`transcript_path: null` to make transcript dependence observable. All identities
and text in the fixture are synthetic.

Run the hook commands from a clean Python 3.12 environment with this repo
installed locally and `musubi-harness` from PyPI. The 0.2.0 baseline receipt
used the published plugin; CI and the 0.3.0 live proof used local checkouts.
Use a temporary `PLUGIN_DATA/config.json` with actor `proof`, presence `proof/test`, zone
`home`, and `delivery_mode: shadow`. No `memory-data` binary, Musubi service,
or private fleet path is part of this capture proof.

Run `python evals/run_native_hook_proof.py` with the Python interpreter that
has the plugin and harness console scripts installed. The runner sets an
isolated home, Codex home, and plugin data root, and restricts `PATH` to that
interpreter's bin directory plus system tools. It prints a JSON receipt and
exits 0 only when the frozen assertions pass. Before the implementation, exit
1 with missing hooks and no shadow event is expected.
To check another detached checkout, run with
`MUSUBI_CODEX_PROOF_ROOT=/path/to/checkout` using that checkout's installed
Python interpreter. The fixture remains this branch's frozen input.

## Assertions

1. The marketplace plugin's hook manifest registers `UserPromptSubmit`, `Stop`,
   and `Interrupt` command hooks. Their commands are present in the separately
   installed Python package.
2. The matching prompt and final message produce exactly one shadow event with
   event ID `codex:thr-proof-001:turn-proof-001` and **exact** fixture text in
   `user_text` and `assistant_text`. The event belongs to `proof/home`.
3. A wrong-turn Stop cannot borrow the pending prompt. A duplicate Stop does
   not add a second event. An interrupted turn and a null-final Stop create no
   completed-turn event.
4. Hook commands exit without blocking Codex, reveal no prompt or answer on
   stdout/stderr, and leave remote delivery disabled. The Stop hook returns
   valid JSON (`{}`) on stdout.
5. The hook state is scoped by both session and turn. After the stream, no
   pending prompt file remains: the completed, interrupted, and null-final
   turns are all terminal. A malformed pending prompt fails closed and records
   a local degradation reason.

**Red proof before implementation:** the current 0.2.0 plugin has only
`SessionStart` and `Stop`; its Stop path requires a nonempty transcript path.
The frozen primary turn therefore cannot meet assertions 1 and 2. Preserve
the observed failing result, then run the same fixture against the proposed
implementation and preserve the passing result. Do not relax the fixture to
match the code.

The CI gate was red-proofed on 2026-09-26. Before #5 merged, [PR #6 run
36265759533](https://github.com/sourceblender/musubi-codex/actions/runs/36265759533)
failed the `Native hook proof` step on Python 3.12 and 3.13: the manifest lacked
`UserPromptSubmit` and `Interrupt`, the outbox had zero events, and the runner
exited 1. After #5 merged, [run
36265938620](https://github.com/sourceblender/musubi-codex/actions/runs/36265938620)
passed the same step on both Pythons with one exact shadow event and zero
pending prompts. The runner input was unchanged.

Yua's review caught a leak-check gap: the original runner compared each hook's
output only with that hook's own text. Before the fix, an Interrupt hook
echoing an earlier prompt was marked `text_leaked: false`. After changing the
runner to compare every hook output with all fixture prompt and final strings,
a temporary Interrupt wrapper echoed that earlier prompt: the gate exited 1
with `text_leaked: true`, while the shadow event and pending prompt counts were
otherwise correct. The wrapper was removed and the normal gate passed again.

## Live Codex proof after the fixture passes

Install the built plugin from an isolated local marketplace into a fresh Codex
home. Review and trust the exact plugin hook definitions in `/hooks`; Codex
skips untrusted hooks. Start one ordinary main-thread Codex turn containing a
unique marker and a short requested final answer. Read back its shadow event
by exact event ID and compare both texts and the session/turn IDs. Run a second
turn that is interrupted and verify no completed-turn event. Record Codex CLI
version, plugin commit and hook hash, session/turn IDs, local event readback,
and the hook trust state in a receipt with no secrets or private paths.

Codex sets `PLUGIN_DATA` for the installed plugin. Put the synthetic
`config.json` in that host assigned directory; a caller supplied `PLUGIN_DATA`
is overridden during hook execution. An automated proof may use Codex's
explicit hook trust bypass after inspecting the exact hook manifest, but its
receipt must label that trust mode and must not claim the interactive `/hooks`
trust flow was tested.

This establishes local capture only. It does not prove remote Musubi delivery,
semantic recall quality, or that every Codex surface invokes these hooks.
