"""OS-37 V-3 / V-4 / V-5.  The closed lifecycle contract, and READINESS THAT DOES NOT REST ON TEXT.

The five readiness negative-property cases are the evidence for F-002, and four of them
QUANTIFY OVER INPUTS rather than enumerating them.  That distinction is the whole point: a
fixture for an observed frame proves the runtime refuses THAT frame, and G-4 -- what an
update prompt looks like on the wire -- is UNKNOWN, so no set of fixtures can be complete.
A property over 10 000 generated frames, including structurally novel ones, holds for shapes
nobody has seen, because acceptance is a POSITIVE requirement (R-A and R-B) rather than the
absence of a pattern match.
"""
from __future__ import annotations

import inspect
import json
import random
import pathlib
import unittest

from scripts.deterministic_workflow import standalone_lifecycle as lifecycle
from scripts.deterministic_workflow.contracts import (OWNERSHIP_AXIS_VOCABULARIES,
                                                      VocabularyError, validate_axes)

MINTED = "s-0123456789abcdef0123"
DECLARED = ("system", "session.started")

LIVE = {"identity_matches": True, "not_exited": True,
        "foreground_is_child_group": True, "foreground_executable_matches": True,
        "observed": {}}
DEAD = {"identity_matches": False, "not_exited": False,
        "foreground_is_child_group": False, "foreground_executable_matches": False,
        "observed": {}}


def bound(session_id: str = MINTED, record_type: str = "system",
          channel: str = "structured") -> dict:
    return {"channel": channel, "record_type": record_type, "session_id": session_id,
            "at": "2026-09-10T00:00:00Z", "raw": {}}


def evidence(*, liveness=LIVE, bound_signal=None, refusals=(), supplementary=()):
    return {"liveness": liveness, "bound_signal": bound_signal,
            "refusals": tuple(refusals), "supplementary": tuple(supplementary)}


def verdict(ev, **kwargs):
    return lifecycle.may_send_prompt(ev, minted_session_id=MINTED,
                                     declared_record_types=DECLARED, **kwargs)


# =====================================================================================
class ReadinessQuorumTests(unittest.TestCase):
    """D5.3(4): ``ready`` iff R-A and R-B and R-C, and none of the three is text."""

    def test_the_quorum_accepts_only_when_all_three_hold(self) -> None:
        self.assertEqual(verdict(evidence(bound_signal=bound()))["verdict"], "ready")
        self.assertEqual(
            verdict(evidence(liveness=DEAD, bound_signal=bound()))["verdict"], "not_ready")
        self.assertEqual(verdict(evidence(bound_signal=None))["verdict"], "not_ready")
        self.assertEqual(
            verdict(evidence(bound_signal=bound(),
                             refusals=("blocked_prompt_beats_idle",)))["verdict"],
            "not_ready")

    def test_readiness_decision_ignores_supplementary(self) -> None:
        """``decide_readiness`` does NOT ACCEPT ``supplementary`` as a parameter at all.

        Two assertions.  First the signature: "a title accepted readiness" is a
        ``TypeError``, not a bug somebody could write.  Then, belt and braces, that no
        title or screen value changes any verdict when it is carried through the surface
        that does receive it.
        """
        parameters = tuple(inspect.signature(lifecycle.decide_readiness).parameters)
        self.assertNotIn(
            "supplementary", parameters,
            "decide_readiness must not take supplementary; if it can be passed, it can be "
            f"read. signature={parameters}")
        with self.assertRaises(TypeError):
            lifecycle.decide_readiness(  # type: ignore[call-arg]
                LIVE, None, (), minted_session_id=MINTED,
                declared_record_types=DECLARED,
                supplementary=({"tier": "title", "text": "ready", "live_observed": True,
                                "at": ""},))
        # And through the surface: a title claiming readiness changes nothing.
        titles = [
            ({"tier": "title", "text": "claude — idle", "live_observed": True, "at": ""},),
            ({"tier": "screen_preview", "text": "> ready for input", "live_observed": True,
              "at": ""},),
            ({"tier": "title", "text": "READY", "live_observed": True, "at": ""},),
        ]
        for supplementary in titles:
            with self.subTest(text=supplementary[0]["text"]):
                self.assertEqual(
                    verdict(evidence(bound_signal=None,
                                     supplementary=supplementary))["verdict"],
                    "not_ready",
                    "a title or screen reading made readiness accept; text is "
                    "supplementary and may only SUBTRACT")

    def test_arbitrary_interactive_frame_is_never_ready(self) -> None:
        """N = 10 000 GENERATED frames, and not one of them reaches ``ready``.

        Drawn from uniform random bytes, random CSI/OSC/DCS control sequences, random
        natural-language lines, random JSON whose ``type`` is NOT a declared readiness type,
        the G-1/G-4/G-5 prompt shapes, and structurally novel frames the runtime has never
        seen.  The property is quantified over arbitrary input, so it holds for whatever
        shape G-4's update prompt turns out to have -- which is why G-4 can stay UNKNOWN.
        """
        rng = random.Random(20260910)
        failures: list[str] = []
        for index in range(10_000):
            frame = _generate_frame(rng, index)
            # R-B is unsatisfied by construction: no declared record carrying the minted
            # session id arrived on the structured channel.  R-A is granted, so the ONLY
            # thing that could make this ready is the frame -- and nothing may.
            result = verdict(evidence(
                bound_signal=None,
                refusals=lifecycle.classify_refusals(frame),
                supplementary=({"tier": "screen_preview", "text": frame,
                                "live_observed": True, "at": ""},)))
            if result["verdict"] == "ready":
                failures.append(f"frame {index}: {frame[:120]!r}")
            if len(failures) > 3:
                break
        self.assertEqual(
            failures, [],
            "an arbitrary interactive frame was accepted as READY; acceptance must be a "
            "positive requirement (R-A and R-B), never the absence of a pattern match:\n"
            + "\n".join(failures))

    def test_bound_signal_replayed_as_screen_text_is_never_ready(self) -> None:
        """The EXACT BYTES of a valid readiness record, printed as screen text, are refused.

        R-B reads a CHANNEL, not a string.  A CLI -- or an agent -- that echoes a
        well-formed readiness record into its own output must not thereby become ready.
        """
        replayed = json.dumps({"type": "system", "session_id": MINTED})
        result = verdict(evidence(
            bound_signal=None,
            supplementary=({"tier": "screen_preview", "text": replayed,
                            "live_observed": True, "at": ""},)))
        self.assertEqual(result["verdict"], "not_ready")
        self.assertEqual(result["reason"], "missing:bound_signal")
        # And with the same content declared on a non-structured channel.
        self.assertFalse(
            lifecycle.r_b_satisfied(bound(channel="screen"), minted_session_id=MINTED,
                                    declared_record_types=DECLARED),
            "a record claiming a non-structured channel satisfied R-B")

    def test_bound_signal_with_foreign_session_id_is_never_ready(self) -> None:
        """A well-formed record of a DECLARED type carrying a DIFFERENT session id is refused.

        The binding is equality against a value minted in this process microseconds before
        the spawn.  A frame the runtime did not cause cannot quote it.
        """
        for foreign in ("s-ffffffffffffffffffff", "", MINTED[:-1], MINTED + "x"):
            with self.subTest(session_id=foreign):
                self.assertEqual(
                    verdict(evidence(bound_signal=bound(session_id=foreign)))["verdict"],
                    "not_ready")
        # An undeclared record TYPE is refused too, even with the right session id.
        self.assertEqual(
            verdict(evidence(bound_signal=bound(record_type="update_available")))["verdict"],
            "not_ready")

    def test_ready_requires_live_incarnation(self) -> None:
        """A fresh, valid, correctly bound signal with a dead incarnation is refused.

        R-A is independent of every byte of text, so this case cannot be fixed by any
        amount of output: the process is not there.
        """
        self.assertEqual(
            verdict(evidence(liveness=DEAD, bound_signal=bound()))["verdict"], "not_ready")
        for leg in ("identity_matches", "not_exited", "foreground_is_child_group",
                    "foreground_executable_matches"):
            with self.subTest(missing=leg):
                partial = dict(LIVE, **{leg: False})
                self.assertEqual(
                    verdict(evidence(liveness=partial, bound_signal=bound()))["verdict"],
                    "not_ready",
                    f"R-A accepted with {leg} false; all four legs are conjunctive")
        self.assertEqual(
            verdict(evidence(liveness=None, bound_signal=bound()))["verdict"], "not_ready")

    def test_unknown_frame_times_out_rather_than_advancing(self) -> None:
        """The bounded deadline yields ``TIMED_OUT``-shaped ``unprovable``.

        Never ``READY``, and never "not ready" as an established fact: the runtime did not
        establish that the agent is not ready, it failed to establish that it is.
        """
        result = verdict(evidence(bound_signal=None), deadline_expired=True)
        self.assertEqual(result["verdict"], "unprovable")
        self.assertEqual(result["reason"], "deadline_expired")
        self.assertEqual(
            lifecycle.resolve_unknown("readiness_timeout")["state"], "TIMED_OUT")

    def test_unreadable_structured_channel_is_unprovable_not_ready(self) -> None:
        result = verdict(evidence(bound_signal=None), structured_channel_readable=False)
        self.assertEqual(result["verdict"], "unprovable")
        self.assertEqual(result["reason"], "structured_channel_unreadable")

    def test_the_verdict_is_three_valued(self) -> None:
        self.assertEqual(set(lifecycle.READINESS_VERDICTS),
                         {"ready", "not_ready", "unprovable"})

    def test_only_one_tier_can_accept_readiness(self) -> None:
        self.assertEqual(lifecycle.ACCEPTING_READINESS_TIERS, frozenset({"bound_structured"}))
        self.assertEqual(lifecycle.SUPPLEMENTARY_TIERS,
                         frozenset({"screen_preview", "title"}))


def _generate_frame(rng: random.Random, index: int) -> str:
    """One frame from the six declared families, including a structurally novel one."""
    family = index % 6
    if family == 0:                                   # uniform random bytes
        return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 200))).decode(
            "utf-8", "replace")
    if family == 1:                                   # random CSI / OSC / DCS
        kind = rng.choice(("[", "]", "P", "_", "^"))
        body = "".join(rng.choice("0123456789;?<>=ABCDEFGHJKLMNPSTfhilmnrst")
                       for _ in range(rng.randrange(1, 20)))
        return f"\x1b{kind}{body}\x07"
    if family == 2:                                   # random natural language
        words = ("ready", "waiting", "idle", "done", "complete", "logged", "in",
                 "please", "press", "enter", "continue", "agent", "session", "ok")
        return " ".join(rng.choice(words) for _ in range(rng.randrange(1, 12)))
    if family == 3:                                   # JSON with an UNDECLARED type
        return json.dumps({"type": rng.choice(("update_available", "login_required",
                                               "permission_request", "banner",
                                               "notice", "wizard", "unknown_kind")),
                           "session_id": MINTED, "n": rng.randrange(1000)})
    if family == 4:                                   # the G-1 / G-4 / G-5 shapes
        return rng.choice((
            "Please log in to continue.\nRun `login` to authenticate.",
            "An update is available. Install now? [y/N]",
            "Allow this tool to write to /etc/hosts?",
            "Welcome to setup. Configure your workspace now?",
            "You are not logged in.",
            "API key required -- paste it here:",
        ))
    # family 5: structurally novel -- shapes the runtime has never been shown
    return rng.choice((
        "\x1b]1337;SetUserVar=ready=MQ==\x07",
        "\x00\x01\x02READY\x03\x04",
        "{\"jsonrpc\":\"2.0\",\"method\":\"ready\",\"params\":{\"session\":\"" + MINTED + "\"}}",
        "<<<AGENT-READY " + MINTED + ">>>",
        "\x1b[?2004h\x1b[6n\x1b[1;1H\x1b[2Jready",
        "ready\x00" * rng.randrange(1, 8),
    ))


# =====================================================================================
class SurfaceSeparationTests(unittest.TestCase):
    """V-4 / AC-37-08: the four surfaces stay four, with four separate orderings."""

    def test_there_are_exactly_four_surfaces_and_four_functions(self) -> None:
        self.assertEqual(lifecycle.SURFACES, ("S1", "S2", "S3", "S4"))
        self.assertEqual(sorted(lifecycle.SURFACE_FUNCTIONS), ["S1", "S2", "S3", "S4"])
        bodies = {name: fn for name, fn in lifecycle.SURFACE_FUNCTIONS.items()}
        self.assertEqual(
            len({id(fn) for fn in bodies.values()}), 4,
            "two surfaces share an implementation; a merged status would silently change "
            "behaviour for consumers that legitimately combine them differently")

    def test_surface_ordering_s1_reports_layers_separately(self) -> None:
        """S1's title layer must be readable as ABSENT by a liveness-gated consumer."""
        with_title = lifecycle.report_activity(
            structured_row=None,
            title={"tier": "title", "text": "busy", "live_observed": False, "at": ""},
            live_pty=False)
        self.assertIsNone(with_title["value"],
                          "S1 served a title-derived value with no live PTY")
        self.assertIsNotNone(with_title["fallback"],
                             "the layer must still be CARRIED, just not counted")
        live = lifecycle.report_activity(
            structured_row=None,
            title={"tier": "title", "text": "busy", "live_observed": True, "at": ""},
            live_pty=True)
        self.assertIsNotNone(live["value"])

    def test_surface_ordering_s2_puts_an_approval_prompt_first(self) -> None:
        result = lifecycle.read_status(
            live_permission=True, blocked_text="", structured_row={"status": "idle"},
            foreground_is_shell=False, identity_resolved_title=None, process_probe=None)
        self.assertEqual(result["status"], "WAITING_FOR_INPUT")
        self.assertEqual(result["tier"], "permission")

    def test_surface_ordering_s2_gates_the_structured_row_on_the_foreground(self) -> None:
        """A fresh row emitted while a plain SHELL holds the foreground is not the status."""
        shelled = lifecycle.read_status(
            live_permission=False, blocked_text="", structured_row={"status": "working"},
            foreground_is_shell=True, identity_resolved_title=None,
            process_probe={"alive": False})
        self.assertNotEqual(shelled["tier"], "structured_stream")

    def test_surface_ordering_s3_is_the_quorum_and_nothing_else(self) -> None:
        self.assertIs(lifecycle.SURFACE_FUNCTIONS["S3"], lifecycle.may_send_prompt)
        self.assertEqual(verdict(evidence(bound_signal=bound()))["verdict"], "ready")

    def test_surface_ordering_s4_treats_not_connected_as_busy(self) -> None:
        result = lifecycle.may_stop(structured_row=None, blocked_text="",
                                    known_ready_preview=False, live_title_idle=False,
                                    connected=False)
        self.assertFalse(result["may_stop"])
        self.assertEqual(result["reason"], "not_connected_is_busy")

    def test_merged_ordering_fails_at_least_one_surface(self) -> None:
        """A single merged precedence list cannot satisfy all four surfaces at once.

        Demonstrated rather than asserted: one input set on which S3 must refuse and S4 must
        permit.  Any merged ordering returns one answer, so it is wrong for one of them --
        which is exactly why the contract keeps them apart.
        """
        # A known-ready screen preview with no bound signal: S3 must NOT accept (readiness
        # rests on no text), while S4 may stop (nothing is running).
        s3 = verdict(evidence(bound_signal=None,
                              supplementary=({"tier": "screen_preview",
                                              "text": "> ", "live_observed": True,
                                              "at": ""},)))
        s4 = lifecycle.may_stop(structured_row=None, blocked_text="",
                                known_ready_preview=True, live_title_idle=False,
                                connected=True)
        self.assertNotEqual(s3["verdict"], "ready")
        self.assertTrue(s4["may_stop"])


# =====================================================================================
class ClosedVocabularyTests(unittest.TestCase):
    """V-5 / AC-37-07 / AC-37-13 / AC-37-14: every member round-trips; no default; no boolean."""

    def test_every_vocabulary_member_round_trips(self) -> None:
        for axis, members in OWNERSHIP_AXIS_VOCABULARIES.items():
            for member in members:
                with self.subTest(axis=axis, member=member):
                    axes = {"settlement": "not_settled",
                            "worker_resource": "unsupervised",
                            "process_liveness": "disputed",
                            "cleanup_authority": "unknown"}
                    axes[axis] = member
                    record = lifecycle.make_state_record(
                        state="RUNNING", evidence={}, axes=axes,
                        source_vocabulary={"axis": axis, "member": member}, at="t")
                    self.assertEqual(record["axes"][axis], member)
                    self.assertEqual(record["source_vocabulary"]["member"], member,
                                     "the source vocabulary must survive normalization")

    def test_interrupt_outcome_members_round_trip(self) -> None:
        """The recorded widening: V-5 enumerates ``interrupt_outcome`` too, not only ``status``."""
        from scripts.deterministic_workflow import standalone_interrupt as interrupt_mod
        self.assertEqual(len(lifecycle.INTERRUPT_OUTCOMES), 5)
        for member in lifecycle.INTERRUPT_OUTCOMES:
            with self.subTest(member=member):
                mapped = interrupt_mod.lifecycle_for(member)
                self.assertNotIn(
                    mapped["state"], ("COMPLETED", "FAILED"),
                    "COMPLETED/FAILED must be unreachable from any interrupt outcome")
        with self.assertRaises(ValueError):
            interrupt_mod.lifecycle_for("terminated_probably")

    def test_validator_refuses_out_of_set(self) -> None:
        with self.assertRaises(VocabularyError):
            validate_axes({"settlement": "probably", "worker_resource": "retain",
                           "process_liveness": "live", "cleanup_authority": "unknown"})
        with self.assertRaises(VocabularyError):
            # Three valid axes and a MISSING key is an invalid answer, not a partial one.
            validate_axes({"settlement": "settled", "worker_resource": "retain",
                           "process_liveness": "live"})
        # The shared closed-set validator raises the base type; `LifecycleError` derives
        # from it, so one `except VocabularyError` catches every closed-set refusal.
        with self.assertRaises(VocabularyError):
            lifecycle.validate_state_name("ALMOST_DONE")
        with self.assertRaises(VocabularyError):
            lifecycle.validate_event_name("probably_finished")
        self.assertTrue(issubclass(lifecycle.LifecycleError, VocabularyError))

    def test_no_member_may_be_reduced_to_a_boolean(self) -> None:
        for members in OWNERSHIP_AXIS_VOCABULARIES.values():
            self.assertGreater(
                len(members), 2,
                f"{members!r} has two or fewer members and could be read as a boolean")

    def test_source_vocabulary_is_required_by_normalize(self) -> None:
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.normalize(state="RUNNING", source_vocabulary={}, evidence={},
                                axes={"settlement": "not_settled",
                                      "worker_resource": "unsupervised",
                                      "process_liveness": "disputed",
                                      "cleanup_authority": "unknown"}, at="t")

    def test_host_scope_never_defaults_to_local(self) -> None:
        from scripts.deterministic_workflow.contracts import parse_host_scope
        for value in ("remote", "gui", "", None, "LOCAL", 1):
            with self.subTest(value=value):
                self.assertIsNone(
                    parse_host_scope(value),
                    "an unparsable host scope must be None, never defaulted to local -- "
                    "defaulting would let a handle minted for another host be signalled")
        self.assertEqual(parse_host_scope("local"), "local")


# =====================================================================================
class InvariantTests(unittest.TestCase):
    """The five invariants, and V-3's fail-closed directions."""

    def test_i1_requires_identity_bound_then_readiness_observed_in_order(self) -> None:
        ready_evidence = {"liveness": LIVE, "bound_signal": bound(), "tier":
                          "bound_structured"}
        good = lifecycle.check_transition(
            source="STARTING", target="READY", event="readiness_observed",
            log=("spawned", "identity_bound", "readiness_observed"),
            evidence=ready_evidence)
        self.assertTrue(good["allowed"], good["violations"])
        # readiness_observed ALONE never advances and never establishes identity.
        alone = lifecycle.check_transition(
            source="STARTING", target="READY", event="readiness_observed",
            log=("spawned", "readiness_observed"), evidence=ready_evidence)
        self.assertFalse(alone["allowed"])
        # And the reverse order is refused, not merely the missing event.
        reversed_log = lifecycle.check_transition(
            source="STARTING", target="READY", event="readiness_observed",
            log=("readiness_observed", "identity_bound"), evidence=ready_evidence)
        self.assertFalse(reversed_log["allowed"])

    def test_i2_delivery_needs_a_proof_not_the_absence_of_a_failure(self) -> None:
        proven = lifecycle.check_transition(
            source="READY", target="PROMPT_DELIVERED",
            event="delivery_proof_observed", evidence={"tier": "structured_stream"})
        self.assertTrue(proven["allowed"], proven["violations"])
        unobserved = lifecycle.check_transition(
            source="READY", target="PROMPT_DELIVERED", event="delivery_unobserved",
            evidence={"tier": "structured_stream"})
        self.assertFalse(unobserved["allowed"])
        self.assertEqual(
            lifecycle.resolve_unknown("delivery_verify_timeout")["state"], "TIMED_OUT")

    def test_i3_completed_and_failed_have_exactly_one_entry_edge_each(self) -> None:
        for target in ("COMPLETED", "FAILED"):
            with self.subTest(target=target):
                ok = lifecycle.check_transition(
                    source="RUNNING", target=target, event="settlement_confirmed",
                    evidence={"tier": "structured_stream"},
                    from_settlement_predicate=True)
                self.assertTrue(ok["allowed"], ok["violations"])
                # `settlement_accepted` alone does not reach them.
                accepted = lifecycle.check_transition(
                    source="RUNNING", target=target, event="settlement_accepted",
                    evidence={"tier": "structured_stream"},
                    from_settlement_predicate=True)
                self.assertFalse(accepted["allowed"])
                # Nor does `settlement_confirmed` from anywhere but the predicate.
                forged = lifecycle.check_transition(
                    source="RUNNING", target=target, event="settlement_confirmed",
                    evidence={"tier": "structured_stream"},
                    from_settlement_predicate=False)
                self.assertFalse(forged["allowed"])
                # And no other event on any surface may enter them.
                for event in lifecycle.EVENTS:
                    if event == "settlement_confirmed":
                        continue
                    self.assertFalse(
                        lifecycle.check_transition(
                            source="RUNNING", target=target, event=event,
                            evidence={"tier": "structured_stream"},
                            from_settlement_predicate=True)["allowed"],
                        f"{event} entered {target}")

    def test_readiness_only_never_completes(self) -> None:
        """V-3: a readiness verdict has no member that can express an outcome.

        Structural, not behavioural: S3 returns a ``ReadinessVerdict`` whose ``verdict`` is
        drawn from a three-member set that names no outcome, so its result cannot BE a
        completion however it is routed.
        """
        result = verdict(evidence(bound_signal=bound()))
        self.assertEqual(set(result) , {"verdict", "reason", "quorum"})
        for member in lifecycle.READINESS_VERDICTS:
            self.assertNotIn(member, ("COMPLETED", "FAILED", "succeeded", "failed"))
        self.assertNotIn("outcome", result)

    def test_i4_every_unreadable_authority_routes_to_lost_with_a_reason(self) -> None:
        for situation in ("required_evidence_missing", "liveness_unverifiable",
                          "capture_truncated", "exit_status_absent"):
            with self.subTest(situation=situation):
                resolved = lifecycle.resolve_unknown(situation)
                self.assertEqual(resolved["state"], "LOST")
                self.assertIn(resolved["lost_reason"], lifecycle.LOST_REASONS)
        with self.assertRaises(VocabularyError):
            lifecycle.make_state_record(
                state="LOST", evidence={}, axes={"settlement": "unknown",
                                                 "worker_resource": "unsupervised",
                                                 "process_liveness": "unverifiable",
                                                 "cleanup_authority": "unknown"},
                source_vocabulary={"x": 1}, at="t")   # no lost_reason

    def test_i5_a_supplementary_tier_advances_nothing(self) -> None:
        for tier in ("title", "screen_preview"):
            for target in ("READY", "RUNNING", "COMPLETED", "PROMPT_DELIVERED"):
                with self.subTest(tier=tier, target=target):
                    self.assertFalse(
                        lifecycle.check_transition(
                            source="STARTING", target=target,
                            event="readiness_observed", evidence={"tier": tier},
                            log=("identity_bound", "readiness_observed"))["allowed"])

    def test_i5_ready_refuses_evidence_lacking_r_a_or_r_b(self) -> None:
        log = ("spawned", "identity_bound", "readiness_observed")
        self.assertFalse(lifecycle.check_transition(
            source="STARTING", target="READY", event="readiness_observed", log=log,
            evidence={"liveness": None, "bound_signal": bound(),
                      "tier": "bound_structured"})["allowed"])
        self.assertFalse(lifecycle.check_transition(
            source="STARTING", target="READY", event="readiness_observed", log=log,
            evidence={"liveness": LIVE, "bound_signal": None,
                      "tier": "bound_structured"})["allowed"])

    def test_absent_exit_status_is_lost_not_exited_zero(self) -> None:
        resolved = lifecycle.resolve_unknown("exit_status_absent")
        self.assertEqual(resolved["state"], "LOST")
        self.assertIsNone(resolved["exit_status"])
        self.assertEqual(lifecycle.UNVERIFIED_PROCESS_EXIT_CODE, -1)
        # An UNMAPPED code is LOST too -- G-7 is UNKNOWN and an empty table is valid.
        self.assertEqual(lifecycle.map_exit_code(0, {})["state"], "LOST")
        self.assertEqual(lifecycle.map_exit_code(0, {})["lost_reason"],
                         "exit_code_unmapped")
        self.assertEqual(lifecycle.map_exit_code(None, {"0": "x"})["lost_reason"],
                         "cause_unreported")
        self.assertEqual(lifecycle.map_exit_code(0, {0: "COMPLETED"})["state"], "COMPLETED")

    def test_not_observed_writes_nothing_twice(self) -> None:
        resolved = lifecycle.resolve_unknown("delivery_verify_timeout")
        self.assertFalse(resolved["second_write"])
        self.assertEqual(resolved["delivery"], "not_observed")

    def test_reread_failing_any_check_is_named_refusal(self) -> None:
        resolved = lifecycle.resolve_unknown("settlement_reread_unconfirmed")
        self.assertEqual(resolved["refusal"], "unconfirmed_is_not_settled")
        self.assertIsNone(resolved["state"])
        self.assertEqual(resolved["route"], "recovery")

    def test_resolve_unknown_refuses_an_undeclared_situation(self) -> None:
        """There is no ``else`` that guesses.  An undeclared unknown raises."""
        with self.assertRaises(lifecycle.LifecycleError):
            lifecycle.resolve_unknown("something_nobody_declared")

    def test_the_state_and_event_sets_are_closed_and_exactly_sized(self) -> None:
        self.assertEqual(len(lifecycle.STATES), 10)
        self.assertEqual(len(lifecycle.EVENTS), 16)
        self.assertEqual(len(lifecycle.REFUSALS), 7)


# =====================================================================================
class PerDriverMatrixLivesElsewhereTests(unittest.TestCase):
    """R1's per-driver matrix moved to `scripts/test_os37_per_driver_matrix.py`.

    What used to be here was a twelve-case table whose helper RETURNED the answer --
    ``delivered_confirmed``, ``INTERRUPTED`` and ``TIMED_OUT`` were literals, and
    completion/failure/lost only called ``completion_evidence`` with a caller-chosen exit
    code and empty output.  Asserting that those strings belong to a closed vocabulary is
    true of the literals and says nothing about either driver, which is finding **F-003**.

    It was replaced, not relaxed: the new module drives each of the six outcomes out of
    production code for BOTH drivers, twice -- deterministically over real pty writes, real
    fenced exit sentinels and the real interrupt ladder, and live over twelve real local
    processes through :class:`StandaloneSession`.

    This class stays behind so the move is checkable rather than a claim in a report: it
    asserts the replacement exists, covers both drivers and all six outcomes, and that the
    tautological helper is really gone from this file.
    """

    def test_the_replacement_module_exists_and_covers_both_drivers(self) -> None:
        from scripts import test_os37_per_driver_matrix as matrix
        self.assertEqual(matrix.DRIVER_NAMES, ("claude", "codex"))
        self.assertEqual(set(matrix.OUTCOMES),
                         {"delivery", "completion", "failure", "interruption",
                          "timeout", "lost"})
        for klass in (matrix.PerDriverEvidenceBoundaryTests,
                      matrix.LivePerDriverOutcomeMatrixTests):
            cases = [attr for attr in dir(klass) if attr.startswith("test_")]
            self.assertTrue(cases, f"{klass.__name__} has no cases")

    def test_the_tautological_helper_is_gone_from_this_file(self) -> None:
        """It returned the value it was supposed to be measuring.  It must not come back."""
        source = pathlib.Path(__file__).read_text()
        # Assembled at run time so this test's own text cannot satisfy the search it makes.
        needle = "def " + "_case_" + "outcome("
        self.assertNotIn(
            needle, source,
            "the helper returned literal outcome strings without invoking any driver or "
            "runtime path; reinstating it would reinstate F-003")


# =====================================================================================
class UncappedSettleGateTests(unittest.TestCase):
    """C11: the settle delay before Enter is UNCAPPED, and this asserts it LITERALLY.

    The gate exists so a long paste frame is fully ingested before the newline; truncating
    it is exactly the split-frame failure bracketed paste is meant to prevent, and a frame
    that loses its beginning loses the instruction rather than a character.

    Asserted two ways, because neither alone is enough.  A substring search for ``min(``
    would match the function's own docstring saying there is none -- so the structural half
    walks the AST for a real CALL.  And a structural check alone would pass an implementation
    that capped by arithmetic rather than by ``min`` -- so the behavioural half shows the
    value growing without bound.
    """

    def test_no_capping_call_exists_in_the_settle_computation(self) -> None:
        import ast
        import inspect
        import textwrap

        from scripts.deterministic_workflow import standalone_drivers as drivers
        tree = ast.parse(textwrap.dedent(inspect.getsource(drivers.settle_ms)))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else getattr(node.func, "id", ""))
                self.assertNotIn(
                    name, ("min", "max", "clamp"),
                    f"settle_ms calls {name}(); the settle gate must be uncapped, and a "
                    "cap here silently truncates the ingest window for a long frame")

    def test_the_settle_value_grows_without_bound(self) -> None:
        """The behavioural half: linear in frame size, with no ceiling anywhere."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        previous = 0
        for size in (1_000, 100_000, 10_000_000, 200_000_000):
            frame = b"x" * size
            value = drivers.settle_ms(frame, measured_ingest_rate=1.0,
                                      settle_floor_ms=120)
            with self.subTest(size=size):
                self.assertGreater(
                    value, previous,
                    f"settle_ms stopped growing at {size} bytes; it is capped")
                self.assertGreaterEqual(value, size,
                                        "the value is below the frame size at rate 1.0")
                previous = value

    def test_the_floor_is_a_floor_and_the_rate_is_re_measured_not_transcribed(self) -> None:
        """``settle_floor_ms`` only ever ADDS, and the rate is a required argument.

        A required argument rather than a module constant: C11 says Orca's measured constants
        "must be re-measured, not transcribed as truth", and a default here would be a
        transcription that every call site silently inherited.
        """
        import inspect

        from scripts.deterministic_workflow import standalone_drivers as drivers
        parameters = inspect.signature(drivers.settle_ms).parameters
        self.assertIs(parameters["measured_ingest_rate"].default,
                      inspect.Parameter.empty,
                      "measured_ingest_rate has a default; it must be measured per host")
        self.assertIs(parameters["settle_floor_ms"].default, inspect.Parameter.empty)
        empty = drivers.settle_ms(b"", measured_ingest_rate=1.0, settle_floor_ms=120)
        self.assertEqual(empty, 120, "the floor must apply even to an empty frame")
        with self.assertRaises(ValueError):
            # A zero rate would make the gate INFINITE rather than uncapped -- a different
            # defect, and refused by name.
            drivers.settle_ms(b"x", measured_ingest_rate=0.0, settle_floor_ms=1)

    def test_the_prompt_frame_is_written_as_one_buffer_retried_only_on_eintr(self) -> None:
        """C10: a split frame can lose its beginning, so the retry re-sends the SAME buffer."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        writes: list[bytes] = []
        calls = {"n": 0}

        def flaky(fd, payload):
            calls["n"] += 1
            if calls["n"] == 1:
                raise InterruptedError("EINTR")
            if calls["n"] == 2:
                writes.append(payload[:5])       # a short write
                return 5
            writes.append(payload)
            return len(payload)

        frame = drivers.frame_prompt("hello world")
        written = drivers.write_frame(0, frame, writer=flaky)
        self.assertEqual(written, len(frame))
        self.assertEqual(b"".join(writes), frame,
                         "the retried writes do not reconstruct exactly one frame")
        self.assertEqual(writes[0], frame[:5],
                         "the short write did not start at the frame's beginning")
        self.assertEqual(frame.count(b"\x1b[200~"), 1,
                         "the bracket was re-emitted; a partial write must be resumed, "
                         "never re-framed")


# =========================================================================================
# DESIGN §D13.2a -- THE DELIVERY-SELECTOR CASES (iteration 4, REVIEW_DESIGN_iteration3 F-001)
# =========================================================================================
STREAMS = pathlib.Path(__file__).resolve().parent / "fixtures" / "os37" / "streams"


def _stream(name: str) -> str:
    """The RECORDED bytes of a measurement, read verbatim.

    Every input in this section is captured output from a real run against the CLIs
    installed on this host -- never a hand-written fixture -- so a reviewer can diff the
    test input against `scripts/fixtures/os37/streams/README.md` and the DESIGN §D4.0
    measurement it names.
    """
    return (STREAMS / name).read_text(encoding="utf-8", errors="replace")


def _stream_session_id(name: str) -> str:
    return (STREAMS / name).read_text(encoding="utf-8").strip()


def _claude_driver(**overrides):
    from scripts.deterministic_workflow import standalone_drivers as drivers
    from scripts.deterministic_workflow.standalone_profile import (
        CompletionSelector, DeliveryProofSelector, ReadinessSelector, StandaloneProfile)
    fields = dict(
        driver="claude", binary="claude", supported_range=((1, 0, 0), (99, 0, 0)),
        delivery_mode="launch_with_prompt", identity_binding="minted_echo",
        identity_flag="--session-id",
        readiness_records=(ReadinessSelector(channel="structured", record_type="system",
                                             session_field="session_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="assistant"),),
        completion_records=(CompletionSelector(channel="structured", record_type="result",
                                               error_field="is_error",
                                               success_field="terminal_reason",
                                               success_values=("completed",)),),
        auth_markers=(("error", "authentication_failed"),
                      ("is_api_error_message", "True"),
                      ("terminal_reason", "api_error")))
    fields.update(overrides)
    return drivers.driver_for(StandaloneProfile(**fields))


def _codex_driver(**overrides):
    from scripts.deterministic_workflow import standalone_drivers as drivers
    from scripts.deterministic_workflow.standalone_profile import (
        CompletionSelector, DeliveryProofSelector, ReadinessSelector, StandaloneProfile)
    fields = dict(
        driver="codex", binary="codex", supported_range=((0, 1, 0), (99, 0, 0)),
        delivery_mode="launch_with_prompt", identity_binding="adopted",
        readiness_records=(ReadinessSelector(channel="structured",
                                             record_type="thread.started",
                                             session_field="thread_id"),),
        delivery_proofs=(DeliveryProofSelector(channel="structured",
                                               record_type="item.completed",
                                               item_type="agent_message"),
                         DeliveryProofSelector(channel="structured",
                                               record_type="turn.completed"),),
        completion_records=(CompletionSelector(channel="structured",
                                               record_type="turn.completed"),),
        auth_markers=(("type", "turn.failed"),))
    fields.update(overrides)
    return drivers.driver_for(StandaloneProfile(**fields))


def _intent(session_id: str, payload: str = "Reply with exactly: OK") -> dict:
    from scripts.deterministic_workflow import standalone_drivers as drivers
    return dict(drivers.make_delivery_intent(
        intent_id="intent-d13-2a", dispatch_id="dispatch-1", task_id="task-1",
        session_id=session_id, payload=payload, argv_digest="argv-digest",
        attempt_incarnation="inc-1", delivery_mode="launch_with_prompt"))


class DeliverySelectorTests(unittest.TestCase):
    """§D13.2a.  Delivery is a SEPARATE question from completion, and it was answered wrongly.

    Iteration 3 declared *any* ``{"type":"assistant", ...}`` carrying the minted session id
    to be class-B delivery evidence.  M-15 measures that a **pure login failure** emits
    exactly that shape.  An identity-bound record proves process and session PROVENANCE; it
    does not prove that the dispatched prompt EXECUTED.  Under the old selector a login
    failure would have constructed a `DeliveryProof` and entered `PROMPT_DELIVERED` -- the
    same family of defect this ticket exists to prevent, one state earlier.

    The negative case and its positive twin are the actual specification.  Without the twin,
    a selector that refused EVERYTHING would pass the negative case.
    """

    def test_claude_synthetic_auth_error_assistant_is_never_delivery(self) -> None:
        """THE negative test F-001 requires, on the exact recorded M-15 bytes."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _claude_driver()
        text = _stream("m15_claude_auth_failure.stream")
        minted = _stream_session_id("m15_claude_auth_failure.session_id")
        intent = _intent(minted)
        records = driver.structured_records(text)
        assistant = [r for r in records if r.get("type") == "assistant"]
        self.assertEqual(len(assistant), 1,
                         "the recorded M-15 stream must carry exactly one assistant record")
        record = assistant[0]

        # (6) FIRST, positively: R-B DID close on the `system/init` record.  Without this
        # the test would prove nothing at all -- it would be indistinguishable from a stream
        # the driver could not read.  This is *admission without delivery*.
        bound_signal = driver.bound_readiness_signal(text, minted_session_id=minted)
        self.assertIsNotNone(bound_signal, "R-B did not close on the recorded init record")
        self.assertEqual(bound_signal["record_type"], "system")
        self.assertEqual(bound_signal["session_id"], minted)
        self.assertEqual(record.get("session_id"), minted,
                         "the M-15 assistant record must be IDENTITY-BOUND -- that is "
                         "precisely what makes it dangerous")

        # (1) the selector returns False...
        self.assertFalse(
            drivers.claude_delivery_selector(record, intent,
                                             declared_types=("assistant",)),
            "the measured synthetic authentication-failure record was accepted as delivery")

        # ...and it fails on C-4, C-5, C-6 and C-7 INDEPENDENTLY.  Each sub-case repairs
        # every conjunct except the one under test, so a selector that happened to reject
        # for the wrong reason is caught.
        base = dict(record)
        with self.subTest("C-4 error present"):
            probe = _repair_m15(base)
            probe["error"] = "authentication_failed"
            self.assertFalse(drivers.claude_delivery_selector(
                probe, intent, declared_types=("assistant",)))
        with self.subTest("C-5 is_api_error_message true"):
            probe = _repair_m15(base)
            probe["is_api_error_message"] = True
            self.assertFalse(drivers.claude_delivery_selector(
                probe, intent, declared_types=("assistant",)))
        with self.subTest("C-6 placeholder model"):
            probe = _repair_m15(base)
            probe["message"] = {**probe["message"], "model": "<synthetic>"}
            self.assertFalse(drivers.claude_delivery_selector(
                probe, intent, declared_types=("assistant",)))
        with self.subTest("C-7 no API round-trip marker"):
            probe = _repair_m15(base)
            probe.pop("request_id", None)
            probe["message"] = {**probe["message"],
                                "id": "8d43a109-8f26-480c-be78-142541823987",
                                "usage": {"input_tokens": 0, "output_tokens": 0,
                                          "cache_creation_input_tokens": 0,
                                          "cache_read_input_tokens": 0}}
            self.assertFalse(drivers.claude_delivery_selector(
                probe, intent, declared_types=("assistant",)))
        # ...and the repair itself is load-bearing: with every conjunct fixed it PASSES,
        # so the four sub-cases above each isolate exactly one refusal.
        self.assertTrue(drivers.claude_delivery_selector(
            _repair_m15(base), intent, declared_types=("assistant",)),
            "the repaired record does not pass, so the sub-cases above are not isolating "
            "one conjunct each")

        # (2) NO DeliveryProof is constructible from the whole recorded stream.
        self.assertIsNone(
            driver.delivery_evidence(text, intent=intent),
            "a DeliveryProof was constructed from a pure login failure")

        # (3) and (4): no `delivery_proof_observed` event exists, so I-2's
        # STARTING -> PROMPT_DELIVERED edge is not takeable.
        log = ["spawned", "identity_bound", "readiness_observed"]
        self.assertNotIn("delivery_proof_observed", log)
        check = lifecycle.check_transition(
            source="STARTING", target="PROMPT_DELIVERED", event="turn_start_observed",
            log=log, evidence={"tier": "raw"})
        self.assertFalse(check["allowed"])
        self.assertTrue(any("I-2" in v for v in check["violations"]))

        # (5) READY is never reported: `launch_with_prompt` has no `deliver` at all.
        self.assertFalse(hasattr(driver, "deliver"))
        with self.assertRaises(drivers.DeliveryModeMismatch):
            driver.may_send_prompt({}, minted_session_id=minted)

    def test_genuine_assistant_record_is_delivery(self) -> None:
        """The POSITIVE twin, on the exact recorded M-14 bytes.

        Without this case a selector that refused EVERYTHING would pass the negative test.
        The pair is the actual specification.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _claude_driver()
        text = _stream("m14_claude_genuine_turn.stream")
        minted = _stream_session_id("m14_claude_genuine_turn.session_id")
        intent = _intent(minted)
        record = [r for r in driver.structured_records(text)
                  if r.get("type") == "assistant"][0]
        self.assertNotIn("error", record)
        self.assertTrue(record.get("request_id", "").startswith("req_"))
        self.assertTrue(record["message"]["id"].startswith("msg_"))
        self.assertNotEqual(record["message"]["model"], "<synthetic>")
        self.assertTrue(drivers.claude_delivery_selector(
            record, intent, declared_types=("assistant",)))
        proof = driver.delivery_evidence(text, intent=intent)
        self.assertIsNotNone(proof, "no DeliveryProof from a genuine measured turn")
        self.assertEqual(proof["proof_class"], "B")
        self.assertEqual(proof["record_type"], "assistant")
        self.assertEqual(proof["prompt_digest"], intent["prompt_digest"])
        # ...and PROMPT_DELIVERED is reachable, which is the other half of "not vacuous".
        check = lifecycle.check_transition(
            source="STARTING", target="PROMPT_DELIVERED", event="delivery_proof_observed",
            log=["spawned", "identity_bound", "readiness_observed"],
            evidence={"tier": "structured_stream"})
        self.assertTrue(check["allowed"], check["violations"])

    def test_an_absent_identity_never_satisfies_the_equality_conjunct(self) -> None:
        """`None == None` is true, and an equality two absences can satisfy is no binding.

        C-2 compares the record's `session_id` against the intent's. If both are missing the
        naive comparison SUCCEEDS, so a record carrying no identity at all would clear the
        conjunct that exists to bind it. Refused before the comparison, on both sides.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        text = _stream("m14_claude_genuine_turn.stream")
        minted = _stream_session_id("m14_claude_genuine_turn.session_id")
        driver = _claude_driver()
        genuine = [r for r in driver.structured_records(text)
                   if r.get("type") == "assistant"][0]

        with self.subTest("both absent"):
            stripped = {k: v for k, v in genuine.items() if k != "session_id"}
            self.assertFalse(
                drivers.claude_delivery_selector(stripped, {"session_id": ""},
                                                 declared_types=("assistant",)),
                "a record with NO identity satisfied the identity conjunct against an "
                "intent with NO identity")
        with self.subTest("intent absent, record present"):
            self.assertFalse(drivers.claude_delivery_selector(
                genuine, {"session_id": None}, declared_types=("assistant",)))
        with self.subTest("record absent, intent present"):
            stripped = {k: v for k, v in genuine.items() if k != "session_id"}
            self.assertFalse(drivers.claude_delivery_selector(
                stripped, _intent(minted), declared_types=("assistant",)))
        with self.subTest("the replayed-acknowledgement member, same rule"):
            self.assertFalse(drivers.claude_replayed_user_selector(
                {"type": "user", "message": {"content": [{"type": "text", "text": "x"}]}},
                {"session_id": "", "prompt_digest": drivers.prompt_digest("x")},
                composed_argv=("claude", "--input-format", "stream-json",
                               "--replay-user-messages")))
        # ...and the positive still passes, so the guard did not simply reject everything.
        self.assertTrue(drivers.claude_delivery_selector(
            genuine, _intent(minted), declared_types=("assistant",)))

    def test_delivery_selector_conjuncts_are_each_load_bearing(self) -> None:
        """ANTI-ROT.  One conjunct broken at a time over the measured M-14 record.

        F-001 arose because the selector was a `type`-only lookup.  This case fails the
        moment a refactor quietly reduces the conjunction back to one, which is the only
        durable protection against the same defect returning.
        """
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _claude_driver()
        text = _stream("m14_claude_genuine_turn.stream")
        minted = _stream_session_id("m14_claude_genuine_turn.session_id")
        intent = _intent(minted)
        genuine = [r for r in driver.structured_records(text)
                   if r.get("type") == "assistant"][0]
        mutations = {
            "C-1 undeclared type": lambda r: {**r, "type": "not_assistant"},
            "C-2 foreign session": lambda r: {**r, "session_id": "someone-else"},
            "C-4 error injected": lambda r: {**r, "error": "authentication_failed"},
            "C-5 api error marker": lambda r: {**r, "is_api_error_message": True},
            "C-6 placeholder model": lambda r: {
                **r, "message": {**r["message"], "model": "<synthetic>"}},
            "C-7 every round-trip marker stripped together": lambda r: {
                **{k: v for k, v in r.items() if k != "request_id"},
                "message": {**r["message"], "id": "bare-uuid4",
                            "usage": {k: 0 for k in r["message"]["usage"]
                                      if isinstance(r["message"]["usage"][k], int)}}},
        }
        self.assertTrue(drivers.claude_delivery_selector(
            genuine, intent, declared_types=("assistant",)),
            "the unmutated measured record must pass, or every mutation below is vacuous")
        for label, mutate in mutations.items():
            with self.subTest(label):
                self.assertFalse(
                    drivers.claude_delivery_selector(mutate(dict(genuine)), intent,
                                                     declared_types=("assistant",)),
                    f"{label} did not break the selector; the conjunction has been "
                    "weakened and F-001's defect is reachable again")

    def test_codex_turn_started_alone_is_never_delivery(self) -> None:
        """The Codex analogue, on the exact recorded M-8 failure-leg bytes."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _codex_driver()
        text = _stream("m8_codex_auth_failure.stream")
        records = driver.structured_records(text)
        types = [r.get("type") for r in records]
        # The measured order: `turn.started` precedes auth AND precedes any model work.
        self.assertEqual(types[:3], ["thread.started", "turn.started", "error"])
        self.assertIn("turn.failed", types)

        # (1) `turn.started` is not a declared delivery proof at all.
        self.assertNotIn("turn.started",
                         tuple(s.record_type for s in driver.profile.delivery_proofs))
        thread = [r for r in records if r.get("type") == "thread.started"][0]
        intent = _intent(str(thread["thread_id"]))

        # (2) the selector refuses `turn.started` AND the error-shaped `item.completed`.
        for record in records:
            if record.get("type") == "turn.started":
                self.assertFalse(drivers.codex_delivery_selector(
                    record, intent,
                    declared_types=("item.completed", "turn.completed")))
            if record.get("type") == "item.completed":
                self.assertEqual(record["item"]["type"], "error")
                self.assertFalse(drivers.codex_delivery_selector(
                    record, intent,
                    declared_types=("item.completed", "turn.completed")),
                    "K-1 accepted an item.completed whose item.type is `error`")

        # (3) no DeliveryProof, so PROMPT_DELIVERED is unreachable.
        self.assertIsNone(driver.delivery_evidence(text, intent=intent))

        # (4) and the run still settles as a NAMED failure rather than as a mode mismatch
        # or a timeout: a typed auth marker is present, which is D4.3c's precedence rule.
        marker = driver.auth_marker_present(text)
        self.assertIsNotNone(marker, "the measured 401 leg carries no typed auth marker")
        self.assertEqual(marker["record_type"], "turn.failed")
        # The unparsable interleaved lines are RAW CAPTURE, never evidence, and never
        # silently dropped: they are in the stream and are not among the parsed records.
        self.assertIn("ERROR codex_api", text)

    def test_codex_agent_message_item_is_delivery(self) -> None:
        """Codex's positive twin, on the exact recorded M-8 success-leg bytes."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _codex_driver()
        text = _stream("m8_codex_success.stream")
        records = driver.structured_records(text)
        self.assertEqual([r.get("type") for r in records],
                         ["thread.started", "turn.started", "item.completed",
                          "turn.completed"])
        thread = records[0]
        intent = _intent(str(thread["thread_id"]))
        item = records[2]
        self.assertEqual(item["item"]["type"], "agent_message")     # K-1
        self.assertTrue(drivers.codex_delivery_selector(
            item, intent, declared_types=("item.completed", "turn.completed")))
        self.assertTrue(drivers.codex_delivery_selector(          # K-3, sufficient alone
            records[3], intent, declared_types=("item.completed", "turn.completed")))
        proof = driver.delivery_evidence(text, intent=intent)
        self.assertIsNotNone(proof)
        self.assertEqual(proof["proof_class"], "B")
        self.assertIsNone(driver.auth_marker_present(text),
                          "the success leg must carry no auth marker")

    def test_turn_start_without_delivery_proof_never_reaches_running(self) -> None:
        """The demoted record cannot re-enter the lifecycle through a different door."""
        for label, driver, name in (("claude", _claude_driver(),
                                     "m15_claude_auth_failure.stream"),
                                    ("codex", _codex_driver(),
                                     "m8_codex_auth_failure.stream")):
            with self.subTest(label):
                text = _stream(name)
                turn_start = driver.turn_start_evidence(text)
                self.assertIsNotNone(turn_start,
                                     "the measured stream carries a turn-start record")
                self.assertEqual(turn_start["tier"], "raw",
                                 "a turn-start record emitted on the auth-failure path "
                                 "must be carried as a RAW lifecycle observation")
                blocked = lifecycle.check_transition(
                    source="STARTING", target="RUNNING", event="turn_start_observed",
                    log=["spawned", "identity_bound", "readiness_observed"],
                    evidence={"tier": "raw"})
                self.assertFalse(blocked["allowed"],
                                 "turn_start_observed entered RUNNING with no delivery "
                                 "proof in the log")
                allowed = lifecycle.check_transition(
                    source="PROMPT_DELIVERED", target="RUNNING",
                    event="turn_start_observed",
                    log=["spawned", "identity_bound", "readiness_observed",
                         "delivery_proof_observed"],
                    evidence={"tier": "raw"})
                self.assertTrue(allowed["allowed"], allowed["violations"])

    def test_auth_failure_result_record_is_never_completed(self) -> None:
        """The EXISTING completion gate, RETAINED, on the same recorded stream.

        It is not replaced by, and does not replace, the delivery case above.  The two
        answer different questions -- *was the prompt delivered?* and *did the work
        complete?* -- and F-001 was a defect in the first while the second was already
        correct.  The measured trap is that ``subtype`` reads as ``success`` on a login
        failure, and `is_error` and the exit status both refuse it.
        """
        driver = _claude_driver()
        text = _stream("m15_claude_auth_failure.stream")
        record = driver.completion_record(text)
        self.assertIsNotNone(record)
        self.assertEqual(record["subtype"], "success",
                         "the measured trap: `subtype` reads as success on a LOGIN FAILURE")
        self.assertTrue(record["is_error"])
        self.assertEqual(record["terminal_reason"], "api_error")
        evidence_row = driver.completion_evidence(text, exit_status=1, exit_proven=True)
        self.assertEqual(evidence_row["exit_status"], 1)
        settled = lifecycle.map_exit_code(1, {})
        self.assertNotEqual(settled["state"], "COMPLETED")
        # ...and the SUCCESS twin, so this gate is not vacuous either.
        ok_text = _stream("m14_claude_genuine_turn.stream")
        ok = driver.completion_record(ok_text)
        self.assertFalse(ok["is_error"])
        self.assertEqual(ok["terminal_reason"], "completed")

    def test_both_auth_failure_gates_exist(self) -> None:
        """META.  Fails if EITHER auth-failure case is deleted or renamed.

        The delivery gate and the completion gate answer different questions, and the
        temptation in a later refactor is to notice they replay the same stream and keep
        only one.  Keeping only the completion gate is exactly the state the code was in
        when F-001 was found.
        """
        names = {name for name in dir(DeliverySelectorTests) if name.startswith("test_")}
        for required in ("test_claude_synthetic_auth_error_assistant_is_never_delivery",
                         "test_auth_failure_result_record_is_never_completed",
                         "test_genuine_assistant_record_is_delivery",
                         "test_codex_turn_started_alone_is_never_delivery",
                         "test_codex_agent_message_item_is_delivery"):
            self.assertIn(required, names,
                          f"{required} is missing; the delivery gate and the completion "
                          "gate are separate requirements and neither substitutes for the "
                          "other")

    def test_the_recorded_streams_are_real_captures_not_handwritten(self) -> None:
        """Every §D13.2a input is measured bytes, and the fixtures say which measurement.

        A hand-written fixture proves what its author believed the CLI does.  These prove
        what it did.
        """
        for name in ("m14_claude_genuine_turn.stream", "m15_claude_auth_failure.stream",
                     "m8_codex_auth_failure.stream", "m8_codex_success.stream"):
            with self.subTest(name):
                path = STREAMS / name
                self.assertTrue(path.exists(), f"{name} is missing")
                self.assertGreater(path.stat().st_size, 200,
                                   f"{name} is too small to be a real transcript")
        readme = (STREAMS / "README.md").read_text()
        for measurement in ("M-14", "M-15", "M-8"):
            self.assertIn(measurement, readme)


class SpawnIsNeverDeliveryByTypeTests(unittest.TestCase):
    """DESIGN §D4.3b / USER DIRECTIVE D-D.2, realised at the TYPE level.

    "Spawn success alone is never accepted as delivery" is not a rule a later refactor can
    forget, because it is a type that cannot say it: `SpawnOutcome` has no field that can
    express delivery, `DeliveryProof` is constructed only by `make_delivery_proof`, and
    there is no function anywhere that converts one into the other.
    """

    def test_a_spawn_outcome_has_no_field_that_can_express_delivery(self) -> None:
        import typing as _typing

        from scripts.deterministic_workflow import standalone_drivers as drivers
        spawn_fields = set(_typing.get_type_hints(drivers.SpawnOutcome))
        proof_fields = set(_typing.get_type_hints(drivers.DeliveryProof))
        self.assertEqual(
            spawn_fields & {"proof_class", "prompt_digest", "record_type"}, set(),
            "SpawnOutcome gained a delivery-shaped field; the separation D-D.2 requires is "
            "carried by the TYPES and not by a convention")
        self.assertIn("proof_class", proof_fields)
        self.assertIn("prompt_digest", proof_fields)

    def test_no_function_converts_a_spawn_outcome_into_a_delivery_proof(self) -> None:
        """Checked over the AST: no callable takes one and returns the other."""
        import ast as _ast
        source = (pathlib.Path(__file__).resolve().parent / "deterministic_workflow"
                  / "standalone_drivers.py").read_text()
        tree = _ast.parse(source)
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.FunctionDef):
                continue
            returns = _ast.unparse(node.returns) if node.returns else ""
            if "DeliveryProof" not in returns:
                continue
            args = " ".join(_ast.unparse(a.annotation) if a.annotation else ""
                            for a in node.args.args + node.args.kwonlyargs)
            self.assertNotIn(
                "SpawnOutcome", args,
                f"{node.name} converts a SpawnOutcome into a DeliveryProof; a successful "
                "execve is not evidence that the prompt executed")

    def test_class_c_evidence_cannot_construct_a_delivery_proof(self) -> None:
        """Class C -- an unambiguous process outcome -- settles a run; it never delivers one."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        with self.assertRaises(ValueError) as caught:
            drivers.make_delivery_proof(driver="claude", proof_class="C", record={},
                                        intent={})
        self.assertIn("class C", str(caught.exception))


class ReplayedUserAcknowledgementTests(unittest.TestCase):
    """The SECOND Claude class-B member, scoped honestly (§D4.3c, D4.0 M-6).

    It is INERT for the shipping profile, which passes the payload positionally and composes
    neither flag it depends on. It is retained because §D4.2b's conformance check must be
    able to refuse a profile that DECLARES it without composing them — and an inert member
    that nothing exercises is an inert member that rots, so it is tested directly.
    """

    def _record(self, text: str, session_id: str) -> dict:
        return {"type": "user", "session_id": session_id,
                "message": {"content": [{"type": "text", "text": text}]}}

    def test_it_requires_the_flags_the_profile_must_actually_compose(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        payload = "do the thing"
        intent = _intent("session-1", payload)
        record = self._record(payload, "session-1")
        self.assertFalse(
            drivers.claude_replayed_user_selector(record, intent, composed_argv=()),
            "a replayed acknowledgement was admitted although the argv composes neither "
            "--input-format nor --replay-user-messages, so the CLI could not have replayed "
            "anything")
        self.assertTrue(drivers.claude_replayed_user_selector(
            record, intent,
            composed_argv=("claude", "--input-format", "stream-json",
                           "--replay-user-messages")))

    def test_it_is_prompt_bound_not_merely_process_bound(self) -> None:
        """The digest must match: a replay of SOMETHING ELSE proves nothing about THIS prompt."""
        from scripts.deterministic_workflow import standalone_drivers as drivers
        intent = _intent("session-1", "do the thing")
        argv = ("claude", "--input-format", "stream-json", "--replay-user-messages")
        self.assertFalse(
            drivers.claude_replayed_user_selector(
                self._record("a completely different prompt", "session-1"), intent,
                composed_argv=argv),
            "a replayed acknowledgement whose content is not this dispatch's prompt was "
            "accepted; the member is prompt-bound by digest, not process-bound")
        self.assertFalse(
            drivers.claude_replayed_user_selector(
                self._record("do the thing", "someone-else"), intent, composed_argv=argv),
            "a replayed acknowledgement carrying a foreign session id was accepted")

    def test_the_shipping_profile_leaves_it_inert(self) -> None:
        from scripts.deterministic_workflow import standalone_drivers as drivers
        driver = _claude_driver()
        argv = driver.launch_argv(session_id="session-1", prompt="do the thing")
        self.assertNotIn("--replay-user-messages", argv)
        self.assertNotIn("--input-format", argv,
                         "the shipping profile composes the streaming-input flags, so the "
                         "replayed-acknowledgement member is no longer inert and its "
                         "scoping needs revisiting")


def _repair_m15(record: dict) -> dict:
    """The M-15 record with EVERY conjunct repaired, so one can be broken again in isolation.

    Used by the four independence sub-cases: each re-breaks exactly one conjunct over an
    otherwise-passing record, so a selector that refused for the wrong reason is caught.
    """
    repaired = {k: v for k, v in record.items()
                if k not in ("error", "is_api_error_message")}
    repaired["request_id"] = "req_011CetxmdEooYh9MaoWDBfyW"
    message = dict(repaired["message"])
    message["model"] = "claude-opus-5"
    message["id"] = "msg_011Cetxmdz"
    message["usage"] = {"input_tokens": 2, "output_tokens": 1,
                        "cache_creation_input_tokens": 1978}
    repaired["message"] = message
    return repaired


if __name__ == "__main__":
    unittest.main()
