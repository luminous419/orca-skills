# OS-37 recorded CLI streams — the D13.2a test inputs

Every file here is **byte-for-byte captured output** from a real run against the CLIs installed on
this host, not a hand-written fixture. The filename carries the DESIGN §D4.0 measurement id, so a
reviewer can diff a test input against the measurement it claims to replay.

| File | Measurement | What it is |
| --- | --- | --- |
| `m14_claude_genuine_turn.stream` | M-14 | `claude 2.1.260`, the PRODUCTION composed argv, `rc=0`. `system/init` → `rate_limit_event` → `assistant` → `result{is_error:false, terminal_reason:"completed"}`. The `assistant` record carries `model:"claude-opus-5"`, a `msg_`-prefixed `message.id`, a `request_id`, and non-zero token counters. This is the **positive twin** without which a reject-everything selector would pass the negative test. |
| `m15_claude_auth_failure.stream` | M-15 | The same CLI under `--bare`, which narrows authentication to `ANTHROPIC_API_KEY` — unset here — so this is a **real login failure**, not a simulated one. `rc=1`. `system/init` → `assistant` → `result{subtype:"success", is_error:true, terminal_reason:"api_error"}`. The `assistant` record is **identity-bound** (`session_id` equals the minted value) and still proves nothing: `error:"authentication_failed"`, `is_api_error_message:true`, `model:"<synthetic>"`, `request_id` absent, every `usage` counter `0`. **These are the bytes F-001 is about.** |
| `m8_codex_auth_failure.stream` | M-8, failure leg | `codex-cli 0.153.2` against an **unseeded** run-scoped `CODEX_HOME`. `401 Unauthorized`, `rc=1`, `-o` absent. `thread.started` → **`turn.started`** → `error` → `item.completed{item.type:"error"}` → `turn.failed`, with unparsable `ERROR codex_api::…` lines interleaved on the same pty. `turn.started` here is emitted **before authentication**, which is why it is demoted. |
| `m8_codex_success.stream` | M-8, success leg | The same argv with the credential file seeded into that root. `rc=0`, `-o` present. `thread.started` → `turn.started` → `item.completed{item.type:"agent_message"}` → `turn.completed{usage}`. Codex's **positive twin**. |

The `.session_id` sidecar next to a Claude stream holds the session id that run was launched with, so
a test can assert R-B closes **by equality** against a value the runtime minted rather than against
one it read out of the transcript.

These files are inputs to `scripts/test_os37_lifecycle.py`'s §D13.2a cases and to
`scripts/test_os37_cli_preconditions.py`'s rehearsal cases. They are **not** a substitute for the
live-CLI gate: a recorded stream proves what the selector does with a measured shape, and only a live
run proves the CLI still produces it. Both exist, and `ORCA_OS37_LIVE_CLI=1` runs the second.
