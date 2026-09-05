# Runtime-neutral deterministic workflow engine

OS-40 moves phase routing, gates, bounded correction, downstream revalidation, and final
review completion into `scripts/deterministic_workflow`. `graph.py` builds the sole executable
LangGraph `StateGraph`; `routing.py` contains the pure functions used by its conditional edge.
Adapters execute stable action intents and return separately bound settlement events.

## Checkpoint and idempotency

`PREPARE_INTENT` completes before the static edge to `EXECUTE_INTENT`, so a LangGraph
checkpointer records the intent before any external effect. Command, intent, and event IDs are
SHA-256 digests of canonical JSON identities. Adapter receipt lookup makes a node replay return
the original effect, while the graph's processed event IDs prevent duplicate budget use.
Process/terminal handles, credentials, clients, bytes, Paths, and unknown state fields are
rejected from checkpoint state.

Every checkpoint ingress is validated as a whole state, not by field name. `update_state` and
`aupdate_state` merge the caller's values onto the persisted checkpoint and run the complete
`validate_state` contract before committing, from every allowed `as_node`, because `as_node`
can resume past `VALIDATE`. `state.UPDATE_COMMANDS` offers a closed typed-command vocabulary
for the fields an operator legitimately sets out of band. Every iteration domain -- each phase
budget and the Final Review budget -- must be an exact `int` (never `bool`) with
`0 <= consumed <= max_iterations`, `0 <= remaining <= max_iterations` and
`consumed + remaining == max_iterations`; the sum alone is not an invariant.

## Ownership, leases, and recovery

`FileRuntimeStateStore` runs `lock -> read -> validate -> claim -> persist -> unlock` as one
inter-process critical section over a sidecar `fcntl.flock` file, and re-reads the ledger only
*after* the lock is held, so two Coordinators racing one stable intent produce exactly one
external start. Lock acquisition has an explicit, injectable timeout and never blocks forever.

Each record carries `owner_id`, `lease_token`, `lease_expires_at` and `last_heartbeat_at`.
While an owner keeps its lease fresh, another Coordinator's claim is refused and it takes the
observer role (`observe`), which also has an explicit finite timeout: a silently killed owner
cannot strand a successor. Lease arithmetic reads an injected `LeaseClockPort`, so no test
sleeps. Ownership identity is the process (host + pid), so a second store object inside one
process resumes its own work rather than locking itself out.

The lease token is a **fence**, not just a renewal ticket. Exclusivity at claim time does not
help if a superseded owner can still write: A claims, blocks inside a slow `create_task`, its
lease expires, B takes over and starts its own Task, and A then returns and records *its*
external identity — two external effects for one stable intent, arriving through the recovery
path rather than the race path. Every ownership-sensitive transition (`record_receipt`,
`settle`, `heartbeat`) therefore **requires** the current lease token and rejects a stale one
(`RuntimeStateLeaseHeld`) or an absent one (`RuntimeStateLeaseRequired`); `lease_token=None`
never means "skip the check". The executor carries the token minted by `claim()` into
`AgentExecutionPort.start(intent, lease_token=...)`, and both adapters thread it into every
ledger write, so the fence is live on the production path rather than an optional argument no
caller passes.

### Lease renewal during long external work

A fence is only half of ownership: a claim is exclusive while the lease is *live*, and the
lease is live only while somebody renews it. Nothing did. `claim()` minted a 60-second lease
and the executor then blocked inside `adapter.start()` for as long as the external agent took
— 5 to 15 minutes for a real Claude/Codex dispatch — so a healthy Coordinator was
indistinguishable from a dead one: its lease lapsed, a second Coordinator took over, and the
fence then refused the healthy owner's own receipt and settlement.

`lease_keeper.LeaseKeeper` closes that gap. Every executor path that blocks on the adapter —
`_settle_now` (`start`), `_collect` (`resume`) and the `_recover` ladder including `lookup` —
runs inside a keeper that renews the claim on a background daemon thread for the whole
duration of the call:

* **Period.** Derived from the ledger's own `lease_seconds`
  (`heartbeat_interval_for` = lease / 3, floor 1 ms), never hard-coded, so `interval < lease`
  stays true when the lease is reconfigured. With the 60-second default that is a beat every
  20 seconds. Both the period and the wait itself are injectable, which is how the tests drive
  renewal with a synchronisation primitive instead of wall-clock time.
* **Fail-closed.** The first failed renewal — rotated token, lost lease, unreadable ledger —
  stops the keeper and is re-raised at the next ownership checkpoint. A Coordinator that lost
  ownership records no receipt, no settlement, and advances no workflow state; the run stops
  as BLOCKED with `IDEMPOTENCY_LEASE_LOST`. A lost lease deliberately does **not** re-enter
  the claim path: this process may already have created the external effect.
* **Checkpoints on both sides of a write, and at the exit.** A checkpoint taken only *before*
  each write leaves a gap the fence cannot cover, because not every renewal failure is an
  ownership rotation: a `RuntimeStateLockTimeout` or a transient unreadable ledger leaves the
  lease token perfectly valid, so a failure landing between the last checkpoint and the write
  is accepted by the fence and would otherwise be swallowed. So `executor._committed()` wraps
  every ownership-sensitive write (`settle`, `record_receipt`) in a checkpoint on *each* side,
  and `LeaseKeeper.__exit__` takes the last one — reporting a recorded renewal failure, not
  only a failed cleanup, so a failure landing after the final write cannot die with the
  keeper. The write that had already landed is deliberately left standing: it is the durable
  record of an external effect that really did settle, and a successor adopts it as
  `ALREADY_SETTLED` instead of re-running the work. What fails closed is the executor — it
  names the loss rather than reporting success on a claim it can no longer vouch for. Exit
  still never masks an exception the wrapped body was already raising.
* **Cleanup, verified.** The keeper is a context manager, so success, exception and
  cancellation all stop and join the beat thread — and `stop()` checks that the join actually
  worked. It revokes the keeper *before* joining (the beat loop re-reads that flag immediately
  before and immediately after each renewal, so a thread that outlives `stop()` writes no
  further renewal), keeps the thread handle until the thread is really gone, and reports a
  join that timed out as `LeaseKeeperNotStopped` — a `LeaseRenewalFailed`, so the executor
  fails closed on it exactly as it does on a failed renewal, rather than reporting a clean
  shutdown on top of a live orphan. That matters because an orphan is the mirror image of
  this whole defect: a thread renewing the lease of an intent nobody is working on any more
  would block a *legitimate* takeover. The failure is sticky (a cleanup that failed is never
  later reported as clean), `stop()` stays safe to repeat, and it never masks an exception the
  wrapped body was already raising. The thread is a daemon on top of all that, so a wedged
  renewal cannot hold up process exit either.
* **Takeover is preserved.** The keeper lives and dies with the process that owns the claim.
  When that process is killed the beats stop, the lease lapses on schedule, and the existing
  observe/takeover/recovery ladder runs exactly as before.

Because renewal happens on a second thread, `FileRuntimeStateStore._locked()` guards its
re-entrancy depth with a `threading.RLock`: re-entrancy is per *thread*, and an unguarded
counter would let the keeper's thread skip `flock` entirely and read/write the ledger with no
inter-process lock at all.

Known limits: renewal cannot interrupt a blocking adapter call, so a lost lease is detected at
the next checkpoint rather than the instant it happens. In that window a *rotated* token is
refused by the fence; a renewal failure that leaves the token valid is caught instead by the
checkpoint taken after the write (or, for the last write, at the keeper's exit), which stops
the executor rather than un-writing what already landed. A renewal already inside the ledger when `stop()`
runs cannot be recalled either: that one write lands, but it is not counted as a beat and no
further renewal follows, so an abandoned lease lapses after at most one more period instead of
being held indefinitely. The default beat waits in real seconds, so a run
wired to a test clock must inject a waiter (as the tests do). And a lease is still lost if the
whole process is stopped (`SIGSTOP`, a long GC pause, a suspended laptop) for longer than one
lease period; that is the case the takeover ladder exists for.

The ledger is validated strictly on read: schema version, top-level container, record
container, closed record keys, status vocabulary, key/`intent_id` agreement, receipt and
settlement shape, and settlement identity. A malformed or incompatible ledger raises
`RuntimeStateCorrupt` *before* any external effect and is never read as an empty ledger.

Records are closed at the field level too. A receipt holds only durable external identifiers
(`RECEIPT_KEYS`: `task_id`, `dispatch_id`, `external_id`, `intent_id`), each a non-empty
string, and once the effect exists (`EFFECTED`/`SETTLED`) it must name at least one of
`RECEIPT_IDENTITY_KEYS` (`task_id`, `external_id`) — an `EFFECTED` record with an empty or
identifier-free receipt asserts that an effect exists while naming nothing that could ever
reconcile it, so it is corrupt on read rather than resumable. A stored settlement must carry
exactly the canonical `SettlementEvent` vocabulary. `claim()` then re-checks the *stored*
identity against the intent presenting itself across all of `IDENTITY_KEYS` (`run_id`,
`phase`, `role`, `round_kind`, `command_id`, `payload_digest`) and raises
`RuntimeStateConflict` on any mismatch: `validate_record` never sees the intent, so record
coherence alone cannot prove the record belongs to *this* intent, and a digest-only
comparison left every other identity field forgeable.

Recovery of a claim left behind by a dead owner follows a fixed ladder that never duplicates
an effect: ask the adapter for a settlement of the stable identity; resume/observe an
`EFFECTED` record's already-named external effect; look an untracked `CLAIMED` record up by
stable intent identity; re-run **only** when the lookup proves nothing was created; otherwise
terminate `BLOCKED`. The two lookup/resume steps are *optional* adapter capabilities
(`external_lookup`, `external_resume`); an adapter that cannot honestly implement one must not
declare it, and the ladder then fails closed instead of guessing.

## Repository and artifact binding

A Worker settlement may carry a normalized `binding` (`repository`: `head_sha`, `tree_digest`,
`dirty`; `artifact`: `artifact_root_id`, `relative_path`, `digest`, `evidence_ids`). It lives
inside `result`, so it is covered by the settlement digest and a tampered binding fails the
integrity check. `APPLY_RESULT` validates it against the intent -- the artifact root is pinned
to the run's own -- and advances `repository_binding`/`artifact_binding` *before* the Reviewer
is dispatched, so a Reviewer intent is always bound to the exact Worker output it judges. A
review whose intent binding no longer matches state fails closed as `STALE_REVIEW_BINDING`.
Gate passes record `head_sha`, `tree_digest`, `artifact_digest` and the full `reviewed_binding`;
`routing.verify_final_review_binding` turns "the Final Reviewer reviewed the final head and
artifacts" into a checkable fact, and a Final Review PASS bound to a stale tree cannot complete
the run.

## Durable pause and resume (OS-31)

A decision block is no longer an absorbing terminal. When the adapter declares both
`human_approval` and `lifecycle_settlement`, `NEEDS_INPUT`/`CONFLICT` routes to the new
`PAUSE` node instead of `BLOCK`; the `TERMINAL` node then writes **no** terminal status and
sets `run_lifecycle = "WAITING_FOR_INPUT"`, which is deliberately absent from
`TERMINAL_STATUSES`. An adapter that declares neither capability keeps exactly the pre-OS-31
behaviour.

Two durable tiers, one authority:

- **Tier 1, `checkpoint_store.py`** — `FileCheckpointSaver`, an in-repository
  `BaseCheckpointSaver` over the already-pinned `langgraph-checkpoint` serializer (no new
  pinned dependency). It is the **sole** input to state reconstruction, keeps an explicit
  `head` pointer written inside the same critical section as the `put`, and *retires* a
  disposed run's thread rather than deleting it.
- **Tier 2, `pause_store.py`** — `.pause_state.json` per run: discovery identity, the
  run-scoped claim/lease fence, the checkpoint pointer and digest, the disposition, the
  applied set, and a subordinate `projection` of the checkpoint. Beside it,
  `.settlement_journal.json` records one row per dispatch, every write landing strictly
  **before** the external effect it describes, so a successor Coordinator can reconstruct
  work the dead process held only in memory.

`pause_runtime.py` ties them together and owns C1-C4: a record is written only after the
checkpoint commits (C1), must name its own thread's head and digest (C2), must agree with the
projection field for field (C3, refused rather than repaired in either direction), and a
checkpoint with no record is re-derived **from the checkpoint** idempotently (C4). The repair
direction is checkpoint → record and never the reverse.

Resume is exactly-once by construction: one complete decision bundle yields one
`resume_bundle_id`, written as one atomic applied entry **before** the single graph
re-entry, so no partial per-item state can exist. A replay short-circuits with
`RESPONSE_ALREADY_APPLIED`; a differing answer is `RESPONSE_CONFLICT`, never arbitrated by
recency. A moved head, artifact digest or policy digest re-enters through the existing
correction machinery rather than applying the answer unconditionally, so the phase Reviewer
and Final Adversarial Review gates cannot be bypassed by a resume.

Terminal ownership is settled before the run may wait. Every dispatch is accounted on the
four axes and must reach `released`, `exited` or `retained_by_named_owner`; anything else
refuses the pause (`TERMINAL_OWNERSHIP_UNKNOWN`, `TERMINAL_ORPHAN_POSSIBLE`,
`TERMINAL_IDENTITY_UNVERIFIED`) and falls back to `BLOCK`. There is no `transferred`
disposition: an abandon that cannot discharge a row records it `residual`, reports it, and
the run then does **not** claim "no ambiguous terminal ownership".

## Ports and adapters

`ports.py` defines agent execution, artifact, runtime receipt, existing OS-30 human approval,
run-scoped pause state (`RunPauseStatePort`), lifecycle settlement (`LifecycleSettlementPort`),
clock, and ID protocols. `fake_adapter.py` provides Orca-independent deterministic execution.
`orca_adapter.py` composes OrcaRuntimeHarness-compatible execution primitives, strips runtime
handles from settlements, and owns no routing rules. Missing declared capabilities block before
dispatch.

## Migration matrix

| Former owner | Graph owner | Disposition |
| --- | --- | --- |
| Skill phase/FAIL/iteration/final-review prose | graph + pure router | prose becomes explanatory contract |
| `e2e_harness.run_workflow` | graph | test-only parity oracle for one compatibility release |
| `downstream_revalidation_set` | pure router | canonical HIGH-only suffix calculation |
| OrcaRuntimeHarness Task/Dispatch/lifecycle | Orca adapter | execution/provenance only |
| OS-28/29 decision contracts | existing modules | reused unchanged |
| OS-30 clarification v2 | existing HumanApprovalPort | publish and terminal block only |

## Extension points

OS-31 has landed: the durable checkpointer is installed by default, `WAITING_FOR_INPUT` is a
real lifecycle state, and the PAUSE/DISPOSE nodes consume OS-30 responses (see above). A
graph built without a durable checkpointer is refused at build time
(`DurableCheckpointerRequired`); `require_durable_checkpointer=False` is the named test-only
escape hatch that keeps the existing `MemorySaver` reconstruction tests valid. OS-37 implements `AgentExecutionPort` with
structured argv, durable idempotency receipts, ownership, and capability declarations; the graph
does not change. OS-38 source extraction remains out of scope.

## Known limitations

- **POSIX only.** The exclusive claim needs `fcntl.flock`. On a platform without it,
  `FileRuntimeStateStore` refuses to construct (`RuntimeStateLockUnavailable`) rather than
  degrade to the unlocked behaviour that allowed duplicate effects. Windows is unsupported.
- **`OrcaAdapter` declares `external_lookup` but not `external_resume`.**
  `orca orchestration task-list --run` returns each Task's full spec and every spec this
  adapter creates is the canonical intent JSON, so an existing Task *can* be found by stable
  `intent_id` — matching parses each spec and compares the top-level `intent_id`, so a foreign
  spec that merely quotes the id is not mistaken for this intent's Task. But `worker_done` is delivered once, to the owning process's message stream:
  a settlement delivered to a process that has since died cannot be re-collected through any
  documented Orca primitive, and `task-create` accepts no idempotency key. Recovery of an
  already-dispatched Orca effect therefore terminates `BLOCKED`
  (`IDEMPOTENCY_RECOVERY_UNSUPPORTED`) and the reconciliation is an operator decision.
  Closing that window is OS-37's production process/PTY ownership work.
- **A residual create-then-crash window remains.** The durable claim is written before
  `create_task`, so a crash is always detectable, but the external identifier only exists
  after the call returns. Without a caller-supplied idempotency key the window cannot be
  eliminated -- only made safe, which is what the lookup rung of the ladder does when the
  adapter supports it.
- **`InMemoryRuntimeStateStore` offers no inter-process exclusion.** Its lock is a thread
  lock; it is for single-process tests and must not stand in for the file store.
- **The runtime-state schema is `os40.runtime_state.v2`.** A `v1` ledger written by an earlier
  build is refused as `INCOMPATIBLE_RUNTIME_STATE` (a BLOCKED terminal, exit code 1) rather
  than silently ignored; stale ledgers must be removed deliberately.
