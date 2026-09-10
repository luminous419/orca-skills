"""OS-37 N10.  The Claude and Codex drivers.  **The only module that may name a CLI.**

Everything above this line -- the lifecycle, the journal, the adapter, and every engine
module including the workflow, decision and review policy -- sees only a
:class:`DriverEvidence`, which is CLI-free by construction.  Two static tests hold that,
because one is not enough: a token sweep over the engine package excluding this module and
its profile, and a pinned digest over the policy modules proving the standalone work added
no branch there.

Argv comes from the PROFILE, never from a table in this file.  AC-37-03 requires explicit
configuration, and U7 -- what Orca's 43-CLI table actually contains -- is routed around
rather than resolved, and stays UNKNOWN.  The per-driver flag sets recorded in the approved
plan are INPUTS TO PREFLIGHT, not assumptions: preflight re-reads the installed binary's
``--help`` and refuses by name when a flag the profile depends on is absent, so a version
drift becomes ``profile_flag_unsupported`` instead of a mystery.

Prompt framing is CLI-INDEPENDENT and lives in :func:`frame_prompt`; only the verification
*proofs* differ per driver.  The settle gate before Enter is deliberately **uncapped** --
there is no ``min(...)`` anywhere on that value, and a test asserts that literally -- because
a truncated settle is how a long paste frame loses its beginning.

**The rule that outranks everything here: a single line of natural-language output is never
sufficient evidence for identity, liveness or completion.**  Where such a line is the only
evidence, the driver emits ``tier="screen_preview"`` and the state does not advance.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from . import standalone_capture as capture
from .standalone_lifecycle import (DELIVERY_OUTCOMES, DELIVERY_PROOFS, EVIDENCE_TIERS,
                                   classify_refusals, decide_readiness)
from .standalone_profile import (DELIVERY_MODES, IDENTITY_BINDINGS, RESUME_CHANNELS,
                                 StandaloneProfile)

#: What a driver produces.  CLI-free: `source_vocabulary` carries the CLI's own words as
#: DATA, so AC-37-07's "the source vocabulary survives normalization" holds without any
#: consumer having to know which CLI produced it.
EVIDENCE_KINDS = ("readiness", "turn_start", "wait", "delivery_proof", "completion", "exit")

#: Paste-bracket framing.  `ESC[200~` ... `ESC[201~`.
BRACKET_START = b"\x1b[200~"
BRACKET_END = b"\x1b[201~"
#: Every raw ESC in the PAYLOAD becomes these seven literal characters, so prompt text
#: cannot inject a control sequence into the frame that carries it.
ESC_REPLACEMENT = b"<ESC>"


class DriverEvidence(TypedDict):
    kind: str
    tier: str                     # a member of EVIDENCE_TIERS
    live_observed: bool
    source_vocabulary: dict[str, Any]
    at: str


def evidence(kind: str, tier: str, *, live_observed: bool,
             source_vocabulary: Mapping[str, Any], at: str) -> DriverEvidence:
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"evidence kind {kind!r} is not a closed-set member")
    if tier not in EVIDENCE_TIERS:
        raise ValueError(f"evidence tier {tier!r} is not a closed-set member")
    return {"kind": kind, "tier": tier, "live_observed": live_observed,
            "source_vocabulary": dict(source_vocabulary), "at": at}


# ---- D4.2a the DELIVERY-MODE capability axis --------------------------------------------
class DeliveryModeMismatch(RuntimeError):
    """A declared capability and the actual execution mode disagree.  FAIL CLOSED.

    Raised, never returned as a flag, and never repaired by falling back to the other mode,
    retrying in the other mode, or downgrading the check to a warning (USER DIRECTIVE D-C).
    """


class IdentityBindingUnverified(RuntimeError):
    """The declared identity binding could not be established.  FAIL CLOSED."""


class CapabilityDeclarationError(ValueError):
    """A `DriverCapabilities` declaration is outside a closed set, or is internally false."""


class DriverCapabilities(TypedDict):
    """What a driver DECLARES about itself (DESIGN D4.2a).

    Declared is not the same as true, and this runtime never assumes it is: D4.2b checks
    every member against actual execution behaviour at preflight and again during the run,
    and every disagreement is a NAMED typed outcome that fails closed.
    """

    delivery_mode: str
    identity_binding: str
    readiness_records: tuple[Any, ...]
    delivery_proofs: tuple[Any, ...]
    completion_records: tuple[Any, ...]
    resume_channel: str


def validate_driver_capabilities(capabilities: Mapping[str, Any]) -> DriverCapabilities:
    """Refuse a capability declaration that is outside a closed set or internally false.

    Every refusal is a RAISE.  A capability that is merely *reported* as doubtful is a
    capability a caller can ignore, and the honesty rule (W-4) is that a runtime declares
    only what it can actually do.
    """
    for field_name, vocabulary in (("delivery_mode", DELIVERY_MODES),
                                   ("identity_binding", IDENTITY_BINDINGS),
                                   ("resume_channel", RESUME_CHANNELS)):
        value = capabilities.get(field_name)
        if value not in vocabulary:
            raise CapabilityDeclarationError(
                f"{field_name} {value!r} is not one of {tuple(vocabulary)!r}; there is no "
                "default for a capability axis")
    if not capabilities.get("readiness_records"):
        raise CapabilityDeclarationError(
            "a driver declaring no readiness record can never close R-B, so its admission "
            "quorum can never close; declare one or the profile is profile_invalid")
    if not capabilities.get("delivery_proofs"):
        raise CapabilityDeclarationError(
            "a driver declaring no delivery proof can never construct a DeliveryProof, so "
            "PROMPT_DELIVERED would be unreachable and a run would be reported as a "
            "delivery_mode_mismatch that is really a profile omission")
    if not capabilities.get("completion_records"):
        raise CapabilityDeclarationError(
            "a driver declaring no completion record can never satisfy D4.4's conjunctive "
            "completion rule")
    return {
        "delivery_mode": str(capabilities["delivery_mode"]),
        "identity_binding": str(capabilities["identity_binding"]),
        "readiness_records": tuple(capabilities["readiness_records"]),
        "delivery_proofs": tuple(capabilities["delivery_proofs"]),
        "completion_records": tuple(capabilities["completion_records"]),
        "resume_channel": str(capabilities["resume_channel"]),
    }


# ---- D4.3a the ATOMIC delivery intent ----------------------------------------------------
class DeliveryIntent(TypedDict):
    """The spawn request AND the prompt digest, as ONE record (USER DIRECTIVE D-D.1).

    Journalled BEFORE the process exists.  It is an OBSERVATION, not a claim, not a lease
    and not a fence: the single claim authority stays `runtime_state` (AC-37-20), and this
    record is appended AFTER the existing claim and BEFORE the fork.
    """

    intent_id: str
    dispatch_id: str
    task_id: str
    session_id: str
    prompt_digest: str
    argv_digest: str
    attempt_incarnation: str
    delivery_mode: str
    at: str


def prompt_digest(payload: str) -> str:
    """``sha256`` of the SANITIZED payload, computed before either handover.

    Sanitized, not raw: the sanitized bytes are what actually reach the CLI in both modes,
    so a digest over the raw text would not identify what was delivered.
    """
    import hashlib
    return hashlib.sha256(sanitize_payload(payload)).hexdigest()


def make_delivery_intent(*, intent_id: str, dispatch_id: str, task_id: str,
                         session_id: str, payload: str, argv_digest: str,
                         attempt_incarnation: str, delivery_mode: str,
                         at: str | None = None) -> DeliveryIntent:
    if delivery_mode not in DELIVERY_MODES:
        raise DeliveryModeMismatch(
            f"delivery_mode {delivery_mode!r} is not one of {tuple(DELIVERY_MODES)!r}")
    return {"intent_id": intent_id, "dispatch_id": dispatch_id, "task_id": task_id,
            "session_id": session_id, "prompt_digest": prompt_digest(payload),
            "argv_digest": argv_digest, "attempt_incarnation": attempt_incarnation,
            "delivery_mode": delivery_mode, "at": at or _now_iso()}   # type: ignore[typeddict-item]


# ---- D4.3b spawn is NEVER delivery, BY TYPE ----------------------------------------------
class SpawnOutcome(TypedDict):
    """What a successful `execve` produces.  It has NO field that can express delivery.

    D-D.2 realised at the type level rather than by convention: `DeliveryProof` is
    constructed only by :func:`make_delivery_proof`, which requires a parsed record and a
    selector verdict, and there is no function anywhere that converts a `SpawnOutcome` into
    one.  "Spawn success alone is never accepted as delivery" is therefore not a rule a
    later refactor can forget -- it is a type that cannot say it.
    """

    pid: int
    pgid: int
    argv_digest: str
    spawned_at: str


class DeliveryProof(TypedDict):
    """Positive, typed evidence that the dispatched prompt reached the agent.

    ``proof_class`` is `A` (a declared structured record on the private fd) or `B` (a
    record that additionally satisfies the driver's CONJUNCTIVE selector).  Class C -- an
    unambiguous process outcome -- is admissible for the TERMINAL outcome only and can
    never construct one of these.
    """

    driver: str
    proof_class: str
    record_type: str
    session_id: str
    prompt_digest: str
    intent_id: str
    source_vocabulary: dict[str, Any]
    at: str


def make_delivery_proof(*, driver: str, proof_class: str, record: Mapping[str, Any],
                        intent: Mapping[str, Any]) -> DeliveryProof:
    if proof_class not in ("A", "B"):
        raise ValueError(
            f"proof_class {proof_class!r} is not admissible for PROMPT_DELIVERED; class C "
            "(a process outcome) settles a run and never delivers one")
    return {"driver": driver, "proof_class": proof_class,
            "record_type": str(record.get("type", "")),
            "session_id": str(intent.get("session_id", "")),
            "prompt_digest": str(intent.get("prompt_digest", "")),
            "intent_id": str(intent.get("intent_id", "")),
            "source_vocabulary": dict(record), "at": _now_iso()}


# ---- D4.3c the CONJUNCTIVE delivery selectors (iteration 4, F-001) ----------------------
#: A model identifier of the form `<...>` is a PLACEHOLDER, not a model.  D4.0 M-15
#: measured `"<synthetic>"` on the login-failure path; M-14 measured `"claude-opus-5"` on a
#: real turn.  The pattern refuses the whole shape, not just the one measured word, so a
#: future `<offline>` or `<stub>` is refused by the same conjunct.
_PLACEHOLDER_MODEL = re.compile(r"^<.*>$")


def _usage_token_total(message: Any) -> int:
    """The sum of the integer token counters in a `message.usage`, or ``0``.

    A missing, unreadable or non-integer counter contributes nothing -- it is NOT read as
    "probably positive".  M-15 measured every counter at `0` on the synthetic response and
    M-14 measured `input 2 / output 1 / cache 1978+2365` on a real one.
    """
    if not isinstance(message, Mapping):
        return 0
    usage = message.get("usage")
    if not isinstance(usage, Mapping):
        return 0
    total = 0
    for value in usage.values():
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            total += value
    return total


def claude_delivery_selector(record: Mapping[str, Any], intent: Mapping[str, Any], *,
                             declared_types: Sequence[str] = ()) -> bool:
    """C-1 .. C-7, ALL of which must hold.  Any one failing means NOT delivery.

    **The defect this exists to refuse, stated as it was measured.**  D4.0 M-15: a pure
    login failure emits `{"type":"assistant", "session_id":"<THE RUNTIME'S OWN MINTED
    ID>", "error":"authentication_failed", "is_api_error_message":true,
    "message":{"model":"<synthetic>", ...}}` with every `usage` counter `0` and no
    `request_id`.  An identity-bound record proves PROCESS AND SESSION PROVENANCE.  It does
    NOT prove that the dispatched prompt executed.  A type-only membership test admitted it
    and would have entered `PROMPT_DELIVERED` on an unauthenticated CLI -- the same family
    of defect this ticket exists to prevent, one state earlier.

    C-4 .. C-7 are FOUR INDEPENDENT refusals of that one record.  The synthetic response
    would have to acquire a server-issued `request_id` *or* an API `msg_` id *or* non-zero
    billed tokens, AND shed both error markers, AND stop declaring itself `<synthetic>`,
    before it could pass -- which is another way of saying it would have to become a real
    turn.

    ``declared_types`` is `profile.delivery_proofs`' record types; C-1 requires membership,
    so an undeclared type is refused before any field is read.
    """
    # C-1: the declared type.
    if record.get("type") != "assistant":
        return False
    if declared_types and "assistant" not in tuple(declared_types):
        return False
    # C-2: EQUALITY against the identity this dispatch is bound to.  Retained, and
    # deliberately NOT sufficient -- M-15 satisfies it.
    #
    # An ABSENT identity on either side is refused BEFORE the comparison, because `None ==
    # None` is true and would let a record with no session id satisfy an intent with no
    # session id.  An equality conjunct that two absences can satisfy is not a binding.
    bound_to = intent.get("session_id")
    if not bound_to or record.get("session_id") != bound_to:
        return False
    # C-4: no error marker.  M-14: absent.  M-15: "authentication_failed".
    if "error" in record:
        return False
    # C-5: independent of C-4.  M-14: key absent.  M-15: true.
    if record.get("is_api_error_message") is True:
        return False
    message = record.get("message")
    if not isinstance(message, Mapping):
        return False
    # C-6: a real model identifier, not a placeholder.  Independent of C-4 and C-5.
    model = message.get("model")
    if not isinstance(model, str) or not model or _PLACEHOLDER_MODEL.match(model):
        return False
    # C-7: at least ONE positive API-round-trip marker.  A disjunction of three
    # independently measured markers, deliberately, so the selector is not over-fitted to
    # one field name.
    request_id = record.get("request_id")
    message_id = message.get("id")
    if (isinstance(request_id, str) and request_id):
        return True
    if isinstance(message_id, str) and message_id.startswith("msg_"):
        return True
    return _usage_token_total(message) > 0


def claude_replayed_user_selector(record: Mapping[str, Any], intent: Mapping[str, Any], *,
                                  composed_argv: Sequence[str] = ()) -> bool:
    """The SECOND Claude class-B member, scoped honestly (D4.0 M-6).

    The replayed `{"type":"user", ...}` acknowledgement is a PROMPT-BOUND positive: it is
    admissible only when the composed argv actually contains
    `--input-format stream-json --replay-user-messages`, AND the sanitized digest of the
    replayed content equals the intent's `prompt_digest`.

    The shipping Claude profile passes the payload POSITIONALLY (M-1) and composes neither
    flag, so this member is INERT for it.  It is retained because D4.2b's conformance check
    must be able to refuse a profile that declares it without composing the flags, not
    because anything currently relies on it.
    """
    if record.get("type") != "user":
        return False
    argv = tuple(composed_argv)
    if "--input-format" not in argv or "--replay-user-messages" not in argv:
        return False
    bound_to = intent.get("session_id")
    if not bound_to or record.get("session_id") != bound_to:
        return False
    message = record.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if isinstance(content, list):
        text = "".join(part.get("text", "") for part in content
                       if isinstance(part, Mapping))
    elif isinstance(content, str):
        text = content
    else:
        return False
    return prompt_digest(text) == intent.get("prompt_digest")


def codex_delivery_selector(record: Mapping[str, Any], intent: Mapping[str, Any], *,
                            declared_types: Sequence[str] = ()) -> bool:
    """K-1 (or K-3).  `turn.started` is DEMOTED and is no longer a delivery proof.

    **The analogous hole, found by measurement rather than assumed away.**  D4.0 M-8's
    failure leg -- an unseeded `CODEX_HOME`, `401 Unauthorized`, `rc=1`, `-o` absent --
    emits, in order: `thread.started` -> **`turn.started`** -> `error{401}` ->
    `item.completed{item.type:"error"}` -> `turn.failed`.  So `turn.started` precedes auth
    AND precedes any model work, exactly as the Claude synthetic `assistant` record does.
    Declaring it a delivery proof would let a Codex 401 enter `PROMPT_DELIVERED`.

    Codex's delivery proof is therefore the MEASURED POSITIVE:
      K-1  `item.completed` whose `item.type` is `agent_message` (M-8's success leg; the
           failure leg's only `item.completed` carries `item.type == "error"`), or
      K-3  `turn.completed`, which is sufficient on its own -- a completed turn entails a
           delivered prompt.
    """
    kind = record.get("type")
    declared = tuple(declared_types)
    if kind == "turn.completed":
        if declared and "turn.completed" not in declared:
            return False
        return True
    if kind != "item.completed":
        return False
    if declared and "item.completed" not in declared:
        return False
    item = record.get("item")
    if not isinstance(item, Mapping):
        return False
    return item.get("type") == "agent_message"


#: Which conjunctive selector belongs to which driver.  A driver whose name is not here has
#: no delivery selector and therefore cannot construct a `DeliveryProof` at all -- which is
#: the correct fail-closed direction for a driver nobody has measured.
DELIVERY_SELECTORS = {"claude": claude_delivery_selector,
                      "codex": codex_delivery_selector}


# ---- D4.3 prompt framing: ONE code path for both drivers -------------------------------
def sanitize_payload(payload: str) -> bytes:
    """Replace every raw ESC with the literal seven characters ``<ESC>``.

    Not stripped and not escaped-for-display: replaced with text that is unambiguous in the
    transcript.  A stripped ESC would silently change the prompt's meaning; a raw one could
    terminate the bracket the frame depends on.
    """
    return payload.encode("utf-8", "replace").replace(b"\x1b", ESC_REPLACEMENT)


def frame_prompt(payload: str) -> bytes:
    """``ESC[200~`` + sanitized payload + ``ESC[201~``.  ONE frame, written once."""
    return BRACKET_START + sanitize_payload(payload) + BRACKET_END


def settle_ms(frame: bytes, *, measured_ingest_rate: float,
              settle_floor_ms: int) -> int:
    """``ceil(len(frame) / measured_ingest_rate) + settle_floor_ms``.  **UNCAPPED.**

    ``measured_ingest_rate`` is re-measured on THIS host at preflight and never transcribed
    from Orca's constants.  There is deliberately no ``min(...)`` and no ceiling constant
    anywhere on this value: this gate exists so a long frame is fully ingested before Enter,
    and truncating it defeats exactly that.
    """
    if measured_ingest_rate <= 0:
        raise ValueError("measured_ingest_rate must be positive; a zero rate would make "
                         "the settle gate infinite rather than uncapped")
    return int(math.ceil(len(frame) / measured_ingest_rate)) + int(settle_floor_ms)


def write_frame(master_fd: int, frame: bytes, *, writer: Any = None) -> int:
    """Write the WHOLE frame, retrying only on ``EINTR``/short write of the same buffer.

    Retrying the same buffer and only the same buffer: a split frame can lose its beginning,
    and re-framing a partial write would send the bracket twice.
    """
    send = writer or os.write
    written = 0
    while written < len(frame):
        try:
            written += send(master_fd, frame[written:])
        except InterruptedError:
            continue
    return written


class DeliveryResult(TypedDict):
    delivery: str
    proof: str | None
    frame_bytes: int
    settle_ms: int


def deliver(master_fd: int, payload: str, *, profile: StandaloneProfile,
            measured_ingest_rate: float, verify: Any,
            writer: Any = None, sleep: Any = None,
            baseline_working: bool = False) -> DeliveryResult:
    """Frame -> one write -> UNCAPPED settle -> ``\\r`` -> poll for one of the three proofs.

    ``not_observed`` is NEVER auto-retried: the bytes were written before verification
    began, so a retry would deliver the prompt twice.  It maps to ``TIMED_OUT`` with unknown
    delivery and is routed to the run's recovery policy.
    """
    import time as _time
    pause = sleep or _time.sleep
    frame = frame_prompt(payload)
    settle = settle_ms(frame, measured_ingest_rate=measured_ingest_rate,
                       settle_floor_ms=profile.timeouts.settle_floor_ms)
    try:
        write_frame(master_fd, frame, writer=writer)
    except OSError:
        return {"delivery": "not_writable", "proof": None, "frame_bytes": len(frame),
                "settle_ms": settle}
    pause(settle / 1000.0)
    try:
        write_frame(master_fd, b"\r", writer=writer)
    except OSError:
        return {"delivery": "not_writable", "proof": None, "frame_bytes": len(frame),
                "settle_ms": settle}
    outcome = verify(baseline_working)
    proof = outcome.get("proof")
    delivery = outcome.get("delivery", "not_observed")
    if delivery not in DELIVERY_OUTCOMES:
        raise ValueError(f"delivery {delivery!r} is not a closed-set member")
    if proof is not None and proof not in DELIVERY_PROOFS:
        raise ValueError(f"proof {proof!r} is not a closed-set member")
    return {"delivery": delivery, "proof": proof, "frame_bytes": len(frame),
            "settle_ms": settle}


# ---- the driver base -------------------------------------------------------------------
class _Driver:
    """Shared behaviour.  Every per-CLI difference is a method the subclass overrides."""

    name = ""

    def __init__(self, profile: StandaloneProfile) -> None:
        if profile.driver != self.name:
            raise ValueError(
                f"profile declares driver {profile.driver!r}, not {self.name!r}")
        self.profile = profile

    # -- D4.2a: the declared capability axis ---------------------------------------------
    def capabilities(self) -> DriverCapabilities:
        """What this driver declares, read from the PROFILE and validated on every call.

        Read, never computed: `delivery_mode` is assigned in exactly one place in this
        runtime -- profile parsing -- and `test_os37_driver_isolation.py::
        test_delivery_mode_is_never_reassigned` asserts that mechanically.  A driver that
        could recompute its own mode could recover from a mismatch by changing it, and
        USER DIRECTIVE D-C requires a mismatch to fail closed instead.
        """
        return validate_driver_capabilities({
            "delivery_mode": self.profile.delivery_mode,
            "identity_binding": self.profile.identity_binding,
            "readiness_records": self.profile.readiness_records,
            "delivery_proofs": self.profile.delivery_proofs,
            "completion_records": self.profile.completion_records,
            "resume_channel": self.profile.resume_channel,
        })

    @property
    def delivery_mode(self) -> str:
        """The DECLARED mode.  A read-only view of the profile's single assignment."""
        return self.profile.delivery_mode

    def may_send_prompt(self, evidence: Mapping[str, Any], *,
                        minted_session_id: str) -> Any:
        """`post_ready_delivery` only.  On a `launch_with_prompt` driver this RAISES.

        D4.3d step 3: there is no code path in which a login frame could be read as
        "permission to send", because on a `launch_with_prompt` driver the question is
        never asked.  `deliver` is not merely guarded here -- it is ABSENT from the class
        (see :func:`driver_for`), so `hasattr(driver, "deliver")` is False.
        """
        raise DeliveryModeMismatch(
            f"driver {self.name!r} declares delivery_mode="
            f"{self.profile.delivery_mode!r}; the prompt left with the execve, so there is "
            "no 'may I send' question to answer and no window in which to answer it")

    # -- argv ----------------------------------------------------------------------------
    def argv(self, *, session_id: str, prompt: str | None = None,
             prompt_mode: bool = True) -> tuple[str, ...]:
        raise NotImplementedError

    def launch_argv(self, *, session_id: str, prompt: str) -> tuple[str, ...]:
        """The argv for a `launch_with_prompt` spawn.  The prompt is NOT optional here.

        A separate entry point rather than an optional parameter, so that "the runtime
        forgot the prompt" is a `DeliveryModeMismatch` at the call site rather than a spawn
        that quietly waits for a stdin that never comes.
        """
        if self.profile.delivery_mode != "launch_with_prompt":
            raise DeliveryModeMismatch(
                f"driver {self.name!r} declares delivery_mode="
                f"{self.profile.delivery_mode!r}; launch_argv composes a prompt onto the "
                "argv and is meaningless for a driver that receives it after READY")
        if not isinstance(prompt, str) or not prompt:
            raise DeliveryModeMismatch(
                "a launch_with_prompt spawn without a prompt has no delivery to prove")
        return self.argv(session_id=session_id, prompt=prompt)

    # -- D4.3c: the CONJUNCTIVE delivery selector, per driver -----------------------------
    def delivery_evidence(self, text: str, *, intent: Mapping[str, Any],
                          channel_owned: bool = True,
                          composed_argv: Sequence[str] = ()) -> DeliveryProof | None:
        """A `DeliveryProof`, or ``None``.  ``None`` is the honest answer, not a failure.

        Three things must hold, and the second is what iteration 4 added:

        C-3 / K-2  the record arrived on the PRIVATE fd this runtime created
                   (``channel_owned``), after the intent record's ``intended_at``;
        the driver's CONJUNCTIVE selector accepted it; and
        the record's type is declared in ``profile.delivery_proofs``.

        ``None`` here does NOT settle the run.  D4.3c's precedence rule stands: a typed
        terminal outcome settles a run whether or not a `DeliveryProof` was ever
        constructed, so an authentication failure is reported as an authentication failure
        rather than as a delivery-mode mismatch.
        """
        if not channel_owned:
            return None
        selector = DELIVERY_SELECTORS.get(self.name)
        if selector is None:
            return None
        declared = tuple(s.record_type for s in self.profile.delivery_proofs)
        for record in self.structured_records(text):
            if selector(record, intent, declared_types=declared):
                return make_delivery_proof(driver=self.name, proof_class="B",
                                           record=record, intent=intent)
            if self.name == "claude" and claude_replayed_user_selector(
                    record, intent, composed_argv=composed_argv):
                return make_delivery_proof(driver=self.name, proof_class="B",
                                           record=record, intent=intent)
        return None

    def auth_marker_present(self, text: str) -> dict[str, Any] | None:
        """The D4.2b W-2 scan: a TYPED authentication/setup marker, or ``None``.

        The markers are record FIELDS, declared by the profile.  The login TEXT is never a
        marker -- reading prose here would put natural language back into a gate, which is
        exactly what RULE 1 forbids.  This exists so that a rehearsal whose delivery leg
        failed is named `auth_absent` rather than `delivery_mode_unverified`: the reason is
        KNOWN, not unverified.
        """
        for record in self.structured_records(text):
            for field_path, expected in self.profile.auth_markers:
                observed = _dig(record, field_path)
                if observed is None:
                    continue
                if str(observed).lower() == str(expected).lower():
                    return {"field": field_path, "expected": expected,
                            "record_type": record.get("type"), "at": _now_iso()}
        return None

    # -- parsing -------------------------------------------------------------------------
    def structured_records(self, text: str) -> tuple[dict[str, Any], ...]:
        """Line-delimited JSON off the pty stream.  Unparsable lines are NEVER dropped.

        They are kept as raw capture records by :func:`standalone_capture.structured_lines`;
        a line that is not JSON is simply not evidence of anything, which is a different
        statement from it not existing.
        """
        return tuple(parsed for parsed, _raw in capture.structured_lines(text)
                     if parsed is not None)

    # -- D4.5: TWO methods, TWO disjoint return types ------------------------------------
    def readiness_evidence(self, text: str, *, minted_session_id: str,
                           liveness: Mapping[str, Any] | None) -> dict[str, Any]:
        """S3 evidence ONLY.  Can never express a completion.

        Builds the three-part quorum record: R-A comes in as ``liveness`` (built from the OS
        by :mod:`standalone_pty`, reading zero terminal bytes), R-B is looked for on the
        structured channel, refusals come from text, and every title/screen reading is
        segregated into ``supplementary``.
        """
        bound = self.bound_readiness_signal(
            text, minted_session_id=minted_session_id,
            adopt=(self.profile.identity_binding == "adopted"
                   and not minted_session_id))
        refusals = classify_refusals(text)
        supplementary: list[dict[str, Any]] = []
        if text.strip():
            supplementary.append({"tier": "screen_preview", "text": text[-2000:],
                                  "live_observed": liveness is not None,
                                  "at": _now_iso()})
        return {"liveness": dict(liveness) if liveness else None,
                "bound_signal": bound, "refusals": refusals,
                "supplementary": tuple(supplementary)}

    def completion_evidence(self, text: str, *, exit_status: int | None,
                            exit_proven: bool,
                            capture_answerable: bool = True) -> dict[str, Any]:
        """Settlement candidates ONLY.  Can never express readiness."""
        record = self.completion_record(text)
        lost_reason = ""
        if not capture_answerable:
            lost_reason = capture.CAPTURE_TRUNCATED_LOST_REASON
        elif exit_status is None and not exit_proven:
            lost_reason = "cause_unreported"
        return {"exit_status": exit_status, "exit_proven": exit_proven,
                "settlement_record": record, "capture_answerable": capture_answerable,
                "lost_reason": lost_reason,
                "source_vocabulary": {"driver": self.name,
                                      "record": record or {},
                                      "exit_code": exit_status},
                "at": _now_iso()}

    # -- per-driver record selection ------------------------------------------------------
    def bound_readiness_signal(self, text: str, *, minted_session_id: str,
                               adopt: bool = False) -> dict[str, Any] | None:
        """R-B, or ``None``.  Looked for on the STRUCTURED channel only.

        The selector is the profile's, and matching is by EQUALITY on the record type and on
        the session identity.  Screen text is never consulted here -- that is what makes
        ``::test_bound_signal_replayed_as_screen_text_is_never_ready`` pass: those bytes
        arrive as a capture line, and a capture line that parses as JSON of the right type
        still carries the session field only if the CLI put it there.
        """
        for selector in self.profile.readiness_records:
            for record in self.structured_records(text):
                if record.get("type") != selector.record_type:
                    continue
                offered = _dig(record, selector.session_field)
                if adopt:
                    # A-2: the FIRST record of a declared type, and ONLY the first.  The
                    # caller freezes what comes back and compares by equality from then on;
                    # this method never decides that a second identity is acceptable.
                    if offered is None or offered == "":
                        continue
                    return {"channel": selector.channel,
                            "record_type": selector.record_type,
                            "session_id": offered, "binding_mode": "adopted",
                            "at": _now_iso(), "raw": dict(record)}
                if offered != minted_session_id:
                    continue
                return {"channel": selector.channel, "record_type": selector.record_type,
                        "session_id": offered, "binding_mode": "minted_echo",
                        "at": _now_iso(), "raw": dict(record)}
        return None

    def turn_start_evidence(self, text: str) -> DriverEvidence | None:
        raise NotImplementedError

    def wait_evidence(self, text: str) -> DriverEvidence | None:
        raise NotImplementedError

    def completion_record(self, text: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def natural_language_only(self, text: str) -> DriverEvidence:
        """The tier a bare natural-language line gets.  It advances NOTHING.

        RULE 1, realised as a return value: the observation is reported with its tier so an
        operator can see it, and the state machine's I-5 then refuses to advance on it.
        """
        return evidence("readiness", "screen_preview", live_observed=False,
                        source_vocabulary={"driver": self.name, "text": text[-500:]},
                        at=_now_iso())

    # -- D4.6 -----------------------------------------------------------------------------
    def graceful_hint(self) -> bytes:
        """An optional in-band hint, used ONLY as interrupt rung 0.

        Never evidence of death and never in place of the OS-confirmed exit proof.  The
        drivers contribute no other termination logic at all.
        """
        return self.profile.graceful_hint


class ClaudeDriver(_Driver):
    """The Claude Code CLI driver.  Every flag comes from the profile."""

    name = "claude"

    def argv(self, *, session_id: str, prompt: str | None = None,
             prompt_mode: bool = True) -> tuple[str, ...]:
        """Composed from D4.0's MEASURED contract, not from PLAN P4.4's transcription.

        Three things changed in iteration 3, and every one of them is a measurement:

        ``--bare`` is REMOVED from the mandated set.  M-2 measured that on this
        OAuth-authenticated host it forces ``apiKeySource:"none"`` and an
        ``authentication_failed`` turn: its own ``--help`` says *"Anthropic auth is
        strictly ANTHROPIC_API_KEY or apiKeyHelper via --settings (OAuth and keychain are
        never read)"*.  It is an AUTH-NARROWING flag, not a neutral isolation flag, and
        mandating it made every real dispatch fail.  A profile MAY still select it through
        ``extra_args`` when it also supplies an API-key secret reference, and preflight's
        ``auth`` check is what decides whether that combination works on this host.

        ``--safe-mode --setting-sources ''`` REPLACES it as the measured isolation set.
        M-4: the same run without them carries TEN ``system/hook_started`` /
        ``system/hook_response`` records from this repository's own `SessionStart` hooks --
        and the un-isolated payload contained this very orchestration run's transcript.
        With them: ``rc=0``, zero hook records, a real completed turn.

        The PROMPT is a POSITIONAL argument (M-1: ``claude [options] [command] [prompt]``).
        It is supplied at process creation because M-6 measured that there is no stdin
        arrangement in which a readiness record precedes delivery -- with stdin on a pipe
        held open and empty for 8 s, the CLI emitted ZERO bytes for the whole window.
        """
        profile = self.profile
        parts: list[str] = [profile.binary]
        if profile.settings_path:
            parts += ["--settings", profile.settings_path]
        if profile.mcp_config_path:
            parts += ["--strict-mcp-config", "--mcp-config", profile.mcp_config_path]
        if profile.identity_flag:
            # `minted_echo` only, and the profile names the flag: the runtime never
            # hard-codes an identity channel it has not been told exists.
            parts += [profile.identity_flag, session_id]
        if prompt_mode:
            # `--verbose` is NOT decoration.  MEASURED on `claude 2.1.260` (M-3): `-p` with
            # `--output-format stream-json` and no `--verbose` exits immediately with
            # `Error: When using --print, --output-format=stream-json requires --verbose`,
            # so the structured channel DESIGN §D4.4 reads never opens at all.  It travels
            # with the two flags it is a precondition of, so no profile can select one
            # without it.
            parts += ["-p", "--output-format", "stream-json", "--verbose"]
        if profile.permission_mode:
            parts += ["--permission-mode", profile.permission_mode]
        if profile.debug_file_path:
            parts += ["--debug-file", profile.debug_file_path]
        if profile.no_session_persistence:
            parts += ["--no-session-persistence"]
        # The measured parent-isolation set (M-4).  It is composed unconditionally because
        # the alternative -- inheriting this repository's `SessionStart` hooks into an
        # agent the runtime is supposed to own -- is the D13.6(c)#6 precondition failing.
        parts += ["--safe-mode", "--setting-sources", ""]
        parts += list(profile.extra_args)
        if prompt is not None:
            # `--` FIRST, and this is a MEASURED requirement rather than tidiness.  The
            # prompt is a POSITIONAL argument (M-1), and this CLI has VARIADIC flags --
            # `--add-dir <directories...>` is one -- which silently consume every following
            # token.  Measured: the same argv with `--add-dir <dir>` before the prompt and
            # no separator answers `Error: Input must be provided either through stdin or
            # as a prompt argument when using --print`, because the variadic flag ate the
            # prompt.  With `--` the same argv completes normally, `rc=0`.  The separator
            # makes the positional immune to EVERY variadic flag, including ones nobody has
            # measured -- which is the only version of this fix that survives a CLI update.
            parts += ["--", prompt]
        return tuple(parts)

    def turn_start_evidence(self, text: str) -> DriverEvidence | None:
        """``turn_start`` ONLY.  It is not, and cannot become, a delivery proof.

        D4.0 M-15 measured an `assistant` record on a pure login failure, carrying this
        runtime's own minted session id.  So a turn-start observation is reported with
        ``tier="raw"`` and emits `turn_start_observed` and nothing else; constructing a
        `DeliveryProof` requires :func:`claude_delivery_selector`, which refuses that record
        on four independent conjuncts.
        """
        for record in self.structured_records(text):
            if record.get("type") in ("message_start", "assistant", "stream_event"):
                return evidence("turn_start", "raw", live_observed=True,
                                source_vocabulary={"driver": self.name,
                                                   "type": record.get("type"),
                                                   "demoted": True,
                                                   "demoted_because": "M-15: an assistant "
                                                   "record is emitted on the login-failure "
                                                   "path, identity-bound and synthetic"},
                                at=_now_iso())
        return None

    def wait_evidence(self, text: str) -> DriverEvidence | None:
        for record in self.structured_records(text):
            if record.get("type") in ("permission_request", "tool_approval",
                                      "can_use_tool"):
                # Provenance `hook`: it came off the structured channel.  U4 keeps
                # WAITING_FOR_INPUT provisional, so the provenance travels with the value.
                return evidence("wait", "structured_stream", live_observed=True,
                                source_vocabulary={"driver": self.name,
                                                   "type": record.get("type"),
                                                   "provenance": "hook"},
                                at=_now_iso())
        return None

    def completion_record(self, text: str) -> dict[str, Any] | None:
        for record in reversed(self.structured_records(text)):
            if record.get("type") == "result":
                return dict(record)
        return None


class CodexDriver(_Driver):
    """The Codex CLI driver.  Every flag comes from the profile."""

    name = "codex"

    def argv(self, *, session_id: str, prompt: str | None = None,
             prompt_mode: bool = True) -> tuple[str, ...]:
        """Composed from D4.0's MEASURED contract (M-7, M-8, M-10, M-11).

        ``--ephemeral`` is NO LONGER MANDATED.  M-11 measured that it leaves no rollout,
        and ``codex exec resume`` is the only re-verification and resume channel Codex has
        -- so mandating it silently withdrew ``external_resume``.  It is now CONDITIONAL:
        a profile that selects it through ``extra_args`` declares
        ``resume_channel="none"``, and the honesty rule decides which one the operator
        gets rather than the argv deciding it behind their back.

        **No identity flag is composed, and that is a positive declaration.**  M-7: no flag
        on ``codex exec`` accepts a caller-supplied thread id.  M-10: supplying
        ``-c thread_id=<uuid>`` is accepted, the turn completes normally, the supplied value
        appears NOWHERE, and there is no error and no warning -- the failure is SILENT.  So
        R-B binds in ``adopted`` mode (D4.4 A-1..A-6) and `StandaloneProfile` refuses a
        profile that declares `adopted` alongside an `identity_flag`.

        ``session_id`` is accepted and deliberately unused here; it is the identity the
        RUNTIME minted for its own records, and passing it to a CLI that would ignore it is
        exactly the silent lie A-6 refuses.
        """
        profile = self.profile
        parts: list[str] = [profile.binary, "exec"]
        if prompt_mode:
            parts += ["--json"]
        parts += ["--ignore-user-config"]
        if profile.identity_flag:
            # Composed from the PROFILE, exactly as every other flag is (AC-37-03), and
            # NEVER from a table in this file.  The shipping Codex profile declares
            # `adopted` and therefore names no flag -- `StandaloneProfile` refuses the two
            # together, because M-10 measured this CLI silently ignoring a supplied
            # identity.  A profile that DOES name one is pointing this driver at a build or
            # a fixture whose identity channel it has declared, and refusing to compose what
            # the operator declared would make that profile silently inert -- the same
            # failure shape, from the other side.
            parts += [profile.identity_flag, session_id]
        if profile.worktree:
            parts += ["-C", profile.worktree]
        for directory in profile.add_dirs:
            parts += ["--add-dir", directory]
        parts += ["--skip-git-repo-check", "--color", "never"]
        if profile.sandbox_mode:
            parts += ["-s", profile.sandbox_mode]
        if profile.output_last_message_path:
            parts += ["-o", profile.output_last_message_path]
        parts += list(profile.extra_args)
        if prompt is not None:
            # **NO `--` separator here, and that is measured rather than an oversight.**
            # This CLI's `--add-dir <DIR>` takes exactly ONE value (`codex exec --help`), so
            # nothing can swallow the positional prompt -- and MEASURED: the same argv with
            # `--` prepended additionally emits `Reading additional input from stdin...` and
            # waits on stdin, which on a runtime-owned pty that never sees EOF is a hang.
            # The two drivers therefore compose the positional differently, and that
            # difference is exactly the kind of per-CLI knowledge this module exists to
            # absorb so that nothing above it has to know.
            parts.append(prompt)
        return tuple(parts)

    def resume_argv(self, *, adopted_id: str, prompt: str) -> tuple[str, ...]:
        """``codex exec resume <adopted id> [options] <PROMPT>`` -- a DIFFERENT contract.

        M-11 measured that ``codex exec resume`` REJECTS ``-C/--cd`` and ``--color`` with
        ``rc=2  error: unexpected argument``, so this argv is composed from its own profile
        field and preflight validates it against ``codex exec resume --help`` independently.
        Reusing the ``exec`` flag set here would produce a `rc=2` that looks like a CLI
        fault rather than a profile fault.
        """
        if self.profile.resume_channel != "cli_resume_subcommand":
            raise DeliveryModeMismatch(
                f"driver {self.name!r} declares resume_channel="
                f"{self.profile.resume_channel!r}; there is no resume argv to compose")
        parts = [self.profile.binary, "exec", "resume", adopted_id]
        parts += list(self.profile.resume_args)
        parts.append(prompt)
        return tuple(parts)

    def seed_auth_home(self, root: str | os.PathLike[str]) -> dict[str, Any]:
        """Copy EXACTLY the credential file the profile names into the run-scoped root.

        M-8 measured both legs: a fresh EMPTY ``CODEX_HOME`` yields ``401 Unauthorized``,
        ``rc=1`` and an ABSENT ``-o`` file; the same root seeded with the credential file
        yields ``rc=0`` and a present ``-o``.  ``--ignore-user-config``'s own help states
        auth still resolves through ``CODEX_HOME``, and M-8 confirms it.

        Nothing else from the real home is copied, the destination is ``0600``, and the
        return value names the destination PATH only -- never a byte of its content, and it
        is never journalled.
        """
        import shutil
        from pathlib import Path
        if not self.profile.auth_seed_source:
            return {"seeded": False, "reason": "no_auth_seed_declared", "path": ""}
        destination = Path(root) / self.profile.auth_seed_dest_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(self.profile.auth_seed_source, destination)
            os.chmod(destination, 0o600)
        except OSError as exc:
            # UNREADABLE is not ABSENT and is certainly not seeded: the caller turns this
            # into `auth_scope_unseeded`, a named preflight failure, never a silent run
            # against an empty credential root.
            return {"seeded": False, "reason": "auth_seed_unreadable",
                    "path": str(destination), "detail": exc.__class__.__name__}
        return {"seeded": True, "reason": "", "path": str(destination)}

    def turn_start_evidence(self, text: str) -> DriverEvidence | None:
        """DEMOTED in iteration 4 (F-001).  ``tier="raw"``: a LIFECYCLE observation.

        D4.0 M-8's `401` leg emits `thread.started` -> **`turn.started`** -> `error{401}` ->
        `item.completed{item.type:"error"}` -> `turn.failed`.  So `turn.started` is emitted
        BEFORE authentication and BEFORE any model work.  It is carried here so the
        transcript stays complete and an operator can see it, and it advances NOTHING on its
        own: I-2 refuses `turn_start_observed` as an entry into `RUNNING` unless a
        `delivery_proof_observed` already exists.
        """
        for record in self.structured_records(text):
            if record.get("type") in ("item.started", "turn.started", "task_started"):
                return evidence("turn_start", "raw", live_observed=True,
                                source_vocabulary={"driver": self.name,
                                                   "type": record.get("type"),
                                                   "demoted": True,
                                                   "demoted_because": "M-8: emitted on the "
                                                   "401 leg, before auth and before work"},
                                at=_now_iso())
        return None

    def wait_evidence(self, text: str) -> DriverEvidence | None:
        for record in self.structured_records(text):
            if record.get("type") in ("approval.requested", "exec_approval_request",
                                      "apply_patch_approval_request"):
                return evidence("wait", "structured_stream", live_observed=True,
                                source_vocabulary={"driver": self.name,
                                                   "type": record.get("type"),
                                                   "provenance": "hook"},
                                at=_now_iso())
        return None

    def completion_record(self, text: str) -> dict[str, Any] | None:
        """The structured final record, plus the ``-o`` file where the profile declares one.

        The ``-o`` file is read as a SECOND source, never as a substitute for the exit
        sentinel: a final-message file says what the agent produced, and the sentinel says
        the process actually ended.  D5's I-3 needs both.
        """
        for record in reversed(self.structured_records(text)):
            if record.get("type") in ("turn.completed", "task_complete", "item.completed"):
                found = dict(record)
                path = self.profile.output_last_message_path
                if path and os.path.exists(path):
                    try:
                        found["last_message_present"] = True
                        found["last_message_bytes"] = os.path.getsize(path)
                    except OSError:
                        found["last_message_present"] = False
                return found
        return None


# ---- D4.2a: `post_ready_delivery`'s contract, kept BYTE-UNCHANGED and kept LIVE ---------
class ReadyToken:
    """Proof that ``may_send_prompt()`` returned ``ready``.  Constructible NOWHERE else.

    ``deliver()`` takes one of these, so "a driver wrote a prompt without a ready verdict"
    is not a bug that can be written -- it is a `TypeError`.  This is the mechanism that
    carries the readiness-before-delivery guarantee, and it is the reason
    `post_ready_delivery` is retained as a live, exercised path rather than deleted along
    with the CLIs that cannot currently honour it (D4.2a, reasons 1-3).
    """

    __slots__ = ("session_id", "at", "_authority")

    def __init__(self, *, session_id: str, authority: object) -> None:
        if authority is not _READY_TOKEN_AUTHORITY:
            raise DeliveryModeMismatch(
                "a ReadyToken is constructible only by may_send_prompt() returning "
                "'ready'; constructing one directly would forge the readiness gate")
        self.session_id = session_id
        self.at = _now_iso()
        self._authority = authority


_READY_TOKEN_AUTHORITY = object()


class _PostReadyDelivery:
    """The mixin that HOLDS ``deliver``.  Mixed in ONLY for `post_ready_delivery`.

    D4.3d step 3: on a `launch_with_prompt` driver the attribute is genuinely ABSENT --
    ``hasattr(driver, "deliver")`` is ``False`` -- rather than present-and-guarded, so no
    call site can reach a prompt write at all.  A guarded method would still be a method a
    later refactor could call with the guard removed.
    """

    def may_send_prompt(self, evidence: Mapping[str, Any], *,
                        minted_session_id: str) -> ReadyToken:
        """The GATE.  Returns a `ReadyToken` only on a ``ready`` verdict.

        The verdict comes from the same conjunctive quorum every other surface uses:
        R-A (process/PTY, zero terminal bytes) AND R-B (a typed record on the structured
        channel carrying the minted identity, by equality) AND R-C (no refusal fired).
        ``supplementary`` is not a parameter of `decide_readiness`, so a title cannot
        contribute to this token.
        """
        verdict = decide_readiness(
            evidence.get("liveness"), evidence.get("bound_signal"),
            tuple(evidence.get("refusals") or ()),
            minted_session_id=minted_session_id,
            declared_record_types=[selector.record_type
                                   for selector in self.profile.readiness_records])
        if verdict["verdict"] != "ready":
            raise DeliveryModeMismatch(
                f"may_send_prompt refused: {verdict['verdict']!r} "
                f"({verdict.get('reason', '')!r}); the prompt is not written")
        return ReadyToken(session_id=minted_session_id,
                          authority=_READY_TOKEN_AUTHORITY)

    def deliver(self, ready_token: ReadyToken, master_fd: int, payload: str, *,
                measured_ingest_rate: float, verify: Any, writer: Any = None,
                sleep: Any = None, baseline_working: bool = False) -> DeliveryResult:
        """Frame -> one write -> UNCAPPED settle -> ``\r`` -> poll for a typed proof."""
        if not isinstance(ready_token, ReadyToken):
            raise DeliveryModeMismatch(
                "deliver() requires the ReadyToken may_send_prompt() returns; a caller "
                "that has not passed the readiness gate has nothing to hand over")
        return deliver(master_fd, payload, profile=self.profile,          # type: ignore[attr-defined]
                       measured_ingest_rate=measured_ingest_rate, verify=verify,
                       writer=writer, sleep=sleep, baseline_working=baseline_working)


DRIVERS = {"claude": ClaudeDriver, "codex": CodexDriver}

#: Built once per (driver, mode) pair.  A cache, not state: the classes are immutable and
#: `delivery_mode` is read from the profile, never stored on the class.
_MODE_CLASSES: dict[tuple[str, str], type] = {}


def driver_for(profile: StandaloneProfile) -> _Driver:
    """The driver this profile configures, IN THE MODE IT DECLARES.

    The declared mode selects the CLASS, so the mode is expressed in the object's shape
    rather than in a branch inside every method.  A `launch_with_prompt` driver has no
    ``deliver`` attribute at all, and its ``may_send_prompt`` raises `DeliveryModeMismatch`.
    """
    try:
        factory = DRIVERS[profile.driver]
    except KeyError:
        raise ValueError(
            f"no driver is implemented for {profile.driver!r}; the standalone MVP supports "
            f"{sorted(DRIVERS)!r}") from None
    if profile.delivery_mode != "post_ready_delivery":
        return factory(profile)
    key = (profile.driver, "post_ready_delivery")
    composed = _MODE_CLASSES.get(key)
    if composed is None:
        composed = type(f"{factory.__name__}PostReady", (_PostReadyDelivery, factory), {})
        _MODE_CLASSES[key] = composed
    return composed(profile)               # type: ignore[return-value]


def _dig(record: Mapping[str, Any], path: str) -> Any:
    """Read a dotted field path out of a parsed record.  Missing is ``None``."""
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
