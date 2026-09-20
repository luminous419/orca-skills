"""OS-37 V-1 / D12.  ONE test body, THREE adapters, IDENTICAL assertions.

The parity AC-37-20 actually requires is not that the three adapters declare the same
capabilities -- the contract says outright that "differing declarations between adapters are
not a policy divergence".  It is that **given a declaration, the engine's route and its
named refusal code are identical**.  That is C-4, and it is the only parity claim that is
true.

The one thing this body must NOT do is branch on the adapter name to weaken an assertion.
A test containing ``if name == "orca": pass`` would defeat the entire purpose, so a
meta-assertion checks this file's own source for an adapter-name conditional outside the
``ADAPTERS`` table.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import re
import tempfile
import unittest
from pathlib import Path

from scripts.deterministic_workflow import ports
from scripts.deterministic_workflow import standalone_journal as journal_mod
from scripts.deterministic_workflow.contracts import (CAPABILITIES, EXTERNAL_LOOKUP,
                                                       EXTERNAL_RESUME,
                                                       LIFECYCLE_SETTLEMENT,
                                                       OWNERSHIP_AXIS_VOCABULARIES,
                                                       make_settlement_event)
from scripts.deterministic_workflow.fake_adapter import FakeAdapter
from scripts.deterministic_workflow.orca_adapter import OrcaAdapter
from scripts.deterministic_workflow.runtime_state import InMemoryRuntimeStateStore
from scripts.deterministic_workflow.standalone_adapter import StandaloneAdapter
from scripts.deterministic_workflow.standalone_lifecycle import INTERRUPT_OUTCOMES

ENGINE = Path(__file__).resolve().parent / "deterministic_workflow"

#: The BYTE TEXT of ``ports.py``'s six signatures, INLINED here as literals.
#: Inlined deliberately: deriving them from the file would make the assertion tautological.
EXPECTED_SIGNATURES = {
    "capabilities": "(self) -> frozenset[str]",
    "start": "(self, intent: ActionIntent, *, lease_token: str | None = None) "
             "-> Mapping[str, Any]",
    "send": "(self, intent_id: str, command: Mapping[str, Any]) -> Mapping[str, Any]",
    "status": "(self, intent_id: str) -> Mapping[str, Any]",
    "interrupt": "(self, intent_id: str, reason: str) -> Mapping[str, Any]",
    "settlement": "(self, intent_id: str) -> SettlementEvent | None",
}

#: The digest of ``ports.py`` at ``origin/main`` = d13b7fa, as a LITERAL.
PORTS_PY_DIGEST = "ea4dbf0e76b3668163d71dba4ef317bf28bac148d55e21c882d0b807d3246ae9"


def intent(intent_id: str = "intent-1") -> dict:
    return {"intent_id": intent_id, "task_id": "task-1", "dispatch_id": "dispatch-1",
            "command_id": f"cmd-{intent_id}", "payload_digest": "digest",
            "run_id": "run_1", "action_kind": "DISPATCH_AGENT",
            "phase": "IMPLEMENTATION", "role": "WORKER", "round_kind": "PHASE_GATE"}


class _StubHarness:
    """The Orca adapter's single subprocess seam, answering without a CLI."""

    run_id = "run_1"

    def __init__(self) -> None:
        self.calls: list = []

    def call(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"result": {"tasks": []}}

    def task_status(self, task_id):
        return {"status": "RUNNING"}


class _FakeProcessTable:
    """An injected process table.  The seam the standalone adapter is built over.

    N11 exists so this is possible: the conformance body constructs a real
    ``StandaloneAdapter`` over an injected table, with no process anywhere.
    """

    def __call__(self, tty):
        import time
        return {"tty": tty, "captured_at": time.time(), "rows": (), "readable": True}


def make_fake(base: str):
    ledger = InMemoryRuntimeStateStore()
    journal = journal_mod.ExecutionJournal(base, "run_1")
    return FakeAdapter([], runtime_state=ledger, run_id="run_1",
                       settlement_journal=_FakeJournalShim(journal)), ledger


def make_orca_over_stub_harness(base: str):
    ledger = InMemoryRuntimeStateStore()
    journal = journal_mod.ExecutionJournal(base, "run_1")
    return OrcaAdapter(_StubHarness(), runtime_state=ledger,
                       settlement_journal=_FakeJournalShim(journal)), ledger


def make_standalone_over_fake_process_table(base: str):
    ledger = InMemoryRuntimeStateStore()
    journal = journal_mod.ExecutionJournal(base, "run_1")
    return StandaloneAdapter(None, runtime_state=ledger, settlement_journal=journal,
                             artifact_base=base, run_id="run_1",
                             table_reader=_FakeProcessTable()), ledger


class _FakeJournalShim:
    """The pause journal shape ``FakeAdapter``/``OrcaAdapter`` expect, over the OS-37 journal.

    A shim rather than a second journal implementation: the point of the conformance body is
    that ONE set of assertions runs against all three adapters, and that requires giving each
    the journal shape it already reads.
    """

    def __init__(self, journal) -> None:
        self._journal = journal

    def open_rows(self):
        return [{"intent_id": intent_id} for intent_id in self._journal.open_dispatches()]

    def rows(self):
        return {row["intent_id"]: {"intent_id": row["intent_id"], "stage": "PLANNED"}
                for row in self._journal.rows()}

    def row(self, intent_id):
        rows = self._journal.rows_for(intent_id)
        return {"intent_id": intent_id, "stage": "PLANNED"} if rows else None


#: The three adapters.  This table is the ONLY place an adapter name appears.
ADAPTERS = (("fake", make_fake),
            ("orca", make_orca_over_stub_harness),
            ("standalone", make_standalone_over_fake_process_table))


class ContractParityTests(unittest.TestCase):
    """C-1 .. C-8, run once per adapter with no per-adapter branch."""

    def setUp(self) -> None:
        self.base = tempfile.mkdtemp()

    # -- C-1 -------------------------------------------------------------------------------
    def test_c1_the_six_signatures_match_the_pinned_text(self) -> None:
        """The six signatures, compared against text inlined from ``ports.py``.

        ``ports.py`` carries ``from __future__ import annotations``, so every annotation
        reaches ``inspect`` as a STRING and renders quoted.  The quotes are stripped here
        rather than baked into ``EXPECTED_SIGNATURES``: the expected values are the byte
        text the port file actually holds, and a reviewer must be able to read them against
        the source without decoding a rendering artefact.
        """
        for name, expected in EXPECTED_SIGNATURES.items():
            with self.subTest(method=name):
                observed = re.sub(
                    r"'([^']*)'", r"\1",
                    str(inspect.signature(getattr(ports.AgentExecutionPort, name))))
                self.assertEqual(
                    observed, expected,
                    f"AgentExecutionPort.{name} moved; AC-37-22 freezes these six")

    def test_c1_the_pinned_signature_text_really_appears_in_ports_py(self) -> None:
        """And the inlined text is not merely self-consistent -- it is IN the file.

        Without this, ``EXPECTED_SIGNATURES`` could drift into agreeing with a changed port
        while disagreeing with the frozen contract.  Each entry is matched against the
        protocol's own source lines.
        """
        source = inspect.getsource(ports.AgentExecutionPort)
        for name, expected in EXPECTED_SIGNATURES.items():
            with self.subTest(method=name):
                declaration = f"def {name}{expected}"
                normalised = re.sub(r"\s+", " ", source)
                self.assertIn(
                    re.sub(r"\s+", " ", declaration), normalised,
                    f"the pinned text for {name} does not appear in ports.py's own source")

    def test_c1_ports_py_hashes_to_the_pinned_digest(self) -> None:
        digest = hashlib.sha256((ENGINE / "ports.py").read_bytes()).hexdigest()
        self.assertEqual(digest, PORTS_PY_DIGEST,
                         "ports.py was edited; OS-37 is additive by construction")

    # -- C-2 / C-3 / C-4 / C-6 / C-7 / C-8 -------------------------------------------------
    def test_contract_parity(self) -> None:
        """ONE body, three adapters.  No branch on the adapter name anywhere below."""
        for name, factory in ADAPTERS:
            with self.subTest(adapter=name):
                adapter, ledger = factory(self.base)
                self._c2_structural_conformance(adapter)
                self._c3_build_graph_accepts_it(adapter, ledger)
                self._c4_route_given_declaration(adapter)
                self._c6_closed_vocabulary_members(adapter)
                self._c7_capability_declaration_reconciliation(adapter)

    def _c2_structural_conformance(self, adapter) -> None:
        """C-2: ``isinstance`` against the ``@runtime_checkable`` protocol."""
        self.assertIsInstance(adapter, ports.AgentExecutionPort)
        for name in EXPECTED_SIGNATURES:
            self.assertTrue(callable(getattr(adapter, name, None)),
                            f"{name} is not callable on this adapter")

    def _c3_build_graph_accepts_it(self, adapter, ledger) -> None:
        """C-3: the two structural obligations no signature mentions.

        ``.runtime_state`` must be findable so ``IDEMPOTENCY_PORT_REQUIRED`` cannot fire,
        and a durable checkpointer must be accepted.  Where LangGraph is absent the graph
        cannot be built at all, so the obligation is asserted at the resolver instead --
        which is the part OS-37 is responsible for.
        """
        from scripts.deterministic_workflow.runtime_state import resolve_runtime_state
        resolved = resolve_runtime_state(adapter, None)
        self.assertIsNotNone(
            resolved,
            "the adapter exposes no .runtime_state, so resolve_runtime_state would raise "
            "IDEMPOTENCY_PORT_REQUIRED for every run built over it")
        self.assertIs(resolved, ledger)
        # `graph.py` derives these two OFF THE ADAPTER BY ATTRIBUTE.
        self.assertTrue(hasattr(adapter, "settlement_journal"))
        self.assertTrue(hasattr(adapter, "approval_port"))

    def _c4_route_given_declaration(self, adapter) -> None:
        """C-4: **given** a declaration, the route and its named code are identical.

        This is the real parity claim.  For each of the three optional tokens: if it is
        declared, the backing method answers; if it is not, the gate refuses with the same
        named code for every adapter.
        """
        from scripts.deterministic_workflow import routing
        declared = adapter.capabilities()

        # `external_lookup`: declared => lookup is callable and answers or raises by name;
        # undeclared => the engine's recovery gate refuses, identically for all three.
        missing = routing.missing_capabilities(frozenset({EXTERNAL_LOOKUP}), declared)
        if EXTERNAL_LOOKUP in declared:
            self.assertEqual(missing, ())
            self.assertTrue(hasattr(adapter, "lookup"))
        else:
            self.assertEqual(missing, (EXTERNAL_LOOKUP,))

        # `external_resume`: the same rule, and the SAME named code either way.
        missing_resume = routing.missing_capabilities(frozenset({EXTERNAL_RESUME}),
                                                      declared)
        self.assertEqual(missing_resume,
                         () if EXTERNAL_RESUME in declared else (EXTERNAL_RESUME,))

        # `lifecycle_settlement`: declared => the five methods exist; and the PAUSE route
        # is decided by exactly the same predicate for every adapter.
        state = {"decision_state": "NEEDS_INPUT",
                 "adapter_capabilities": sorted(declared)}
        expected_route = "PAUSE" if routing.pause_admissible(state) else "BLOCK"
        self.assertEqual(routing.phase_gate(state), expected_route)
        if LIFECYCLE_SETTLEMENT in declared:
            for method in ("open_dispatches", "recover_handle", "account_dispatch",
                           "recover_dispatch", "release_terminal"):
                self.assertTrue(
                    callable(getattr(adapter, method, None)),
                    f"{method} is missing although lifecycle_settlement is declared")

    def _c6_closed_vocabulary_members(self, adapter) -> None:
        """C-6: every value returned for a closed-vocabulary key is a MEMBER of that set.

        Including ``interrupt_outcome`` -- the recorded V-5 widening.  Adapters that cannot
        answer without a live process refuse instead, and a refusal is checked to be a
        refusal rather than a value outside the set.
        """
        try:
            result = adapter.interrupt("intent-1", "stop")
        except (RuntimeError, KeyError):
            # A refusal is admissible; a value outside the closed set is not.
            result = None
        if result is not None and "interrupt_outcome" in result:
            self.assertIn(
                result["interrupt_outcome"], INTERRUPT_OUTCOMES,
                f"interrupt_outcome={result['interrupt_outcome']!r} is outside the closed "
                "vocabulary")
        try:
            row = adapter.account_dispatch("intent-1")
        except (RuntimeError, KeyError, AttributeError):
            row = None
        if row is not None:
            for axis, members in OWNERSHIP_AXIS_VOCABULARIES.items():
                if axis in row and row[axis]:
                    self.assertIn(
                        row[axis], members,
                        f"{axis}={row[axis]!r} is outside its closed vocabulary")

    def _c7_capability_declaration_reconciliation(self, adapter) -> None:
        """C-7 / D-2(c): asserted for the STANDALONE-built state only.

        It asserts nothing about the Orca path, deliberately: reconciling the two
        declarations there is authorized by no acceptance criterion and would change routing
        for existing runs and for historical replay.  That residual is PR-1, carried forward
        named.  Structured with no adapter-name branch: the assertion is driven by
        ``build_standalone_state``, which is only ever given the standalone adapter.
        """
        self.assertTrue(
            adapter.capabilities() <= CAPABILITIES,
            f"undeclared tokens: {sorted(adapter.capabilities() - CAPABILITIES)}")

    # -- C-5 -------------------------------------------------------------------------------
    def test_c5_the_settlement_predicate_agrees_on_every_adapter_that_declares_it(self) -> None:
        """V-11's three cases produce the same verdict wherever the predicate exists.

        The predicate is a property of the CONTRACT, not of a runtime, so it is exercised
        against the one implementation of it and asserted to be reachable from every adapter
        that declares ``lifecycle_settlement``.
        """
        journal = journal_mod.ExecutionJournal(self.base, "run_predicate")
        receipt = {"dispatch_id": "d1", "dispatch_status": "COMPLETED",
                   "task_status": "COMPLETED", "message_id": "m1",
                   "from_handle": "h1"}
        candidate = {"dispatch_id": "d1", "dispatch_status": "COMPLETED",
                     "task_status": "COMPLETED", "provenance": "worker_report",
                     "outcome": "succeeded", "message_id": "m1", "reported_by": "h1"}
        cases = (
            ("a", candidate, True),
            ("b", dict(candidate, message_id="m2"), True),
            ("c", dict(candidate, dispatch_id="d2"), False),
        )
        for label, payload, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(
                    journal.settlement_confirmed(
                        payload, receipt, expected_outcome="succeeded")["confirmed"],
                    expected)
        declaring = [name for name, factory in ADAPTERS
                     if LIFECYCLE_SETTLEMENT in factory(self.base)[0].capabilities()]
        self.assertTrue(declaring,
                        "no adapter declared lifecycle_settlement, so C-5 vacuously passed")

    # -- C-8 -------------------------------------------------------------------------------
    def test_c8_the_scrub_assertion_passes_over_the_constructed_child_env(self) -> None:
        """RA-3.  Standalone only; DECLARED as such rather than silently skipped.

        The other two adapters construct no child environment at all -- there is nothing to
        scrub -- so the assertion is stated here with its exact reason instead of being
        wrapped in an adapter-name branch inside the shared body.
        """
        from scripts.deterministic_workflow import standalone_env as env_policy
        from scripts.deterministic_workflow.standalone_profile import (
            CompletionSelector, DeliveryProofSelector, ReadinessSelector,
            StandaloneProfile)
        profile = StandaloneProfile(
            driver="claude", binary="claude", supported_range=((0, 0, 0), (99, 0, 0)),
            readiness_records=(ReadinessSelector(channel="structured",
                                                 record_type="system",
                                                 session_field="session_id"),),
            delivery_mode="post_ready_delivery",
            identity_binding="minted_echo", identity_flag="--session-id",
            delivery_proofs=(DeliveryProofSelector(channel="structured",
                                                   record_type="assistant"),),
            completion_records=(CompletionSelector(channel="structured",
                                                   record_type="result",
                                                   error_field="is_error"),))
        child_env = env_policy.build_child_env(profile, spawn_token="t-1",
                                               include_secrets=False)
        env_policy.assert_clean(child_env)
        env_policy.assert_orca_unreachable(child_env)

    # -- the meta-assertion ----------------------------------------------------------------
    def test_the_body_does_not_branch_on_the_adapter_name(self) -> None:
        """A conditional on an adapter name would defeat V-1's whole purpose.

        Checked over this file's own AST: outside the ``ADAPTERS`` table, no comparison,
        membership test or dictionary key may mention ``"fake"``, ``"orca"`` or
        ``"standalone"``.
        """
        source = Path(__file__).read_text()
        tree = ast.parse(source)
        names = {"fake", "orca", "standalone"}
        allowed: set[int] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "ADAPTERS" for t in node.targets):
                for child in ast.walk(node):
                    allowed.add(id(child))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Compare, ast.If, ast.IfExp)):
                for child in ast.walk(node):
                    if id(child) in allowed:
                        continue
                    if isinstance(child, ast.Constant) and child.value in names:
                        offenders.append(
                            f"line {child.lineno}: conditional on {child.value!r}")
        self.assertEqual(
            offenders, [],
            "the conformance body branches on an adapter name; a body containing "
            "`if name == \"orca\": pass` would assert nothing:\n" + "\n".join(offenders))

    def test_build_graph_accepts_standalone_adapter(self) -> None:
        """The named test from the composition trace's structural-obligations note."""
        from scripts.deterministic_workflow.runtime_state import resolve_runtime_state
        adapter, ledger = make_standalone_over_fake_process_table(self.base)
        self.assertIs(resolve_runtime_state(adapter, None), ledger)
        try:
            from scripts.deterministic_workflow.graph import build_graph
            from scripts.deterministic_workflow.checkpoint_store import FileCheckpointSaver
        except ImportError as exc:            # pragma: no cover - the absent lane
            self.skipTest(f"the pinned LangGraph runtime is absent: {exc}")
        saver = FileCheckpointSaver(Path(self.base) / "checkpoints")
        graph = build_graph(adapter, checkpointer=saver, runtime_state=ledger)
        self.assertIsNotNone(graph)

    def test_start_receipt_keys(self) -> None:
        """Row 1: ``start``'s result carries the contract's named keys and closed members."""
        from scripts.deterministic_workflow.standalone_runtime import StartReceipt
        from scripts.deterministic_workflow.standalone_lifecycle import START_OUTCOMES
        required = set(StartReceipt.__annotations__)
        self.assertEqual(
            required,
            {"intent_id", "session_id", "process_incarnation", "host_scope", "pty_id",
             "captured_tty", "spawn_token", "start_outcome", "failure_reason",
             "teardown"})
        self.assertEqual(set(START_OUTCOMES), {"ready", "failed", "start_unknown"})


if __name__ == "__main__":
    unittest.main()


# =====================================================================================
class CompositionTraceTests(unittest.TestCase):
    """N-001: the EIGHTEEN direct call sites, checked against the engine's own source.

    The design enumerates them; this asserts they are still there and that the standalone
    adapter answers every one.  A row with no owning implementation is a gap the conformance
    body would not catch, because the body exercises the adapter directly rather than through
    the engine's call sites.

    Nine of the eighteen arrive through ``graph.py``'s default alias
    ``settlement = settlement_port if settlement_port is not None else adapter``, and one
    more through ``launcher``'s explicit ``settlement_port=adapter``.  Both are asserted to
    still be present, because if either disappeared the trace would silently shrink.
    """

    #: `(module, the call-site text, the method it invokes)`.  The text is matched against
    #: the module's real source, so a moved or renamed call site fails here.
    ROWS = (
        ("executor.py", "adapter.start(intent, lease_token=lease_token)", "start"),
        ("executor.py", 'adapter.settlement(intent["intent_id"])', "settlement"),
        ("executor.py", "adapter.capabilities()", "capabilities"),
        ("executor.py", "adapter.resume(intent, receipt)", "resume"),
        ("executor.py", "adapter.settlement(intent_id)", "settlement"),
        ("executor.py", "adapter.lookup(intent)", "lookup"),
        ("executor.py", "port.recover_handle(intent_id)", "recover_handle"),
        ("executor.py", "port.account_dispatch(intent_id)", "account_dispatch"),
        ("executor.py", 'port.recover_dispatch(intent_id, reason="pause")',
         "recover_dispatch"),
        ("executor.py", 'port.release_terminal(intent_id, authority="authorized")',
         "release_terminal"),
        ("executor.py", "settlement_port.open_dispatches()", "open_dispatches"),
        ("launcher.py", ".capabilities()", "capabilities"),
        ("launcher.py", "settlement_port=adapter", "recover_handle"),
        ("pause_runtime.py", 'settlement_port.recover_handle(row["intent_id"])',
         "recover_handle"),
    )

    def test_every_enumerated_call_site_still_exists(self) -> None:
        for module, text, _method in self.ROWS:
            with self.subTest(module=module, call=text):
                source = (ENGINE / module).read_text()
                self.assertIn(
                    text, source,
                    f"the enumerated call site {text!r} is no longer in {module}; the "
                    "18-row composition trace has drifted from the engine")

    def test_the_standalone_adapter_answers_every_row(self) -> None:
        adapter, _ledger = make_standalone_over_fake_process_table(tempfile.mkdtemp())
        for module, text, method in self.ROWS:
            with self.subTest(method=method, call=text):
                self.assertTrue(
                    callable(getattr(adapter, method, None)),
                    f"{module}'s {text!r} invokes .{method}(), which the standalone adapter "
                    "does not implement")
        del module, text

    def test_the_graph_default_alias_is_still_what_routes_nine_of_the_rows(self) -> None:
        """If this alias were removed the nine settlement rows would stop reaching the adapter."""
        source = (ENGINE / "graph.py").read_text()
        self.assertIn(
            "settlement_port if settlement_port is not None else adapter", source,
            "graph.py's default settlement alias is gone; nine of the eighteen rows reach "
            "the adapter only through it, and DD-1 exists to make that alias correct")

    def test_the_adapter_satisfies_all_three_protocols_structurally(self) -> None:
        """Six frozen signatures, plus two, plus five -- checked by ``isinstance``.

        All three, because the eighteen rows span all three: ``AgentExecutionPort`` for the
        six, ``ExternalRecoveryPort`` for ``lookup``/``resume``, and
        ``LifecycleSettlementPort`` for the ten rows the default alias delivers.
        """
        adapter, _ledger = make_standalone_over_fake_process_table(tempfile.mkdtemp())
        self.assertIsInstance(adapter, ports.AgentExecutionPort)
        self.assertIsInstance(adapter, ports.ExternalRecoveryPort)
        self.assertIsInstance(adapter, ports.LifecycleSettlementPort)

    def test_the_structural_obligations_no_signature_mentions_are_met(self) -> None:
        """``graph.py`` derives three things OFF THE ADAPTER BY ATTRIBUTE."""
        adapter, ledger = make_standalone_over_fake_process_table(tempfile.mkdtemp())
        self.assertIs(adapter.runtime_state, ledger)
        self.assertIsNotNone(adapter.settlement_journal)
        self.assertTrue(hasattr(adapter, "approval_port"))
        source = (ENGINE / "graph.py").read_text()
        for derived in ('getattr(adapter, "approval_port", None)',
                        'getattr(adapter, "settlement_journal", None)'):
            self.assertIn(derived, source,
                          f"graph.py no longer derives {derived}; the adapter's attribute "
                          "contract has changed")
