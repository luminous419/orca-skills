"""OS-37 V-13 / V-14.  The conformance record is read MECHANICALLY, not reviewed.

A conformance record nobody parses is a document that drifts from the code it describes.
These tests read `CONFORMANCE.md` against the merged baseline's own C1..C36 table and
against the repository, so a row that loses its obligation, a `reject` row that acquires an
implementation, or an unknown that quietly becomes a fact all fail here.

V-14 is the one that matters most: **U3, U4, U6, U7 and U8 must each carry an explicit
disposition** -- ``still-unknown``, ``routed-around-by-construction`` or
``resolved-with-new-evidence`` -- and every Orca claim must carry an "at this revision"
qualifier.  Upgrading an unknown to a fact is the failure mode the ticket names, and prose
is where it happens.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "docs" / "ORCA_RUNTIME_PRIMITIVES.md"
RECORD = REPO / "artifacts" / "runs" / "run_54d90086bd75" / "CONFORMANCE.md"
ENGINE = REPO / "scripts" / "deterministic_workflow"

#: The three dispositions an unknown may carry.  There is no fourth, and no silence.
#: `resolved-by-measurement` is DESIGN D13.5's fourth member, added at iteration 3.  It is
#: **narrower** than `resolved-with-new-evidence`, not a softer synonym: a row may carry it
#: only with the exact command and its output attached, and it is a claim about the two
#: builds installed on the host that measured it -- never about the CLIs in general.
#: §D6.1's preflight re-establishes each one on the operator's own host before any run.
DISPOSITIONS = ("still-unknown", "routed-around-by-construction",
                "resolved-with-new-evidence", "resolved-by-measurement")

#: The four rows DESIGN D13.5 authorises iteration 3 to move, and no others.  The list is
#: written out rather than derived, so a fifth row quietly acquiring a measurement fails.
MEASUREMENT_RESOLVED = ("G-1", "G-2", "G-5", "U-ENV-2")

#: The rows that MUST stay unknown.  The task boundary names them explicitly.
MUST_STAY_UNKNOWN = ("G-3", "G-4", "G-6", "G-7", "U-ENV-1")

#: The unknowns AC-37-23 names.  Each must appear with a disposition.
CARRIED_UNKNOWNS = ("U3", "U4", "U6", "U7", "U8")

_BASELINE_ROW = re.compile(
    r"^\| (C\d+) \| (.+?) \| (.+?) \| \*\*(reuse|adapt|reimplement|reject)\*\* \| ")
_RECORD_ROW = re.compile(
    r"^\| (C\d+) \| (.+?) \| \*\*(reuse|adapt|reimplement|reject)\*\* \| (.+?) \| (.+?) \|$")


def baseline_rows() -> dict[str, str]:
    """``{C-id: verdict}`` from the merged baseline -- the AUTHORITY for the verdicts."""
    rows: dict[str, str] = {}
    for line in BASELINE.read_text().splitlines():
        match = _BASELINE_ROW.match(line)
        if match:
            rows[match.group(1)] = match.group(4)
    return rows


def record_rows() -> dict[str, tuple[str, str, str]]:
    """``{C-id: (verdict, obligation, test)}`` from the conformance record."""
    rows: dict[str, tuple[str, str, str]] = {}
    for line in RECORD.read_text().splitlines():
        match = _RECORD_ROW.match(line)
        if match:
            rows[match.group(1)] = (match.group(3), match.group(4), match.group(5))
    return rows


class ConformanceRecordTests(unittest.TestCase):

    def setUp(self) -> None:
        self.assertTrue(RECORD.exists(),
                        f"{RECORD} is missing; AC-37-21's record is a deliverable")
        self.baseline = baseline_rows()
        self.record = record_rows()

    def test_the_record_covers_every_baseline_row(self) -> None:
        self.assertEqual(len(self.baseline), 36,
                         f"the baseline table has {len(self.baseline)} rows, not 36")
        self.assertEqual(
            sorted(self.record, key=lambda c: int(c[1:])),
            sorted(self.baseline, key=lambda c: int(c[1:])),
            "the conformance record's row set differs from the baseline's")

    def test_every_verdict_matches_the_baselines(self) -> None:
        """The record may not re-decide a verdict, only discharge it."""
        for cid, verdict in sorted(self.baseline.items()):
            with self.subTest(row=cid):
                self.assertEqual(
                    self.record[cid][0], verdict,
                    f"{cid}: the record says {self.record[cid][0]!r} but the merged "
                    f"baseline says {verdict!r}; the baseline is the authority")

    def test_every_reuse_adapt_row_has_an_obligation(self) -> None:
        """V-13, first half.  A row with no owning obligation is a gap wearing a verdict."""
        for cid, verdict in sorted(self.baseline.items()):
            if verdict == "reject":
                continue
            with self.subTest(row=cid):
                _v, obligation, test = self.record[cid]
                self.assertTrue(
                    obligation.strip() and obligation.strip() != "*no implementation*",
                    f"{cid} is {verdict!r} but names no discharging obligation")
                self.assertTrue(
                    test.strip(),
                    f"{cid} names no proving test; an obligation nobody tests is a claim")
                self.assertIn(
                    "`", obligation,
                    f"{cid}'s obligation names no symbol; it must point at real code")

    def test_every_reject_row_has_no_implementation(self) -> None:
        """V-13, second half.  A ``reject`` row must not acquire an implementation."""
        for cid, verdict in sorted(self.baseline.items()):
            if verdict != "reject":
                continue
            with self.subTest(row=cid):
                _v, obligation, _test = self.record[cid]
                self.assertIn(
                    "*no implementation*", obligation,
                    f"{cid} is a reject row but the record names an implementation for it")

    def test_the_rejected_capabilities_really_are_absent_from_the_code(self) -> None:
        """And not merely declared absent: the code is swept for each one.

        The record saying "rejected" is a claim; this is the check.  Each rejected
        capability has a token that would have to appear if it had been implemented.
        """
        tokens = {
            "C5": ("ConPTY", "conpty", "windows_pty"),
            "C19": ("renderer", "pane_agent_evidence"),
            "C33": ("orchestration task-list", "worker-start", "worker_done_cli"),
            "C34": ("relay", "mobile_session_layout", "desktop_surface"),
            "C35": ("electron",),
            "C36": ("node_pty", "node-pty", "xterm", "ssh2"),
        }
        for cid, needles in sorted(tokens.items()):
            for path in sorted(ENGINE.glob("standalone_*.py")):
                source = path.read_text()
                for needle in needles:
                    with self.subTest(row=cid, module=path.name, token=needle):
                        if needle == "xterm":
                            # `xterm-256color` is a TERM *type* the headless pty
                            # legitimately advertises, not the `@xterm/*` dependency.
                            self.assertNotIn(
                                "xterm.js", source,
                                f"{path.name} references the rejected xterm dependency")
                            continue
                        self.assertNotIn(
                            needle, source,
                            f"{path.name} implements rejected capability {cid} "
                            f"({needle!r})")

    def test_u3_u4_u6_u7_u8_not_upgraded(self) -> None:
        """V-14.  Every carried unknown appears WITH one of the three dispositions."""
        text = RECORD.read_text()
        for unknown in CARRIED_UNKNOWNS:
            with self.subTest(unknown=unknown):
                rows = [line for line in text.splitlines()
                        if line.startswith(f"| {unknown} |")]
                self.assertTrue(rows, f"{unknown} has no disposition row at all")
                row = rows[0]
                found = [d for d in DISPOSITIONS if d in row]
                self.assertTrue(
                    found,
                    f"{unknown}'s row carries no disposition; it must be one of "
                    f"{DISPOSITIONS}: {row}")
                self.assertNotIn(
                    "resolved-with-new-evidence", found,
                    f"{unknown} is marked resolved.  This ticket did not re-verify it, and "
                    "the task boundary forbids upgrading it to fact")

    def test_every_g_item_and_u_env_item_carries_a_disposition(self) -> None:
        text = RECORD.read_text()
        for item in [f"G-{n}" for n in range(1, 8)] + ["U-ENV-1", "U-ENV-2"]:
            with self.subTest(item=item):
                rows = [line for line in text.splitlines()
                        if line.startswith(f"| {item} |")]
                self.assertTrue(rows, f"{item} has no disposition row")
                self.assertTrue(any(d in rows[0] for d in DISPOSITIONS),
                                f"{item}'s row carries no disposition: {rows[0]}")
                if item in MUST_STAY_UNKNOWN:
                    # The task boundary names these five explicitly.  A measurement that
                    # happened to touch one of them does not license moving it: nothing in
                    # this iteration depends on resolving them, and an unknown resolved
                    # "while we were in there" is exactly the silent fact upgrade AC-37-23
                    # exists to prevent.
                    self.assertIn(
                        "still-unknown", rows[0],
                        f"{item} moved off `still-unknown`.  The task boundary keeps "
                        "G-3, G-4, G-6, G-7 and U-ENV-1 UNTOUCHED, and nothing in this "
                        "iteration depends on resolving them")
                if "resolved-by-measurement" in rows[0]:
                    self.assertIn(
                        item, MEASUREMENT_RESOLVED,
                        f"{item} is marked resolved-by-measurement, but DESIGN D13.5 "
                        f"authorises only {MEASUREMENT_RESOLVED} to move at this iteration")
                    self.assertIn(
                        "MEASURED", rows[0],
                        f"{item} claims a measurement without attaching one.  A "
                        "`resolved-by-measurement` row must carry the command and its "
                        "output, or it is an assertion wearing a measurement's name")

    def test_no_carried_unknown_is_resolved_by_measurement_either(self) -> None:
        """U3/U4/U6/U7/U8 stay exactly as they were, on EVERY disposition value.

        The iteration-3 vocabulary addition is the obvious way these could drift: a new
        member that reads like a weaker `resolved` is precisely what a later edit would
        reach for.  This case closes that door as well as the original one.
        """
        text = RECORD.read_text()
        for unknown in CARRIED_UNKNOWNS:
            with self.subTest(unknown=unknown):
                rows = [line for line in text.splitlines()
                        if line.startswith(f"| {unknown} |")]
                self.assertTrue(rows)
                self.assertNotIn("resolved-by-measurement", rows[0],
                                 f"{unknown} was upgraded by measurement; the task "
                                 "boundary forbids upgrading any of U3/U4/U6/U7/U8")
                self.assertNotIn("resolved-with-new-evidence", rows[0])

    def test_every_orca_claim_carries_an_at_this_revision_qualifier(self) -> None:
        """V-14's second half.  A claim about Orca must say WHEN it was true.

        Checked where it matters most -- the ``interrupt`` refusal, which is the one place
        this ticket asserts something about what Orca does not have.
        """
        source = (ENGINE / "orca_adapter.py").read_text()
        interrupt_body = source.split("def interrupt(", 1)[1].split("\n\ndef ", 1)[0]
        self.assertIn(
            "pinned revision", interrupt_body,
            "OrcaAdapter.interrupt asserts an Orca absence without qualifying the "
            "revision; U8 is unknown and 'absent at the pinned revision' is the only "
            "claim this ticket may make")
        self.assertIn(
            "U8", interrupt_body,
            "the docstring must name U8 explicitly rather than implying the verb never "
            "existed")
        self.assertIn("U8", RECORD.read_text())

    def test_the_record_names_every_amendment_rather_than_absorbing_it(self) -> None:
        """A deviation recorded nowhere is a deviation nobody reviewed."""
        text = RECORD.read_text()
        self.assertIn("### Explicit amendments", text)
        amendments = [line for line in text.splitlines() if line.startswith("| A-")]
        self.assertGreaterEqual(
            len(amendments), 9,
            "the design names at least nine amendments (D-1, the V-5 widening, the S3 "
            "narrowing, profile_readiness_unverified, CG-1, CG-2, NF-7, the Permit token "
            f"and C-DESIGN-1); the record lists {len(amendments)}")
        for required in ("S3", "profile_readiness_unverified", "NF-7", "Permit",
                         "C-DESIGN-1"):
            self.assertIn(required, text,
                          f"amendment {required!r} is not recorded")

    def test_a_check_that_cannot_run_is_recorded_as_not_established(self) -> None:
        """AC-37-24.  Never as a pass.

        Two rows must carry it: the live per-CLI half and the six-job CI matrix.  Both are
        genuinely unrunnable here -- the first needs a real agent CLI, the second needs CI --
        and recording either as "met" would be the exact dishonesty AC-37-24 forbids.
        """
        text = RECORD.read_text()
        self.assertIn("**not established**", text)
        rows = [line for line in text.splitlines() if "not established" in line]
        self.assertGreaterEqual(len(rows), 2, f"only {len(rows)} not-established rows")
        for row in rows:
            self.assertNotIn(
                "| met |", row,
                f"a row is both met and not established: {row}")

    def test_pr_1_is_named_and_not_claimed_closed(self) -> None:
        text = RECORD.read_text()
        self.assertIn("PR-1", text)
        pr1 = [line for line in text.splitlines() if line.startswith("| PR-1 |")]
        self.assertTrue(pr1, "PR-1 has no residual row")
        self.assertIn("Out of scope", pr1[0])
        self.assertIn("STANDALONE path only", pr1[0])

    def test_dr_7_and_dr_2_residuals_are_named(self) -> None:
        text = RECORD.read_text()
        for residual in ("DR-2", "DR-7"):
            self.assertTrue(
                any(line.startswith(f"| {residual} |") for line in text.splitlines()),
                f"{residual} has no residual row; the design names it as a cost")


if __name__ == "__main__":
    unittest.main()
