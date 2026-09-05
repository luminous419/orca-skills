"""OS-31 F-002 conformance for the REAL adapter over the REAL harness, offline.

``FakeAdapter`` was written to be restart-safe, so proving the property on it proves
nothing about ``OrcaAdapter``/``OrcaRuntimeHarness`` and their in-memory ``_receipts`` and
``_terminals``.  Every test here therefore drives the real classes through
``OfflineHarnessTestCase``, which stubs only ``_exec_orca`` -- the subprocess boundary --
so every line of the real adapter, the real terminal ledger, the real ``account_axes`` and
the real journal writes actually execute.

Three fixture rules make this a proof rather than a staging:

* **No pre-seeded harness state.**  The recovering harness is built fresh and asserted to
  have ``_terminals == {}`` and ``_receipts == {}`` immediately before recovery; the test
  never calls ``register_terminal`` itself.
* **Response shapes are the real ones.**  ``worker-show`` / ``dispatch-show`` are pinned to
  the shapes the live runtime actually returns -- neither carries a role, an origin, an
  owner or a terminal handle, and a guard assertion says so.
* **Provenance comes only from the journal**, asserted both ways: the recovered row equals
  what ``PLANNED`` wrote, and a variant with the journal deleted recovers
  ``unknown_role``/``unknown`` instead.
"""
from __future__ import annotations

import hashlib
import json
import unittest
from os import environ
from pathlib import Path
from unittest.mock import patch

from scripts.deterministic_workflow import pause_policy, pause_store
from scripts.deterministic_workflow.contracts import (BASE_CAPABILITIES,
                                                      LIFECYCLE_SETTLEMENT)
from scripts.deterministic_workflow.orca_adapter import OrcaAdapter, WORKTREE_ALIASES
from scripts.test_orca_runtime_contract import OfflineHarnessTestCase, RecordingExec

WORKTREE_A_ID = "repoA::/wt/a"
WORKTREE_B_ID = "repoB::/wt/b"
SELECTOR_A = f"id:{WORKTREE_A_ID}"
SELECTOR_B = f"id:{WORKTREE_B_ID}"
RUN = "run_offline"
INTENT = "intent_os31"
TITLE = f"os31-{RUN}-{INTENT}"
HANDLE = "term_created"

# The response shapes the live runtime actually returns.  Nothing here carries a role, an
# origin, an owner or a terminal handle, because `worker-show` and `dispatch-show` do not.
WORKER_SHOW = {"dispatch": {"status": "completed", "completed_at": "2026-01-01T00:00:00Z"},
               "worker": {"state": "settled"},
               "terminalResource": {"releaseState": "live"}}
DISPATCH_SHOW = {"dispatch": {"id": "ctx_1", "status": "completed",
                              "completed_at": "2026-01-01T00:00:00Z"}}
# The receipt observed 12/12 against the live runtime: NOT a release (D-6/R8-iii).
WORKER_RELEASE = {"state": "retained", "reason": "external_terminal",
                  "processAction": "none"}


class _Os31Exec(RecordingExec):
    """RecordingExec plus the two OS-31 read verbs, scripted per worktree selector."""

    def __init__(self, *, current_worktree=WORKTREE_A_ID, listing=None,
                 resolvable=(WORKTREE_A_ID,), listing_ok=True, **kwargs):
        results = dict(kwargs.pop("results", None) or {})
        # A dispatch must actually settle, or every ordering assertion below would be
        # about a run that timed out rather than one that completed.
        results.setdefault("check", RecordingExec.ACCEPTED_DONE)
        super().__init__(results=results, **kwargs)
        self.current_worktree = current_worktree
        self.listing = {SELECTOR_A: list(listing if listing is not None else [])}
        self.resolvable = set(resolvable)
        self.listing_ok = listing_ok

    def __call__(self, args):
        args = tuple(args)
        if args[:2] == ("worktree", "current"):
            self.commands.append(args)
            return 0, json.dumps({"ok": True, "result": {"worktree": {
                "id": self.current_worktree, "repoId": self.current_worktree.split("::")[0],
                "path": self.current_worktree.split("::")[-1]}}})
        if args[:2] == ("worktree", "show"):
            self.commands.append(args)
            selector = _flag(args, "--worktree")
            identity = selector.split("id:", 1)[-1]
            if identity in self.resolvable:
                return 0, json.dumps({"ok": True,
                                      "result": {"worktree": {"id": identity}}})
            return 0, json.dumps({"ok": False, "error": {"code": "selector_not_found"}})
        if args[:2] == ("terminal", "list"):
            self.commands.append(args)
            if not self.listing_ok:
                return 0, json.dumps({"ok": False, "error": "listing unreadable"})
            selector = _flag(args, "--worktree")
            return 0, json.dumps({"ok": True, "result": {
                "terminals": list(self.listing.get(selector, [])),
                "totalCount": len(self.listing.get(selector, [])), "truncated": False}})
        return super().__call__(args)


def worker_result(attempt, intent):
    """The settlement body parser is not the subject here; the journal ordering is."""
    return {"status": "COMPLETE", "unit_test_status": "NOT_APPLICABLE"}


def _flag(args, name):
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
    return ""


def terminal_element(handle=HANDLE, title=TITLE, worktree=WORKTREE_A_ID):
    """The observed live shape, field for field."""
    return {"handle": handle, "title": title, "worktreeId": worktree,
            "worktreePath": worktree.split("::")[-1], "orphaned": False,
            "connected": True}


class HarnessSeamTests(OfflineHarnessTestCase):
    """The five additive seams: all defaulted, so every existing call site is unchanged."""

    def test_create_fake_terminal_defaults_preserve_todays_title_and_worktree(self):
        recorder = _Os31Exec()
        harness = self.build(recorder)
        harness.create_fake_terminal("worker", "complete", iteration=3)
        created = next(args for args in recorder.commands
                       if args[:2] == ("terminal", "create"))
        self.assertEqual(_flag(created, "--title"), "fake-worker-3")
        self.assertEqual(_flag(created, "--worktree"), "current")

    def test_create_fake_terminal_sends_the_run_unique_title_and_stable_worktree(self):
        recorder = _Os31Exec()
        harness = self.build(recorder)
        harness.create_fake_terminal("worker", "complete", iteration=3,
                                     title=TITLE, worktree=SELECTOR_A)
        created = next(args for args in recorder.commands
                       if args[:2] == ("terminal", "create"))
        self.assertEqual(_flag(created, "--title"), TITLE)
        self.assertEqual(_flag(created, "--worktree"), SELECTOR_A)

    def test_list_terminals_issues_the_verified_grammar_and_mutates_nothing(self):
        recorder = _Os31Exec(listing=[terminal_element()])
        harness = self.build(recorder)
        found = harness.list_terminals(worktree=SELECTOR_A)
        self.assertEqual([item["handle"] for item in found], [HANDLE])
        issued = next(args for args in recorder.commands
                      if args[:2] == ("terminal", "list"))
        self.assertEqual(issued, ("terminal", "list", "--worktree", SELECTOR_A))
        self.assertEqual(harness.lifecycle_commands(), [])

    def test_resolve_worktree_distinguishes_absent_from_unresolvable(self):
        recorder = _Os31Exec()
        harness = self.build(recorder)
        self.assertEqual(harness.resolve_worktree(SELECTOR_A)["id"], WORKTREE_A_ID)
        self.assertIsNone(harness.resolve_worktree(SELECTOR_B))

    def test_the_terminal_observer_runs_between_terminal_create_and_worker_start(self):
        recorder = _Os31Exec()
        harness = self.build(recorder)
        seen: list[tuple[str, int]] = []

        def observer(handle: str) -> None:
            seen.append((handle, len(recorder.commands)))

        harness.run_existing_task("worker", 1, "complete", "task_g", phase="analysis",
                                  spec="{}", terminal_observer=observer,
                                  terminal_title=TITLE, terminal_worktree=SELECTOR_A)
        self.assertEqual(len(seen), 1)
        handle, position = seen[0]
        self.assertEqual(handle, HANDLE)
        verbs = [args[1] if len(args) > 1 else args[0] for args in recorder.commands]
        self.assertLess(verbs.index("create"), position, "observed after terminal create")
        self.assertGreater(verbs.index("worker-start"), position - 1,
                           "observed before worker-start")


class WorktreeIdentityTests(OfflineHarnessTestCase):
    """SS4.2.1: a stable identity is resolved once, before E1, or the dispatch refuses."""

    def adapter(self, recorder, *, journal=None):
        harness = self.build(recorder)
        return OrcaAdapter(harness, worker_result, settlement_journal=journal), harness

    def journal(self):
        return pause_store.journal_for(RUN, artifact_base=self.artifact_dir)

    def test_the_selector_is_the_stable_repo_id_and_path_never_an_alias(self):
        adapter, _ = self.adapter(_Os31Exec())
        selector = adapter.origin_worktree_selector()
        self.assertEqual(selector, SELECTOR_A)
        self.assertTrue(selector.startswith("id:"))
        self.assertIn("::", selector)
        for alias in WORKTREE_ALIASES:
            self.assertNotIn(alias, selector.split("id:", 1)[-1].split("::")[0])

    def test_an_unresolvable_origin_refuses_before_any_effect(self):
        from scripts.deterministic_workflow.contracts import ExternalLookupUnavailable
        for bad in ("current", "active", "repoA", ""):
            with self.subTest(bad=bad):
                adapter, _ = self.adapter(_Os31Exec(current_worktree=bad))
                with self.assertRaises(ExternalLookupUnavailable) as ctx:
                    adapter.origin_worktree_selector()
                self.assertIn("DISPATCH_UNACCOUNTED", str(ctx.exception))

    def test_the_capability_is_declared_only_when_the_journal_backs_it(self):
        bare, _ = self.adapter(_Os31Exec())
        self.assertNotIn(LIFECYCLE_SETTLEMENT, bare.capabilities())
        wired, _ = self.adapter(_Os31Exec(), journal=self.journal())
        self.assertIn(LIFECYCLE_SETTLEMENT, wired.capabilities())
        self.assertTrue(BASE_CAPABILITIES <= wired.capabilities())


class DurableProvenanceOrderingTests(OfflineHarnessTestCase):
    """Ordering, asserted from the real command log: every write precedes its effect."""

    def journal(self):
        return pause_store.journal_for(RUN, artifact_base=self.artifact_dir)

    _DEFAULT = object()

    def dispatch(self, recorder=None, *, journal=_DEFAULT):
        recorder = recorder or _Os31Exec()
        harness = self.build(recorder)
        journal = self.journal() if journal is self._DEFAULT else journal
        adapter = OrcaAdapter(harness, worker_result, settlement_journal=journal)
        intent = _intent()
        adapter.start(intent, lease_token=None)
        return adapter, harness, recorder, journal, intent

    def test_planned_precedes_task_create_and_opened_precedes_terminal_create(self):
        adapter, harness, recorder, journal, intent = self.dispatch()
        row = journal.row(intent["intent_id"])
        self.assertEqual(row["stage"], "INTENDED")
        verbs = [args[1] if len(args) > 1 else args[0] for args in recorder.commands]
        # `worktree current` is read BEFORE task-create: the PLANNED row cannot be written
        # without it, and it must be durable before the first effect exists.
        self.assertLess(verbs.index("current"), verbs.index("task-create"))
        self.assertLess(verbs.index("task-create"), verbs.index("create"))
        self.assertLess(verbs.index("create"), verbs.index("worker-start"))
        self.assertTrue(row["planned_at"] <= row["opened_at"] <= row["intended_at"])

    def test_e2_receives_the_byte_identical_selector_the_planned_row_journalled(self):
        adapter, harness, recorder, journal, intent = self.dispatch()
        row = journal.row(intent["intent_id"])
        created = next(args for args in recorder.commands
                       if args[:2] == ("terminal", "create"))
        self.assertEqual(_flag(created, "--worktree"), row["terminal_worktree"])
        self.assertEqual(_flag(created, "--title"), row["terminal_title"])
        self.assertEqual(row["terminal_worktree"], SELECTOR_A)

    def test_no_worktree_argument_anywhere_in_the_run_is_an_alias(self):
        adapter, harness, recorder, journal, intent = self.dispatch()
        for args in recorder.commands:
            value = _flag(args, "--worktree")
            if value:
                self.assertNotIn(value, WORKTREE_ALIASES, args)

    def test_the_intended_row_carries_the_digest_and_never_the_handle(self):
        adapter, harness, recorder, journal, intent = self.dispatch()
        row = journal.row(intent["intent_id"])
        self.assertEqual(row["terminal_digest"],
                         hashlib.sha256(HANDLE.encode("utf-8")).hexdigest())
        self.assertEqual(row["provenance_source"], "journal")
        text = pause_store.settlement_journal_path(
            RUN, artifact_base=self.artifact_dir).read_text()
        self.assertNotIn(HANDLE, text)

    def test_the_dispatch_is_unchanged_when_no_journal_is_wired_in(self):
        """The non-pause path must not change for a Coordinator that never pauses."""
        adapter, harness, recorder, journal, intent = self.dispatch(journal=None)
        created = next(args for args in recorder.commands
                       if args[:2] == ("terminal", "create"))
        self.assertEqual(_flag(created, "--worktree"), "current")
        self.assertTrue(_flag(created, "--title").startswith("fake-"))


class FreshProcessRecoveryTests(OfflineHarnessTestCase):
    """T-47: the handle is recovered by a process that holds NONE of the creator's objects."""

    def journal(self):
        return pause_store.journal_for(RUN, artifact_base=self.artifact_dir)

    def create_in_worktree_a(self):
        """The creating process: bound to worktree A, journals A, creates in A."""
        recorder = _Os31Exec(current_worktree=WORKTREE_A_ID)
        harness = self.build(recorder)
        adapter = OrcaAdapter(harness, worker_result, settlement_journal=self.journal())
        intent = _intent()
        adapter.start(intent, lease_token=None)
        return recorder, intent

    def recovering(self, *, listing=None, resolvable=(WORKTREE_A_ID,), listing_ok=True,
                   current=WORKTREE_B_ID):
        """The successor: bound to a DIFFERENT worktree, holding no prior objects."""
        recorder = _Os31Exec(current_worktree=current, listing=listing,
                             resolvable=resolvable, listing_ok=listing_ok,
                             results={"worker-show": WORKER_SHOW,
                                      "dispatch-show": DISPATCH_SHOW,
                                      "worker-release": WORKER_RELEASE})
        harness = self.build(recorder)
        adapter = OrcaAdapter(harness, worker_result, settlement_journal=self.journal())
        self.assertEqual(harness._terminals, {},  # noqa: SLF001
                         "the recovering harness must hold no terminal state")
        self.assertEqual(adapter._receipts, {},  # noqa: SLF001
                         "the recovering adapter must hold no receipts")
        return adapter, harness, recorder

    def test_the_handle_is_recovered_from_the_scoped_listing_and_proved_by_the_digest(self):
        _, intent = self.create_in_worktree_a()
        adapter, harness, recorder = self.recovering(
            listing=[terminal_element(title=f"✳ {TITLE}"),
                     terminal_element(handle="term_other", title="orca-skills"),
                     terminal_element(handle="term_foreign",
                                      title=f"os31-run_OTHER-{INTENT}")])
        found = adapter.recover_handle(intent["intent_id"])
        self.assertEqual(found, {"handle": HANDLE, "handle_recovery": "listing_verified"})
        issued = next(args for args in recorder.commands
                      if args[:2] == ("terminal", "list"))
        self.assertEqual(_flag(issued, "--worktree"), SELECTOR_A,
                         "the recovery must replay the RECORDED selector, not its own")

    def test_the_recovering_process_is_bound_elsewhere_and_that_is_irrelevant(self):
        _, intent = self.create_in_worktree_a()
        adapter, harness, recorder = self.recovering(
            listing=[terminal_element()], current=WORKTREE_B_ID)
        self.assertEqual(adapter.origin_worktree_selector(), SELECTOR_B,
                         "this process really is bound to a different worktree")
        self.assertEqual(adapter.recover_handle(intent["intent_id"])["handle"], HANDLE)

    def test_a_journalled_alias_makes_the_recovery_fail(self):
        """The guard against reintroducing alias replay: a test that replayed the same
        alias would otherwise pass either way."""
        _, intent = self.create_in_worktree_a()
        self.journal().record(intent["intent_id"], stage="INTENDED",
                              terminal_worktree="current")
        adapter, _, _ = self.recovering(listing=[terminal_element()])
        found = adapter.recover_handle(intent["intent_id"])
        self.assertEqual(found["handle_recovery"], "scope_unresolved")
        self.assertIsNone(found["handle"])

    def test_a_digest_mutated_by_one_byte_refuses_even_though_the_handle_is_listed(self):
        _, intent = self.create_in_worktree_a()
        self.journal().record(
            intent["intent_id"], stage="INTENDED",
            terminal_digest=hashlib.sha256(b"a-different-handle").hexdigest())
        adapter, _, _ = self.recovering(listing=[terminal_element()])
        found = adapter.recover_handle(intent["intent_id"])
        self.assertEqual(found["handle_recovery"], "unverified")
        self.assertIsNone(found["handle"])

    def test_the_same_handle_listed_twice_is_an_anomaly_not_a_coin_toss(self):
        _, intent = self.create_in_worktree_a()
        adapter, _, _ = self.recovering(
            listing=[terminal_element(), terminal_element(title=f"◐ {TITLE}")])
        self.assertEqual(adapter.recover_handle(intent["intent_id"])["handle_recovery"],
                         "unverified")

    def test_a_missing_element_with_a_resolvable_scope_is_not_listed(self):
        _, intent = self.create_in_worktree_a()
        adapter, _, _ = self.recovering(listing=[])
        self.assertEqual(adapter.recover_handle(intent["intent_id"])["handle_recovery"],
                         "not_listed")

    def test_an_unresolvable_scope_is_unknown_never_empty(self):
        _, intent = self.create_in_worktree_a()
        adapter, _, _ = self.recovering(listing=[], resolvable=())
        self.assertEqual(adapter.recover_handle(intent["intent_id"])["handle_recovery"],
                         "scope_unresolved")

    def test_a_scope_that_echoes_a_different_id_is_refused_the_same_way(self):
        """The guard compares the ECHOED id, not merely `ok`."""
        _, intent = self.create_in_worktree_a()
        adapter, _, _ = self.recovering(listing=[], resolvable=(WORKTREE_B_ID,))
        self.assertEqual(adapter.recover_handle(intent["intent_id"])["handle_recovery"],
                         "scope_unresolved")

    def test_an_unreadable_listing_refuses_rather_than_enumerating_nothing(self):
        """Unreadable is UNKNOWN, never empty -- the rule `lookup` already states."""
        from scripts.deterministic_workflow.pause_policy import PauseRefused
        _, intent = self.create_in_worktree_a()
        adapter, _, _ = self.recovering(listing=[], listing_ok=False, resolvable=())
        with self.assertRaises(PauseRefused) as ctx:
            adapter.recover_handle(intent["intent_id"])
        self.assertEqual(ctx.exception.code, "DISPATCH_UNACCOUNTED")

    def test_provenance_comes_from_the_journal_and_a_deleted_journal_recovers_nothing(self):
        _, intent = self.create_in_worktree_a()
        adapter, harness, _ = self.recovering(listing=[terminal_element()])
        row = adapter.account_dispatch(intent["intent_id"])
        self.assertEqual(row["terminal_role"], "active_worker")
        self.assertEqual(row["terminal_origin"], "self_created")
        self.assertEqual(row["provenance_source"], "journal")
        # The negative twin: with the journal gone, nothing can name role or origin.
        pause_store.settlement_journal_path(RUN,
                                            artifact_base=self.artifact_dir).unlink()
        successor, harness2, _ = self.recovering(listing=[terminal_element()])
        empty = successor.account_dispatch(intent["intent_id"])
        self.assertEqual(empty["terminal_role"], "unknown_role")
        self.assertEqual(empty["terminal_origin"], "")
        with self.assertRaises(pause_policy.PauseRefused):
            pause_policy.require_pause_disposition({
                **empty, "provenance_source": "absent",
                "handle_recovery": "not_attempted"})

    def test_account_dispatch_issues_no_mutation(self):
        _, intent = self.create_in_worktree_a()
        adapter, harness, recorder = self.recovering(listing=[terminal_element()])
        adapter.account_dispatch(intent["intent_id"])
        adapter.account_dispatch(intent["intent_id"])
        self.assertEqual(harness.lifecycle_commands(), [],
                         "accounting is read-only and safe to repeat")

    def test_the_scripted_observations_carry_only_what_the_runtime_really_returns(self):
        """The fixture cannot rescue the recovery by inventing a field."""
        self.assertEqual(set(WORKER_SHOW), {"dispatch", "worker", "terminalResource"})
        self.assertEqual(set(DISPATCH_SHOW), {"dispatch"})
        for payload in (WORKER_SHOW, DISPATCH_SHOW):
            flat = json.dumps(payload)
            for forbidden in ("role", "origin", "owner", "handle", "terminal_handle"):
                self.assertNotIn(f'"{forbidden}"', flat)

    def test_open_dispatches_reconstructs_from_disk_not_from_process_memory(self):
        _, intent = self.create_in_worktree_a()
        adapter, harness, recorder = self.recovering(listing=[terminal_element()])
        self.assertEqual(adapter.open_dispatches(), (intent["intent_id"],))
        self.assertEqual(adapter._receipts, {},  # noqa: SLF001
                         "process memory was never the source")


class OrcaVersionCompatibilityTests(unittest.TestCase):
    """V7 / OD-2: OS-31 changes neither the pinned version nor its refusal behaviour."""

    def test_the_supported_version_tuple_is_unchanged(self):
        from scripts.orca_runtime_harness import SUPPORTED_ORCA_APP_VERSIONS
        self.assertEqual(SUPPORTED_ORCA_APP_VERSIONS, ("1.4.196",))

    def test_any_other_version_is_still_refused(self):
        from scripts.orca_runtime_harness import (UnsupportedOrcaContract,
                                                  validate_orca_contract)
        with self.assertRaises(UnsupportedOrcaContract):
            validate_orca_contract("1.4.197", "", "")


def _intent():
    from scripts.deterministic_workflow.contracts import make_intent
    from scripts.deterministic_workflow.state import initial_state
    state = dict(initial_state(run_id=RUN, thread_id="t", phases=("ANALYSIS",),
                               capabilities=BASE_CAPABILITIES))
    intent = dict(make_intent(state, "WORKER", "PHASE_GATE"))
    intent["intent_id"] = INTENT
    return intent


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
