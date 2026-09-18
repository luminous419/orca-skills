"""OS-37 N7.  The closed lifecycle contract: ten states, sixteen events, five invariants.

**No CLI name appears in this module.**  Every driver difference has already been absorbed
into a :class:`DriverEvidence` by :mod:`standalone_drivers` before anything here sees it,
which is what keeps the workflow, decision and review policy free of per-CLI branching.

The single most dangerous confusion in this ticket is READINESS mistaken for COMPLETION,
and it is refused three times over:

*At the type level* -- ``ReadinessEvidence`` has no field that can express an outcome, and
``CompletionEvidence`` has no field that can express readiness.  There is no function that
converts one into the other.

*At the surface level* -- S1..S4 stay four separate functions with four separate orderings
(AC-37-08).  They are not merged into one precedence list, because consumers legitimately
combine them differently and a single merged status would silently change behaviour.

*At the invariant level* -- ``COMPLETED`` and ``FAILED`` have exactly one entry edge each,
``settlement_confirmed``, and its only caller is the settlement predicate.  ``S3``'s return
type cannot reach it: ``ReadinessVerdict`` has no member that names an outcome.

And READINESS ITSELF DOES NOT REST ON TEXT.  ``ready`` requires a conjunctive quorum:

    R-A  process/PTY corroboration that reads ZERO bytes of terminal output, and
    R-B  a typed record on the CLI's OWN STRUCTURED CHANNEL, of a type the profile
         declares, carrying the session identity this runtime minted before the spawn,
         compared by EQUALITY, and
    R-C  no refusal fired.

A terminal title, a screen preview and an ``idle`` marker are ``supplementary``: carried,
never counted, and admissible only as *refusal* input.  :func:`decide_readiness` does not
take ``supplementary`` as a parameter at all, so "a title accepted readiness" is a
``TypeError`` rather than a bug somebody could write.  An unrecognised interactive frame --
login, update, permission, setup, or a shape nobody has named -- leaves R-B unsatisfied and
therefore cannot be accepted, whatever it looks like.  That is why this design is correct
**without** resolving G-4, and why the evidence for it is a property quantified over
arbitrary frames rather than a fixture for one observed frame.
"""
from __future__ import annotations

import inspect
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, NotRequired, TypedDict

from .contracts import (OwnershipAxes, VocabularyError, validate_axes,
                        validate_vocabulary_member)
from .standalone_capture import structured_lines

# ---- D5.1 the ten states, closed ------------------------------------------------------
STATES = ("STARTING", "READY", "PROMPT_DELIVERED", "RUNNING", "WAITING_FOR_INPUT",
          "COMPLETED", "FAILED", "INTERRUPTED", "TIMED_OUT", "LOST")
State = Literal["STARTING", "READY", "PROMPT_DELIVERED", "RUNNING", "WAITING_FOR_INPUT",
                "COMPLETED", "FAILED", "INTERRUPTED", "TIMED_OUT", "LOST"]

#: The two states that may only ever be entered through the single settlement edge (I-3).
SETTLED_STATES = frozenset({"COMPLETED", "FAILED"})

# ---- D5.2 the sixteen events, closed --------------------------------------------------
# Named for THE OBSERVABLE, never for the destination.  `exit_observed` says what was seen;
# it does not say which state follows, because that depends on invariants the event does
# not know about.
EVENTS = ("spawned", "identity_bound", "readiness_observed", "prompt_written",
          "delivery_proof_observed", "delivery_unobserved", "turn_start_observed",
          "wait_observed", "wait_cleared", "interrupt_requested", "exit_observed",
          "exit_unproven", "settlement_accepted", "settlement_confirmed",
          "deadline_expired", "evidence_unreadable")

# ---- evidence tiers, closed -----------------------------------------------------------
#: `bound_structured` is the ONLY readiness-accepting tier.  `screen_preview` and `title`
#: are supplementary-or-refusal only, and there is deliberately no tier between them: a
#: half-accepting tier is how a title comes back.
#: `raw` (iteration 4, DESIGN D4.3c) is a LIFECYCLE observation a CLI emits whether or not
#: any work happened.  Both installed CLIs were MEASURED emitting a turn-start record on
#: their AUTHENTICATION-FAILURE path -- before authentication and before any model work --
#: which is why the tier exists.  Which record that is, for which CLI, is knowledge this
#: module deliberately does not hold: the driver absorbs it and hands up a tier.  It is
#: carried so an operator can see it and so the transcript stays complete; it is not
#: accepting, and I-2 below refuses it as a route into `RUNNING` around the delivery gate.
EVIDENCE_TIERS = ("bound_structured", "structured_stream", "raw", "screen_preview", "title")
ACCEPTING_READINESS_TIERS = frozenset({"bound_structured"})
SUPPLEMENTARY_TIERS = frozenset({"screen_preview", "title"})

# ---- D5.4 the seven portable refusals -------------------------------------------------
# `docs/AGENT_EXECUTION_CONTRACT.md:340-350`.  They run on EVERY surface.
REFUSALS = (
    "blocked_prompt_beats_idle",       # R-1
    "permission_is_never_completion",  # R-2
    "readiness_is_never_completion",   # R-3
    "absent_is_not_zero",              # R-4
    "unconfirmed_is_not_settled",      # R-5
    "cross_authority_is_incomparable", # R-6
    "unreadable_is_not_empty",         # R-7
)

#: G-1 / G-4 / G-5 patterns.  DEFENCE IN DEPTH, not the mechanism: readiness acceptance is
#: a POSITIVE requirement (R-B), so this list being incomplete cannot cause a false READY.
#: A list of refusals can never be complete against a frame nobody has observed, which is
#: exactly why the design does not depend on it.
BLOCKING_PROMPT_PATTERNS = (
    re.compile(r"(?i)\b(log ?in|sign ?in|authenticat\w*)\b.*\b(required|please|to continue)\b"),
    re.compile(r"(?i)\bapi[_ -]?key\b.*\b(enter|paste|required|missing)\b"),
    re.compile(r"(?i)\b(update|upgrade)\b.*\b(available|required|install now)\b"),
    re.compile(r"(?i)\b(allow|permission|approve|grant)\b.*\?\s*$", re.MULTILINE),
    re.compile(r"(?i)\b(y/n|\[y/N\]|\(yes/no\))\b"),
    re.compile(r"(?i)\bfirst[- ]run\b|\bwelcome to\b.*\bsetup\b|\bconfigure\b.*\bnow\b"),
    re.compile(r"(?i)\bnot (logged in|authenticated)\b"),
)

#: Named lost reasons.  `LOST` ALWAYS carries one -- asserted in the state record's
#: constructor, not left to convention.
LOST_REASONS = (
    "stop_unverified", "host_status_unavailable", "cause_unreported",
    "exit_code_unmapped", "capture_truncated", "evidence_unreadable",
    "process_table_unreadable", "settlement_unconfirmed", "start_unknown",
    # Round-8 iteration 3 (additive).  The pty stream's END was never positively observed
    # after the proven exit: the post-exit drain ended by its bound (a slave still held
    # open) or the master could not be read (a non-EIO read / select failure).  Whatever
    # record the transcript holds, the stream is not proven final, so nothing settles as
    # a success from it.
    "stream_end_unproven",
    # OS-48 (DESIGN §5/§6, additive).  The fence / ownership machinery's named non-success
    # outcomes.  `stream_end_unproven` above is kept for LEGACY journal rows only: OS-48 has
    # no producer for it -- the positive boundary is the fence marker, and its absence is
    # `boundary_unproven`.
    "boundary_unproven", "fence_missing", "fence_mismatch", "fence_foreign",
    "legacy_finalized_record", "owner_conflict", "finalizer_alive", "exit_unproven",
    "identity_unreadable", "identity_changed", "evidence_inconsistent",
    "provenance_ambiguous", "provenance_unbound", "signal_unbound", "signal_target_reaped",
    "group_signal_refused", "fence_published_no_claim", "succession_unwitnessed",
    # REVIEW_IMPLEMENTATION_iteration7 F-015: a completion-shaped object inside a line of the
    # fenced range that is not itself a record -- R2 ("exactly one") is unprovable.
    "record_framing_ambiguous",
)

#: OS-48 named outcomes that are FAILED verdict reasons (a settlement, not a loss).
OS48_FAILED_REASONS = ("refusal_in_boundary", "refusal_free_text", "no_completion_record")
#: OS-48 DIAGNOSTIC (journal-only) names: they never move a settlement.
OS48_DIAGNOSTIC_NAMES = ("descendants_unreaped", "diagnostic_tail_unaccounted",
                         "release_record_missing", "path_opener_indistinguishable",
                         "membership_unreadable", "descendants_unknown", "sidecar_unproven",
                         "sidecar_unreadable")
#: The closed set of OS-48 LOST outcomes (a subset of LOST_REASONS), for readers that must
#: refuse an unknown name rather than pass it through.
OS48_LOST_OUTCOMES = frozenset({
    "boundary_unproven", "fence_missing", "fence_mismatch", "fence_foreign",
    "legacy_finalized_record", "owner_conflict", "finalizer_alive", "exit_unproven",
    "identity_unreadable", "identity_changed", "evidence_inconsistent",
    "provenance_ambiguous", "provenance_unbound", "signal_unbound", "signal_target_reaped",
    "group_signal_refused", "fence_published_no_claim", "succession_unwitnessed",
    "capture_truncated", "evidence_unreadable", "record_framing_ambiguous"})

#: The sentinel that is NOT an exit code.  A reader of this value reports LOST.
UNVERIFIED_PROCESS_EXIT_CODE = -1

#: `interrupt_outcome`'s closed vocabulary (PLAN P3.1 note (a)'s recorded widening: V-5
#: enumerates its members through `interrupt()` as well as through `status()`).
INTERRUPT_OUTCOMES = ("interrupted_confirmed", "terminated_forced", "exit_unproven",
                      "not_owned", "unsupported")

#: `delivery`'s closed vocabulary (AC-37-04).
DELIVERY_OUTCOMES = ("delivered_confirmed", "not_observed", "blocked", "stale_handle",
                     "not_writable")
#: `agent_response` (iteration 4) is the CONJUNCTIVE class-B proof: a record that carries
#: positive evidence that the dispatched prompt reached model work.  `turn_start` REMAINS
#: in the closed vocabulary because journal rows written before the round-7 correction
#: carry it, but the runtime EMITS it for NO delivery mode any more: a turn was MEASURED
#: starting on the authentication-failure path of both installed CLIs, before any prompt
#: could have executed, so in `post_ready_delivery` too the structured proof is the
#: driver's own conjunctive delivery selector (`agent_response`), exactly as for
#: `launch_with_prompt`.
DELIVERY_PROOFS = ("screen_echo", "turn_start", "output_sequence", "agent_response")

#: DESIGN D4.2b.  The CLOSED set of `failure_reason` values a capability-vs-reality
#: disagreement can produce.  **Every one of them fails closed, and NONE of them is
#: recoverable by retrying in the other mode** -- `resolve_unknown` has no branch that
#: changes a delivery mode, and a static test asserts that `delivery_mode` is assigned in
#: exactly one place in the runtime (profile parsing) and reassigned nowhere.
#:
#: `delivery_mode_mismatch`      the CLI provably cannot honour the DECLARED mode.  For a
#:                               `launch_with_prompt` driver this is reserved for the case
#:                               where NEITHER a delivery proof NOR a typed terminal
#:                               outcome arrives by the deadline -- an authentication
#:                               failure is reported as an authentication failure (D4.3c's
#:                               precedence rule), never as a mode mismatch.
#: `delivery_mode_unverified`    the rehearsal could not EVALUATE the declared behaviour.
#:                               Unverified is not a pass and it is not a mismatch either.
#: `delivery_mode_ambiguous`     the declaration is TOO WEAK: the CLI also honours the
#:                               stronger mode.  M-10 is why both directions are probed --
#:                               a CLI that IGNORES an input rather than rejecting it
#:                               produces a SILENT mismatch, and a check that looked only
#:                               for the declared behaviour would pass.
#: `identity_binding_unverified` the declared binding could not be established at all.
#: `identity_binding_violated`   a SECOND, DIFFERENT identity arrived after the first was
#:                               frozen.  The run is settled from NEITHER.
CAPABILITY_FAILURE_REASONS = ("delivery_mode_mismatch", "delivery_mode_unverified",
                              "delivery_mode_ambiguous", "identity_binding_unverified",
                              "identity_binding_violated")

#: `start_outcome`'s closed vocabulary.  `start_unknown` IS A FAILURE, not an unknown
#: success: the spawn was attempted and its result cannot be established.
START_OUTCOMES = ("ready", "failed", "start_unknown")

#: `wait` provenance.  U4 keeps `WAITING_FOR_INPUT` PROVISIONAL, so the provenance travels
#: with every wait value rather than being dropped once normalised.
WAIT_PROVENANCE = ("hook", "prompt-text", "title")

#: The four surfaces (AC-37-08).  Four, and they stay four.
SURFACES = ("S1", "S2", "S3", "S4")

#: `ReadinessVerdict.verdict`.  THREE-valued.  "not ready" is not a fact and neither is
#: "ready": `unprovable` is what an unreadable structured channel produces.
READINESS_VERDICTS = ("ready", "not_ready", "unprovable")


class LifecycleError(VocabularyError):
    """A structural lifecycle refusal: a bad transition, or an undeclared unknown.

    Derived from :class:`contracts.VocabularyError` so that a caller which wants to catch
    "something was outside a closed set" catches both this and the shared validator's own
    refusal with one ``except``.  The two are still distinguishable when that matters: the
    validators raise the base type, and this module's own structural refusals raise this one.
    """


def validate_state_name(value: Any) -> str:
    return validate_vocabulary_member(STATES, value, name="state")


def validate_event_name(value: Any) -> str:
    return validate_vocabulary_member(EVENTS, value, name="event")


# ---- evidence types --------------------------------------------------------------------
class TextObservation(TypedDict):
    """A title or a screen reading.  The ONLY place either may appear.

    Carried so an operator can see what the runtime saw.  Counted by nothing.
    """

    tier: str          # `screen_preview` | `title`
    text: str
    live_observed: bool
    at: str


class ProcessLivenessProof(TypedDict):
    """R-A, as produced by :func:`standalone_pty.liveness_proof`.

    Structurally mirrored here rather than imported so this module keeps its declared
    import set (stdlib + `.contracts`) and stays testable with a pure dict.
    """

    identity_matches: bool
    not_exited: bool
    foreground_is_child_group: bool
    foreground_executable_matches: bool
    observed: dict[str, Any]


class BoundReadinessSignal(TypedDict):
    """R-B.  A typed record on the CLI's own structured channel.

    ``session_id`` is compared by EQUALITY against the value this runtime minted before the
    spawn.  ``channel`` must be ``structured``: a record that arrived as screen text is not
    this, whatever its bytes say, because R-B reads a channel and not a string.
    """

    channel: str            # must be `structured`
    record_type: str        # must be a member of the profile's declared set
    session_id: str         # compared by EQUALITY to the locally minted value
    at: str
    raw: dict[str, Any]


class ReadinessEvidence(TypedDict):
    """The three-part quorum record, with text SEGREGATED.

    ``supplementary`` is the only field a title or screen reading may occupy, and
    :func:`decide_readiness` does not receive it.
    """

    liveness: ProcessLivenessProof | None
    bound_signal: BoundReadinessSignal | None
    refusals: tuple[str, ...]
    supplementary: tuple[TextObservation, ...]
    #: F-002 / B1: the echo-provenance verdict the refusal scan was partitioned by
    #: (:func:`resolve_delivery_echo`).  `echo_unproven` here makes S3 `unprovable`.
    echo: Mapping[str, Any]
    #: Where the refusals came from: ``structured`` / ``free_text`` / ``none``.
    refusal_source: str


class CompletionEvidence(TypedDict):
    """Settlement candidates only.  No field here can express readiness."""

    exit_status: int | None          # None means ABSENT, never 0
    exit_proven: bool
    settlement_record: dict[str, Any] | None
    capture_answerable: bool
    lost_reason: str
    source_vocabulary: dict[str, Any]
    at: str


class ReadinessVerdict(TypedDict):
    """S3's return type.  There is no member that can express an outcome.

    That is not a convention: it is why S3's result can never reach ``COMPLETED``.  I-3's
    only caller takes a settlement receipt, a type this cannot produce.
    """

    verdict: str            # a member of READINESS_VERDICTS
    reason: str
    quorum: dict[str, bool]  # {"R-A": ..., "R-B": ..., "R-C": ...}
    #: Present only on a `deadline_expired` verdict: the verdict/reason it expired on, so
    #: an `echo_unproven` readiness timeout stays named through the settlement.
    expired_on: NotRequired[dict[str, str]]


class StateRecord(TypedDict):
    state: str
    lost_reason: str
    evidence: dict[str, Any]
    axes: OwnershipAxes
    source_vocabulary: dict[str, Any]
    at: str


def make_state_record(*, state: str, evidence: Mapping[str, Any],
                      axes: Mapping[str, Any], source_vocabulary: Mapping[str, Any],
                      at: str, lost_reason: str = "") -> StateRecord:
    """Build a state record, asserting the LOST invariant in the constructor.

    ``LOST`` without a ``lost_reason`` is refused HERE rather than checked by a caller,
    because "LOST for no stated reason" is exactly the shape an unknown takes when it has
    been reduced to a status.
    """
    validate_state_name(state)
    if state == "LOST":
        validate_vocabulary_member(LOST_REASONS, lost_reason, name="lost_reason")
    elif lost_reason:
        raise LifecycleError(
            f"lost_reason={lost_reason!r} is set on state {state!r}; it is non-empty IFF "
            "the state is LOST")
    return {"state": state, "lost_reason": lost_reason, "evidence": dict(evidence),
            "axes": validate_axes(axes), "at": at,
            "source_vocabulary": dict(source_vocabulary)}


# ---- D5.3(4) THE READY QUORUM ----------------------------------------------------------
def r_a_satisfied(liveness: Mapping[str, Any] | None) -> bool:
    """R-A: OS/process/PTY corroboration, reading ZERO terminal bytes.

    Conjunctive across all four legs.  No frame of any shape can influence any of them,
    which is the structural reason an arbitrary interactive frame cannot be accepted.
    """
    if liveness is None:
        return False
    return all(bool(liveness.get(key)) for key in
               ("identity_matches", "not_exited", "foreground_is_child_group",
                "foreground_executable_matches"))


def r_b_satisfied(bound_signal: Mapping[str, Any] | None, *,
                  minted_session_id: str,
                  declared_record_types: Sequence[str]) -> bool:
    """R-B: one declared, structured record carrying the LOCALLY MINTED session identity.

    Three equality checks and no pattern anywhere:

    * the channel is ``structured`` -- so the exact bytes of a valid record printed as
      screen text are refused, because R-B reads a channel and not a string;
    * the record type is a member of the profile's DECLARED closed set -- so a well-formed
      record of an undeclared type is refused;
    * the session id EQUALS the value minted before the spawn -- so a well-formed record of
      a declared type carrying a foreign session id is refused.

    Acceptance is therefore a POSITIVE requirement.  It is not the absence of a pattern
    match, which is why a frame nobody has ever observed cannot satisfy it.
    """
    if bound_signal is None:
        return False
    if bound_signal.get("channel") != "structured":
        return False
    if not minted_session_id:
        # No locally minted identity to compare against means nothing can be bound to it.
        return False
    if bound_signal.get("record_type") not in tuple(declared_record_types):
        return False
    return bound_signal.get("session_id") == minted_session_id


def r_c_satisfied(refusals: Sequence[str]) -> bool:
    """R-C: no refusal fired.  Text can only SUBTRACT."""
    return not tuple(refusals)


def decide_readiness(liveness: Mapping[str, Any] | None,
                     bound_signal: Mapping[str, Any] | None,
                     refusals: Sequence[str],
                     *, minted_session_id: str,
                     declared_record_types: Sequence[str],
                     structured_channel_readable: bool = True) -> ReadinessVerdict:
    """``ready`` IFF R-A and R-B and R-C.  **Takes no ``supplementary`` parameter.**

    That omission is the enforcement, not a comment about one: there is no argument through
    which a title or a screen reading could reach this decision, so "a title accepted
    readiness" cannot be written -- it is a ``TypeError``.

    Three-valued.  An unreadable or unparsable structured channel is ``unprovable``, which a
    bounded retry turns into ``TIMED_OUT``; it is never ``READY`` and never "not ready" as a
    fact.
    """
    quorum = {
        "R-A": r_a_satisfied(liveness),
        "R-B": r_b_satisfied(bound_signal, minted_session_id=minted_session_id,
                             declared_record_types=declared_record_types),
        "R-C": r_c_satisfied(refusals),
    }
    if not quorum["R-C"]:
        return {"verdict": "not_ready", "reason": f"refused:{tuple(refusals)[0]}",
                "quorum": quorum}
    if not structured_channel_readable:
        return {"verdict": "unprovable", "reason": "structured_channel_unreadable",
                "quorum": quorum}
    if not quorum["R-A"]:
        return {"verdict": "not_ready", "reason": "missing:liveness", "quorum": quorum}
    if not quorum["R-B"]:
        # This is the branch an unrecognised frame lands in.  It is fail-closed by
        # construction: nothing was recognised, so nothing was accepted.
        return {"verdict": "not_ready", "reason": "missing:bound_signal", "quorum": quorum}
    return {"verdict": "ready", "reason": "", "quorum": quorum}


def readiness_decision_signature() -> tuple[str, ...]:
    """The parameter names of :func:`decide_readiness`, for the static assertion.

    Exposed so the test asserting "``supplementary`` is not a parameter" reads the real
    signature rather than a transcription of it.
    """
    return tuple(inspect.signature(decide_readiness).parameters)


# ---- F-002 / B1: delivery-echo PROVENANCE ---------------------------------------------
#: The closed vocabulary of echo-provenance states.  `no_delivery` -- nothing was delivered,
#: so nothing can be an echo; `echo_absent` -- something was delivered and the recorded
#: transport PROVES the line discipline cannot have echoed it (it left with the `execve`,
#: or `ECHO` was clear at the write); `echo_proven` -- exactly one span, at/after the
#: recorded raw-byte offset, equals the delivered bytes under the transformation derived
#: from the recorded transport; `echo_unproven` -- none of the above could be established.
#: `echo_unproven` NAMES its reason and blocks readiness (`may_send_prompt` -> `unprovable`);
#: it never excludes and never silently passes.
ECHO_STATES = ("no_delivery", "echo_absent", "echo_proven", "echo_unproven")
#: How a payload reached the CLI.  `argv`: at process creation, never through the pty
#: input queue.  `pty_write`: written to the pty master by this runtime.
ECHO_TRANSPORT_KINDS = ("argv", "pty_write")
#: The reasons an echo can be `echo_unproven`, closed so a test can enumerate them.
ECHO_UNPROVEN_REASONS = (
    "transport_unrecorded",                     # the event carries no transport record
    "transport_kind_unknown",                   # a kind outside ECHO_TRANSPORT_KINDS
    "termios_unreadable",                       # tcgetattr failed at delivery time
    "control_byte_consumed_by_line_discipline", # a c_cc special byte is in the payload
    "delivery_before_window",                   # the recorded offset precedes this window
    "echo_not_found",                           # no span equals any derived form (partial,
                                                # delayed past the window, or absent)
    "ambiguous_multiple_matches",               # more than one span equals a derived form
    "echonl_partial_echo",                      # ECHO clear but ECHONL+ICANON echo the NLs
)
#: The LINE DISCIPLINES this runtime can derive an echo for, each measured on a real pty:
#: the BSD `ttydisc` (macOS; measured on this host) and Linux `n_tty` (measured by CI, run
#: 34821771744).  A transport whose evidence names neither is `transport_unrecorded`: a form
#: is never derived for a kernel the evidence did not identify.
LINE_DISCIPLINES = ("bsd_ttydisc", "linux_n_tty")
#: xterm bracketed-paste framing.  Terminal-standard bytes, not a CLI name.
BRACKETED_PASTE_START = b"\x1b[200~"
BRACKETED_PASTE_END = b"\x1b[201~"
#: `standalone_drivers.sanitize_payload`'s substitution for a raw ESC in the payload.
ESC_REPLACEMENT = b"<ESC>"
#: The line discipline's tab stop.  Fixed at 8 by every termios implementation.
TAB_STOP = 8
#: The termios flags a transport record must carry for a `pty_write` echo to be derivable.
TERMIOS_ECHO_FLAGS = ("echo", "echoctl", "echonl", "icanon", "isig", "iexten", "ixon",
                      "opost", "onlcr", "ocrnl", "onocr", "onlret", "tab_expand", "icrnl",
                      "inlcr", "igncr")


class EchoTransport(TypedDict):
    """What the driver/runtime recorded about the transport AT the delivery."""

    kind: str                     # ECHO_TRANSPORT_KINDS
    framed: bool                  # a bracketed-paste frame was written around the payload
    termios: Mapping[str, Any] | None   # TERMIOS_ECHO_FLAGS + `special_bytes` +
                                        # `platform` + `discipline` (LINE_DISCIPLINES), or None
    cols: int


class EchoResolution(TypedDict):
    """The provenance verdict over a capture for a sequence of delivery events."""

    state: str                    # ECHO_STATES
    reason: str                   # "" or an ECHO_UNPROVEN_REASONS member (event-prefixed)
    spans: tuple[tuple[int, int], ...]    # raw-byte spans, only when `echo_proven`
    events: tuple[Mapping[str, Any], ...]  # one per event: index/offset/state/reason/span


def _delivered_echo_bytes(payload: str) -> bytes:
    """The exact BYTES the runtime handed over for ``payload`` -- what the line discipline
    RECEIVES.  This is `standalone_drivers.sanitize_payload`: UTF-8, with every raw ESC
    byte replaced by the literal ``b"<ESC>"``.  The echo is derived from these bytes and the
    recorded transport; matching the derived form at the recorded raw-byte offset is what
    binds an excluded span to the delivered payload's digest."""
    return payload.encode("utf-8", "replace").replace(b"\x1b", ESC_REPLACEMENT)


def _render_echo(body: bytes, flags: Mapping[str, Any], *, discipline: str,
                 start_column: int) -> bytes:
    r"""ONE candidate echo of ``body`` under the termios ``flags`` of ONE line discipline:
    input translation (``c_iflag``), control-character echo rendering (``ECHOCTL``) and
    output post-processing (``c_oflag``: ``ONLCR``, ``OCRNL``, ``ONOCR``, tab expansion)
    applied byte by byte, tracking the output column the way THAT discipline does (per
    BYTE -- a multi-byte character advances it by its byte count, ``^X`` by two).

    The two disciplines differ in exactly the places the CI run on `a4c8c5f` measured:

    * BSD ``ttydisc`` (macOS, measured on this host): ``ECHOCTL`` exempts TAB and NL; a
      received NL goes to output processing (``ONLCR`` -> CRLF) and resets the column
      whether or not ``ONLCR``/``ONLRET`` is set.
    * Linux ``n_tty`` (measured by ubuntu CI): in NON-canonical mode a LITERAL NL byte is not
      a special character, so ``echo_char`` renders it ``^J`` under ``ECHOCTL`` -- no
      ``\r\n``/``\n`` is emitted and the column advances by two, which is what shifts
      ``XTABS`` expansion.  A CR that ``ICRNL`` turns into NL, and every NL in CANONICAL
      mode, reach ``echo_char_raw`` and go through output processing (``ONLCR`` -> CRLF,
      column reset; without ``ONLCR`` the column resets only under ``ONLRET``).

    ``start_column`` is the column the pty was at when the echo began -- unknown to this
    runtime, so the caller enumerates it modulo the tab stop.
    """
    linux = discipline == "linux_n_tty"
    echoctl = bool(flags.get("echoctl"))
    icanon = bool(flags.get("icanon"))
    opost = bool(flags.get("opost"))
    onlcr = bool(flags.get("onlcr"))
    ocrnl = bool(flags.get("ocrnl"))
    onocr = bool(flags.get("onocr"))
    onlret = bool(flags.get("onlret"))
    tab_expand = bool(flags.get("tab_expand"))
    icrnl = bool(flags.get("icrnl"))
    inlcr = bool(flags.get("inlcr"))
    igncr = bool(flags.get("igncr"))
    out = bytearray()
    column = start_column
    for received in body:
        # c_iflag: what the discipline RECEIVES for the byte that was written.
        byte = received
        translated = False
        if byte == 0x0D:
            if igncr:
                continue
            if icrnl:
                byte, translated = 0x0A, True
        elif byte == 0x0A and inlcr:
            byte, translated = 0x0D, True
        # ECHOCTL rendering.  BSD exempts TAB and NL.  Linux exempts TAB only, but a NL that
        # arrived as a special character (ICRNL-translated CR, or any NL in canonical mode)
        # is echoed RAW rather than through `echo_char`.
        control = (byte < 0x20 and byte != 0x09) or byte == 0x7F
        if byte == 0x0A:
            ctrl_rendered = echoctl and linux and not icanon and not translated
        else:
            ctrl_rendered = echoctl and control
        if ctrl_rendered:
            out += b"^" + bytes([byte ^ 0x40])
            column += 2
            continue
        if not opost:
            out.append(byte)
            continue
        # c_oflag post-processing.
        if byte == 0x0A:
            if onlcr:
                out += b"\r\n"
                column = 0
            else:
                out.append(byte)
                if onlret or not linux:              # BSD resets on NL regardless
                    column = 0
            continue
        if byte == 0x0D:
            if onocr and column == 0:
                continue                             # CR at column 0 is suppressed
            if ocrnl:
                out.append(0x0A)
                if onlret or not linux:
                    column = 0
            else:
                out.append(byte)
                column = 0
            continue
        if byte == 0x09 and tab_expand:
            width = TAB_STOP - (column % TAB_STOP)
            out += b" " * width
            column += width
            continue
        out.append(byte)
        if byte == 0x08:
            column = max(column - 1, 0)
        elif byte >= 0x20 and byte != 0x7F:
            column += 1
    return bytes(out)


def expected_echo_forms(payload: str, transport: Mapping[str, Any]) -> dict[str, Any]:
    """The echo forms the recorded ``transport`` can produce for ``payload`` -- DERIVED from
    evidence (the transport kind, the termios flags read at delivery time and the line
    discipline they were read from), never from a hard-coded alternate list.

    Returns ``{"state", "reason", "forms"}`` where ``state`` is ``echo_absent`` (the
    transport proves there is no line-discipline echo: the payload left with the ``execve``,
    or ``ECHO`` was clear), ``echo_unproven`` (the transformation cannot be derived --
    ``reason`` names why) or ``echo_expected`` (``forms`` is the non-empty tuple of byte
    strings one of which the echo MUST equal).  Several forms exist only when the discipline
    depends on state this runtime cannot observe -- the output column at the moment the echo
    began (tab expansion) -- and every such form is enumerated rather than guessed at.  The
    discipline itself is never enumerated: it is read from the evidence (`LINE_DISCIPLINES`),
    and an evidence block that names none is `transport_unrecorded`.
    """
    kind = transport.get("kind")
    if kind == "argv":
        return {"state": "echo_absent", "reason": "argv_transport_cannot_echo", "forms": ()}
    if kind != "pty_write":
        return {"state": "echo_unproven", "reason": "transport_kind_unknown", "forms": ()}
    flags = transport.get("termios")
    if not isinstance(flags, Mapping) or any(name not in flags for name in TERMIOS_ECHO_FLAGS):
        return {"state": "echo_unproven", "reason": "termios_unreadable", "forms": ()}
    discipline = flags.get("discipline")
    if discipline not in LINE_DISCIPLINES:
        return {"state": "echo_unproven", "reason": "transport_unrecorded", "forms": ()}
    body = _delivered_echo_bytes(payload)
    if transport.get("framed"):
        body = BRACKETED_PASTE_START + body + BRACKETED_PASTE_END
    received_newline = (0x0A in body and not flags.get("inlcr")) or \
        (0x0D in body and bool(flags.get("icrnl")) and not flags.get("igncr"))
    if not flags.get("echo"):
        if flags.get("echonl") and flags.get("icanon") and received_newline:
            # Only the newlines echo (both disciplines): a partial echo of unknown
            # alignment is not provable -- and it carries no text to fire on.
            return {"state": "echo_unproven", "reason": "echonl_partial_echo", "forms": ()}
        return {"state": "echo_absent", "reason": "echo_flag_clear", "forms": ()}
    special = {int(b) for b in (flags.get("special_bytes") or ())}
    if special and any(byte in special for byte in body):
        return {"state": "echo_unproven",
                "reason": "control_byte_consumed_by_line_discipline", "forms": ()}
    columns: tuple[int, ...] = (0,)
    if flags.get("opost") and flags.get("tab_expand") and 0x09 in body:
        columns = tuple(range(TAB_STOP))
    forms: list[bytes] = []
    for column in columns:
        form = _render_echo(body, flags, discipline=str(discipline), start_column=column)
        if form and form not in forms:
            forms.append(form)
    if not forms:
        return {"state": "echo_absent", "reason": "empty_payload", "forms": ()}
    return {"state": "echo_expected", "reason": "", "forms": tuple(forms)}


def _occurrences(raw: bytes, forms: Sequence[bytes], lo: int, hi: int) -> list[tuple[int, int]]:
    """Every DISTINCT span in ``raw[lo:hi]`` equal to one of ``forms`` (the whole form
    inside the window), in raw-byte coordinates -- at EVERY start position.

    Iteration-1 review B1: advancing the cursor by ``len(form)`` after a hit skipped a second
    candidate that begins INSIDE the first, so payload ``aaa`` in ``aaaa`` was ``echo_proven``
    at ``(0, 3)`` although ``(1, 4)`` is equally supported.  The cursor now advances by ONE
    byte, so overlapping candidates are enumerated, and the result is a set of spans: two
    distinct forms matching the same raw span are one candidate, any two distinct spans are
    two.  Ambiguity is therefore decided by SPAN IDENTITY, not by search-loop behaviour.
    """
    found: set[tuple[int, int]] = set()
    for form in forms:
        cursor = lo
        while True:
            idx = raw.find(form, cursor, hi)
            if idx == -1:
                break
            found.add((idx, idx + len(form)))
            cursor = idx + 1
    return sorted(found)


def resolve_delivery_echo(raw: bytes,
                          delivery_events: Sequence[Mapping[str, Any]] = ()) -> EchoResolution:
    r"""The echo-provenance verdict over the RAW capture bytes for the recorded delivery
    events -- in ONE coordinate space (raw capture bytes), fail-closed BY NAME.

    Final Adversarial Review F-002, iteration-8 review B1.  Iterations 1-3 excluded by
    content/position; iteration 5 by a bracketed-paste frame (not provenance -- an agent can
    print those bytes); iterations 6-7 by a delivery *event* but mixed raw-byte offsets with
    text indices; iteration 8 matched the delivered bytes byte-for-byte at the raw offset and
    so assumed the pty echoes a write verbatim -- which it does not: the line discipline
    translates LF to CRLF (``ONLCR``), renders control bytes as ``^X`` (``ECHOCTL``), expands
    tabs, or echoes nothing at all (``ECHO`` clear, or a payload that left with the
    ``execve``).

    A span is PROVEN to be the echo of event *i* when ALL of these hold:

    (a) it begins at or after the raw-byte ``offset`` the driver recorded at the write (the
        capture size just before the bytes went out) and ends before the next event's offset;
    (b) the event carries the `EchoTransport` recorded at that moment, and the transport
        proves an echo is POSSIBLE (`pty_write` with ``ECHO`` set);
    (c) the span EQUALS one of the forms :func:`expected_echo_forms` derives from the
        delivered bytes and that transport;
    (d) it is the ONLY DISTINCT such span in the window -- every start position of every
        form is enumerated (overlapping candidates included), so ``aaa`` in ``aaaa`` is two
        spans and therefore ambiguous, while two forms on one span are one candidate.

    Anything less is not a guess: it is `echo_unproven` with a NAMED reason
    (`ECHO_UNPROVEN_REASONS`), and an unproven echo excises NOTHING -- the refusal scan then
    runs over the superset, so a refusal the runtime itself may have authored fires (a
    fail-closed BLOCK, never a spurious pass) and `may_send_prompt` reports `unprovable`.
    A transport that proves NO echo is possible (`argv`, or ``ECHO`` clear) is `echo_absent`:
    nothing is excluded and nothing is unproven -- every captured byte is the agent's.
    """
    ordered: list[tuple[int, int, Mapping[str, Any], str]] = []
    for index, ev in enumerate(delivery_events):
        if not isinstance(ev, Mapping):
            continue
        payload = str(ev.get("payload") or "")
        if not payload:
            continue
        ordered.append((int(ev.get("offset", 0) or 0), index, ev, payload))
    if not ordered:
        return {"state": "no_delivery", "reason": "", "spans": (), "events": ()}
    ordered.sort(key=lambda item: (item[0], item[1]))
    per_event: list[dict[str, Any]] = []
    proven: list[tuple[int, int]] = []
    first_unproven = ""
    for position, (offset, index, ev, payload) in enumerate(ordered):
        window_end = ordered[position + 1][0] if position + 1 < len(ordered) else len(raw)
        window_end = max(window_end, offset)
        entry: dict[str, Any] = {"index": index, "offset": offset, "window_end": window_end,
                                 "state": "echo_unproven", "reason": "", "span": None,
                                 "forms": 0}
        transport = ev.get("transport")
        if offset < 0:
            # The recorded delivery began BEFORE this byte window: its echo is not provable
            # here.  A provenance offset is never clamped to 0 (the iteration-6 bypass).
            entry["reason"] = "delivery_before_window"
        elif not isinstance(transport, Mapping):
            entry["reason"] = "transport_unrecorded"
        else:
            derived = expected_echo_forms(payload, transport)
            entry["forms"] = len(derived["forms"])
            if derived["state"] == "echo_absent":
                entry["state"] = "echo_absent"
                entry["reason"] = derived["reason"]
            elif derived["state"] == "echo_unproven":
                entry["reason"] = derived["reason"]
            else:
                hits = _occurrences(raw, derived["forms"], offset, window_end)
                if not hits:
                    entry["reason"] = "echo_not_found"
                elif len(hits) > 1:
                    entry["reason"] = "ambiguous_multiple_matches"
                else:
                    entry["state"] = "echo_proven"
                    entry["span"] = hits[0]
                    proven.append(hits[0])
        if entry["state"] == "echo_unproven" and not first_unproven:
            first_unproven = f"event[{index}]:{entry['reason']}"
        per_event.append(entry)
    if first_unproven:
        return {"state": "echo_unproven", "reason": first_unproven, "spans": (),
                "events": tuple(per_event)}
    if proven:
        return {"state": "echo_proven", "reason": "", "spans": tuple(sorted(proven)),
                "events": tuple(per_event)}
    return {"state": "echo_absent", "reason": "", "spans": (), "events": tuple(per_event)}


def _excise(raw: bytes, spans: Sequence[tuple[int, int]]) -> bytes:
    if not spans:
        return raw
    out: list[bytes] = []
    cursor = 0
    for start, end in sorted(spans):
        if start < cursor:                               # overlapping / already-excised span
            cursor = max(cursor, end)
            continue
        out.append(raw[cursor:start])
        cursor = end
    out.append(raw[cursor:])
    return b"".join(out)


def strip_delivery_echo(raw: bytes, delivery_events: Sequence[Mapping[str, Any]] = ()) -> bytes:
    """The RAW capture bytes with the runtime's OWN delivered-prompt echo removed -- ONLY
    when :func:`resolve_delivery_echo` PROVES it (`echo_proven`).  Every other state
    (`no_delivery`, `echo_absent`, `echo_unproven`) returns ``raw`` unchanged: nothing is
    ever subtracted on a guess."""
    if not raw or not delivery_events:
        return raw
    resolution = resolve_delivery_echo(raw, delivery_events)
    if resolution["state"] != "echo_proven":
        return raw
    return _excise(raw, resolution["spans"])


def _decode_capture(raw: bytes) -> str:
    r"""Decode raw capture bytes the way `standalone_capture.transcript` does: UTF-8 with
    ``errors="replace"``, then undo the pty's ``\r\n`` line-ending translation."""
    return raw.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def refusal_evidence(raw: bytes,
                     delivery_events: Sequence[Mapping[str, Any]] = (),
                     *, structured_refusal: Any = None,
                     blocked_hint: bool = False) -> dict[str, Any]:
    """Refusals over a capture, with echo PROVENANCE and STRUCTURED-FIRST classification.

    1. :func:`resolve_delivery_echo` partitions the raw bytes.  Only an `echo_proven` span
       is excised; an unproven echo leaves the superset in place (fail closed) and is named
       in the returned ``echo`` block.
    2. The remainder is decoded exactly as the capture's ``transcript`` view and split into
       STRUCTURED records (lines that parse as JSON objects on the CLI's own channel) and
       FREE TEXT (everything else), by the same rule as `standalone_capture.structured_lines`.
    3. Structured records are consulted FIRST, through ``structured_refusal`` -- the driver's
       typed-marker predicate over one parsed record (the profile's declared auth/setup
       markers).  A hit fires the refusal with ``source == "structured"``.
    4. Only when no structured record fires is the free text scanned by the portable
       patterns (``source == "free_text"``).  Prose INSIDE a structured record -- an assistant
       message, a tool result quoting the task, a replayed prompt -- is that record's
       content, never a terminal gate, and is not pattern-matched: post-delivery free text is
       a fallback, never an override of what the structured channel already typed.
    """
    echo = resolve_delivery_echo(raw, delivery_events)
    remainder = _excise(raw, echo["spans"]) if echo["state"] == "echo_proven" else raw
    text = _decode_capture(remainder)
    records: list[Mapping[str, Any]] = []
    prose: list[str] = []
    for parsed, line in structured_lines(text):
        if parsed is not None:
            records.append(parsed)
        else:
            prose.append(line)
    hit: dict[str, Any] | None = None
    if structured_refusal is not None:
        for record in records:
            marker = structured_refusal(record)
            if marker:
                hit = {"record_type": record.get("type"), "marker": marker}
                break
    if hit is not None:
        refusals: tuple[str, ...] = ("blocked_prompt_beats_idle",)
        source = "structured"
    else:
        refusals = classify_refusals("\n".join(prose), blocked_hint=blocked_hint)
        source = "free_text" if refusals else "none"
    return {"refusals": refusals, "source": source, "echo": echo, "structured_hit": hit,
            "structured_records": len(records), "free_text_lines": len(prose)}


def classify_refusals_in_capture(raw: bytes,
                                 delivery_events: Sequence[Mapping[str, Any]] = (),
                                 *, blocked_hint: bool = False,
                                 structured_refusal: Any = None) -> tuple[str, ...]:
    """The refusal tuple of :func:`refusal_evidence`.  Exclusion is applied on the RAW bytes
    at the recorded raw-byte offset under the recorded transport BEFORE any decode, so a
    byte offset is never compared with a character index (F-002 coordinate integrity)."""
    return refusal_evidence(raw, delivery_events, structured_refusal=structured_refusal,
                            blocked_hint=blocked_hint)["refusals"]


def classify_refusals(text: str, *, blocked_hint: bool = False) -> tuple[str, ...]:
    """Refusals derived from OBSERVED TEXT.  Text may only subtract, and this function does
    NOT subtract on its own: it scans exactly the text it is given.

    F-002: there is deliberately no ``exclude_text`` content parameter any more.  A caller
    that must exclude the runtime's own delivered echo passes text already narrowed by
    :func:`strip_delivery_echo` (bound to the recorded delivery event, not to content or to
    markers).  So the pure probe ``classify_refusals("login required")`` FIRES -- a bare
    refusal string is never silently dropped -- and the only exclusion anywhere is a span
    that provably equals a delivered payload at/after the offset the driver recorded.
    """
    fired: list[str] = []
    if blocked_hint:
        fired.append("blocked_prompt_beats_idle")
    if isinstance(text, str) and text:
        # Scanned BOTH as given and with terminal control sequences removed: a CSI/OSC
        # sequence glued to a word (``ESC[1mlogin``) is invisible on screen but defeats a
        # word boundary, so the stripped view can only ADD a match, never remove one.
        for view in (text, _strip_control_sequences(text)):
            if any(pattern.search(view) for pattern in BLOCKING_PROMPT_PATTERNS):
                if "blocked_prompt_beats_idle" not in fired:
                    fired.append("blocked_prompt_beats_idle")
                break
    return tuple(fired)


#: CSI (``ESC [ ... final``), OSC (``ESC ] ... BEL`` / ``ESC \``) and two-byte ESC sequences
#: -- what a terminal consumes without displaying.
_CONTROL_SEQUENCE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\].*?(?:\x07|\x1b\\)|\x1b[@-Z\\-_]", re.DOTALL)


def _strip_control_sequences(text: str) -> str:
    return _CONTROL_SEQUENCE.sub("", text)


# ---- D5.3(2) the four surfaces, four separate orderings --------------------------------
def report_activity(*, structured_row: Mapping[str, Any] | None,
                    title: TextObservation | None,
                    live_pty: bool) -> dict[str, Any]:
    """S1: "What do I report as this agent's activity?"

    Layers are reported SEPARATELY, never collapsed: a liveness-gated consumer must be able
    to treat the title layer as ABSENT, and it cannot do that if the two have already been
    merged into one value.
    """
    layers: dict[str, Any] = {"authoritative": None, "fallback": None,
                              "fallback_live": live_pty}
    if structured_row:
        layers["authoritative"] = dict(structured_row)
    if title is not None:
        layers["fallback"] = dict(title)
    layers["value"] = (layers["authoritative"] or
                       (layers["fallback"] if live_pty else None))
    layers["surface"] = "S1"
    return layers


def read_status(*, live_permission: bool, blocked_text: str,
                structured_row: Mapping[str, Any] | None,
                foreground_is_shell: bool,
                identity_resolved_title: TextObservation | None,
                process_probe: Mapping[str, Any] | None) -> dict[str, Any]:
    """S2: "What is this session's agent status right now?"

    An approval prompt is UNCONDITIONAL and comes first -- R-2 -- and the structured row is
    gated on the foreground process not being a plain shell, because a fresh row emitted
    while a shell holds the foreground describes a turn that has already ended.
    """
    if live_permission:
        return {"surface": "S2", "tier": "permission", "status": "WAITING_FOR_INPUT",
                "provenance": "hook"}
    refusals = classify_refusals(blocked_text)
    if refusals:
        return {"surface": "S2", "tier": "blocked_text", "status": "WAITING_FOR_INPUT",
                "provenance": "prompt-text", "refusals": refusals}
    if structured_row and not foreground_is_shell:
        return {"surface": "S2", "tier": "structured_stream",
                "status": structured_row.get("status", ""), "row": dict(structured_row)}
    if identity_resolved_title is not None:
        return {"surface": "S2", "tier": "title",
                "status": identity_resolved_title.get("text", ""),
                "provenance": "title"}
    return {"surface": "S2", "tier": "none", "status": None,
            "probe": dict(process_probe or {})}


def may_send_prompt(evidence: Mapping[str, Any], *, minted_session_id: str,
                    declared_record_types: Sequence[str],
                    structured_channel_readable: bool = True,
                    deadline_expired: bool = False) -> ReadinessVerdict:
    """S3: **"May I send a prompt yet?"  READINESS ONLY.**

    The one accepting path is the quorum.  ``supplementary`` is read here for exactly one
    purpose -- to derive refusals, which can only subtract -- and is then not passed to the
    decision at all.

    At the bounded deadline the verdict is reported by the caller as ``TIMED_OUT``: never
    ``READY``, and never "not ready" as an established fact.
    """
    refusals = list(evidence.get("refusals") or ())
    # `evidence["refusals"]` is authoritative: the driver computed it over the FULL raw
    # capture in ONE coordinate space (byte offsets), excluding only the proven delivery echo.
    # A supplementary observation is a TAIL of the capture; it can only ADD a refusal (never
    # remove one).  When it carries its RAW bytes and the absolute byte `start_offset` where
    # they begin, the delivery event's raw-byte offset is translated into that window
    # (`offset - start_offset`) and the echo is excluded in raw-byte space too -- a byte
    # offset is never compared with a character index (iteration-8 B1).  An observation with
    # no raw bytes cannot establish provenance, so it adds nothing beyond the authoritative
    # scan (never clamps an offset to 0, never re-derives it with character lengths).
    delivery_events = tuple(ev for ev in (evidence.get("delivery_events") or ())
                            if isinstance(ev, Mapping) and ev.get("payload"))
    for observation in evidence.get("supplementary") or ():
        raw = observation.get("raw")
        if not isinstance(raw, (bytes, bytearray)):
            continue                                     # no byte provenance -> add nothing
        start = int(observation.get("start_offset", 0) or 0)
        # The event travels WHOLE -- offset translated, transport untouched -- so the tail
        # is partitioned under the same proven transformation as the authoritative scan.
        # An event whose echo lies before the tail resolves `delivery_before_window` in
        # this window and excises nothing; that is a property of the window, not of the
        # capture, so it adds no refusal and does not by itself make S3 unprovable.
        preview_events = tuple({**dict(ev), "offset": int(ev.get("offset", 0) or 0) - start}
                               for ev in delivery_events)
        for code in classify_refusals_in_capture(bytes(raw), preview_events):
            if code not in refusals:
                refusals.append(code)
    verdict = decide_readiness(
        evidence.get("liveness"), evidence.get("bound_signal"), tuple(refusals),
        minted_session_id=minted_session_id,
        declared_record_types=declared_record_types,
        structured_channel_readable=structured_channel_readable)
    # F-002 / B1, fail closed BY NAME: when the driver's authoritative scan could not prove
    # the delivered echo's provenance, the refusal partition is undecided.  No refusal fired
    # over the superset is NOT the same as "no refusal" being established, so the verdict is
    # `unprovable` -- named after the echo state -- and never `ready`.  A refusal that did
    # fire keeps precedence: R-C already refused on the superset.
    echo = evidence.get("echo")
    if (verdict["verdict"] == "ready" and isinstance(echo, Mapping)
            and echo.get("state") == "echo_unproven"):
        verdict = {"verdict": "unprovable",
                   "reason": f"echo_unproven:{echo.get('reason', '')}",
                   "quorum": verdict["quorum"]}
    if verdict["verdict"] != "ready" and deadline_expired:
        # The deadline verdict CARRIES the verdict it expired on, so a readiness timeout
        # caused by an unproven echo settles as `deadline_expired` WITH `echo_unproven:...`
        # visible in the journal row and the failure reason, not as a bare timeout.
        return {"verdict": "unprovable", "reason": "deadline_expired",
                "quorum": verdict["quorum"],
                "expired_on": {"verdict": verdict["verdict"],
                               "reason": verdict.get("reason", "")}}
    return verdict


def may_stop(*, structured_row: Mapping[str, Any] | None, blocked_text: str,
             known_ready_preview: bool, live_title_idle: bool,
             connected: bool) -> dict[str, Any]:
    """S4: "May I stop this session?"

    Kept separate from S3 even though both consult similar inputs, because they answer
    opposite questions: S3 asks whether work may START and S4 asks whether it may be
    INTERRUPTED, and the safe default differs.  Not connected means BUSY, not idle.
    """
    if structured_row:
        return {"surface": "S4", "tier": "structured_stream",
                "may_stop": bool(structured_row.get("idle")), "row": dict(structured_row)}
    if classify_refusals(blocked_text):
        return {"surface": "S4", "tier": "blocked_text", "may_stop": False,
                "reason": "blocked_prompt_beats_idle"}
    if known_ready_preview:
        return {"surface": "S4", "tier": "screen_preview", "may_stop": True}
    if live_title_idle:
        return {"surface": "S4", "tier": "title", "may_stop": True}
    if not connected:
        return {"surface": "S4", "tier": "none", "may_stop": False,
                "reason": "not_connected_is_busy"}
    return {"surface": "S4", "tier": "none", "may_stop": False, "reason": "unknown_is_busy"}


SURFACE_FUNCTIONS = {"S1": report_activity, "S2": read_status, "S3": may_send_prompt,
                     "S4": may_stop}


# ---- D5.2 the five invariants ----------------------------------------------------------
def _i1_ordered_start(log: Sequence[str], target: str) -> tuple[bool, str]:
    """I-1: ``STARTING -> READY`` needs ``identity_bound`` THEN ``readiness_observed``.

    Checks the ORDER in the event log, not a boolean pair.  ``readiness_observed`` alone
    never advances and never establishes identity: readiness says the agent will accept a
    prompt, and identity says it is the agent we started.
    """
    if target != "READY":
        return True, ""
    try:
        bound = list(log).index("identity_bound")
    except ValueError:
        return False, "I-1: identity_bound was never observed"
    try:
        observed = list(log).index("readiness_observed", bound + 1)
    except ValueError:
        return False, "I-1: readiness_observed did not follow identity_bound"
    return observed > bound, "" if observed > bound else "I-1: out of order"


def _i2_delivery_proof(event: str, target: str,
                       log: Sequence[str] = ()) -> tuple[bool, str]:
    """I-2: ``PROMPT_DELIVERED`` needs a PROOF, never the absence of a failure.

    **Strengthened in iteration 4 (F-001), on the same axis rather than as a sixth
    invariant.**  A `turn_start_observed` may not enter ``RUNNING`` unless a
    ``delivery_proof_observed`` already exists in the log.  The measurements behind it are
    the drivers' to hold; the lifecycle fact is that BOTH installed CLIs emit a turn-start
    record on their AUTHENTICATION-FAILURE path, so a demoted record that could enter
    `RUNNING` directly would re-enter the lifecycle through a different door and skip the
    delivery gate entirely.  I-2 owns the delivery gate, so closing the back door belongs
    here.
    """
    if target == "PROMPT_DELIVERED":
        if event != "delivery_proof_observed":
            return False, (f"I-2: PROMPT_DELIVERED requires delivery_proof_observed, got "
                           f"{event!r}; delivery_unobserved routes to TIMED_OUT")
        return True, ""
    if target == "RUNNING" and event == "turn_start_observed":
        if "delivery_proof_observed" not in tuple(log):
            return False, ("I-2: turn_start_observed may not enter RUNNING before a "
                           "delivery_proof_observed exists; a turn-start record is emitted "
                           "on the authentication-failure path too (D4.0 M-8, M-15)")
    return True, ""


def _i3_single_entry_edge(event: str, target: str, *,
                          from_settlement_predicate: bool) -> tuple[bool, str]:
    """I-3: ``COMPLETED`` / ``FAILED`` have exactly ONE entry edge each.

    ``settlement_confirmed``, and only when it came from the settlement predicate.
    ``settlement_accepted`` alone does not reach them, and no event on any surface may.
    """
    if target not in SETTLED_STATES:
        return True, ""
    if event != "settlement_confirmed":
        return False, (f"I-3: {target} has one entry edge, settlement_confirmed; "
                       f"{event!r} may not enter it")
    if not from_settlement_predicate:
        return False, ("I-3: settlement_confirmed is only admissible from the settlement "
                       "predicate, which takes a receipt no surface can produce")
    return True, ""


def _i4_lost_sink(target: str, lost_reason: str) -> tuple[bool, str]:
    """I-4: every unreadable authority routes to ``LOST`` WITH a reason.

    ``LOST`` is enterable from any state and is never entered by inference from a signal
    nobody looked for -- that is ABSENT, and absent is reported as such.
    """
    if target != "LOST":
        return True, ""
    if lost_reason not in LOST_REASONS:
        return False, f"I-4: LOST requires a named lost_reason, got {lost_reason!r}"
    return True, ""


def _i5_evidence_tier(target: str, evidence: Mapping[str, Any]) -> tuple[bool, str]:
    """I-5: no transition may be justified by a title, a screen reading or one NL line.

    Strengthened for ``READY``: a ``STARTING -> READY`` transition whose
    :class:`ReadinessEvidence` lacks R-A or R-B is refused, so text is never consulted on
    the accepting side at all.
    """
    tier = evidence.get("tier")
    if tier in SUPPLEMENTARY_TIERS and target != "LOST":
        return False, (f"I-5: tier {tier!r} is supplementary; it carries evidence but "
                       "advances no state")
    if target != "READY":
        return True, ""
    if not r_a_satisfied(evidence.get("liveness")):
        return False, "I-5/READY: evidence lacks R-A (process/PTY proof)"
    if evidence.get("bound_signal") is None:
        return False, "I-5/READY: evidence lacks R-B (bound structured readiness record)"
    return True, ""


def check_transition(*, source: str, target: str, event: str,
                     log: Sequence[str] = (), evidence: Mapping[str, Any] | None = None,
                     lost_reason: str = "",
                     from_settlement_predicate: bool = False) -> dict[str, Any]:
    """Run all five invariants.  ``{"allowed": bool, "violations": (...)}``.

    Five predicates, not a 10x16 matrix.  A matrix would have 160 cells to get right and
    would say nothing about WHY a transition is legal; these five say exactly why.
    """
    validate_state_name(source)
    validate_state_name(target)
    validate_event_name(event)
    ev = dict(evidence or {})
    violations: list[str] = []
    for ok, message in (
        _i1_ordered_start(log, target),
        _i2_delivery_proof(event, target, log),
        _i3_single_entry_edge(event, target,
                              from_settlement_predicate=from_settlement_predicate),
        _i4_lost_sink(target, lost_reason),
        _i5_evidence_tier(target, ev),
    ):
        if not ok:
            violations.append(message)
    return {"allowed": not violations, "violations": tuple(violations)}


# ---- the TYPED FAILED SETTLEMENT (external review #2 and #8) ---------------------------
#: The workflow's OWN failure vocabulary, per role.  It is READ here, never invented: a
#: Worker settlement carries `status` (`contracts.validate_event` accepts COMPLETE|BLOCKED)
#: and every Reviewer settlement carries `result` (PASS|FAIL).  A standalone dispatch that
#: did not succeed reports itself in exactly those words, so the engine's existing policy --
#: `routing.phase_gate`, which sends a Reviewer FAIL to the correction path and a Worker
#: non-COMPLETE to a named BLOCK -- decides what happens.  Nothing here is a verdict policy
#: of its own, and no module above `standalone_*` gains a branch.
FAILED_RESULT_BY_ROLE = {"WORKER": ("status", "BLOCKED")}
FAILED_RESULT_DEFAULT = ("result", "FAIL")

#: Round 4, finding 6.  The roles whose RUNTIME failure is NEVER a settlement.  A Reviewer's
#: verdict vocabulary is `PASS|FAIL`, and `FAIL` is a judgement about the work: routing
#: sends it to a correction Worker and charges a phase iteration.  An authentication
#: expiry, an OOM kill, a readiness or completion timeout, a truncated capture, a missing
#: secret -- none of those is a judgement about anything, so for these roles the runtime
#: produces NO verdict at all: the exit is proven, the evidence is journalled under
#: `REVIEWER_RUNTIME_FAILURE`, and the run stops as a typed BLOCKED terminal that dispatches
#: no correction and spends no iteration.  A Worker keeps the workflow's own `BLOCKED`
#: status, which is the vocabulary for "could not complete" rather than a judgement.
RUNTIME_FAILURE_NOT_A_VERDICT_ROLES = ("PHASE_REVIEWER", "FINAL_REVIEWER")
REVIEWER_RUNTIME_FAILURE = "REVIEWER_RUNTIME_FAILURE"


def typed_failed_result(parsed: Mapping[str, Any], *, role: str,
                        verdict: Mapping[str, Any]) -> dict[str, Any]:
    """The parsed body, OVERRIDDEN by the runtime's typed failure.

    The body is still parsed by the shared parser and still travels -- an agent that said
    something before failing should not have it thrown away -- but the VERDICT field is the
    runtime's, because the runtime is what observed the failure.  A CLI that exited 1 on an
    authentication error and left `STATUS: COMPLETE` in a half-written report must not be
    able to talk its way to a pass.

    `standalone_failure` carries the named leg that refused, so an operator reading the
    settlement sees `error_field_set` or `exit_code_nonzero` rather than a bare FAIL.
    """
    field, value = FAILED_RESULT_BY_ROLE.get(role, FAILED_RESULT_DEFAULT)
    result = dict(parsed)
    result[field] = value
    if field == "status":
        # A BLOCKED Worker settlement is never a phase pass, and `routing.phase_gate`
        # additionally refuses a non-PASS unit-test status on the code-bearing phases.
        result["unit_test_status"] = "BLOCKED"
    result["standalone_failure"] = {
        "reason": str(verdict.get("reason") or "dispatch_failed"),
        "detail": str(verdict.get("detail") or ""),
        "stage": str(verdict.get("stage") or "completion"),
    }
    return result


#: The two members of the journal's EXECUTION-OUTCOME axis.  This is the vocabulary
#: `SETTLEMENT_OBSERVED.outcome` carries: whether the AGENT TURN completed under the
#: profile's success predicate.  It is a different axis from `SettlementEvent.outcome`,
#: whose frozen transport vocabulary says only that the settlement was DELIVERED
#: (`SUCCEEDED`) -- a typed FAILED settlement travels as a `SUCCEEDED` event -- and it is
#: a different axis again from the workflow verdict the result body carries
#: (`status`/`result`).  Consolidated review finding 4 is what happens when one of these
#: is compared against another.
EXECUTION_OUTCOMES = ("succeeded", "failed")
#: The key `typed_failed_result` stamps on every result the runtime settled as FAILED.
EXECUTION_FAILURE_KEY = "standalone_failure"


def execution_outcome_of(result: Mapping[str, Any] | None) -> str:
    """The agent EXECUTION outcome a settled result carries, in the journal's own words.

    Derived from the result the RUNTIME wrote, never from the transport envelope:
    `typed_failed_result` is the single writer of a FAILED settlement's result and it stamps
    `standalone_failure` on every one, so its presence IS the runtime's own record that the
    turn did not succeed.  Everything else the runtime settled succeeded by construction --
    `_settle` reaches `COMPLETED` only through the profile's predicate.  ``""`` for a result
    that is not a mapping: no axis is derived from a shape nothing wrote.
    """
    if not isinstance(result, Mapping):
        return ""
    return "failed" if EXECUTION_FAILURE_KEY in result else "succeeded"


def workflow_verdict_of(result: Mapping[str, Any] | None, *, role: str = "") -> tuple[str, str]:
    """``(field, value)`` of the WORKFLOW verdict a result carries -- `status` for a Worker,
    `result` for a Reviewer -- or ``("", "")`` when it carries none.  A third axis, read by
    the same rule `typed_failed_result` writes by, so a comparison of two settlements reads
    the same field on both sides."""
    if not isinstance(result, Mapping):
        return ("", "")
    if role in FAILED_RESULT_BY_ROLE:
        field = FAILED_RESULT_BY_ROLE[role][0]
    elif "status" in result and "result" not in result:
        field = "status"
    elif "result" in result:
        field = "result"
    else:
        field = "status"
    value = result.get(field)
    return (field, str(value).upper()) if isinstance(value, str) and value else ("", "")


# ---- D5.4 the single fail-closed resolver ----------------------------------------------
def resolve_unknown(situation: str, **facts: Any) -> dict[str, Any]:
    """ONE place to audit every unknown's disposition.

    A per-call-site decision about what an unknown means is how a fleet of small,
    individually reasonable choices adds up to a success guess.  Every branch here is total
    over its closed input and none of them returns a success.
    """
    if situation == "readiness_text_only":
        return {"verdict": "not_ready", "reason": "missing:bound_signal",
                "note": "text is supplementary; READY requires R-A and R-B"}
    if situation == "readiness_quorum_met":
        return {"verdict": "ready", "reason": "",
                "note": "READY at most -- never a completion, never RUNNING"}
    if situation == "structured_channel_unreadable":
        return {"verdict": "unprovable", "reason": "structured_channel_unreadable",
                "then": "bounded retry, then TIMED_OUT; never READY"}
    if situation == "unrecognised_frame":
        return {"verdict": "not_ready", "reason": "missing:bound_signal",
                "then": "TIMED_OUT at the deadline; unknown output advances nothing"}
    if situation == "readiness_timeout":
        return {"state": "TIMED_OUT", "reason": "readiness_deadline",
                "note": "unknown -- never 'not ready', never 'finished'"}
    if situation == "exit_status_absent":
        return {"state": "LOST", "lost_reason": facts.get("lost_reason") or "cause_unreported",
                "exit_status": None,
                "note": f"UNVERIFIED_PROCESS_EXIT_CODE={UNVERIFIED_PROCESS_EXIT_CODE} is a "
                        "sentinel, not an exit code"}
    if situation == "delivery_verify_timeout":
        return {"state": "TIMED_OUT", "delivery": "not_observed", "second_write": False,
                "note": "the bytes were written before verification began; no retry"}
    if situation == "settlement_reread_unconfirmed":
        return {"refusal": "unconfirmed_is_not_settled", "state": None,
                "route": "recovery", "note": "not a completion"}
    if situation == "liveness_unverifiable":
        return {"state": "LOST", "lost_reason": "stop_unverified",
                "process_liveness": "disputed", "cleanup_authority": "unknown",
                "note": "never 'exited'; never permission to close"}
    if situation == "stream_end_unproven":
        return {"state": "LOST", "lost_reason": "stream_end_unproven",
                "ended": facts.get("ended", ""),
                "note": "the pty stream's end was not observed after the proven exit; a "
                        "record read before the end is not the final record"}
    if situation == "boundary_unproven":
        return {"state": "LOST", "lost_reason": "boundary_unproven",
                "ended": facts.get("ended", ""),
                "note": "OS-48: the fence marker was never observed in an answerable capture; "
                        "no record it holds is inside a proven boundary"}
    if situation == "os48_named":
        reason = str(facts.get("lost_reason") or "")
        if reason not in OS48_LOST_OUTCOMES:
            raise LifecycleError(f"{reason!r} is not an OS-48 LOST outcome")
        return {"state": "LOST", "lost_reason": reason, "detail": str(facts.get("detail") or ""),
                "note": "OS-48: a named fail-closed outcome; never a success"}
    if situation == "required_evidence_missing":
        return {"state": "LOST", "lost_reason": facts.get("lost_reason") or "evidence_unreadable",
                "note": "RULE 4: never a success guess"}
    if situation == "capture_truncated":
        return {"state": "LOST", "lost_reason": "capture_truncated",
                "note": "R-7 on the capture surface"}
    raise LifecycleError(
        f"situation {situation!r} has no declared disposition; an undeclared unknown must "
        "be added here rather than resolved at a call site")


# ---- D5.5 source vocabulary survives normalization (AC-37-07) --------------------------
def normalize(*, state: str, source_vocabulary: Mapping[str, Any],
              evidence: Mapping[str, Any], axes: Mapping[str, Any], at: str,
              lost_reason: str = "") -> StateRecord:
    """Normalize to a closed state while CARRYING the source's own words alongside.

    Alongside, never instead of.  A normalised value answers "what does the workflow do
    next"; the source vocabulary answers "what did the CLI actually say", and an operator
    debugging a run needs the second one.  No member is reduced to a boolean and none is
    defaulted.
    """
    if not isinstance(source_vocabulary, Mapping) or not source_vocabulary:
        raise LifecycleError(
            "source_vocabulary must be carried; normalizing without it discards the "
            "evidence AC-37-07 requires to survive")
    return make_state_record(state=state, evidence=evidence, axes=axes,
                             source_vocabulary=source_vocabulary, at=at,
                             lost_reason=lost_reason)


def map_exit_code(code: int | None, exit_code_map: Mapping[int, str]) -> dict[str, Any]:
    """Map an exit code through the profile's table.  An UNMAPPED code is LOST.

    G-7 -- the exact codes each CLI emits per cause -- is UNKNOWN, and an empty table is a
    valid, fail-closed configuration.  Nothing here has a branch that reads an unmapped
    code as success.
    """
    if code is None:
        return {"state": "LOST", "lost_reason": "cause_unreported", "exit_status": None}
    if code in exit_code_map:
        return {"state": exit_code_map[code] if exit_code_map[code] in STATES else "FAILED",
                "lost_reason": "", "exit_status": code,
                "source_vocabulary": {"exit_code": code, "mapped": exit_code_map[code]}}
    if code == 0 and 0 in exit_code_map:
        return {"state": exit_code_map[0], "lost_reason": "", "exit_status": 0}
    return {"state": "LOST", "lost_reason": "exit_code_unmapped", "exit_status": code,
            "source_vocabulary": {"exit_code": code}}
