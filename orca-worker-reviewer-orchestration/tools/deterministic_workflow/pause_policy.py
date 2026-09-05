"""Pure, runtime-neutral pause/resume policy for OS-31.

This module owns the *policy* half of durable pause and resume: the lifecycle transition
table, the closed vocabularies, the three stable identities, the terminal disposition exit
invariant, the terminal-handle resolution decision table and the re-entry rule.

It imports neither LangGraph nor Orca, by design (OS-31 §10.1).  The engine therefore owns
pause/resume policy and stays runtime-neutral, while the adapter only translates lifecycle
signals and performs the I/O these pure functions decide over.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import contracts
from .routing import downstream_revalidation_set

# ---- lifecycle -----------------------------------------------------------------------
RUN_LIFECYCLE_STATES = ("ACTIVE", "WAITING_FOR_INPUT", "SETTLED")
PAUSE_EVENTS = ("ENTER_PAUSE", "RESUME", "CANCEL", "ABANDON", "TERMINATE")
PAUSE_TRANSITIONS: frozenset[tuple[str, str, str]] = frozenset({
    ("ACTIVE", "ENTER_PAUSE", "WAITING_FOR_INPUT"),
    ("ACTIVE", "TERMINATE", "SETTLED"),
    ("WAITING_FOR_INPUT", "RESUME", "ACTIVE"),
    ("WAITING_FOR_INPUT", "CANCEL", "SETTLED"),
    ("WAITING_FOR_INPUT", "ABANDON", "SETTLED"),
})

# ---- closed reason-code vocabulary ---------------------------------------------------
PAUSE_REFUSAL_CODES = frozenset({
    "PAUSE_NOT_ADMISSIBLE", "DISPATCH_UNACCOUNTED", "TERMINAL_OWNERSHIP_UNKNOWN",
    "TERMINAL_ORPHAN_POSSIBLE", "TERMINAL_IDENTITY_UNVERIFIED",
    "PAUSE_CHECKPOINT_MISSING", "STALE_CHECKPOINT_HEAD", "PAUSE_PROJECTION_DIVERGED",
    "PAUSE_RECORD_MISSING", "PAUSE_RECORD_CORRUPT", "CHECKPOINT_STORE_RETIRED",
    "PAUSE_CLAIM_HELD", "PAUSE_CLAIM_LOST", "PAUSE_OBSERVATION_TIMEOUT",
    "PAUSE_TRANSITION_FORBIDDEN", "PAUSE_LIFECYCLE_INCOHERENT",
    # A run pauses more than once.  Each pause is a generation; these two name the only
    # ways a NEW generation may fail to become the active one (see FilePauseRecordStore
    # .create): an unanswered generation is still active, or the successor's lineage --
    # its own checkpoint and a non-decreasing binding_generation -- does not hold.
    "PAUSE_GENERATION_ACTIVE", "PAUSE_GENERATION_LINEAGE",
    # A resume commits its graph continuation to the checkpoint BEFORE the effect can be
    # proven complete (see APPLIED_STAGES).  A successor that finds such a continuation
    # recovers it; this names the one case it may not -- the thread's head is not a
    # descendant of the checkpoint this pause record names, so the head is not this
    # bundle's continuation and continuing it would drive somebody else's run.
    "PAUSE_CONTINUATION_UNRECOVERABLE",
    "SETTLEMENT_JOURNAL_CORRUPT",
    "RESPONSE_NOT_FOUND", "RESPONSE_ALREADY_APPLIED", "RESPONSE_STALE_REVISION",
    "RESPONSE_CONFLICT", "RESPONSE_ITEM_UNRESOLVED",
    "RUN_ALREADY_RESUMED", "RUN_ALREADY_CANCELLED", "RUN_ALREADY_ABANDONED",
    "CHECKPOINT_UNVERIFIED",
})
# Deliberately DISJOINT from the refusals: a legitimately changed source must never be
# mistaken for a refusal (that would make a resumed run uncompletable), and a refusal must
# never be mistaken for a revalidation (that would apply a stale answer).
PAUSE_REVALIDATION_CODES = frozenset({
    "STALE_SOURCE_BINDING", "STALE_ARTIFACT_BINDING", "STALE_POLICY_DIGEST",
})
# Also disjoint from both sets above, and for the same reason: a RECOVERY is neither a
# refusal (the run continued) nor a revalidation (no source changed).  These two name what
# a successor PROVED about a continuation it inherited, and they are reported on a resume
# that succeeded -- never on one that refused.
PAUSE_RECOVERY_CODES = frozenset({
    "PAUSE_CONTINUATION_RECOVERED",         # the head advanced under this successor
    "PAUSE_CONTINUATION_ALREADY_COMPLETE",  # the head was already terminal / next-pause
})

# ---- four-axis settlement vocabularies -----------------------------------------------
# The first three are re-exports of the harness's own vocabularies.  The engine must not
# import the harness (PLAN D1), so a contract test asserts the copies are equal rather
# than letting two vocabularies drift silently.
SETTLEMENT_OUTCOMES = ("settled", "recovered", "not_settled")
WORKER_RESOURCE_OUTCOMES = ("reuse", "retain", "release", "unsupervised")
PROCESS_LIVENESS_STATES = ("live", "already exited", "disputed")
CLEANUP_AUTHORITY_STATES = ("authorized", "not_authorized", "unknown")

# OS-31's own exit conditions.  There is deliberately no "transferred" member: a genuine
# ownership transfer would be `worker-start --task <new> --terminal <handle>` reaching a
# ready state, and no OS-31 path performs it, so the closed set omits the name rather than
# letting a stored label claim what the mechanism does not do.
TERMINAL_DISPOSITIONS = ("released", "exited", "retained_by_named_owner", "residual")
AC1_DISCHARGING_DISPOSITIONS = frozenset({"released", "exited", "retained_by_named_owner"})
PROVENANCE_SOURCES = ("journal", "absent")
HANDLE_RECOVERY_OUTCOMES = (
    "in_process",          # the handle never left this process's memory
    "listing_verified",    # enumerated, title-matched, digest-PROVED
    "listing_candidate",   # title-matched only -- NOT proof, never acted on
    "not_listed",          # the scoped listing was read and holds no match
    "unverified",          # a match the digest contradicts, or an ambiguity
    "scope_unresolved",    # the recorded worktree selector no longer resolves
    "not_attempted",       # stage < OPENED, so no terminal was ever requested
)

PROCESS_TERMINATING_ACTIONS = frozenset({"killed", "terminated", "exited"})

# ---- closed key sets -----------------------------------------------------------------
PAUSE_BINDING_KEYS = (
    "pause_record_id", "paused_at", "request_id", "decision_item_ids",
    "source_ledger_keys", "responsible_phase", "repository_binding", "artifact_binding",
    "policy_digest", "settlement_ledger", "disposition",
)
PAUSE_PROJECTION_KEYS = (
    "current_phase", "current_phase_index", "phase_iteration", "final_review_iteration",
    "round_kind", "risk", "requested_phases", "decision_state", "decision_reason_code",
    "pending_clarification_id", "responsible_phase", "request_id", "decision_item_ids",
    "source_ledger_keys", "repository_binding", "artifact_binding", "policy_digest",
    "binding_generation", "settlement_ledger",
)
SETTLEMENT_ROW_KEYS = (
    "intent_id", "task_id", "dispatch_id",
    "terminal_title", "terminal_digest", "terminal_role", "terminal_origin",
    "terminal_owner", "provenance_source", "handle_recovery",
    "settlement", "worker_resource", "process_liveness", "cleanup_authority",
    "terminal_disposition", "recovery", "accounted_at",
)
APPLIED_ENTRY_KEYS = ("resume_bundle_id", "request_id", "items", "stage",
                      "recorded_at", "resumed_at", "resumed_checkpoint_id")
#: The THREE durable facts a resume must be able to tell apart after the process dies.
#: They were two, and conflating the middle one with the first is what stranded a run:
#:
#: ``RECORDED``    the resume INTENT is committed -- this bundle is the answer being
#:                 applied -- and nothing has touched the checkpoint yet, so the thread's
#:                 head is still the pause this record names.
#: ``CONTINUING``  the graph continuation is committed, or is about to be: this stage is
#:                 written strictly BEFORE ``update_state_command`` moves the head to
#:                 ACTIVE, so a head that has moved past the pause is always covered by
#:                 it.  It says nothing about whether the effect finished -- the
#:                 CHECKPOINT says that, and it is the authority.
#: ``RESUMED``     the continuation returned and the promotion is committed.
#:
#: A successor reads this stage plus the checkpoint head and needs nothing else: no
#: in-memory state, no wall clock, no "it has probably finished by now".
APPLIED_STAGES = ("RECORDED", "CONTINUING", "RESUMED")
#: The stages a successor may still act on.  ``RESUMED`` is finished and is not here.
APPLIED_IN_FLIGHT_STAGES = ("RECORDED", "CONTINUING")
DISPOSITION_KEYS = ("kind", "cancellation_id", "actor_id", "actor_type",
                    "submission_id", "reason", "requested_at")
DISPOSITION_KINDS = ("CANCEL", "ABANDON")
ACTOR_TYPES = ("human", "service")


class PauseTransitionRefused(ValueError):
    """A lifecycle transition outside :data:`PAUSE_TRANSITIONS` was attempted."""

    def __init__(self, message: str, *, code: str = "PAUSE_TRANSITION_FORBIDDEN") -> None:
        super().__init__(f"{code}:{message}")
        self.code = code
        self.detail = message


class PauseRefused(ValueError):
    """A pause, resume or disposition is refused with a closed reason code.

    ``code`` is always a member of :data:`PAUSE_REFUSAL_CODES`, so a caller can project the
    refusal onto a BLOCKED terminal reason without matching on exception classes.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        if code not in PAUSE_REFUSAL_CODES:
            raise ValueError(f"unknown pause refusal code: {code}")
        super().__init__(f"{code}:{detail}" if detail else code)
        self.code = code
        self.detail = detail


def transition(current: str, event: str) -> str:
    """The one lifecycle transition function.  Everything outside the table is refused."""
    if current not in RUN_LIFECYCLE_STATES:
        raise PauseTransitionRefused(f"unknown lifecycle state: {current!r}")
    if event not in PAUSE_EVENTS:
        raise PauseTransitionRefused(f"unknown lifecycle event: {event!r}")
    for source, name, target in PAUSE_TRANSITIONS:
        if source == current and name == event:
            return target
    raise PauseTransitionRefused(f"{current} --{event}--> is forbidden")


# ---- the three identities ------------------------------------------------------------
def pause_record_id(*, run_id: str, thread_id: str, request_id: str,
                    decision_item_ids: Iterable[str]) -> str:
    return contracts.stable_id("pause", {
        "run_id": run_id, "thread_id": thread_id, "request_id": request_id,
        "decision_item_ids": sorted(decision_item_ids)})


def in_flight_bundle(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """The one applied bundle that is claimed but not yet PROVEN resumed, or ``None``.

    ``FilePauseRecordStore.record_applied`` admits at most one bundle in any
    :data:`APPLIED_STAGES` stage per record, so "the one" is a fact of the store and not an
    assumption made here.  Pure: the record is the only input, and the answer is a
    statement about durable bytes -- never about what some process was doing.
    """
    for entry in (record.get("applied") or {}).values():
        if entry.get("stage") in APPLIED_IN_FLIGHT_STAGES:
            return dict(entry)
    return None


def resume_bundle_id(*, run_id: str, request_id: str, pause_record_id: str,
                     decisions: Iterable[tuple[str, str]]) -> str:
    """The identity of ONE application of a COMPLETE decision bundle.

    ``decisions`` is the whole answer, not one item: ``(decision_item_id, decision_id)``
    pairs, sorted by ``decision_item_id``, covering exactly the pause binding's
    ``decision_item_ids``.  A byte-identical replay yields the same id and is caught as a
    replay; a *different* answer to any item yields a different id and is therefore never
    mistaken for a replay of this one.

    ``repository_binding`` / ``artifact_binding`` / ``phase_iteration`` are deliberately
    excluded: revalidation may legitimately move the bindings after the answer arrives, and
    the id must stay stable across a process restart.
    """
    return contracts.stable_id("resume_bundle", {
        "run_id": run_id, "request_id": request_id, "pause_record_id": pause_record_id,
        "decisions": [list(pair) for pair in sorted(decisions)]})


def cancellation_id(*, run_id: str, pause_record_id: str, cancel_submission_id: str,
                    cancel_kind: str) -> str:
    return contracts.stable_id("cancel_run", {
        "run_id": run_id, "pause_record_id": pause_record_id,
        "cancel_submission_id": cancel_submission_id, "cancel_kind": cancel_kind})


# ---- the terminal disposition exit invariant (AC-1) ----------------------------------
def terminal_disposition(row: Mapping[str, Any]) -> str:
    """Total: every settlement row reaches exactly one :data:`TERMINAL_DISPOSITIONS` value.

    Evaluated in order; the first that holds wins.  ``residual`` is the honest name for
    "no nameable owner" -- it is a member of the closed set and is *not* a member of
    :data:`AC1_DISCHARGING_DISPOSITIONS`.
    """
    if (row.get("cleanup_authority") == "authorized"
            and row.get("worker_resource") == "release"
            and str(row.get("recovery") or "").startswith("released:")):
        return "released"
    if row.get("process_liveness") == "already exited":
        return "exited"
    if (row.get("provenance_source") == "journal"
            and row.get("terminal_role") not in (None, "", "unknown_role")
            and row.get("terminal_origin") not in (None, "", "unknown")
            and row.get("terminal_owner")):
        return "retained_by_named_owner"
    return "residual"


def require_pause_disposition(row: Mapping[str, Any]) -> str:
    """The PAUSE-path gate: a row that cannot be discharged refuses the pause.

    Recording an ambiguity does not discharge it, and neither does labelling one.  On the
    abandon path :func:`terminal_disposition` is used directly and a ``residual`` row is
    reported rather than refused (§9.2 step 6); here it raises.
    """
    disposition = terminal_disposition(row)
    if disposition in AC1_DISCHARGING_DISPOSITIONS:
        return disposition
    intent_id = row.get("intent_id")
    if row.get("handle_recovery") == "unverified":
        raise PauseRefused(
            "TERMINAL_IDENTITY_UNVERIFIED",
            f"{intent_id}: the scoped terminal listing disagrees with the journalled digest")
    if row.get("provenance_source") == "absent" or row.get("handle_recovery") in (
            "listing_candidate", "not_listed"):
        raise PauseRefused(
            "TERMINAL_ORPHAN_POSSIBLE",
            f"{intent_id}: a terminal may exist that no process can now prove is ours "
            f"(title={row.get('terminal_title')!r}, task={row.get('task_id')!r})")
    raise PauseRefused(
        "TERMINAL_OWNERSHIP_UNKNOWN",
        f"{intent_id}: cleanup_authority={row.get('cleanup_authority')!r} "
        f"process_liveness={row.get('process_liveness')!r} "
        f"role={row.get('terminal_role')!r} origin={row.get('terminal_origin')!r} "
        f"owner={row.get('terminal_owner')!r}")


# The SS4.2.1a outcomes that are NOT a recovery, and the closed code each refuses with.
# A row that cannot produce a proven handle is refused HERE, before a single mutating verb
# is considered -- ahead of any question about who owns the terminal.
UNRECOVERED_HANDLE_REFUSALS = {
    "unverified": "TERMINAL_IDENTITY_UNVERIFIED",
    "listing_candidate": "TERMINAL_ORPHAN_POSSIBLE",
    "not_listed": "TERMINAL_ORPHAN_POSSIBLE",
    "scope_unresolved": "DISPATCH_UNACCOUNTED",
}


def refuse_unrecovered_handle(row: Mapping[str, Any], outcome: str) -> None:
    """Raise the closed refusal that the handle-resolution outcome names, or return.

    ``not_attempted`` is the one non-recovery that is not a refusal: the row never reached
    ``OPENED``, so no terminal was ever requested and there is nothing to own (W-A).
    """
    if outcome in ("in_process", "listing_verified", "not_attempted"):
        return
    code = UNRECOVERED_HANDLE_REFUSALS.get(outcome)
    if code is None:
        raise PauseRefused("DISPATCH_UNACCOUNTED",
                           f"{row.get('intent_id')}: unknown handle recovery outcome "
                           f"{outcome!r}")
    raise PauseRefused(
        code,
        f"{row.get('intent_id')}: handle_recovery={outcome} "
        f"(title={row.get('terminal_title')!r}, task={row.get('task_id')!r}); a terminal "
        "this run cannot prove is its own is never released, closed or adopted")


def ac1_discharged(rows: Sequence[Mapping[str, Any]]) -> bool:
    """AC-1 is a computed fact, never an assertion: every row reached a discharging value."""
    return all(row.get("terminal_disposition") in AC1_DISCHARGING_DISPOSITIONS
               for row in rows)


# ---- §4.2.1a: recovering the plaintext handle ----------------------------------------
_TITLE_ALPHABET = re.compile(r"[A-Za-z0-9_-]")
_STRIPPABLE_CATEGORIES = frozenset({"So", "Sk", "Cf", "Cn"})


def normalize_terminal_title(raw: Any) -> str:
    """NFC-normalise, strip leading decoration glyphs and whitespace, strip the ends.

    Orca decorates live terminal titles with a leading status glyph plus a space (``"✳ "``,
    ``"◐ "`` observed live).  The decoration alphabet is not assumed to be closed: every
    leading code point whose Unicode general category is ``So``/``Sk``/``Cf``/``Cn``, or
    which is whitespace, is stripped.  Idempotent, and inert on an undecorated title.
    """
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFC", raw)
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace() or unicodedata.category(char) in _STRIPPABLE_CATEGORIES:
            index += 1
            continue
        break
    return text[index:].strip()


def match_terminal_title(raw: Any, target: str) -> bool:
    """True when a listed title denotes ``target``.

    Predicate 1 is equality after normalisation -- which already suffices for every
    decoration observed live.  Predicate 2 is a suffix match whose residual prefix must
    contain no character of the title alphabet ``[A-Za-z0-9_-]``; it absorbs a decoration
    this design has not enumerated and, because an OS-31 title is drawn entirely from
    ``[a-z0-9_-]``, cannot collide with another OS-31 terminal.
    """
    if not target:
        return False
    normalized = normalize_terminal_title(raw)
    if normalized == target:
        return True
    if not normalized.endswith(target):
        return False
    residual = normalized[: len(normalized) - len(target)]
    return not _TITLE_ALPHABET.search(residual)


def terminal_digest(handle: str) -> str:
    """The only form of a live terminal handle any durable OS-31 record may carry."""
    return hashlib.sha256(handle.encode("utf-8")).hexdigest()


def resolve_terminal_handle(row: Mapping[str, Any], listing: Any, *,
                            scope_resolved: bool = True,
                            corroborated_absent: bool = False) -> dict[str, Any]:
    """The §4.2.1a decision table, as a pure function over an already-fetched listing.

    The I/O (``terminal list``, ``worktree show``) lives in the adapter; the *decision*
    lives here, so every cell -- including all the fail-closed ones -- is unit-testable
    with no Orca, no adapter and no fixture at all.

    ``listing`` is the ``result.terminals`` array, or ``None`` when the listing could not
    be read.  ``scope_resolved`` is the ``worktree show`` guard's verdict: a zero-length
    listing under an unresolvable scope is *unknown*, never *empty*.
    """
    stage = row.get("stage")
    if stage in (None, "PLANNED"):
        return {"handle": None, "handle_recovery": "not_attempted"}
    if listing is None:
        raise PauseRefused(
            "DISPATCH_UNACCOUNTED",
            f"{row.get('intent_id')}: the terminal listing could not be read; "
            "unreadable is unknown, never empty")
    target = row.get("terminal_title") or ""
    candidates = [element for element in listing
                  if isinstance(element, Mapping)
                  and match_terminal_title(element.get("title"), target)]
    digest = row.get("terminal_digest") or ""
    if not digest:
        # W-C, keyed on the fact rather than on the stage: no digest was ever journalled,
        # so nothing can VERIFY a candidate.  "unverified" means a match the digest
        # contradicts; with no digest there is nothing to contradict, so a title match is
        # a candidate -- addressable, unproven, and never acted on.  Enumeration works
        # here; the verifier is what is missing.
        if candidates:
            handle = candidates[0].get("handle")
            return {"handle": None, "handle_recovery": "listing_candidate",
                    "candidate_handle": handle if isinstance(handle, str) else ""}
        if not scope_resolved:
            return {"handle": None, "handle_recovery": "scope_unresolved"}
        return {"handle": None, "handle_recovery": "not_listed"}
    verified = [element for element in candidates
                if digest and isinstance(element.get("handle"), str)
                and terminal_digest(element["handle"]) == digest]
    if len(verified) == 1:
        return {"handle": verified[0]["handle"], "handle_recovery": "listing_verified"}
    if candidates:
        # A title match the digest contradicts is somebody else's terminal; two digest
        # matches is an anomaly.  Choosing among candidates is the guess this gate forbids.
        return {"handle": None, "handle_recovery": "unverified"}
    if not scope_resolved:
        return {"handle": None, "handle_recovery": "scope_unresolved"}
    return {"handle": None, "handle_recovery": "not_listed",
            "corroborated_absent": bool(corroborated_absent)}


# ---- the projection ------------------------------------------------------------------
def project_pause(state: Mapping[str, Any]) -> dict[str, Any]:
    """The read-only human/auditor view of a paused run, derived ONLY from the checkpoint.

    C3 is literally ``project_pause(reconstructed) == record["projection"]``.  A projected
    field cannot be forgotten, because this function is the definition.
    """
    binding = state.get("pause_binding") or {}
    return {
        "current_phase": state["current_phase"],
        "current_phase_index": state["current_phase_index"],
        "phase_iteration": state["phase_iterations"][state["current_phase"]],
        "final_review_iteration": state["final_review_iterations"],
        "round_kind": state["round_kind"],
        "risk": state["risk"],
        "requested_phases": list(state["requested_phases"]),
        "decision_state": state["decision_state"],
        "decision_reason_code": state["decision_reason_code"],
        "pending_clarification_id": state["pending_clarification_id"],
        "responsible_phase": binding.get("responsible_phase"),
        "request_id": binding.get("request_id"),
        "decision_item_ids": list(binding.get("decision_item_ids") or ()),
        "source_ledger_keys": list(binding.get("source_ledger_keys") or ()),
        "repository_binding": dict(binding.get("repository_binding") or {}),
        "artifact_binding": dict(binding.get("artifact_binding") or {}),
        "policy_digest": binding.get("policy_digest"),
        "binding_generation": state["binding_generation"],
        "settlement_ledger": [dict(row) for row in binding.get("settlement_ledger") or ()],
    }


def projection_diff(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[str, ...]:
    """The per-key difference C3's refusal message names, so neither side is preferred."""
    return tuple(sorted(key for key in PAUSE_PROJECTION_KEYS
                        if left.get(key) != right.get(key)))


def policy_digest(skill_path: str | Path) -> str:
    """``sha256`` over the ``decision_policy`` sub-object of the SKILL policy contract.

    Exactly the sub-object ``decision_policy.load_decision_policy`` parses, so the digest
    changes if and only if the policy actually in force changes; prose edits elsewhere in
    ``SKILL.md`` do not spuriously invalidate a pending decision.
    """
    try:
        from scripts.skill_policy import load_policy_contract
    except ImportError:  # installed Skill layout exposes sibling tools directly
        from skill_policy import load_policy_contract  # type: ignore[no-redef]
    contract = load_policy_contract(Path(skill_path))
    return hashlib.sha256(
        contracts.canonical_bytes(contract["decision_policy"])).hexdigest()


# ---- validation ----------------------------------------------------------------------
def validate_settlement_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, Mapping) or set(row) != set(SETTLEMENT_ROW_KEYS):
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT",
                           f"settlement row closed fields: {sorted(row) if isinstance(row, Mapping) else type(row).__name__}")
    for key in SETTLEMENT_ROW_KEYS:
        if not isinstance(row[key], str):
            raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", f"settlement row {key} type")
    if row["settlement"] not in SETTLEMENT_OUTCOMES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "settlement outcome")
    if row["worker_resource"] not in WORKER_RESOURCE_OUTCOMES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "worker resource outcome")
    if row["process_liveness"] not in PROCESS_LIVENESS_STATES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "process liveness state")
    if row["cleanup_authority"] not in CLEANUP_AUTHORITY_STATES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "cleanup authority state")
    if row["terminal_disposition"] not in TERMINAL_DISPOSITIONS:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "terminal disposition")
    if row["provenance_source"] not in PROVENANCE_SOURCES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "provenance source")
    if row["handle_recovery"] not in HANDLE_RECOVERY_OUTCOMES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "handle recovery outcome")
    return dict(row)


def validate_disposition(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != set(DISPOSITION_KEYS):
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "disposition closed fields")
    if value["kind"] not in DISPOSITION_KINDS:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "disposition kind")
    if value["actor_type"] not in ACTOR_TYPES:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "disposition actor type")
    for key in DISPOSITION_KEYS:
        if not isinstance(value[key], str):
            raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", f"disposition {key} type")
    if not value["actor_id"]:
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "disposition actor_id required")
    return dict(value)


def validate_pause_binding(binding: Any) -> dict[str, Any]:
    if not isinstance(binding, Mapping) or set(binding) != set(PAUSE_BINDING_KEYS):
        raise PauseRefused(
            "PAUSE_LIFECYCLE_INCOHERENT",
            f"pause binding closed fields: "
            f"{sorted(binding) if isinstance(binding, Mapping) else type(binding).__name__}")
    for key in ("pause_record_id", "paused_at", "request_id", "responsible_phase",
                "policy_digest"):
        if not isinstance(binding[key], str) or not binding[key]:
            raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", f"pause binding {key}")
    for key in ("decision_item_ids", "source_ledger_keys"):
        value = binding[key]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", f"pause binding {key}")
    for key in ("repository_binding", "artifact_binding"):
        if not isinstance(binding[key], dict):
            raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", f"pause binding {key}")
    if not isinstance(binding["settlement_ledger"], list):
        raise PauseRefused("PAUSE_LIFECYCLE_INCOHERENT", "pause binding settlement_ledger")
    for row in binding["settlement_ledger"]:
        validate_settlement_row(row)
    if binding["disposition"] is not None:
        validate_disposition(binding["disposition"])
    return dict(binding)


# ---- stale-source revalidation and re-entry ------------------------------------------
@dataclass(frozen=True)
class ReEntry:
    """The pure re-entry decision a resume applies to the reconstructed state."""

    round_kind: str
    current_phase: str
    correction_queue: tuple[str, ...]
    correction_index: int
    phase_pass_floor: dict[str, int]
    binding_generation: int
    revalidation_codes: tuple[str, ...]

    @property
    def revalidated(self) -> bool:
        return bool(self.revalidation_codes)


def stale_source_codes(state: Mapping[str, Any], *, current_repository: Mapping[str, Any],
                       current_artifact: Mapping[str, Any],
                       current_policy_digest: str) -> tuple[str, ...]:
    """The three independent staleness comparisons, read from the reconstructed checkpoint.

    These are *revalidation triggers*, never refusals: the codes are members of
    :data:`PAUSE_REVALIDATION_CODES`, which is disjoint from :data:`PAUSE_REFUSAL_CODES`.
    """
    binding = state.get("pause_binding") or {}
    codes: list[str] = []
    frozen_repository = dict(binding.get("repository_binding") or {})
    frozen_artifact = dict(binding.get("artifact_binding") or {})
    if frozen_repository != contracts.normalize_repository_binding(dict(current_repository)):
        codes.append("STALE_SOURCE_BINDING")
    if frozen_artifact != contracts.normalize_artifact_binding(dict(current_artifact)):
        codes.append("STALE_ARTIFACT_BINDING")
    if binding.get("policy_digest") != current_policy_digest:
        codes.append("STALE_POLICY_DIGEST")
    return tuple(codes)


def resume_reentry(state: Mapping[str, Any], *, current_repository: Mapping[str, Any],
                   current_artifact: Mapping[str, Any],
                   current_policy_digest: str) -> ReEntry:
    """Pure.  Decide how a resumed run re-enters the workflow.

    Unchanged sources redo the paused round: the answer is consumed as context by the
    re-dispatched agent and never substitutes for the round.  A changed source re-enters
    through the *existing* correction machinery, so the phase Reviewer and Final Review
    gates are structurally unavoidable rather than promised.
    """
    codes = stale_source_codes(state, current_repository=current_repository,
                               current_artifact=current_artifact,
                               current_policy_digest=current_policy_digest)
    binding = state.get("pause_binding") or {}
    generation = int(state.get("binding_generation") or 0)
    floors = dict(state.get("phase_pass_floor") or {})
    if not codes:
        return ReEntry(round_kind=state["round_kind"], current_phase=state["current_phase"],
                       correction_queue=tuple(state.get("correction_queue") or ()),
                       correction_index=int(state.get("correction_index") or 0),
                       phase_pass_floor=floors, binding_generation=generation,
                       revalidation_codes=())
    responsible = binding.get("responsible_phase") or state["current_phase"]
    generation += 1
    requested = tuple(state["requested_phases"])
    raised = [responsible, *downstream_revalidation_set([responsible], requested,
                                                        state["risk"])]
    for phase in raised:
        if phase in requested:
            floors[phase] = generation
    return ReEntry(round_kind="CORRECTION", current_phase=responsible,
                   correction_queue=(responsible,), correction_index=0,
                   phase_pass_floor=floors, binding_generation=generation,
                   revalidation_codes=codes)


def responsible_phase_for(sources: Sequence[Mapping[str, Any]], requested: Sequence[str],
                          fallback: str) -> str:
    """The earliest phase in ``requested`` order named by any source, else ``fallback``.

    Fixed when the pause is created, never guessed at resume time, and using the same
    ordering rule ``routing.responsible_phases`` uses -- so revalidation starts as early as
    the change requires and never later.
    """
    order = list(requested)
    named = [str(source.get("phase")) for source in sources
             if source.get("phase") in order]
    if not named:
        return fallback
    return min(named, key=order.index)
