"""Orca adapter composed only from real ``OrcaRuntimeHarness`` primitives."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Callable

from . import artifact_identity
from . import pause_policy
from . import pause_store
from .contracts import (BASE_CAPABILITIES, EXTERNAL_LOOKUP, LIFECYCLE_SETTLEMENT,
                        ActionIntent, ExternalLookupUnavailable, SettlementEvent,
                        make_settlement_event)

# OS-31 SS4.2.1. `active`/`current` are documented ALIASES that the reading process
# re-resolves, so persisting one persists no worktree at all. Only a stable
# `id:<repo-id>::<path>` selector denotes the same worktree in every process.
WORKTREE_ALIASES = frozenset({"current", "active"})

# OS-37 D-1 / WI-02.  The named refusal ``interrupt`` returns.  A refusal with a name is
# handleable; an unnamed CLI failure invoking a nonexistent verb is not.
ORCA_INTERRUPT_PRIMITIVE_ABSENT = "ORCA_INTERRUPT_PRIMITIVE_ABSENT"

# ---- OS-14 pair preparation ----------------------------------------------------------
#: Fixed order => deterministic logs and a deterministic create sequence.
_PAIR_ROLES = ("worker", "reviewer")
#: Mirrors `start`'s own `mode` derivation.
_PAIR_ROLE_MODES = {"worker": "complete", "reviewer": "pass"}

PAIR_PREPARATION_REFUSAL_CODES = frozenset({        # CLOSED.  The complete set.
    "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT",   # neither record nor entry proves anything
    "PAIR_PREPARATION_BINDING_LOST",           # entries exist, the record does not
    "PAIR_PREPARATION_BINDING_UNVERIFIABLE",   # this process cannot verify the binding
    "PAIR_PREPARATION_BINDING_MISMATCH",       # a different routing, DRIVER or launch kind
    "PAIR_PREPARATION_RECORD_CORRUPT",         # incl. an older schema_version
    "PAIR_PREPARATION_OWNERSHIP_MISMATCH",     # the entry names a different run
    "PAIR_PREPARATION_OUTCOME_UNKNOWN",        # neither success nor confirmed absence
    "PAIR_PREPARATION_SESSION_UNVERIFIED",     # title matched, digest did not
    "PAIR_PREPARATION_SESSION_ABSENT",         # provably gone
    "PAIR_PREPARATION_SCOPE_UNRESOLVED",       # the scope could not be established
    "PAIR_PREPARATION_SESSION_NOT_DISTINCT",   # one handle for both roles
    "PAIR_PREPARATION_MODEL_DRIFT",            # cross-process model drift
    "PAIR_PREPARATION_MODEL_UNOBSERVED",       # mandatory re-verification left no
                                               # readable resolved model
    # ---- OS-14 BUGFIX, this correction run --------------------------------------
    "PAIR_PREPARATION_RECORD_LOST",            # (B3) preparation HAD started and the
                                               # document is gone: never a first pass
    "PAIR_PREPARATION_SESSION_USE_UNKNOWN",    # (B1) the session was used and the
                                               # outcome of that use is unrecorded
    "PAIR_PREPARATION_MODEL_HISTORY_CONFLICT", # (B2) the run's own model history
                                               # contradicts itself or is incomplete
})


def _observed_detail(observed: dict[str, str]) -> str:
    """``{"1": "<model-a>"}`` -> ``"gate iteration 1 -> '<model-a>'"``.

    OS-14 BUGFIX (review R2).  Ordered by the gate iteration as a NUMBER -- every stored
    key is a validated decimal -- so the refusal message a reader or a test sees is
    deterministic rather than dict-ordered.  A non-decimal key cannot come from the
    document (the entry validator refuses one) and is ordered last rather than raising:
    this function only ever builds the TEXT of a refusal, and a refusal must not become a
    ValueError on its way out.
    """
    def order(item: tuple[str, str]) -> tuple[int, int, str]:
        return (0, int(item[0]), "") if item[0].isdigit() else (1, 0, item[0])
    return "; ".join(f"gate iteration {round_} -> {model!r}" for round_, model
                     in sorted(observed.items(), key=order))


def _OrcaCommandRefused() -> type[BaseException]:
    """The typed refusal class, resolved lazily; ``Exception`` when the runtime is absent.

    This package deliberately takes no module-import-time dependency on
    ``scripts/orca_runtime_harness.py`` (see ``fake_adapter._import_orca_runtime``).
    Resolving to ``Exception`` cannot weaken anything: the generic ``except Exception`` arm
    already refuses with ``PAIR_PREPARATION_OUTCOME_UNKNOWN``, so a missing runtime can
    only make a verdict MORE conservative, never less.
    """
    try:  # repository layout
        from scripts.orca_runtime_harness import OrcaCommandRefused
    except ImportError:  # pragma: no cover - flat installed layout
        try:
            from orca_runtime_harness import OrcaCommandRefused  # type: ignore[no-redef]
        except ImportError:
            return Exception
    return OrcaCommandRefused


def _unknown_create_detail(exc: BaseException) -> str:
    """What an UNKNOWN creation outcome is allowed to say about itself.

    The exception TYPE is the verdict; the text is only the message.  No private recorder
    state, no `lifecycle_commands()`, no `self._raw` is read.
    """
    return (f"the terminal create for a prepared session did not report an outcome this "
            f"process could read ({type(exc).__name__}: {exc}); a session may or may not "
            "exist, so this is NEITHER success NOR confirmed absence and no durable "
            "result was recorded")


def _default_result_parser(attempt: Any, intent: ActionIntent) -> dict[str, Any]:
    """`decision_contract.parse_agent_settlement`, imported lazily.

    Lazy and here rather than at module scope for the same reason `graph._resolve_gate_
    contract` is: this module is inside the shipped engine package and
    `decision_contract` is a `tools/` sibling, so a module-scope import would make the
    package unimportable whenever the sibling is absent.
    """
    try:  # repository layout
        from scripts import decision_contract
    except ImportError:  # pragma: no cover - flat installed layout
        import decision_contract  # type: ignore[no-redef]
    return decision_contract.parse_agent_settlement(attempt, intent)


class OrcaAdapter:
    """Synchronous façade over ``create_task`` and ``run_existing_task``."""

    def __init__(self, harness: Any,
                 result_parser: Callable[[Any, ActionIntent], dict[str, Any]] | None = None,
                 runtime_state: Any = None, settlement_journal: Any = None,
                 approval_port: Any = None,
                 pair_binding: Any = None, pair_preparation: Any = None):
        self.harness = harness
        # OS-42: the default parser now TRANSPORTS an agent's decision-gate output into
        # `result["gate"]` instead of refusing any body that is not JSON. A JSON body
        # still parses exactly as before, so every scripted path is unchanged.
        self.result_parser = result_parser or _default_result_parser
        self.runtime_state = runtime_state
        # OS-31: the durable, run-scoped, append-then-promote journal. Every write lands
        # strictly BEFORE the external effect it describes, because process memory
        # (`_receipts` here, `_terminals` in the harness) is exactly what a successor
        # Coordinator does not have.
        self.settlement_journal = settlement_journal
        self.approval_port = approval_port
        # OS-14: the run's LAUNCH RECORD (read-only here; only the launcher writes one)
        # and the per-(phase, gate_iteration, role) PREPARATION entries.  Both default to
        # None so every existing construction binds unchanged, and when `pair_binding` is
        # None the binding check does not run at all -- that is the FEATURE being unwired,
        # a construction-level axis deliberately separate from the run-root axis.
        self.pair_binding = pair_binding
        self.pair_preparation = pair_preparation
        self._receipts: dict[str, dict[str, Any]] = {}
        self._events: dict[str, SettlementEvent] = {}

    def capabilities(self) -> frozenset[str]:
        """The capabilities Orca's primitives actually support -- and no others.

        ``external_lookup`` is declared because ``orca orchestration task-list --run`` returns
        each Task's full spec, and every spec this adapter creates is the canonical intent
        JSON, so an existing Task can be found by stable ``intent_id``.

        ``external_resume`` is deliberately **not** declared.  ``worker_done`` is delivered
        once, to the message stream of the process that owns the run; a settlement delivered
        to a process that has since died cannot be re-collected through any documented Orca
        primitive, and ``task-create`` accepts no idempotency key that would let one be
        reconstructed.  Rather than pretend otherwise, recovery of an already-dispatched
        effect fails closed (``IDEMPOTENCY_RECOVERY_UNSUPPORTED`` -> BLOCKED) and the
        remaining reconciliation is an operator decision.  Closing that window is OS-37's
        production process/PTY ownership work, not OS-40's.

        OS-49: ``model_selection_verified`` is deliberately **not** declared, for TWO
        independent reasons, either of which alone forbids the token.

        (i) This adapter cannot REQUEST a model selection.  Orca's
        ``worker-start --model`` is unreachable from this runtime -- the recognized-agent-id
        rung has no code path here, and the two rungs this adapter does use refuse
        ``--model`` at flag validation -- and no in-band model-selection syntax,
        acknowledgement format or output semantics has been observed for any agent this
        adapter drives.  So it can name no member of the harness's closed
        ``MODEL_SELECTION_REQUEST_METHODS``.  That vocabulary is deliberately per-runtime
        rather than per-CLI: this module branches on no agent's identity, here or anywhere.

        (ii) It cannot OBSERVE a resolution.  It has no declared resolved-model locator,
        and a launch receipt's own echo of what was asked for is not an observation of what
        a provider resolved.

        This is what makes the real-runtime path fail closed: a profile that declares a
        model is refused at profile-validation time with ``AGENT_MODEL_NOT_SUPPORTED``,
        before any Run exists, and a declared model that somehow reached a dispatch would be
        refused again at the pre-delivery barrier.  Supplying the first adapter that can
        honestly declare both legs is OS-14's work, not OS-49's.

        OS-14 PRECISION, because the pair-preparation work could otherwise be misread as
        changing this answer: this adapter now PREPARES and VERIFIES a Worker/Reviewer pair
        through the harness's injected ``model_driver`` seam, and that changes NOTHING here.
        The token still says what THIS adapter can request and observe BY ITSELF, and the
        answer is still neither: the request and the observation belong to the driver, the
        driver is injected rather than named here, and a run with no driver stays refused at
        the declaration gate.  Wiring the two OS-14 pair stores declares no token either --
        ``LIFECYCLE_SETTLEMENT`` still hangs off ``settlement_journal`` and off nothing else.
        """
        offered = BASE_CAPABILITIES | frozenset(
            {"dispatch_provenance", "dependency_edges", "runtime_ownership", EXTERNAL_LOOKUP}
        )
        # OS-31: declared only when the durable journal that makes them honourable is
        # actually wired in. An adapter that cannot reconstruct the dispatch set from disk
        # does not satisfy LifecycleSettlementPort and must not claim it -- pause then
        # correctly falls back to BLOCK.
        if self.settlement_journal is not None:
            offered = offered | frozenset({LIFECYCLE_SETTLEMENT})
        if self.approval_port is not None:
            offered = offered | frozenset({"human_approval"})
        return offered

    def lookup(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Find the Task created for this stable intent, or prove that none was.

        Returns ``None`` only when the run's Task listing was read successfully and contains
        no Task whose spec *is* this intent -- the one situation in which re-running the
        effect is safe.  Matching parses each spec and compares the top-level ``intent_id``
        rather than searching the raw text, so an unrelated spec that merely quotes the id is
        not mistaken for this intent's Task.  Anything that leaves existence unknown raises
        :class:`ExternalLookupUnavailable` so the caller stops instead of guessing.
        """
        run_id = getattr(self.harness, "run_id", None)
        if not run_id:
            raise ExternalLookupUnavailable("no run is bound; task existence cannot be read")
        try:
            payload = self.harness.call("orchestration", "task-list", "--run", run_id)
            tasks = payload["result"]["tasks"]
        except Exception as exc:  # noqa: BLE001 - any read failure is "unknown", not "absent"
            raise ExternalLookupUnavailable(f"task listing unreadable: {exc}") from exc
        if not isinstance(tasks, list):
            raise ExternalLookupUnavailable("task listing has an unexpected shape")
        for task in tasks:
            if not isinstance(task, dict) or "spec" not in task:
                raise ExternalLookupUnavailable(
                    "task listing omits specs; intent identity cannot be matched")
            if self._spec_intent_id(task["spec"]) == intent["intent_id"]:
                return {"task_id": task.get("id"), "intent_id": intent["intent_id"]}
        return None

    @staticmethod
    def _spec_intent_id(spec: Any) -> str | None:
        """The top-level ``intent_id`` of a Task spec this adapter wrote, or ``None``.

        Every spec ``start`` creates is the canonical intent JSON, so identity is a parsed
        field comparison.  A substring search over the raw text would also match a spec that
        merely *mentions* the id -- for example one whose payload quotes another intent --
        and a foreign spec must not be mistaken for this intent's effect.  Anything that is
        not a JSON object simply belongs to no intent.
        """
        if not isinstance(spec, str):
            return None
        try:
            parsed = json.loads(spec)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        found = parsed.get("intent_id")
        return found if isinstance(found, str) else None

    @staticmethod
    def _parse_result(attempt: Any, intent: ActionIntent) -> dict[str, Any]:
        try:
            result = json.loads(attempt.body)
        except (AttributeError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY") from exc
        if not isinstance(result, dict):
            raise ValueError("MALFORMED_ORCA_SETTLEMENT_BODY")
        return result

    @staticmethod
    def _role(intent: ActionIntent) -> str:
        return {"WORKER": "worker", "PHASE_REVIEWER": "reviewer",
                "FINAL_REVIEWER": "final_reviewer"}[intent["role"]]

    def start(self, intent: ActionIntent, *, lease_token: str | None = None) -> dict[str, Any]:
        """Create the Task once, recording its identity under the caller's lease.

        Every ledger write below is fenced by ``lease_token``: an executor whose lease was
        taken over while it was blocked in ``create_task`` is refused here rather than
        overwriting the successor's receipt with a second Task's identity.
        """
        existing = self._receipts.get(intent["intent_id"]) or self._durable_receipt(intent)
        if existing is not None:
            if existing["payload_digest"] != intent["payload_digest"]:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            return deepcopy(existing)
        spec = json.dumps(intent, sort_keys=True, separators=(",", ":"))
        # ---- two PURE derivations, hoisted from below.  No I/O, no effect. ----
        role = self._role(intent)
        # OS-42 F-001: read the ONE derivation. `validate_settlement_node` binds the
        # returned record with the identical call, so ingress and egress cannot drift.
        phase = artifact_identity.contract_phase(intent["role"], intent["phase"])
        # OS-42: READ the one authoritative one-based ordinal instead of recomputing it.
        # The expression that used to live here -- final_review_iteration for a Final
        # Reviewer, phase_iteration + 1 otherwise -- IS the derivation, and it is now
        # single-sourced in `artifact_identity.gate_iteration` and stamped on the intent,
        # so the dispatched task context, the artifact path and the applied result can no
        # longer disagree about which gate attempt this was.
        iteration = intent["gate_iteration"]
        # ---- OS-14: the LAUNCH-RECORD check.  UNCONDITIONAL, before ANY effect, and
        #      deliberately OUTSIDE the pair_admission_required guard. ----
        self._assert_preparation_binding(intent)
        prepare = (self.pair_preparation is not None
                   and self._pair_admission_required(role, phase))
        # ---- E0: the PLANNED row, written BEFORE `task-create` ----
        # Not one field of it is a runtime observation of an effect -- role, origin, the
        # run-unique title and the stable worktree selector are all this caller's own
        # choice -- so not one field of it can be lost with the effect.
        planned = self._journal_planned(
            intent,
            pair_title=(self._pair_terminal_title(phase=phase, attempt=iteration,
                                                  role=role) if prepare else None))
        # ---- OS-14: PAIR PREPARATION, strictly BEFORE create_task ----
        # The only placement that keeps the same-process retry working: the durable
        # receipt is written only AFTER `create_task`, so a preparation failure leaves the
        # runtime record at CLAIMED and `_recover` takes the LOOKUP rung and re-runs
        # `start` instead of stopping at IDEMPOTENCY_RECOVERY_UNSUPPORTED.
        prepared = (self._prepare_pair(phase=phase, attempt=iteration, planned=planned,
                                       delivery_role=role)
                    if prepare else {})
        task_id = self.harness.create_task(spec)
        # The external Task now exists.  Record its durable identity immediately so a crash
        # before the dispatch settles cannot look like "never started" on the next process.
        self._record_receipt(intent, {"task_id": task_id}, lease_token)
        self._journal(intent["intent_id"], stage="OPENED", task_id=task_id,
                      opened_at=_now())
        mode = "complete" if intent["role"] == "WORKER" else "pass"
        # ---- OS-14 BUGFIX (review B1): the session USE record, BEFORE the delivery ----
        # The same record-then-effect discipline `_prepare_role` uses for
        # `CREATE_INTENDED`, and for the same reason: a crash between this write and the
        # dispatch leaves the session recorded as USED WITH AN UNKNOWN OUTCOME, which is
        # refused -- never as unused, which would be re-delivered.
        #
        # Placed as LATE as it can be and still precede the delivery: strictly after
        # `create_task` and strictly before `run_existing_task`, which is the call that
        # delivers.  Earlier -- before `create_task` -- would turn the ADMISSION-boundary
        # interruption (T8(d): the Task creation itself failed, so nothing was delivered
        # and nothing could have been) into an unknown use, which is a refusal this
        # record has no business producing.
        use = self._record_delivery_intent(role=role, phase=phase, attempt=iteration,
                                           intent=intent, handle=prepared.get(role))
        attempt, terminal = self.harness.run_existing_task(
            role, iteration, mode, task_id,
            phase=phase, spec=spec, round_kind=intent["round_kind"].lower(),
            # `None` is `run_existing_task`'s own default, so the non-prepared path is
            # byte-identical: a prepared handle suppresses the lazy create, nothing else.
            terminal=prepared.get(role),
            terminal_title=(planned or {}).get("terminal_title"),
            terminal_worktree=(planned or {}).get("terminal_worktree"),
            terminal_observer=(self._journal_intended(intent["intent_id"])
                               if planned else None),
            repair_instruction=intent.get("repair_instruction"),
        )
        # The OBSERVED half of the same use record: which Dispatch actually used the
        # session.  After the effect, because that is when it exists -- exactly as the
        # entry's `CREATED` digest is written only after the create was observed.
        # A delivery whose Dispatch id this process cannot read leaves the row at
        # `DELIVERY_INTENDED`, which is exactly "used, outcome unrecorded" -- the
        # fail-closed reading.  It is NOT promoted to `DELIVERED` with an empty owner,
        # which the use-row validator refuses anyway, and it does not turn a settled
        # delivery into an exception.
        if use is not None and attempt.dispatch_id:
            self.pair_preparation.record_session_use(
                use["terminal_digest"], stage="DELIVERED", phase=phase,
                gate_iteration=iteration, role=role, intent_id=intent["intent_id"],
                dispatch_id=attempt.dispatch_id,
                delivery_attempt=use["delivery_attempt"])
        result = self.result_parser(attempt, intent)
        event = make_settlement_event(intent, result, occurred_at="1970-01-01T00:00:00Z")
        receipt = {"intent_id": intent["intent_id"], "payload_digest": intent["payload_digest"],
                   "task_id": task_id, "dispatch_id": attempt.dispatch_id, "terminal": terminal}
        self._receipts[intent["intent_id"]] = receipt
        self._events[intent["intent_id"]] = event
        # ``terminal`` is a runtime handle and is deliberately never persisted.
        self._record_receipt(intent, {"task_id": task_id, "dispatch_id": attempt.dispatch_id},
                             lease_token)
        if self.runtime_state is not None:
            self.runtime_state.settle(intent["intent_id"], event, lease_token)
        return deepcopy(receipt)

    # ---- OS-31: the durable settlement journal (SS4.2.1) ----
    def _journal(self, intent_id: str, *, stage: str, **fields: Any) -> None:
        if self.settlement_journal is not None:
            self.settlement_journal.record(intent_id, stage=stage, **fields)

    def origin_worktree_selector(self) -> str:
        """The stable `id:<repo-id>::<path>` selector of the worktree this process is in.

        Read through the verb the harness ALREADY executes during contract validation, so
        no new grammar is introduced, and read strictly before E1 -- it is a property of
        where this Coordinator is, not an observation of an effect E1 or E2 produced, which
        is exactly why it can be journalled before the crash window opens.
        """
        try:
            payload = self.harness.call("worktree", "current")
            worktree_id = payload["result"]["worktree"]["id"]
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never an alias
            raise ExternalLookupUnavailable(
                f"DISPATCH_UNACCOUNTED: the origin worktree could not be resolved: {exc}"
            ) from exc
        if (not isinstance(worktree_id, str) or "::" not in worktree_id
                or worktree_id in WORKTREE_ALIASES):
            raise ExternalLookupUnavailable(
                "DISPATCH_UNACCOUNTED: `worktree current` returned no stable "
                f"<repo-id>::<path> identity ({worktree_id!r}); substituting the alias "
                "would produce a row that LOOKS recoverable and is not")
        return f"id:{worktree_id}"

    def _journal_planned(self, intent: ActionIntent, *,
                         pair_title: str | None = None) -> dict[str, Any] | None:
        """Refuse BEFORE E1 rather than fall back to the alias: nothing is made, nothing leaks.

        OS-14: on the pair-admission path the row carries the PREPARED title, because
        `terminal=<prepared handle>` means `create_fake_terminal` does not run inside
        `run_existing_task` and the title seam is unused for that dispatch -- while
        `terminal_observer` still writes INTENDED with the prepared handle's digest.  With
        the row's title left at `os31-{run}-{intent}` the row would hold a digest for a
        session whose title it does not name, and `recover_handle` would title-narrow to
        ZERO candidates and report `not_listed` -- claiming a live session is gone.
        `pair_title` is computed from the intent alone, so it is available BEFORE
        preparation runs, and it is `None` on every routing-less path (where the
        expression below is the current literal, byte for byte).
        """
        if self.settlement_journal is None:
            return None
        run_id = getattr(self.harness, "run_id", "") or ""
        intent_id = intent["intent_id"]
        role = "phase_reviewer" if intent["role"] != "WORKER" else "phase_worker"
        self._journal(
            intent_id, stage="PLANNED", run_id=run_id,
            payload_digest=intent["payload_digest"],
            terminal_title=(pair_title or f"os31-{run_id}-{intent_id}"),
            terminal_worktree=self.origin_worktree_selector(),
            terminal_role="active_worker", terminal_origin="self_created",
            terminal_intended_role=role, terminal_owner=run_id or intent_id,
            created_by=run_id or intent_id, provenance_source="journal",
            planned_at=_now())
        return self.settlement_journal.row(intent_id)

    # ---- OS-14: pair preparation -----------------------------------------------------
    def _pair_admission_required(self, role: str, phase: str) -> bool:
        """Read the HARNESS's predicate; keep no second copy."""
        predicate = getattr(self.harness, "pair_admission_required", None)
        return bool(predicate(role, phase)) if callable(predicate) else False

    def _pair_terminal_title(self, *, phase: str, attempt: Any, role: str) -> str:
        run_id = getattr(self.harness, "run_id", "") or ""
        return f"{run_id}-pair-{phase}-{attempt}-{role}"

    def _refuse_preparation(self, code: str, message: str) -> None:
        """The ONE exit for every new refusal.  Returns nothing: it always raises.

        ``IdempotencyRecoveryError`` is the shipped adapter -> BLOCKED projection
        (``executor.py``: "``code`` is the terminal reason a caller projects onto a BLOCKED
        terminal state"), and ``launcher.execute_state`` performs that projection.  Raising
        it from an adapter is already done twice in ``standalone_adapter.py`` behind the
        same lazy import.
        """
        assert code in PAIR_PREPARATION_REFUSAL_CODES, code    # a typo cannot ship
        from .executor import IdempotencyRecoveryError          # lazy, as standalone does
        raise IdempotencyRecoveryError(code, message)

    def _assert_preparation_binding(self, intent: ActionIntent) -> None:
        """Require a POSITIVE durable statement before taking ANY non-blocking path.

        Deliberately OUTSIDE the pair-admission guard: a successor built with no routing
        answers ``False`` to that predicate, so a check placed inside it would never run --
        which is exactly how the shipped adoption path delivered unverified before OS-14.
        """
        if self.pair_binding is None:
            return                  # the FEATURE is unwired (construction-level axis)
        try:
            binding = self.pair_binding.binding()
            prepared_any = (self.pair_preparation is not None
                            and self.pair_preparation.has_any())
        except pause_store.PauseStoreError as exc:   # incl. PairPreparationCorrupt
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            return
        if binding is None:
            if prepared_any:
                self._refuse_preparation(
                    "PAIR_PREPARATION_BINDING_LOST",
                    "preparation entries exist for this run but its run-root launch "
                    "record does not; an entry can only exist if the record was written "
                    "first, so the record has been LOST -- never a legacy run")
            self._refuse_preparation(
                "PAIR_PREPARATION_LAUNCH_RECORD_ABSENT",
                "no launch record and no preparation entry: nothing proves whether this "
                "run was launched model-aware, and an absence is not a verdict in either "
                "direction.  Relaunch the run (nothing was prepared under it)")
            return
        # The ONE derivation, resolved DEFENSIVELY and only now -- after the two
        # absent-record rows above, which do not consult it at all, so a harness that
        # cannot answer never hides `PAIR_PREPARATION_BINDING_LOST` or
        # `PAIR_PREPARATION_LAUNCH_RECORD_ABSENT` behind an AttributeError.
        #
        # A harness that does not implement `routing_binding()` cannot STATE its own
        # routing identity, so this process cannot verify the run's launch binding: that is
        # an unanswered question, and the answer to an unanswered question here is a NAMED
        # refusal, never a crash and never a pass.  Same discipline as
        # `_pair_admission_required` above (which reads its predicate through `getattr`)
        # and as `OrcaRuntimeHarness._routing_is_model_aware` (which reads an object that
        # cannot answer as model-AWARE): an undeclared fact is unknown, not false.
        derive = getattr(self.harness, "routing_binding", None)
        if not callable(derive):
            self._refuse_preparation(
                "PAIR_PREPARATION_BINDING_UNVERIFIABLE",
                "this process's harness cannot state its own routing identity "
                f"({type(self.harness).__name__} implements no routing_binding()), so "
                "this run's launch record cannot be reconciled against anything")
        live = derive()
        if binding["model_aware"] == "false":
            if live["model_aware"] == "true":
                self._refuse_preparation(
                    "PAIR_PREPARATION_BINDING_MISMATCH",
                    "this run was LAUNCHED legacy and this process holds a model-aware "
                    "routing; a run is not re-bound to a different kind of launch mid-run")
            return                  # LEGACY -- authorised by a POSITIVE statement
        if live["model_aware"] != "true":
            self._refuse_preparation(
                "PAIR_PREPARATION_BINDING_UNVERIFIABLE",
                "this run was launched model-aware and this process holds no model-aware "
                "routing, so its routing/driver binding cannot be verified")
        # ---- the FULL launch identity, cell by cell, BEFORE any effect ---------------
        # Driven off the key tuple itself, so a cell added to the record later is compared
        # by construction rather than being silently ignored.
        for key in pause_store.PAIR_LAUNCH_IDENTITY_KEYS:
            if live[key] != binding[key]:
                self._refuse_preparation(
                    "PAIR_PREPARATION_BINDING_MISMATCH",
                    f"this run's launch record binds {key}={binding[key]!r} and this "
                    f"process holds {live[key]!r}: a DIFFERENT launch identity under this "
                    "run's own name.  The routing AND the driver are both part of that "
                    "identity -- a run is not re-bound to a different model-selection "
                    "driver mid-run, and past verified evidence is never restored as this "
                    "process's verification success")

    def _prepare_pair(self, *, phase: str, attempt: Any,
                      planned: dict[str, Any] | None,
                      delivery_role: str = "") -> dict[str, str]:
        """Prepare BOTH roles of this gate round; return ``{role: handle}``.

        Creates no Task, no Dispatch, and SENDS NOTHING to any session.  Session creation,
        ledger adoption and verification only -- the adoption is a write to THIS process's
        in-memory terminal ledger and issues no ``orca`` command.

        ``delivery_role`` is the ONE role this ``start`` is about to deliver to.  It is
        load-bearing for review B1: a pair must be prepared and verified in FULL before
        the first delivery of EITHER role (OS-49 pair admission), so the COUNTERPART's
        already-used session is legitimately adopted for verification -- but only the
        role actually being dispatched is a candidate for a SECOND delivery, and only that
        role is therefore put through the reuse gate.  Without the distinction the
        Reviewer's own turn would put the Worker's settled session through a reuse gate it
        has no reason to pass and replace it for nothing.
        """
        store = self.pair_preparation
        run_id = getattr(self.harness, "run_id", "") or ""
        selector = ((planned or {}).get("terminal_worktree")
                    or self.origin_worktree_selector())
        try:
            entries = {role: store.entry(phase, attempt, role) for role in _PAIR_ROLES}
            prepared_any = store.has_any()
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            raise                                   # unreachable; _refuse always raises
        # ---- OS-14 BUGFIX (review B3): an ABSENT record is not a FIRST preparation ----
        self._assert_preparation_not_lost(prepared_any=prepared_any)
        # ---- OS-14 BUGFIX (review R2): the MODEL HISTORY, before ANY effect -----------
        # Hoisted out of the verification loop below, which is where it used to run and
        # which is already too late: `_prepare_role` CREATES sessions, so a run whose
        # required baseline row had been removed created two iteration-2 sessions (and,
        # with the shipped engine, a Task) before the refusal.  This reads nothing but the
        # durable document, so it can run here -- strictly before the first `terminal
        # list`, the first `terminal create`, the first adoption, the first
        # `verify_model_identity` and therefore before any selection has switched a
        # session's model at all.  Which is also why no authority has to be revoked on
        # this path: there is none yet to revoke.
        baselines = {role: self._durable_model_baseline(
            store, phase=phase, attempt=attempt, role=role, entry=entries[role])
            for role in _PAIR_ROLES}
        # The listing is read ONCE, and ONLY when something was already prepared: a first
        # pass has no title and no digest to resolve, so it issues no `terminal list`.
        listing: Any = None
        scope_resolved = True
        if any(entry is not None for entry in entries.values()):
            listing, scope_resolved = self._prepared_listing(selector)
        handles: dict[str, str] = {}
        for role in _PAIR_ROLES:
            handles[role] = self._prepare_role(
                role=role, phase=phase, attempt=attempt, selector=selector,
                run_id=run_id, store=store, entry=entries[role],
                listing=listing, scope_resolved=scope_resolved,
                is_delivery_target=(role == delivery_role))
        if len(set(handles.values())) != len(handles):
            self._refuse_preparation(
                "PAIR_PREPARATION_SESSION_NOT_DISTINCT",
                f"the runtime returned ONE handle for both roles of {phase!r} attempt "
                f"{attempt}; one physical session cannot be both sides of a pair")
        # ---- verification: BOTH roles, UNCONDITIONALLY, on EVERY pass ---------------
        for role in _PAIR_ROLES:
            expected = baselines[role]          # reconciled BEFORE any session existed
            declared = self.harness.resolved_agent_model(role, phase)
            self.harness.verify_model_identity("", handles[role], role=role, phase=phase,
                                               attempt=attempt)
            # The accepted canonical result, through the PUBLIC accessor, which works for
            # a handle created in THIS process and for an ADOPTED one alike -- because
            # `_prepare_role` registered the adopted handle BEFORE this call.
            resolved = self.harness.ledger_terminal(handles[role]).get(
                "resolved_model") or ""
            if declared and not resolved:
                # FAIL CLOSED: an unregistered handle reads back as the synthetic
                # `unknown_role` row, which carries no `resolved_model` key at all, so a
                # missing adoption would otherwise surface as a silent "" -- skipping the
                # drift comparison AND erasing the durable observation.
                self._revoke_model_authority(handles[role])
                self._refuse_preparation(
                    "PAIR_PREPARATION_MODEL_UNOBSERVED",
                    f"{phase!r} attempt {attempt} {role}: the routing declares "
                    f"{declared!r} and this process's re-verification left no readable "
                    "resolved model for the session; nothing is adopted, refreshed or "
                    "recorded on an unobservable verification")
            if expected and resolved and expected != resolved:
                # BEFORE any write: the prior durable observation is still on disk when
                # this refuses, so the next reader -- and the operator -- can still see
                # what the run originally observed.
                #
                # OS-14 BUGFIX (review B2).  `expected` is now the RUN-scoped
                # (phase, role) baseline, not only this gate iteration's own entry cell,
                # so the comparison survives an iteration change, a process change and a
                # session change.  And the refusal REVOKES this session's current
                # verification authority first: selection already ran, so the session is
                # on whatever the driver left it on and must not stay advertised as
                # verified -- while the HISTORY that grounds the refusal is preserved.
                self._revoke_model_authority(handles[role])
                self._refuse_preparation(
                    "PAIR_PREPARATION_MODEL_DRIFT",
                    f"{phase!r} attempt {attempt} {role}: this run's durable history "
                    f"established {expected!r} for this (phase, role) and this process "
                    f"re-verified {resolved!r}; the in-process drift leg is empty after a "
                    "restart and this run's own baseline is the only thing that still "
                    "knows what it resolved to.  A role's resolved model is a NEW RUN's "
                    "business, never a change inside this one")
            # ---- the DURABLE baseline, written BEFORE the entry observation ----------
            # OS-14 BUGFIX (review B2).  History first, deliberately: a crash between the
            # two leaves the STRICTER record standing, so the next process still refuses a
            # drift.  The other order would leave an entry observation with no baseline,
            # which `_durable_model_baseline` reads as a CONFLICT rather than as a licence
            # to mint a new one.
            if resolved:
                try:
                    store.record_role_history(phase, role, resolved,
                                              requested_model=declared,
                                              gate_iteration=attempt)
                except pause_store.PauseStoreError as exc:
                    self._revoke_model_authority(handles[role])
                    self._refuse_preparation(
                        "PAIR_PREPARATION_MODEL_HISTORY_CONFLICT", str(exc))
            # PRESERVE-UNTIL-COMPARED.  Reached only when the comparison SUCCEEDED
            # (equal, or no prior observation).  `resolved_model_observed` is supplied
            # ONLY when it is non-empty: `record()` merges the supplied cells and leaves
            # every other cell as it stands, so an undeclared role's pass refreshes the
            # stage and the timestamp without overwriting a stored observation with "".
            store.record(phase, attempt, role, stage="VERIFIED",
                         observed_at_run=run_id, verified_at=_now(),
                         **({"resolved_model_observed": resolved} if resolved else {}))
        return handles

    # ---- OS-14 BUGFIX helpers --------------------------------------------------------
    def _assert_preparation_not_lost(self, *, prepared_any: bool) -> None:
        """(B3) Separate a GENUINE first preparation from a LOST preparation document.

        The distinction cannot be made from the preparation document: an absent file and a
        never-written file read identically, which is the whole defect -- absence became
        "nothing was prepared", the role entry became ``None``, the listing was skipped
        and new sessions were created under the SAME titles as sessions that were still
        live.

        It IS made from the run's LAUNCH RECORD, which is a different document with a
        different lifetime and is the one place a marker can survive the loss it detects.
        The marker is set strictly BEFORE the first ``CREATE_INTENDED`` of the run, and a
        ``CREATE_INTENDED`` is itself written strictly before every ``terminal create``,
        so:

        * marker ABSENT  -> no entry was ever written -> no session was ever created.  A
          genuine first preparation, and the POSITIVE control proceeds exactly as before.
        * marker PRESENT and the document holds NO entry at all -> an entry existed and is
          gone.  Prior effects cannot be excluded, so this stops with a NAMED refusal
          BEFORE any new effect rather than creating a duplicate pair.

        Deliberately conservative in one documented place: a process that died between the
        marker and the first ``CREATE_INTENDED`` produced no external effect and is still
        refused here, because this gate reads the marker rather than guessing at the
        window.  The remedy is the operator procedure in ``docs/COMPATIBILITY.md`` -- it is
        never an unconditional relaunch.

        A construction with no launch-record store wired in (`pair_binding is None`) is the
        FEATURE being unwired and is left alone, exactly as `_assert_preparation_binding`
        leaves it alone.
        """
        if self.pair_binding is None:
            return
        try:
            marker = self.pair_binding.preparation_started()
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            return
        if prepared_any:
            return                      # entries exist: the ordinary recovery path
        if marker:
            self._refuse_preparation(
                "PAIR_PREPARATION_RECORD_LOST",
                "this run's launch record states that a preparation entry was written at "
                f"{marker} and this run's preparation document holds no entry at all, so "
                "the document has been LOST.  An absent record is not a first "
                "preparation: sessions this run created may still exist, and nothing "
                "here can prove they do not, so no session, Task or Dispatch is created")
            return
        try:
            self.pair_binding.mark_preparation_started()
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))

    def _durable_model_baseline(self, store: Any, *, phase: str, attempt: Any, role: str,
                                entry: dict[str, Any] | None) -> str:
        """(B2) This ``(run, phase, role)``'s durable NON-DRIFT baseline, or ``""``.

        The ``role_history`` row is the RUN-scoped authority: it survives an iteration, a
        process and a session change.  An entry's ``resolved_model_observed`` is one GATE
        ITERATION's accepted observation -- the narrow leg, and exactly why it was empty
        at the next iteration and the drifted value got stored as verified.

        OS-14 BUGFIX (review R2).  The observation leg is now read for EVERY gate
        iteration of this ``(run, phase, role)``, not only the current one.  Reading the
        current entry alone made the ROW's presence uncheckable precisely at an iteration
        boundary, where the current entry is legitimately absent: removing the one
        required row left both legs empty while iteration 1's own ``VERIFIED`` entry, on
        the same disk, still proved a baseline had been established -- and the drifted
        value was then minted as the new write-once baseline.

        So the row's PRESENCE is reconciled against every durable prior observation, and
        both failures are named refusals rather than choices:

        * any observation stands and there is NO row -> the row the writer always writes
          FIRST is missing.  A missing required record is not a licence to establish a new
          one, at this gate iteration or any later one.
        * the row and any observation DISAGREE -> the run's own history contradicts
          itself, and this process does not get to pick one of its two answers.

        ``""`` -- "no baseline, a first observation may establish one" -- is returned only
        when this run has recorded NO observation of this ``(phase, role)`` anywhere.  A
        genuinely new run reads its own empty document and gets exactly that.
        """
        try:
            # Two reads, and this ORDER is the strict one: were a concurrent writer to add
            # a baseline row between them, reading the row FIRST means this pass still
            # sees the row-less state and still refuses.  The other order could see a row
            # that the observations it then reads do not yet account for.
            history = store.role_history(phase, role)
            observed = dict(store.role_observations(phase, role))
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            return ""                               # unreachable; _refuse always raises
        baseline = (history or {}).get("resolved_model") or ""
        previous = (entry or {}).get("resolved_model_observed") or ""
        if previous:
            # The caller's own entry, taken from the SAME read that drove this pass, so
            # this method's answer cannot disagree with the entry the caller will promote.
            observed[str(attempt)] = previous
        if observed and not baseline:
            self._refuse_preparation(
                "PAIR_PREPARATION_MODEL_HISTORY_CONFLICT",
                f"{phase!r} attempt {attempt} {role}: this run's own preparation entries "
                f"record the accepted observation(s) {_observed_detail(observed)} and "
                "this run holds NO (phase, role) baseline row for them.  The baseline is "
                "written BEFORE every observation, so a row that is absent while an "
                "observation still stands means a REQUIRED RECORD IS MISSING -- which is "
                "never a licence to establish a new one")
        disagreeing = {round_: model for round_, model in observed.items()
                       if model != baseline}
        if baseline and disagreeing:
            self._refuse_preparation(
                "PAIR_PREPARATION_MODEL_HISTORY_CONFLICT",
                f"{phase!r} attempt {attempt} {role}: this run's (phase, role) baseline "
                f"row is {baseline!r} and this run's own preparation entries record "
                f"{_observed_detail(disagreeing)}; the run's own history contradicts "
                "itself and this process does not pick one")
        return baseline

    def _revoke_model_authority(self, handle: str) -> None:
        """Revoke the session's model verification AUTHORITY; keep its HISTORY.

        Called on every post-selection refusal in this method.  Reads the harness's own
        public operation through ``getattr`` -- a harness that does not implement it
        cannot have granted any authority through it either, so its absence can only make
        a later delivery MORE refused, never less.
        """
        revoke = getattr(self.harness, "invalidate_model_authority", None)
        if callable(revoke):
            try:
                revoke(handle)
            except Exception:  # noqa: BLE001 - a revocation never replaces the refusal
                pass

    def _prepare_role(self, *, role: str, phase: str, attempt: Any, selector: str,
                      run_id: str, store: Any, entry: dict[str, Any] | None,
                      listing: Any, scope_resolved: bool,
                      is_delivery_target: bool = False) -> str:
        verdict = pause_policy.resolve_prepared_terminal(
            entry, listing, run_id=run_id, scope_resolved=scope_resolved)
        supersede: dict[str, str] | None = None
        if verdict["action"] == "adopt":
            # ---- OS-14 BUGFIX (review B1): a digest match is not a reuse permission ----
            supersede = self._reuse_verdict(
                role=role, phase=phase, attempt=attempt, store=store,
                handle=verdict["handle"], is_delivery_target=is_delivery_target)
            if supersede is None:
                # A RECORD HIT creates nothing -- but it must be REGISTERED before
                # anything reads harness ledger state for it.  Idempotent: a handle this
                # process created is already in the ledger and keeps its own provenance.
                self.harness.adopt_prepared_terminal(verdict["handle"], role, phase=phase)
                return verdict["handle"]
        if verdict["action"] == "block":
            self._refuse_preparation(
                verdict["code"],
                f"{phase!r} attempt {attempt} {role}: stage="
                f"{(entry or {}).get('stage', 'absent')!r} "
                f"handle_recovery={verdict['handle_recovery']!r} "
                f"candidate={verdict.get('candidate_handle', '')!r}")
        create_attempt = (1 if entry is None
                          else int(entry["create_attempt"])
                          + (1 if entry["stage"] == "CREATE_REFUSED"
                             or supersede is not None else 0))
        title = self._pair_terminal_title(phase=phase, attempt=attempt, role=role)
        # ---- 1. INTENT, strictly BEFORE the external effect -------------------------
        store.record(phase, attempt, role, stage="CREATE_INTENDED",
                     create_attempt=str(create_attempt), terminal_title=title,
                     terminal_worktree=selector,
                     requested_model=self.harness.resolved_agent_model(role, phase),
                     create_intended_at=_now(),
                     # PROVENANCE of a superseding attempt, written in the SAME call that
                     # publishes it, so a replacement can never read as a first create.
                     **(supersede or {}))
        # ---- 2. the ONLY external effect of preparation ------------------------------
        try:
            handle = self.harness.create_fake_terminal(
                role, _PAIR_ROLE_MODES[role], iteration=attempt, phase=phase,
                title=title, worktree=selector)
        except _OrcaCommandRefused() as exc:
            if getattr(exc, "command", ())[:2] == ("terminal", "create") and (
                    getattr(exc, "ok", None) is False):
                store.record(phase, attempt, role, stage="CREATE_REFUSED",
                             create_settled_at=_now(),
                             refusal_command=json.dumps(list(exc.command),
                                                        separators=(",", ":")),
                             refusal_error_code=exc.error_code,
                             refusal_receipt_digest=exc.receipt_digest)
                raise                                # the runtime's OWN error, unchanged
            self._refuse_preparation("PAIR_PREPARATION_OUTCOME_UNKNOWN",
                                     _unknown_create_detail(exc))
        except Exception as exc:  # noqa: BLE001 - unknown is never confirmed absence
            # NO durable write: the entry stays at CREATE_INTENDED, which IS the record of
            # the uncertainty.  BaseException is NOT caught, so KeyboardInterrupt /
            # SystemExit / GeneratorExit escape as themselves.
            self._refuse_preparation("PAIR_PREPARATION_OUTCOME_UNKNOWN",
                                     _unknown_create_detail(exc))
        # ---- 3. IDENTITY, only AFTER observation -------------------------------------
        store.record(phase, attempt, role, stage="CREATED", create_settled_at=_now(),
                     terminal_digest=pause_policy.terminal_digest(handle))
        return handle

    def _reuse_verdict(self, *, role: str, phase: str, attempt: Any, store: Any,
                       handle: str,
                       is_delivery_target: bool) -> dict[str, str] | None:
        """(B1) May this digest-proved session be DELIVERED TO again?

        ``None`` means "adopt it, as before" -- which covers every case the shipped path
        already handled correctly: a session nothing has delivered to yet (its FIRST
        delivery), the COUNTERPART role of this round (not a delivery target at all, only
        a pair-admission participant), and an already-used session the SHIPPED REUSE GATE
        positively permits.  A returned mapping means the session may not be re-used and
        names what is being superseded and why, so the caller prepares a NEW, safely
        recorded session instead.

        The rules this encodes, exactly:

        * ``session_use`` absent -> UNUSED -> first delivery, no gate involved.  OS-14
          BUGFIX (review R1): this reading is now EARNED rather than assumed.  Every row
          the writer creates is CLAIMED by its own pair entry in the same atomic write,
          and the document reader refuses outright when a claimed row is gone -- so a
          REMOVED row never reaches this method, and "absent" means the document itself
          states no row was ever written.  A genuinely prepared-but-undispatched session
          is unaffected: nothing claims it, so nothing is missing.
        * ``session_use`` present but not ``DELIVERED`` -> USED WITH AN UNKNOWN OUTCOME ->
          NAMED refusal.  Unknown use is never read as unused.
        * ``session_use`` at ``DELIVERED`` -> the shipped reuse gate decides, through its
          ONE production consumer ``terminal_for_next_dispatch``, which asks
          ``reuse_eligible()`` with a FRESH liveness observation and includes the OS-49
          model reuse conditions.  Nothing here substitutes for it: not the digest match,
          not the adoption, not this round's model re-verification.
        * the gate refuses, or cannot be asked at all -> supersede.  Never deliver.
        """
        if not is_delivery_target:
            return None
        try:
            use = store.session_use(pause_store.terminal_use_digest(handle))
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            return None                             # unreachable; _refuse always raises
        if use is None:
            return None                             # never delivered to: the first time
        if use["stage"] != "DELIVERED" or not use["dispatch_id"]:
            self._refuse_preparation(
                "PAIR_PREPARATION_SESSION_USE_UNKNOWN",
                f"{phase!r} attempt {attempt} {role}: this run recorded the INTENT to "
                f"deliver to this session (stage={use['stage']!r}, delivery attempt "
                f"{use['delivery_attempt']}) and never recorded which Dispatch used it, "
                "so whether it was used -- and what it was left in -- is unknown.  "
                "Unknown use is not unused")
        gate = getattr(self.harness, "terminal_for_next_dispatch", None)
        reasons: tuple[str, ...] = ("reuse_gate_unavailable",)
        granted = None
        if callable(gate):
            try:
                granted = gate(
                    handle,
                    role=("phase_reviewer" if role.endswith("reviewer")
                          else "phase_worker"),
                    agent_command=self.harness.resolved_agent_command(role, phase),
                    requested_model=self.harness.resolved_agent_model(role, phase),
                    dispatch_id=use["dispatch_id"])
                decision = getattr(self.harness, "last_reuse_decision", None) or {}
                reasons = tuple(decision.get("reasons") or ())
            except Exception as exc:  # noqa: BLE001 - unreadable is never permission
                granted = None
                reasons = (f"reuse_observation_unreadable:{type(exc).__name__}",)
        if granted == handle:
            return None                             # the GATE permitted this reuse
        return {"supersedes_digest": use["terminal_digest"],
                "supersedes_reason": ",".join(reasons) or "reuse_refused"}

    def _record_delivery_intent(self, *, role: str, phase: str, attempt: Any,
                                intent: ActionIntent,
                                handle: str | None) -> dict[str, Any] | None:
        """(B1) Record the INTENT to deliver to a prepared session, before delivering.

        Returns the row written (so the settled half can name the same delivery attempt),
        or ``None`` on every path that has no prepared session -- a non-prepared role, a
        Final Reviewer, an unwired store -- which is exactly where the shipped behaviour
        is unchanged.
        """
        if handle is None or self.pair_preparation is None:
            return None
        digest = pause_store.terminal_use_digest(handle)
        try:
            existing = self.pair_preparation.session_use(digest)
            return self.pair_preparation.record_session_use(
                digest, stage="DELIVERY_INTENDED", phase=phase, gate_iteration=attempt,
                role=role, intent_id=intent["intent_id"],
                delivery_attempt=(1 if existing is None
                                  else int(existing["delivery_attempt"]) + 1))
        except pause_store.PauseStoreError as exc:
            self._refuse_preparation("PAIR_PREPARATION_RECORD_CORRUPT", str(exc))
            return None                             # unreachable; _refuse always raises

    def _prepared_listing(self, selector: str) -> tuple[Any, bool]:
        """The I/O half, copied from ``recover_handle`` rather than re-invented."""
        if not selector or selector in WORKTREE_ALIASES:
            self._refuse_preparation(
                "PAIR_PREPARATION_SCOPE_UNRESOLVED",
                f"{selector!r} is an alias, not a stable id:<repo-id>::<path> selector")
        try:
            listing: Any = list(self.harness.list_terminals(worktree=selector))
        except Exception:  # noqa: BLE001 - unreadable is unknown, never empty
            return None, False
        scope_resolved = True
        if not listing:
            resolved = self.harness.resolve_worktree(selector)
            expected = selector.split("id:", 1)[-1]
            scope_resolved = bool(resolved) and resolved.get("id") == expected
        return listing, scope_resolved

    def _journal_intended(self, intent_id: str) -> Callable[[str], None]:
        def observer(handle: str) -> None:
            # Between `terminal create` and `worker-start`: the ONLY point at which a
            # durable write can sit between the two effects.
            self._journal(intent_id, stage="INTENDED",
                          terminal_digest=pause_policy.terminal_digest(handle),
                          provenance_source="journal", intended_at=_now())
        return observer

    # ---- OS-31 LifecycleSettlementPort ----
    def open_dispatches(self) -> tuple[str, ...]:
        """The three-legged durable reconstruction, never ``self._receipts``.

        (1) journal rows not at DISPOSED, (2) durable runtime-state receipts with no
        journal row, (3) the authoritative `task-list --run` listing, whose Tasks appear in
        neither -- a FOREIGN Task this adapter did not create, reported rather than adopted.
        A source that cannot be read is "unknown", never "empty".
        """
        if self.settlement_journal is None:
            raise ExternalLookupUnavailable(
                "DISPATCH_UNACCOUNTED: no durable settlement journal is wired in")
        rows = self.settlement_journal.rows()
        found = {intent_id for intent_id, row in rows.items() if row["stage"] != "DISPOSED"}
        if self.runtime_state is not None:
            for intent_id in self._durable_intent_ids():
                if intent_id not in rows:
                    found.add(intent_id)
        for intent_id in self._listed_intent_ids():
            if intent_id not in rows:
                found.add(intent_id)
        return tuple(sorted(found))

    def _durable_intent_ids(self) -> tuple[str, ...]:
        reader = getattr(self.runtime_state, "_read", None)
        locked = getattr(self.runtime_state, "_locked", None)
        if reader is None:
            return ()
        records = reader() if locked is None else self._locked_read(locked, reader)
        return tuple(intent_id for intent_id, record in records.items()
                     if record.get("status") in ("EFFECTED", "SETTLED"))

    @staticmethod
    def _locked_read(locked: Any, reader: Any) -> dict[str, Any]:
        with locked():
            return dict(reader())

    def _listed_intent_ids(self) -> tuple[str, ...]:
        run_id = getattr(self.harness, "run_id", None)
        if not run_id:
            return ()
        try:
            tasks = self.harness.call("orchestration", "task-list",
                                      "--run", run_id)["result"]["tasks"]
        except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never empty
            raise ExternalLookupUnavailable(f"task listing unreadable: {exc}") from exc
        found = []
        for task in tasks or ():
            intent_id = self._spec_intent_id((task or {}).get("spec"))
            if intent_id:
                found.append(intent_id)
        return tuple(found)

    def recover_handle(self, intent_id: str) -> dict[str, Any]:
        """Enumerate, narrow by normalised run-unique title, then PROVE with the digest.

        The title narrows; the digest decides.  A handle is returned only for
        ``listing_verified``; every other cell of the SS4.2.1a table fails closed, and the
        decision itself lives in the pure policy module, not here.
        """
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {"intent_id": intent_id, "stage": "PLANNED"}
        selector = row.get("terminal_worktree") or ""
        if not selector or selector in WORKTREE_ALIASES:
            return {"handle": None, "handle_recovery": "scope_unresolved"}
        try:
            listing: Any = list(self.harness.list_terminals(worktree=selector))
        except Exception:  # noqa: BLE001 - unreadable is unknown, never empty
            listing = None
        scope_resolved = True
        if not listing:
            # "Absent" is only meaningful inside a scope that provably resolves: an
            # unresolvable selector returns ok:true with an empty array, which on its own
            # is indistinguishable from a real worktree holding no terminals.
            resolved = self.harness.resolve_worktree(selector)
            expected = selector.split("id:", 1)[-1]
            scope_resolved = bool(resolved) and resolved.get("id") == expected
        return dict(pause_policy.resolve_terminal_handle(
            row, listing, scope_resolved=scope_resolved))

    def account_dispatch(self, intent_id: str) -> dict[str, Any]:
        """Read-only: delegates to ``harness.account_axes``, which issues ZERO commands."""
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        handle = self.recover_handle(intent_id).get("handle") or ""
        task_id = row.get("task_id", "")
        dispatch_id = row.get("dispatch_id", "") or self._dispatch_for_task(task_id)
        observation, task_status, supervised = self._observe(task_id, dispatch_id)
        if handle:
            # Provenance is re-seeded from the JOURNAL, never from worker-show, whose
            # verified response carries no role, no origin and no terminal handle at all.
            self.harness.register_terminal(
                handle, role=row.get("terminal_role") or "unknown_role",
                origin=row.get("terminal_origin") or "unknown",
                intended_role=row.get("terminal_intended_role") or None,
                owner_dispatch_id=dispatch_id or None,
                created_by=row.get("created_by", ""))
        settlement, worker_resource, liveness, authority, role = self.harness.account_axes(
            task_id, dispatch_id, handle, supervised=supervised, observation=observation,
            task_status=task_status, lifecycle="retain")
        accounted = {key: "" for key in pause_policy.SETTLEMENT_ROW_KEYS}
        for key in pause_policy.SETTLEMENT_ROW_KEYS:
            value = row.get(key)
            if isinstance(value, str) and value:
                accounted[key] = value
        accounted.update({
            "intent_id": intent_id, "task_id": task_id, "dispatch_id": dispatch_id,
            "settlement": "settled" if settlement in ("completed", "failed") else "not_settled",
            "worker_resource": worker_resource,
            "process_liveness": liveness, "cleanup_authority": authority,
            "terminal_role": role, "recovery": accounted.get("recovery") or "observed",
            "terminal_disposition": "",
        })
        return accounted

    def _dispatch_for_task(self, task_id: str) -> str:
        if not task_id:
            return ""
        try:
            shown = self.harness.call("orchestration", "dispatch-show",
                                      "--task", task_id)["result"]
        except Exception:  # noqa: BLE001
            return ""
        dispatch = shown.get("dispatch") or {}
        return dispatch.get("id", "") if isinstance(dispatch, dict) else ""

    def _observe(self, task_id: str, dispatch_id: str) -> tuple[dict[str, Any], str, bool]:
        observation: dict[str, Any] = {}
        supervised = bool(dispatch_id)
        if dispatch_id:
            try:
                observation = dict(self.harness.call(
                    "orchestration", "worker-show", "--dispatch", dispatch_id)["result"])
            except Exception:  # noqa: BLE001
                observation = {}
                supervised = False
        task_status = ""
        if task_id:
            try:
                task_status = self.harness.task_status(task_id)
            except Exception:  # noqa: BLE001
                task_status = ""
        return observation, task_status, supervised

    def recover_dispatch(self, intent_id: str, *, reason: str) -> dict[str, Any]:
        """`worker-abandon` -> `worker-release`, or `task-update --status failed`.

        Accounted **recovered**, never "settled": this dispatch produced no accepted
        worker_done, so there is no role promotion here either.
        """
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        dispatch_id = row.get("dispatch_id", "") or self._dispatch_for_task(
            row.get("task_id", ""))
        if not dispatch_id:
            self.harness.call("orchestration", "task-update", "--id", row.get("task_id", ""),
                              "--status", "failed", "--result",
                              json.dumps({"reason": reason}))
            return {"settlement": "recovered", "recovery": "task-update:failed"}
        shown = self.harness.call("orchestration", "worker-show",
                                  "--dispatch", dispatch_id)["result"]
        state = ((shown.get("worker") or {}).get("state") or "")
        recovery = "observed"
        if state in ("outcome_unknown", "ready"):
            abandoned = self.harness.call("orchestration", "worker-abandon",
                                          "--dispatch", dispatch_id)
            recovery = f"abandon:{abandoned['result']['state']}"
        self.harness.call("orchestration", "worker-release", "--dispatch", dispatch_id)
        return {"settlement": "recovered", "recovery": recovery}

    def release_terminal(self, intent_id: str, *, authority: str) -> dict[str, Any]:
        """Called ONLY with proven authority and a `release` lifecycle intent."""
        if authority != "authorized":
            raise ValueError("release_terminal requires proven cleanup authority")
        row = (self.settlement_journal.row(intent_id) if self.settlement_journal
               else None) or {}
        dispatch_id = row.get("dispatch_id", "")
        released = self.harness.call("orchestration", "worker-release",
                                     "--dispatch", dispatch_id)["result"]
        action = released.get("processAction", "")
        if action in pause_policy.PROCESS_TERMINATING_ACTIONS:
            return {"recovery": f"released:{action}", "process_liveness": "already exited"}
        # D-6/R8-iii: a release receipt that does not prove a termination means the runtime
        # KEPT the process, whatever cleanup authority said.
        return {"recovery": f"retained:{action or 'none'}"}

    def _record_receipt(self, intent: ActionIntent, receipt: dict[str, Any],
                        lease_token: str | None) -> None:
        if self.runtime_state is not None:
            self.runtime_state.record_receipt(intent["intent_id"], receipt, lease_token)

    def _durable_receipt(self, intent: ActionIntent) -> dict[str, Any] | None:
        """Recover an external effect created by an earlier process, by stable identity."""
        if self.runtime_state is None: return None
        record = self.runtime_state.get_receipt(intent["intent_id"])
        if not record or record.get("status") == "CLAIMED": return None
        stored = record.get("receipt") or {}
        return {"intent_id": record["intent_id"], "payload_digest": record["payload_digest"],
                "task_id": stored.get("task_id"), "dispatch_id": stored.get("dispatch_id"),
                "terminal": None}

    def settlement(self, intent_id: str) -> SettlementEvent | None:
        event = self._events.get(intent_id)
        if event is None and self.runtime_state is not None:
            event = self.runtime_state.get_settlement(intent_id)
        return deepcopy(event) if event else None

    def send(self, intent_id: str, command: dict[str, Any]) -> dict[str, Any]:
        receipt = self._receipts[intent_id]
        return self.harness.call("terminal", "send", "--terminal", receipt["terminal"],
                                 "--text", json.dumps(command, sort_keys=True), "--enter")

    def status(self, intent_id: str) -> dict[str, Any]:
        receipt = self._receipts[intent_id]
        return {"task_status": self.harness.task_status(receipt["task_id"]),
                "dispatch_id": receipt["dispatch_id"]}

    def interrupt(self, intent_id: str, reason: str) -> dict[str, Any]:
        """No Orca primitive expresses a non-settling interrupt at the pinned revision.

        The pinned spec defines exactly eight ``worker-*`` verbs and none of them is an
        interrupt (``docs/ORCA_RUNTIME_PRIMITIVES.md:880-883``).  The previous body called
        ``orchestration worker-interrupt``, a verb that does not exist, so the only thing
        it could produce was an unnamed CLI failure.  Whether such a verb existed in an
        EARLIER release was never investigated (U8) and is not claimed here.

        Returning the contract's own ``unsupported`` member is a NAMED refusal, not a
        silent no-op: the caller must handle a closed-vocabulary member, no CLI verb is
        invoked, and nothing is settled -- ``ports.py``'s :class:`LifecycleSettlementPort`
        states it directly, "interrupting is not settling".
        """
        receipt = self._receipts[intent_id]
        return {"intent_id": intent_id, "reason": reason,
                "interrupt_outcome": "unsupported",
                "refusal": ORCA_INTERRUPT_PRIMITIVE_ABSENT,
                "dispatch_id": receipt["dispatch_id"]}


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


ORCA_PRIMITIVE_MAP = {
    "start": ("create_task", "run_existing_task"),
    "send": ("call",),
    "status": ("task_status",),
    # OS-37 D-1 / WI-02.  Empty, not ``("call",)``: no Orca primitive expresses a
    # non-settling interrupt at the pinned revision, so claiming one here was the second
    # place in this module asserting a verb that does not exist.  See
    # ``OrcaAdapter.interrupt``, which now returns the contract's named ``unsupported``
    # member instead of invoking a CLI verb.
    "interrupt": (),
}
