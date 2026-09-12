"""OS-37 correction R4 -- the standalone PAUSE/DISPOSE path, at the composition root.

The Final Adversarial Review's R4 finding was that the pause, recovery and refused-interrupt
scenarios the verification contract requires *did not traverse the real production path*.
That was true, and the reason was not a defect in the standalone runtime: it was two missing
WIRINGS at the composition root, and each of them alone was enough to make the graph's own
PAUSE node unreachable from `run_workflow.py`.

**Wiring 1 -- the approval authority.**  `routing.pause_admissible` requires BOTH
`human_approval` and `lifecycle_settlement`.  `StandaloneAdapter.capabilities` has always
declared `human_approval` conditionally, on `self.approval_port is not None` -- literally the
`OrcaAdapter` pattern -- but `launcher.build_standalone_adapter` never passed one, so the
condition was never met and a decision block terminated the run as BLOCKED.

**Wiring 2 -- the declared decision block.**  `decision_state` is written by NO graph node;
`state.SET_DECISION` is its only writer, and nothing on the launch path called it.  A run
launched from this CLI therefore always carried `CLEAR`, so even with an approval port the
pause route could not be entered.  This is not standalone-specific -- it was true of every
adapter -- and it is why wiring 1 alone would still have proved nothing.

Both are now wired, and both are wired **conditionally**:

* `--approval-authority` defaults to `none`, so a run that does not name an authority
  composes exactly the adapter it composed before, declares no `human_approval`, and still
  routes a decision block to BLOCK.  The capability is never granted globally.
* `decision_state` is an OPTIONAL launch-specification field applied through the engine's own
  `SET_DECISION` command, so an unknown member is refused by `state.py`'s rule.

Neither buys a pause on its own, and this module asserts that too: the PAUSE node still
reconstructs the dispatch set durably and still refuses `PAUSE_NOT_ADMISSIBLE` unless the
configured authority can produce blocked sources that AUTHENTICATE against this run's real
OS-29 decision ledger.  A declared block with nothing behind it reaches BLOCK.

**What is NOT claimed.**  `AgentExecutionPort.interrupt` has no caller in `graph.py`,
`routing.py` or `executor.py` at this revision -- the engine's abandon path accounts a
residual dispatch through `recover_handle` / `account_dispatch` / `recover_dispatch`, never
through `interrupt`.  Wiring `interrupt` into the graph would require editing a pinned policy
module, which this correction is forbidden to do, so the refusal is measured HERE as a
standing fact rather than papered over: see `EngineInterruptCallerTests`.  `run_pause_cli`'s
named refusal of `--adapter standalone` also still stands and is asserted, so the limitation
stays named rather than being quietly removed.
"""
from __future__ import annotations

import importlib.metadata
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts import run_logging
from scripts.clarification_protocol import ArtifactHumanApprovalPort, ClarificationSource
from scripts.deterministic_workflow import contracts, launcher, pause_policy, pause_store, routing
from scripts.test_deterministic_workflow_pause_fixture import clarification_item
from scripts.test_os37_external_review_regressions import (execute_graph_cli,
                                                           graph_profile_document)


def _langgraph_ok() -> bool:
    try:
        import langgraph  # noqa: F401
        import langgraph.graph  # noqa: F401
    except ImportError:
        return False
    try:
        return importlib.metadata.version("langgraph") == "0.2.76"
    except importlib.metadata.PackageNotFoundError:
        return False


LANGGRAPH_REASON = "requires pinned langgraph 0.2.76"

#: The decision-gate fixture the graph agent answers with under `OS37_GA_GATE`, and the two
#: ledger-owned fields the persisted declaration must reproduce exactly for OS-30's binding
#: (2) to authenticate it.  Read from the fixture rather than retyped, so a change to the
#: fixture cannot leave this module asserting against a record that no longer exists.
_GATE_FIXTURE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "decision_gate" / "valid"
     / "worker_needs_input.json").read_text(encoding="utf-8"))
OPEN_ITEM = _GATE_FIXTURE["open_item"]
REASON_CODE = _GATE_FIXTURE["reason_code"]


# =====================================================================================
# Wiring 1 -- the approval authority is CONDITIONAL, and nothing else changed
# =====================================================================================
class ApprovalAuthorityIsConditionalTests(unittest.TestCase):
    """The capability is declared on a configured authority and on nothing else."""

    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="os37-r4-caps-"))
        self.addCleanup(shutil.rmtree, self.base, True)
        worktree = self.base / "worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        self.profile = graph_profile_document(worktree=worktree, driver_env={},
                                              auth_probe=True, credential=True,
                                              preflight_ms=1_500, timeouts=None)

    def _compose(self, authority: str):
        """`build_standalone_adapter` -- the SAME call `run_cli` makes for this flag."""
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        port = launcher.configured_approval_port(authority, self.base)
        return launcher.build_standalone_adapter(
            {"run_id": "run_caps", "thread_id": "t", "phases": ["DESIGN"]},
            artifact_base=self.base, run_id="run_caps",
            runtime_state=InMemoryRuntimeStateStore(), profile_spec=self.profile,
            approval_port=port)

    def test_the_default_composition_declares_no_approval_authority(self) -> None:
        """`--approval-authority none` is the pre-R4 composition, unchanged.

        Mutation-sensitivity: make `configured_approval_port` return a port for `none` --
        i.e. grant the capability globally, which is exactly what the correction forbids --
        and this fails on both the adapter's own answer and the state it froze.
        """
        adapter, state = self._compose(launcher.NO_APPROVAL_AUTHORITY)
        self.assertIsNone(adapter.approval_port,
                          "a run that named no authority was given one anyway")
        self.assertNotIn("human_approval", adapter.capabilities())
        self.assertNotIn("human_approval", state["adapter_capabilities"])
        self.assertFalse(
            routing.pause_admissible({"decision_state": "NEEDS_INPUT",
                                      "adapter_capabilities": state["adapter_capabilities"]}),
            "an unconfigured standalone run may not be admitted to the pause route")

    def test_only_a_configured_authority_declares_human_approval(self) -> None:
        """The fix itself: a REAL OS-30 port, and the capability follows the wiring.

        Mutation-sensitivity: drop `approval_port=approval_port` from
        `build_standalone_adapter`'s `StandaloneAdapter(...)` call and this fails -- the
        adapter answers, but the composition never hands it the port.
        """
        adapter, state = self._compose(launcher.ARTIFACT_APPROVAL_AUTHORITY)
        self.assertIsInstance(
            adapter.approval_port, ArtifactHumanApprovalPort,
            "the composition root never handed the adapter the configured authority")
        self.assertIn("human_approval", adapter.capabilities())
        self.assertIn("human_approval", state["adapter_capabilities"],
                      "the capability snapshot the STATE carries missed it, so `routing` "
                      "-- which reads the state, not the adapter -- would still refuse")
        self.assertEqual(
            sorted(contracts.PAUSE_CAPABILITIES - frozenset(state["adapter_capabilities"])),
            [], "the pause capability pair is still incomplete")
        self.assertTrue(
            routing.pause_admissible({"decision_state": "NEEDS_INPUT",
                                      "adapter_capabilities": state["adapter_capabilities"]}))

    def test_the_two_compositions_differ_in_exactly_one_capability(self) -> None:
        """The wiring is a WIDENING of one token, not a different declaration.

        Asserted as a set difference rather than by naming the members, so a change that
        also granted, say, `external_resume` on the same flag is reported.
        """
        _plain, without = self._compose(launcher.NO_APPROVAL_AUTHORITY)
        _wired, with_port = self._compose(launcher.ARTIFACT_APPROVAL_AUTHORITY)
        self.assertEqual(
            sorted(set(with_port["adapter_capabilities"])
                   - set(without["adapter_capabilities"])), ["human_approval"])
        self.assertEqual(
            sorted(set(without["adapter_capabilities"])
                   - set(with_port["adapter_capabilities"])), [],
            "configuring an approval authority WITHDREW a capability")

    def test_an_unknown_authority_is_refused_by_name_before_any_composition(self) -> None:
        """A typo must not compose a run with no authority under a flag that says it has one."""
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.configured_approval_port("artifacts", self.base)
        self.assertIn(launcher.UNKNOWN_APPROVAL_AUTHORITY, str(caught.exception))

    def test_the_command_line_default_is_none(self) -> None:
        """An operator who never heard of the flag gets the pre-R4 behaviour."""
        args = launcher.build_parser().parse_args(["--adapter", "standalone"])
        self.assertEqual(args.approval_authority, launcher.NO_APPROVAL_AUTHORITY)
        self.assertEqual(launcher.APPROVAL_AUTHORITIES,
                         (launcher.NO_APPROVAL_AUTHORITY,
                          launcher.ARTIFACT_APPROVAL_AUTHORITY),
                         "a third authority appeared; each one is a claim that a human can "
                         "really be asked, and it owes its own end-to-end case")

    def test_the_fake_composition_is_untouched_by_the_flag(self) -> None:
        """R4 forbids changing what the Fake and Orca adapters declare, so it does not.

        The flag is read in `run_cli`'s STANDALONE branch alone.  Asserted behaviourally,
        over the adapter the fake branch really composes, rather than by reading the source.
        """
        from scripts.deterministic_workflow.fake_adapter import FakeAdapter
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        adapter = FakeAdapter([], runtime_state=InMemoryRuntimeStateStore())
        self.assertNotIn("human_approval", adapter.capabilities(),
                         "the fake adapter's declaration changed; R4 forbids that")
        self.assertNotIn(
            "approval_port",
            launcher.build_orca_adapter.__code__.co_varnames,
            "the Orca composition grew an approval-port parameter; what an Orca run "
            "declares is `OrcaAdapter`'s own answer and R4 does not authorize changing it")


# =====================================================================================
# Wiring 2 -- the declared decision block
# =====================================================================================
class DeclaredDecisionBlockTests(unittest.TestCase):
    """`decision_state` on the launch specification -- optional, engine-validated."""

    SPEC = {"run_id": "run_decl", "thread_id": "t", "phases": ["DESIGN"]}

    def test_a_specification_that_names_none_is_unchanged(self) -> None:
        state = launcher.build_state(dict(self.SPEC))
        self.assertEqual(state["decision_state"], "CLEAR")
        self.assertIsNone(state["decision_reason_code"])

    def test_a_declared_block_reaches_the_state(self) -> None:
        """The gap the review's R4 is really about: nothing on the launch path set this.

        Mutation-sensitivity: delete the `declared is not None` branch in `build_state` and
        this fails -- the state comes back CLEAR and the pause route is unreachable again.
        """
        for declared in ("NEEDS_INPUT", "CONFLICT"):
            with self.subTest(decision_state=declared):
                state = launcher.build_state(
                    {**self.SPEC, "decision_state": declared,
                     "decision_reason_code": REASON_CODE})
                self.assertEqual(state["decision_state"], declared)
                self.assertEqual(state["decision_reason_code"], REASON_CODE)

    def test_an_unknown_decision_state_is_refused_by_the_engines_own_rule(self) -> None:
        """Applied through `SET_DECISION`, so this file is not a second, laxer vocabulary."""
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.build_state({**self.SPEC, "decision_state": "MAYBE"})
        self.assertIn("SET_DECISION", str(caught.exception))

    def test_the_declaration_is_carried_through_the_standalone_state_rebuild(self) -> None:
        """`build_standalone_state` re-derives the state around the live adapter's
        capabilities; a declaration lost in that rebuild would be a pause that never routes."""
        class _Declaring:
            @staticmethod
            def capabilities():
                return contracts.BASE_CAPABILITIES | contracts.PAUSE_CAPABILITIES
        state = launcher.build_standalone_state(
            {**self.SPEC, "decision_state": "NEEDS_INPUT",
             "decision_reason_code": REASON_CODE}, _Declaring())
        self.assertEqual(state["decision_state"], "NEEDS_INPUT")
        self.assertTrue(routing.pause_admissible(state))


# =====================================================================================
# Wiring 3 -- the pause RECORD, and the fail-closed arm the happy path never reaches
# =====================================================================================
class UnrecordablePauseIsNotReportedAsAPauseTests(unittest.TestCase):
    """`pause_runtime.finalize_pause` had no production caller at all before R4.

    A run that paused therefore reached `WAITING_FOR_INPUT`, published its request and
    committed its checkpoint, and then left NO durable pause record: `discover` could not
    list it and `resume` would refuse it with `PAUSE_RECORD_MISSING`.  The E2E class below
    locks the success arm over a real run.  This class locks the REFUSAL arm, which a
    healthy run never reaches and which would therefore never be exercised at all.

    The helper is the one `execute_state` really calls -- asserted below by inspection, so
    this cannot drift into testing a function nothing uses.
    """

    def _waiting_state(self) -> dict:
        from scripts.deterministic_workflow.state import initial_state
        state = dict(initial_state(run_id="run_unrec", thread_id="t", phases=("DESIGN",),
                                   capabilities=contracts.BASE_CAPABILITIES
                                   | contracts.PAUSE_CAPABILITIES))
        state["run_lifecycle"] = "WAITING_FOR_INPUT"
        state["decision_state"] = "NEEDS_INPUT"
        return state

    def test_a_pause_that_cannot_be_recorded_becomes_a_named_block(self) -> None:
        """Fail-closed.  Exit code 4 on a run no `discover` will ever list would tell an
        operator to wait for a human who can never be reached.

        Mutation-sensitivity: return `final` unchanged from `_pause_not_recorded` and this
        reports `WAITING_FOR_INPUT` for a pause that was never written down.
        """
        class NoDurablePath:
            """A checkpointer with no `path`: nothing could reopen the store it names."""

        final = launcher._finalize_pause_if_waiting(
            self._waiting_state(), checkpointer=NoDurablePath(),
            artifact_base=Path(tempfile.mkdtemp(prefix="os37-r4-unrec-")))
        self.assertEqual(final["run_lifecycle"], "SETTLED")
        self.assertEqual(final["terminal_status"], "BLOCKED")
        code = (final["terminal_reason"] or {}).get("code")
        self.assertEqual(code, launcher.PAUSE_RECORD_NOT_WRITTEN)
        self.assertIn(code, pause_policy.PAUSE_REFUSAL_CODES,
                      "the refusal is not a member of the shared pause-refusal vocabulary, "
                      "so `terminal_node` would report it as an ordinary decision block")

    def test_a_caller_that_named_no_run_root_is_left_untouched(self) -> None:
        """`artifact_base is None` means there is nowhere a pause record belongs.

        Every in-process test that drives the graph without an artifact tree relies on
        this, so it is asserted rather than left to be rediscovered.
        """
        state = self._waiting_state()
        final = launcher._finalize_pause_if_waiting(state, checkpointer=object(),
                                                    artifact_base=None)
        self.assertEqual(final, state)

    def test_a_run_that_did_not_pause_is_left_untouched(self) -> None:
        state = self._waiting_state()
        state["run_lifecycle"] = "ACTIVE"
        final = launcher._finalize_pause_if_waiting(
            state, checkpointer=object(),
            artifact_base=Path(tempfile.mkdtemp(prefix="os37-r4-active-")))
        self.assertEqual(final, state)

    def test_the_graph_entry_point_really_calls_it(self) -> None:
        """The wiring itself, so this class cannot drift into testing dead code.

        Mutation-sensitivity: drop the call from `execute_state` and this reports that the
        entry point no longer writes a pause record, which is the R4 defect restored.
        """
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(launcher.execute_state))
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        self.assertIn(
            "_finalize_pause_if_waiting", called,
            "`execute_state` no longer finalizes a pause, so a run that pauses through "
            "`run_workflow.py` leaves no durable record and cannot be resumed")


# =====================================================================================
# The real graph path: launcher -> profile -> adapter -> graph -> PAUSE
# =====================================================================================
def _publish_open_decision(run_id: str, artifact_base: Path) -> str:
    """Publish this run's OS-29 open decision record and return its canonical ledger key.

    This is the COORDINATOR's act, through the shipped `run_logging` publisher -- the same
    function `orca_runtime_harness` calls on the Orca path.  It is deliberately not the
    engine's: OS-30 refuses to invent a question ("fail closed: no invented question or
    option set"), so the open set and the declaration behind it are supplied by whoever
    drives the run, and the approval authority AUTHENTICATES them against this ledger.
    """
    run_logging.append_decision_ledger_record(
        run_id, {"run": run_id, "phase": "design", "iteration": 1, "role": "coordinator",
                 "boundary": "B1", "state": "CLEAR", "reason_code": None,
                 "open_decision_item": False, "open_item": None, "verifies": None},
        base=artifact_base, ledger_schema_version=1)
    _published, sequence = run_logging.append_decision_ledger_record(
        run_id, {"run": run_id, "phase": "design", "iteration": 1, "role": "worker",
                 "boundary": "B2", "state": "NEEDS_INPUT", "reason_code": REASON_CODE,
                 "open_decision_item": True, "open_item": OPEN_ITEM, "verifies": None},
        base=artifact_base, ledger_schema_version=1)
    return f"{run_id}/design/1/B2#{sequence}"


def _declare_blocked_source(run_id: str, artifact_base: Path, key: str) -> ClarificationSource:
    """Persist the operator's question through the REAL OS-30 port and return it."""
    request = clarification_item(run_id, suffix="1", open_item=OPEN_ITEM)
    request.update(source_ledger_key=key, source_ledger_keys=[key], phase="design",
                   source_state="NEEDS_INPUT", source_reason_code=REASON_CODE)
    source = ClarificationSource(
        open_item=OPEN_ITEM, source_ledger_key=key, source_ledger_keys=(key,),
        state="NEEDS_INPUT", reason_code=REASON_CODE, phase="design", iteration=1,
        request_input=request)
    ArtifactHumanApprovalPort(artifact_base).persist_blocked_sources(run_id, (source,))
    return source


class FixtureDeclarationAuthenticatesTests(unittest.TestCase):
    """Binding (2) of `load_blocked_sources`, over the declaration the E2E fixtures publish
    -- in BOTH CI lanes.

    Correction iteration 6.  This case lived in `E2EStandalonePauseThroughTheGraphTests`
    under its LangGraph gate, and the gate was wider than the case: run with that gate
    disabled in an interpreter WITHOUT langgraph it passed, because nothing it reads is
    written by the graph.  `_publish_open_decision` writes the ledger through the real
    OS-29 port and `_declare_blocked_source` persists the question through the real OS-30
    port, and both are LangGraph-free.  A case that needs no runtime must not leave the
    dependency-absent lane because its neighbours do, so it runs here, ungated, against a
    tree built by the same two helpers the gated fixtures call in `setUpClass`.

    What stays gated is the CONSEQUENCE, which does drive the graph: that the PAUSE node
    accepts this declaration (`..._reaches_a_durable_pause`, and
    `..._published_a_real_clarification_request` binds the published item back to this
    same ledger key) and refuses without it (`..._does_not_manufacture_a_pause`).
    """

    RUN = "run_r4decl"

    def setUp(self) -> None:
        room = Path(tempfile.mkdtemp(prefix="os37-r4-decl-"))
        self.addCleanup(shutil.rmtree, room, True)
        self.base = room / "artifact_base"
        self.base.mkdir()
        self.key = _publish_open_decision(self.RUN, self.base)
        _declare_blocked_source(self.RUN, self.base, self.key)

    def test_the_declaration_authenticates_against_the_runs_ledger(self) -> None:
        """Without this the pause could be passing on a declaration nothing backs, which
        is precisely the "asserted rather than measured" failure R4 is about."""
        loaded = ArtifactHumanApprovalPort(self.base).load_blocked_sources(self.RUN)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].source_ledger_key, self.key)
        records = run_logging.read_decision_ledger(self.RUN, base=self.base)
        self.assertTrue(any(record.get("open_decision_item") is True for record in records),
                        "the run's ledger holds no open decision item at all")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EStandalonePauseThroughTheGraphTests(unittest.TestCase):
    """ONE room, three real `run_workflow.py --adapter standalone` invocations.

    Invocation 1 is an ordinary run whose agent answers with the NEEDS_INPUT decision-gate
    record; it blocks, and it is here to make the run's own artifact tree real.  Then the
    coordinator publishes the open OS-29 record and declares the question.  Invocations 2
    and 3 differ in EXACTLY ONE argument -- `--approval-authority` -- which is what makes
    the pair a before/after rather than two unrelated runs.
    """

    RUN = "run_r4pause"

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix="os37-r4-e2e-"))
        cls.first = execute_graph_cli(
            cls.room, run_id=cls.RUN,
            driver_env={"OS37_GA_GATE": "worker_needs_input"})
        cls.base = cls.first.artifact_base
        cls.key = _publish_open_decision(cls.RUN, cls.base)
        cls.source = _declare_blocked_source(cls.RUN, cls.base, cls.key)
        common = {"driver_env": {"OS37_GA_GATE": "worker_needs_input"},
                  "decision_state": "NEEDS_INPUT", "decision_reason_code": REASON_CODE}
        cls.without = execute_graph_cli(cls.room, run_id=cls.RUN, thread_id="unwired",
                                        checkpoint_name="cp-unwired.json", **common)
        cls.wired = execute_graph_cli(cls.room, run_id=cls.RUN, thread_id="wired",
                                      checkpoint_name="cp-wired.json",
                                      approval_authority="artifact", **common)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def test_without_a_configured_authority_the_decision_block_still_blocks(self) -> None:
        """The BEFORE, produced by the same code as the after and differing in one flag.

        Mutation-sensitivity: this is the state of the world before R4, and it is asserted
        rather than remembered -- if a later change granted `human_approval` globally, this
        case fails and says so.
        """
        self.assertIsNone(self.without.escaped)
        self.assertEqual(self.without.summary.get("terminal_status"), "BLOCKED",
                         f"{self.without.summary!r}\n{self.without.stderr}")
        self.assertEqual(self.without.summary.get("run_lifecycle"), "SETTLED")
        self.assertEqual((self.without.summary.get("terminal_reason") or {}).get("code"),
                         "NEEDS_INPUT")
        self.assertEqual(self.without.exit_code, launcher.EXIT_CODES["BLOCKED"])

    def test_with_a_configured_authority_the_graph_reaches_a_durable_pause(self) -> None:
        """The AFTER: `run_lifecycle` is WAITING_FOR_INPUT and the run has its own exit code.

        Mutation-sensitivity: revert either wiring -- the `approval_port=` argument in
        `build_standalone_adapter`, or the `SET_DECISION` branch in `build_state` -- and
        this reverts to the BLOCKED terminal the case above asserts.
        """
        self.assertIsNone(self.wired.escaped)
        self.assertEqual(self.wired.summary.get("run_lifecycle"), "WAITING_FOR_INPUT",
                         f"the graph did not pause: {self.wired.summary!r}\n"
                         f"{self.wired.stderr}")
        self.assertIsNone(self.wired.summary.get("terminal_status"),
                          "a paused run is not a terminal one")
        self.assertEqual(self.wired.exit_code, launcher.EXIT_CODES["WAITING_FOR_INPUT"])

    def test_the_pause_published_a_real_clarification_request(self) -> None:
        """The authority really asked: an immutable OS-30 request a human can be shown.

        A pause that published nothing would be a run stopped with no question to answer.
        Read back through the port's OWN `show`, which re-validates the whole authority set,
        rather than by globbing the artifact tree: a file this test could find by walking
        directories is not evidence that the protocol can serve it.
        """
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertIsNotNone(record, "no pause record, so no request to show")
        request_id = record["projection"]["request_id"]
        self.assertTrue(request_id, "the pause names no clarification request")
        shown = ArtifactHumanApprovalPort(self.base).show(run_id=self.RUN,
                                                          request_id=request_id)
        items = list(shown["request"]["items"])
        self.assertEqual(len(items), 1,
                         f"the pause published {len(items)} items, not one")
        self.assertEqual(items[0]["source_ledger_key"], self.key,
                         "the published question does not name the open ledger record it "
                         "came from, so its provenance is unverifiable")
        self.assertTrue(items[0]["question"], "the published item asks nothing")
        self.assertEqual(sorted(shown["item_statuses"].values()), ["unresolved"],
                         "the run is waiting on a question that already carries a "
                         "decision, so nothing a human does could ever resume it")
        self.assertEqual(shown["effective_decisions"], {item["decision_item_id"]: None
                                                        for item in items},
                         "a pause published a question that is already answered")

    def test_the_pause_record_is_durable_and_names_where_to_resume(self) -> None:
        """C1: a fresh process reads the run's pause from disk alone, with no live object."""
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertIsNotNone(record, "no durable pause record was written at all")
        self.assertEqual(record["run_id"], self.RUN)
        self.assertEqual(record["thread_id"], "wired")
        self.assertTrue(record["checkpoint_id"],
                        "the pause record names no checkpoint to resume from")
        self.assertEqual(record["status"], "WAITING_FOR_INPUT")
        projection = record["projection"]
        self.assertEqual(projection["decision_state"], "NEEDS_INPUT")
        self.assertEqual(projection["decision_reason_code"], REASON_CODE)
        self.assertTrue(projection["request_id"], "the pause record names no request")
        self.assertEqual(projection["source_ledger_keys"], [self.key],
                         "the pause does not name the open ledger record it is waiting on")
        self.assertTrue(record["checkpoint_store_path"],
                        "the record names no checkpoint store, so nothing can reopen it")

    def test_the_pause_binding_validates_under_the_shared_pause_policy(self) -> None:
        """The binding a standalone pause produced is one the ENGINE's own validator admits."""
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertIsNotNone(record)
        binding = record["projection"]
        self.assertEqual(binding["responsible_phase"], "DESIGN")
        self.assertTrue(record["ac1_discharged"],
                        "the pause left a dispatch unaccounted, which AC-1 forbids")
        rows = binding.get("settlement_ledger") or ()
        for row in rows:
            with self.subTest(intent=row.get("intent_id")):
                self.assertIn(row["terminal_disposition"],
                              pause_policy.AC1_DISCHARGING_DISPOSITIONS,
                              f"a standalone row blocks the pause: {row!r}")

    def test_a_configured_authority_alone_does_not_manufacture_a_pause(self) -> None:
        """Fail-closed: with nothing to ask, the pause is REFUSED and named.

        This is the assertion that keeps the wiring honest.  If the capability alone were
        enough to pause, the declaration would be exactly the dishonest one R4 forbids.
        """
        room = Path(tempfile.mkdtemp(prefix="os37-r4-nosrc-"))
        self.addCleanup(shutil.rmtree, room, True)
        run = execute_graph_cli(
            room, run_id="run_r4nosrc", thread_id="nosrc",
            driver_env={"OS37_GA_GATE": "worker_needs_input"},
            approval_authority="artifact", decision_state="NEEDS_INPUT",
            decision_reason_code=REASON_CODE)
        self.assertIsNone(run.escaped)
        self.assertEqual(run.summary.get("terminal_status"), "BLOCKED")
        code = (run.summary.get("terminal_reason") or {}).get("code")
        self.assertIn(code, pause_policy.PAUSE_REFUSAL_CODES,
                      f"a pause with no askable source was not refused by name: {code!r}")


class _PausedStandaloneRun:
    """A run the real production composition really paused, plus the disposal helpers.

    A MIXIN rather than a `TestCase`, so unittest never collects it on its own: the two
    dispositions below each get their OWN room and their own pause -- which they must,
    because a run can be disposed exactly once.  It is the same shape `_GraphAssertions`
    already uses in the external-review module.

    `pause_runtime.dispose_run` is the engine's own disposal entry point: it takes the pause
    claim, revalidates the checkpoint, issues `REQUEST_DISPOSITION` and drives
    `graph.invoke()` -- so the DISPOSE node, the settlement ledger and the terminal
    disposition are all the engine's.  The graph it drives is built here from the SAME
    `build_standalone_adapter` composition `run_cli` uses.

    It is driven directly because it exercises the ENGINE's disposal entry point; the
    shipped `resume` verb composes the same standalone runtime since the follow-up review
    (finding 2) and refuses any other adapter on a standalone run --
    `test_the_pause_cli_composes_standalone_and_refuses_a_foreign_adapter` asserts that.
    """

    RUN = "run_r4dispose"

    @classmethod
    def setUpClass(cls) -> None:
        cls.room = Path(tempfile.mkdtemp(prefix=f"os37-r4-{cls.RUN}-"))
        first = execute_graph_cli(cls.room, run_id=cls.RUN,
                                  driver_env={"OS37_GA_GATE": "worker_needs_input"})
        cls.base = first.artifact_base
        cls.key = _publish_open_decision(cls.RUN, cls.base)
        _declare_blocked_source(cls.RUN, cls.base, cls.key)
        cls.paused = execute_graph_cli(
            cls.room, run_id=cls.RUN, thread_id="wired", checkpoint_name="cp-wired.json",
            driver_env={"OS37_GA_GATE": "worker_needs_input"},
            approval_authority="artifact", decision_state="NEEDS_INPUT",
            decision_reason_code=REASON_CODE)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.room, ignore_errors=True)

    def _composition(self):
        """`build_standalone_adapter`, with the configured authority -- `run_cli`'s call."""
        from scripts.deterministic_workflow.runtime_state import FileRuntimeStateStore
        profile = json.loads((self.room / "profile.json").read_text(encoding="utf-8"))
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": self.RUN, "thread_id": "wired", "phases": ["DESIGN"]},
            artifact_base=self.base, run_id=self.RUN,
            runtime_state=FileRuntimeStateStore(self.room / "ledger.json"),
            profile_spec=profile,
            approval_port=launcher.configured_approval_port(
                launcher.ARTIFACT_APPROVAL_AUTHORITY, self.base))
        return adapter

    def _dispose(self, kind: str):
        from scripts.deterministic_workflow import pause_runtime
        from scripts.deterministic_workflow.graph import build_graph
        adapter = self._composition()
        port = launcher.configured_approval_port(
            launcher.ARTIFACT_APPROVAL_AUTHORITY, self.base)
        journal = pause_store.journal_for(self.RUN, artifact_base=self.base)

        def graph_factory(saver):
            return build_graph(adapter, checkpointer=saver,
                               runtime_state=adapter.runtime_state,
                               approval_port=port, journal=journal)

        return pause_runtime.dispose_run(
            self.RUN, artifact_base=self.base, kind=kind, actor_id="operator",
            actor_type="human", submission_id=f"sub-{kind.lower()}",
            reason="the operator withdrew the question", graph_factory=graph_factory,
            approval_port=port, settlement_port=adapter)

    def test_the_run_really_paused_before_anything_is_disposed(self) -> None:
        """The precondition, asserted rather than assumed: a dispose of a run that never
        paused would exercise nothing."""
        self.assertIsNone(self.paused.escaped)
        self.assertEqual(self.paused.summary.get("run_lifecycle"), "WAITING_FOR_INPUT",
                         f"{self.paused.summary!r}\n{self.paused.stderr}")


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EStandaloneCancelThroughTheGraphTests(_PausedStandaloneRun, unittest.TestCase):
    """CANCEL, through the engine's own disposal entry point.

    `pause_runtime.dispose_run` takes the pause claim, revalidates the checkpoint, issues
    `REQUEST_DISPOSITION` and drives `graph.invoke()` -- so the DISPOSE node, the settlement
    ledger and the terminal disposition are all the ENGINE's.  The graph it drives is built
    from the SAME `build_standalone_adapter` composition `run_cli` uses.

    It is driven directly because it exercises the ENGINE's disposal entry point; since
    the follow-up review (finding 2) the shipped `resume` verb composes the same
    standalone runtime and refuses any other adapter on a standalone run --
    `test_the_pause_cli_composes_standalone_and_refuses_a_foreign_adapter` asserts that.
    """

    RUN = "run_r4cancel"

    def test_cancel_traverses_the_real_dispose_node_and_is_idempotent(self) -> None:
        """CANCEL through `pause_runtime.dispose_run` -> `REQUEST_DISPOSITION` -> DISPOSE.

        Both calls live in ONE case because a run can be disposed exactly once and unittest
        orders cases alphabetically, not by intent: split across two cases the second call
        could run first and the pair would assert the opposite of what it means.

        Mutation-sensitivity: the run this disposes exists ONLY because the R4 wiring made
        the pause reachable AND recorded; revert either the `approval_port=` argument in
        `build_standalone_adapter` or the `finalize_pause` call in `execute_state` and
        `setUpClass` leaves no claimable pause record, so `dispose_run` refuses and this
        fails naming the refusal.
        """
        outcome = self._dispose("CANCEL")
        self.assertEqual(outcome.status, "CANCELLED",
                         f"the disposal did not cancel: {outcome.status}/{outcome.code} "
                         f"{outcome.detail}")
        self.assertTrue(outcome.ac1_discharged,
                        f"a dispatch was left unaccounted by the cancel: "
                        f"{outcome.residual_terminals!r}")
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertEqual(record["status"], "CANCELLED")
        self.assertEqual(record["disposition"]["kind"], "CANCEL")
        self.assertEqual(record["disposition"]["actor_type"], "human")
        self.assertTrue(record["disposition"]["cancellation_id"],
                        "the disposition carries no cancellation identity")
        # A disposed run performs no second effect, however many times it is asked.
        again = self._dispose("CANCEL")
        self.assertEqual(again.status, "ALREADY_DISPOSED",
                         f"{again.status}/{again.code} {again.detail}")
        self.assertEqual(again.code, "RUN_ALREADY_CANCELLED")
        self.assertFalse(again.effect_performed)

    def test_the_pause_cli_composes_standalone_and_refuses_a_foreign_adapter(self) -> None:
        """Follow-up review finding 2 REPLACED the named limitation this case used to lock.

        `resume --adapter standalone` used to be refused by name while omitting the flag
        composed the FAKE adapter over a standalone run -- two contracts, neither
        coherent.  Now there is one: a standalone-launched run is re-entered with the
        standalone composition (its recorded profile, ledger and approval authority --
        `test_os37_recovery_boundary_regressions.F02StandalonePauseResumesEndToEndTests`
        drives that end to end), and the fake default is refused ON THIS RUN by name.
        Disposal through this CLI therefore composes the same runtime the graph paused
        with, and the in-process `pause_runtime.dispose_run` route above stays valid.
        """
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = launcher.run_cli(["resume", "--run-id", self.RUN,
                                     "--artifact-base", str(self.base),
                                     "--adapter", "fake", "--cancel", "--json"])
        self.assertEqual(code, launcher.USAGE_EXIT_CODE)
        self.assertIn(launcher.STANDALONE_RUN_ADAPTER_MISMATCH, err.getvalue())
        self.assertNotIn(launcher.STANDALONE_ADAPTER_UNSUPPORTED_HERE, err.getvalue())


@unittest.skipUnless(_langgraph_ok(), LANGGRAPH_REASON)
class E2EStandaloneAbandonThroughTheGraphTests(_PausedStandaloneRun, unittest.TestCase):
    """ABANDON, the other disposition, on its own paused run.

    A run can be disposed exactly ONCE, so abandon cannot share the cancel run: it gets its
    own room and its own pause.  Both classes take the composition and the `_dispose` helper
    from the same base rather than restating them, so the two dispositions are driven
    through identical wiring and any divergence would be a difference in the ENGINE.

    ABANDON is the leg that reaches `executor._residual_row` -- and therefore the standalone
    adapter's `recover_handle` / `account_dispatch` / `recover_dispatch` -- for any dispatch
    the pause did not already account.  That enumeration is asserted EMPTY here, and that is
    not a gap: `pause_node` refuses `DISPATCH_UNACCOUNTED` while any row is `not_settled`,
    so a legitimately paused run has nothing left running by construction.  The property is
    locked rather than assumed, because an abandon that silently invented a residual -- or
    one that quietly discharged a real one -- is exactly the failure AC-1 is about.
    """

    RUN = "run_r4abandon"

    def test_abandon_settles_the_disposition_and_invents_no_residual(self) -> None:
        outcome = self._dispose("ABANDON")
        self.assertEqual(outcome.status, "ABANDONED",
                         f"{outcome.status}/{outcome.code} {outcome.detail}")
        self.assertEqual(
            outcome.residual_terminals, [],
            "the abandon enumerated a residual dispatch on a run whose pause had already "
            f"accounted every one of them: {outcome.residual_terminals!r}")
        self.assertTrue(outcome.ac1_discharged)
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        self.assertEqual(record["status"], "ABANDONED")
        self.assertEqual(record["disposition"]["kind"], "ABANDON")
        self.assertEqual(record["residual_terminals"], [])

    def test_the_pause_refuses_to_leave_a_dispatch_running(self) -> None:
        """Why the enumeration above is legitimately empty, stated as a property of the
        record rather than as an explanation in prose."""
        record = pause_store.store_for(self.RUN, artifact_base=self.base).read(self.RUN)
        rows = record["projection"].get("settlement_ledger") or ()
        self.assertEqual(
            [row for row in rows if row.get("settlement") == "not_settled"], [],
            "the pause committed with a dispatch still running, which `pause_node` must "
            "refuse as DISPATCH_UNACCOUNTED")


# =====================================================================================
# The refused interrupt -- measured, and its bound named
# =====================================================================================
class EngineInterruptCallerTests(unittest.TestCase):
    """`AgentExecutionPort.interrupt` has no caller in the engine's pinned policy modules.

    This is the R4 residual, stated as a MEASUREMENT rather than as prose.  The engine's
    abandon path accounts a residual dispatch through `recover_handle`, `account_dispatch`
    and `recover_dispatch`; `interrupt` is reachable only from an operator or a supervisor
    calling the port directly.  Wiring it into the graph would mean editing `graph.py`,
    `routing.py` or `executor.py`, which this correction is forbidden to do -- so the fact
    is locked here, and it fails the day it stops being true, which is the correct outcome:
    the limitation would then be stale and the interrupt path would owe its own case.
    """

    PINNED = ("graph.py", "routing.py", "executor.py")

    def _calls(self, module: str) -> list[str]:
        import ast
        source = (Path(__file__).resolve().parent / "deterministic_workflow"
                  / module).read_text(encoding="utf-8")
        found = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr == "interrupt":
                found.append(f"{module}:{node.lineno}")
        return found

    def test_no_pinned_policy_module_calls_the_interrupt_port(self) -> None:
        sites = [site for module in self.PINNED for site in self._calls(module)]
        self.assertEqual(
            sites, [],
            "a pinned policy module now calls `interrupt`; the R4 residual is stale and "
            "the refused-interrupt path owes an end-to-end case through the graph")

    def test_the_abandon_path_reaches_the_standalone_ports_the_engine_does_call(self) -> None:
        """What IS reachable: the three lifecycle verbs `dispose_node` really invokes.

        Named individually so a rename in `executor._residual_row` cannot leave this class
        asserting against a verb the engine stopped using.
        """
        import ast
        source = (Path(__file__).resolve().parent / "deterministic_workflow"
                  / "executor.py").read_text(encoding="utf-8")
        called = {node.func.attr for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        for verb in ("recover_handle", "account_dispatch", "recover_dispatch"):
            with self.subTest(verb=verb):
                self.assertIn(verb, called)

    def test_the_composed_adapter_refuses_an_interrupt_it_does_not_own(self) -> None:
        """The port verb itself, on an adapter `build_standalone_adapter` composed.

        A refusal, named -- never a silent success, and never an adoption: no process is
        started merely to be asked.
        """
        from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
        base = Path(tempfile.mkdtemp(prefix="os37-r4-int-"))
        self.addCleanup(shutil.rmtree, base, True)
        worktree = base / "worktree"
        worktree.mkdir(parents=True, exist_ok=True)
        profile = graph_profile_document(worktree=worktree, driver_env={}, auth_probe=True,
                                         credential=True, preflight_ms=1_500, timeouts=None)
        adapter, _state = launcher.build_standalone_adapter(
            {"run_id": "run_int", "thread_id": "t", "phases": ["DESIGN"]},
            artifact_base=base, run_id="run_int",
            runtime_state=InMemoryRuntimeStateStore(), profile_spec=profile,
            approval_port=launcher.configured_approval_port(
                launcher.ARTIFACT_APPROVAL_AUTHORITY, base))
        outcome = adapter.interrupt("intent-nobody-owns", "operator asked")
        self.assertEqual(outcome["interrupt_outcome"], "not_owned")
        self.assertEqual(outcome["refusal"], "no_live_session_in_this_process")
        self.assertEqual(outcome["ladder"], ())


if __name__ == "__main__":       # pragma: no cover - convenience only
    unittest.main()
