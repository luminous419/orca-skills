#!/usr/bin/env python3
"""Tests for scripts/review_isolation.py: D-G's mechanism and its negative contract.

T-8 is the mechanism as units. T-9 is the negative test itself, and every one of its
denial assertions is paired with a POSITIVE CONTROL in the same test method -- the
identical operation, run without the sandbox, which must succeed. A negative test that
passes because the probe used the wrong path proves nothing, and a control that lives in
a separate test can be skipped independently of the assertion it is controlling.

T-9's sandboxed half is darwin-only and is skipped with an explicit reason elsewhere.
T-8.9 -- `--enforcement seatbelt` on a host with no `sandbox-exec` exits 4 -- is what then
carries the fail-closed guarantee, so the guarantee is never merely unasserted.
"""

from __future__ import annotations

import ast
import dataclasses
import errno
import hashlib
import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

import review_isolation
from scripts import e2e_harness
from scripts import final_review_eval as evaluator
from scripts import run_logging

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "scripts" / "fixtures" / "final_review_eval"
KEY_PATH = FIXTURE / "key" / "answer_key.json"
EVAL_CLI = REPO_ROOT / "scripts" / "final_review_eval.py"

DARWIN_ONLY = unittest.skipUnless(
    sys.platform == "darwin",
    "the seatbelt backend is darwin-only; T-8.9 carries the fail-closed guarantee "
    "on every other platform",
)
NEEDS_SANDBOX = unittest.skipUnless(
    Path(review_isolation.SANDBOX_EXEC).exists(),
    f"{review_isolation.SANDBOX_EXEC} is not present on this host",
)

# Two Class IMM roots that prove in milliseconds. The point of a unit test is the
# MECHANISM; walking /System to re-derive what an integration run already established
# would add three minutes per test and prove nothing new.
FAST_IMM = ("/bin", "/sbin")


def _function_body_statements(function) -> list[str]:
    """The function's own statements, source order, docstring dropped.

    T-13.4' needs "the gate PRECEDES every other statement", which a substring search
    over the whole module cannot express -- a gate deleted from one function and left in
    a sibling would still match. Parsing the one function is what makes the assertion
    about placement rather than about presence.
    """
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    body = tree.body[0].body
    if (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return [ast.unparse(statement) for statement in body]


def run_cli(*argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(EVAL_CLI), *argv],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )


class _IsolationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.sessions: list[Path] = []

    def tearDown(self) -> None:
        for session in self.sessions:
            shutil.rmtree(session, ignore_errors=True)
        self.temporary.cleanup()

    def build(self, run_id: str = "run_t", **kwargs) -> Path:
        session = review_isolation.build_session(run_id, fixture=FIXTURE, **kwargs)
        self.sessions.append(session)
        return session

    def synthetic_fixture(self) -> Path:
        """A copy of the real fixture. Tests that PLANT a key never touch the real one."""
        destination = self.base / "fixture"
        shutil.copytree(str(FIXTURE), str(destination))
        return destination


class SessionLayoutTests(_IsolationTestCase):
    """T-8.1 .. T-8.3: the six rules of G.2."""

    def test_t81_the_session_has_the_designed_layout(self) -> None:
        session = self.build()

        for relative in (
            "review_root/subject/MANIFEST.json",
            "review_root/subject/DIFF.patch",
            "review_root/policy/REVIEW_COMMON.md",
            "review_root/artifacts/runs/run_t",
            "tmp",
            "home",
            "control/probes",
        ):
            self.assertTrue((session / relative).exists(), relative)

    def test_t81b_control_is_a_sibling_of_review_root_never_a_descendant(self) -> None:
        session = self.build()

        control = (session / "control").resolve()
        review_root = (session / "review_root").resolve()
        self.assertEqual(control.parent, review_root.parent)
        self.assertNotIn(review_root, control.parents)
        # control/ holds the profile, which NAMES the denied roots -- i.e. it contains the
        # repository path and hence the key's directory path. Inside review_root/ it would
        # hand the Reviewer the exact path NEG-1 exists to prove absent.

    def test_t82_a_session_inside_the_repository_is_refused_with_nothing_left(
        self,
    ) -> None:
        inside = REPO_ROOT / "artifacts" / "_isolation_probe"
        inside.mkdir(parents=True, exist_ok=True)
        try:
            with self.assertRaises(review_isolation.IsolationContractError):
                review_isolation.build_session(
                    "run_t", fixture=FIXTURE, session_base=inside
                )
            self.assertEqual(list(inside.iterdir()), [], "no session may be left behind")
        finally:
            shutil.rmtree(inside, ignore_errors=True)

    def test_t82b_the_cli_maps_that_to_exit_two(self) -> None:
        inside = REPO_ROOT / "artifacts" / "_isolation_probe_cli"
        inside.mkdir(parents=True, exist_ok=True)
        try:
            completed = run_cli(
                "isolate", "--run-id", "run_t", "--session-base", str(inside),
                "--enforcement", "none", "--no-plant",
            )
            self.assertEqual(completed.returncode, evaluator.EXIT_CONTRACT_VIOLATION)
        finally:
            shutil.rmtree(inside, ignore_errors=True)

    def test_t83_a_symlink_in_the_policy_copy_list_is_refused(self) -> None:
        link = REPO_ROOT / "artifacts" / "_isolation_policy_link.md"
        link.symlink_to(REPO_ROOT / "COMPATIBILITY.md")
        try:
            with self.assertRaises(review_isolation.IsolationError):
                review_isolation.build_session(
                    "run_t", fixture=FIXTURE,
                    policy_files=("artifacts/_isolation_policy_link.md",),
                )
        finally:
            link.unlink()

    def test_the_policy_copy_list_is_closed_and_has_no_glob(self) -> None:
        source = Path(review_isolation.__file__).read_text(encoding="utf-8")
        policy_section = source.split('policy.mkdir()')[1].split("# Rule 4")[0]
        self.assertIn("for relative in policy_files:", policy_section)
        for globbing in ("glob(", "rglob(", "iterdir("):
            self.assertNotIn(globbing, policy_section)
        self.assertEqual(
            review_isolation.DEFAULT_POLICY_FILES,
            ("orca-worker-reviewer-orchestration/reviews/common.md",),
        )


class ReadableSetScanTests(_IsolationTestCase):
    """T-8.4: passes A, B, C and D each catch a planted copy."""

    def setUp(self) -> None:
        super().setUp()
        self.key = review_isolation._load_key_with_source(FIXTURE)
        self.root = self.base / "usr_root"
        self.root.mkdir()

    def scan(self) -> list[dict]:
        return review_isolation.scan_readable_set(self.key, self.root)["hits"]

    def test_the_clean_root_is_clean(self) -> None:
        (self.root / "notes.txt").write_text("nothing here\n", encoding="utf-8")
        self.assertEqual(self.scan(), [])

    def test_t84_pass_a_catches_a_fixture_tree_by_name(self) -> None:
        tree = self.root / "somewhere"
        (tree / "subject").mkdir(parents=True)
        (tree / "key").mkdir()
        hits = self.scan()
        self.assertTrue([hit for hit in hits if hit["pass"] == "A"], hits)

    def test_t84_pass_b_catches_a_key_shingle(self) -> None:
        summary = self.key["seeded_defects"][0]["summary"]
        (self.root / "leaked.md").write_text(summary, encoding="utf-8")
        self.assertTrue([hit for hit in self.scan() if hit["pass"] == "B"])

    def test_t84_pass_c_catches_a_byte_identical_copy_under_another_name(self) -> None:
        (self.root / "harmless.json").write_bytes(KEY_PATH.read_bytes())
        hits = self.scan()
        self.assertTrue(
            [hit for hit in hits if hit["pass"] == "C"],
            "a RENAMED byte-identical copy is exactly what pass A cannot see",
        )

    def test_t84_pass_d_catches_a_packaged_copy_without_extracting_it(self) -> None:
        import tarfile

        archive = self.root / "release.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(str(KEY_PATH), arcname="pkg/key/answer_key.json")
        hits = [hit for hit in self.scan() if hit["pass"] == "D"]
        self.assertTrue(hits, "this is what catches dist/orca-skills-*.tar.gz")

    def test_t84_pass_d_treats_an_unenumerable_archive_as_a_hit(self) -> None:
        (self.root / "broken.zip").write_bytes(b"not a zip at all")
        self.assertTrue([hit for hit in self.scan() if hit["pass"] == "D"])

    def test_an_escaping_symlink_inside_a_usr_root_is_a_hit(self) -> None:
        (self.root / "escape").symlink_to(KEY_PATH)
        hits = self.scan()
        self.assertTrue(
            [hit for hit in hits if hit["pass"] == "S"],
            "for Class USR the scan IS the evidence, so a path the walk did not cover "
            "is a gap",
        )

    def test_an_escaping_symlink_is_not_a_hit_for_a_proven_immutable_root(self) -> None:
        (self.root / "escape").symlink_to(KEY_PATH)
        hits = review_isolation.scan_readable_set(
            self.key, self.root, passes=review_isolation.SCAN_PASSES_IMM
        )["hits"]
        self.assertEqual(
            [hit for hit in hits if hit["pass"] == "S"], [],
            "for Class IMM the PROFILE is the evidence: seatbelt evaluates the resolved "
            "target, so the link grants nothing the profile does not already grant",
        )
        self.assertEqual(
            [hit for hit in hits if hit["pass"] == "B"], [],
            "and refusing to follow the link costs no CONTENT coverage either: the walk "
            "never opens it, so mandatory pass B does not read the key through it",
        )
        self.assertIn("S", review_isolation.SCAN_PASSES_ALL)
        self.assertNotIn("S", review_isolation.SCAN_PASSES_IMM)

    def test_a_carved_out_subtree_is_not_scanned_because_it_is_not_readable(self) -> None:
        # The carve-outs are part of the readable set's DEFINITION. Scanning beneath one
        # is wrong in the loud direction: /System/Volumes/Data re-exposes the whole data
        # volume, so a rescan that ignored the carve-out would report every answer-key
        # copy on the machine as a hit while the sandboxed process can reach none of them.
        carved = self.root / "denied"
        carved.mkdir()
        (carved / "answer_key.json").write_bytes(KEY_PATH.read_bytes())
        # The key's PROSE as well as its filename, so this proves the carve-out prunes
        # mandatory pass B and not merely pass A. Pass B is driven by THIS walk rather
        # than delegated to `scan_leak()`, whose `rglob` has no exclusion parameter and
        # would therefore read beneath a boundary the profile denies.
        (carved / "notes.txt").write_text(
            self.key["seeded_defects"][0]["summary"], encoding="utf-8"
        )
        # The IMM pass set, because a carve-out only ever arises inside a Class IMM root.
        passes = review_isolation.SCAN_PASSES_IMM
        self.assertTrue(
            review_isolation.scan_readable_set(self.key, self.root, passes=passes)["hits"],
            "without the carve-out this is a hit",
        )
        self.assertEqual(
            review_isolation.scan_readable_set(
                self.key, self.root, passes=passes, carve_outs=[str(carved)]
            )["hits"],
            [],
        )

    # -- T-8.4b .. T-8.4f: the Class IMM pass set and its vocabulary (D-5.1) ----------

    def test_t84b_pass_c_survives_the_imm_pass_set(self) -> None:
        """A renamed byte-identical copy is a pass-C hit under `SCAN_PASSES_IMM`.

        The shipped `("A", "D")` set returns ZERO hits over the same root -- that is the
        gap D-5.1 closes. Mandatory pass B fires on this file too, and that is the
        CORRECT relationship rather than a defect: a byte-identical copy of the key
        contains all of the key's prose, so the two passes corroborate each other.
        Asserting "exactly one hit in total" would have been asserting that pass B does
        not work.
        """
        planted = self.root / "libfoo.dat"
        planted.write_bytes(KEY_PATH.read_bytes())

        hits = review_isolation.scan_readable_set(
            self.key, self.root, passes=review_isolation.SCAN_PASSES_IMM
        )["hits"]

        c_hits = [hit for hit in hits if hit["pass"] == "C"]
        self.assertEqual(len(c_hits), 1, c_hits)
        self.assertEqual(c_hits[0]["path"], str(review_isolation._realpath(planted)))
        self.assertTrue(
            [
                hit
                for hit in hits
                if hit["pass"] == "B"
                and hit["path"] == str(review_isolation._realpath(planted))
            ],
            "pass B corroborates pass C on the same path",
        )
        self.assertEqual(
            review_isolation.scan_readable_set(
                self.key, self.root, passes=("A", "D")
            )["hits"],
            [],
            "the shipped name-only pass set sees nothing here",
        )

    def test_t84c_the_pass_c_size_prefilter_is_an_equivalence(self) -> None:
        """Not an approximation: a file whose length differs cannot be byte-identical."""
        raw = KEY_PATH.read_bytes()
        identical = self.root / "identical.dat"
        identical.write_bytes(raw)
        same_length = self.root / "same_length.dat"
        same_length.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0x01]))
        one_longer = self.root / "one_longer.dat"
        one_longer.write_bytes(raw + b"\n")

        hits = review_isolation.scan_readable_set(
            self.key, self.root, passes=("C",)
        )["hits"]

        self.assertEqual(
            [hit["path"] for hit in hits],
            [str(review_isolation._realpath(identical))],
        )
        self.assertEqual(len(same_length.read_bytes()), len(raw), "hashed, not skipped")
        self.assertNotEqual(len(one_longer.read_bytes()), len(raw), "size-filtered out")

    def test_t84d_mandatory_pass_b_catches_what_a_c_d_cannot_see(self) -> None:
        """The DESIGN review's counterexample, reproduced and closed.

        A reformatted copy, a partial excerpt and a quoted fragment, each under an
        unrelated basename and none byte-identical to the key. Iteration 4's
        `("A", "C", "D")` pass set finds NOTHING in any of the three; the mandatory
        pass B of D-5.1 finds all three, because the key's own prose is what the Class
        IMM vocabulary is made of and the match happens after the same normalisation on
        both sides.
        """
        defect = self.key["seeded_defects"][0]
        reformatted = self.root / "vendor.dat"
        reformatted.write_text(
            json.dumps(self.key, indent=4, ensure_ascii=False), encoding="utf-8"
        )
        excerpt = self.root / "notes.log"
        excerpt.write_text(
            textwrap.fill(defect["negative_space_argument"], 40), encoding="utf-8"
        )
        quoted = self.root / "chat.txt"
        quoted.write_text(
            "unrelated prose above\n"
            + "\n".join("> " + line for line in textwrap.wrap(defect["summary"], 50))
            + "\nunrelated prose below\n",
            encoding="utf-8",
        )

        scan = review_isolation.scan_readable_set(
            self.key,
            self.root,
            passes=review_isolation.SCAN_PASSES_IMM,
            vocabulary="key_material",
        )
        b_paths = {hit["path"] for hit in scan["hits"] if hit["pass"] == "B"}
        for planted in (reformatted, excerpt, quoted):
            self.assertIn(
                str(review_isolation._realpath(planted)), b_paths, scan["hits"]
            )
        self.assertEqual(scan["content_scanned"], 3)

        self.assertEqual(
            review_isolation.scan_readable_set(
                self.key, self.root, passes=("A", "C", "D"), vocabulary="key_material"
            )["hits"],
            [],
            "iteration 4's pass set is exactly the gap the DESIGN review named",
        )

    def test_t84e_the_imm_vocabulary_is_specific_not_merely_smaller(self) -> None:
        """The measured `/usr` situation, reduced to a unit test.

        A contributor who "simplifies" the two vocabularies back into one fails here
        with the reason in front of them: these are the shapes that collide with vendor
        files on this host, and a hard-failure gate offers no remedy for them.
        """
        vendor = self.root / "vendor"
        vendor.mkdir()
        excluded = [
            marker
            for marker in evaluator.FIXED_LEAK_MARKERS
            if not evaluator._is_identifier_form(marker)
        ] + [str(entry["id"]) for entry in self.key["seeded_defects"]]
        (vendor / "collides.txt").write_text(
            " ".join(excluded) + "\n"
            # AVFoundation's AVContentKeySession.h, verbatim in shape.
            "a persistable content key cannot be used to answer key requests\n"
            # And a sentence the two expected-count heuristics recognise.
            "you should find three defects\n",
            encoding="utf-8",
        )

        self.assertEqual(
            review_isolation.scan_readable_set(
                self.key, vendor, passes=("B",), vocabulary="key_material"
            )["hits"],
            [],
        )
        self.assertTrue(
            review_isolation.scan_readable_set(
                self.key, vendor, passes=("B",), vocabulary="key_leak"
            )["hits"],
            "Class USR keeps the full vocabulary AND the count heuristics",
        )

        material = self.root / "material"
        material.mkdir()
        (material / "excerpt.txt").write_text(
            "archetype: " + str(self.key["seeded_defects"][0]["archetype"]) + "\n",
            encoding="utf-8",
        )
        for vocabulary in review_isolation.SCAN_VOCABULARIES:
            self.assertTrue(
                review_isolation.scan_readable_set(
                    self.key, material, passes=("B",), vocabulary=vocabulary
                )["hits"],
                f"an archetype is key MATERIAL under {vocabulary!r}",
            )

    def test_t84f_the_two_vocabularies_cannot_drift_apart(self) -> None:
        """Structural containment, computed rather than hard-coded to eleven strings."""
        leak = evaluator.key_leak_tokens(self.key)
        material = evaluator.key_material_tokens(self.key)

        self.assertLess(material, leak, "proper subset, structurally")
        self.assertEqual(
            leak - material,
            {
                marker.casefold()
                for marker in evaluator.FIXED_LEAK_MARKERS
                if not evaluator._is_identifier_form(marker)
            }
            | {
                str(entry["id"]).casefold()
                for entry in self.key["seeded_defects"]
                if entry.get("id")
            },
        )

        counted = self.root / "counted.txt"
        counted.write_text("you should find three defects\n", encoding="utf-8")
        text = counted.read_text(encoding="utf-8")
        self.assertTrue(
            [
                hit
                for hit in evaluator.scan_leak_text(
                    counted, text, material, count_heuristics=True
                )
                if "expected_count_statement" in hit
            ],
            "the heuristics are ON for the key_leak vocabulary",
        )
        self.assertEqual(
            evaluator.scan_leak_text(counted, text, material, count_heuristics=False),
            [],
            "and OFF for the key_material vocabulary",
        )

    def test_the_unknown_vocabulary_is_refused_rather_than_defaulted(self) -> None:
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.scan_readable_set(
                self.key, self.root, passes=("B",), vocabulary="whatever"
            )

    def test_a_usr_root_with_a_hit_is_never_admitted(self) -> None:
        session = self.build()
        (session / "tmp" / "planted.json").write_bytes(KEY_PATH.read_bytes())
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.compute_readable_set(
                session, self.key, imm_candidates=FAST_IMM
            )


class ImmutabilityProofTests(_IsolationTestCase):
    """T-8.5: the proof rejects what the superseded root-only W_OK rule admitted."""

    def test_t85_a_writable_descendant_at_any_depth_rejects_the_root(self) -> None:
        # The F-001 SHAPE, exactly: a root whose own W_OK is false but which contains a
        # writable descendant. `/private/var` was that root and `tempfile.gettempdir()`
        # was that descendant.
        root = self.base / "imm"
        deep = root / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "writable.txt").write_text("x", encoding="utf-8")
        for directory in (root, root / "a", root / "a" / "b", deep):
            os.chmod(directory, 0o555)
        try:
            self.assertFalse(
                os.access(root, os.W_OK),
                "the root itself must be non-writable, or this is not the F-001 shape",
            )
            proof = review_isolation.prove_immutable(root)
            self.assertFalse(proof["passed"])
            self.assertEqual(proof["writable_files"], 1)
            self.assertEqual(
                [failure["check"] for failure in proof["failures"]], ["I-3"]
            )
        finally:
            for directory in (root, root / "a", root / "a" / "b", deep):
                os.chmod(directory, 0o755)

    def test_t85b_a_writable_directory_at_any_depth_rejects_the_root(self) -> None:
        root = self.base / "imm2"
        (root / "a").mkdir(parents=True)
        os.chmod(root, 0o555)
        try:
            proof = review_isolation.prove_immutable(root)
            self.assertFalse(proof["passed"])
            self.assertEqual(proof["writable_dirs"], 1)
        finally:
            os.chmod(root, 0o755)

    def test_a_genuinely_immutable_tree_passes(self) -> None:
        root = self.base / "imm3"
        (root / "a").mkdir(parents=True)
        (root / "a" / "f.txt").write_text("x", encoding="utf-8")
        os.chmod(root / "a" / "f.txt", 0o444)
        os.chmod(root / "a", 0o555)
        os.chmod(root, 0o555)
        try:
            proof = review_isolation.prove_immutable(root)
            self.assertTrue(proof["passed"], proof["failures"])
            self.assertEqual(proof["writable_dirs"], 0)
            self.assertEqual(proof["writable_files"], 0)
        finally:
            for path in (root, root / "a"):
                os.chmod(path, 0o755)

    def test_a_failing_root_is_never_admitted_as_imm(self) -> None:
        root = self.base / "imm4"
        root.mkdir()
        key = review_isolation._load_key_with_source(FIXTURE)
        session = self.build()
        with self.assertRaises(review_isolation.IsolationError) as caught:
            review_isolation.compute_readable_set(
                session, key, imm_candidates=(str(root),)
            )
        self.assertIn("immutability proof FAILED", str(caught.exception))

    def test_narrowing_carves_out_what_it_cannot_certify_and_never_what_is_mutable(
        self,
    ) -> None:
        root = self.base / "imm5"
        opaque = root / "opaque"
        opaque.mkdir(parents=True)
        (opaque / "hidden.txt").write_text("x", encoding="utf-8")
        mutable = root / "mutable"
        mutable.mkdir()
        os.chmod(opaque, 0o000)
        os.chmod(root, 0o555)
        try:
            proof, carved = review_isolation.prove_immutable_narrowing(root)
            # I-1 (cannot enumerate) is narrowable -- denying it is strictly safer.
            self.assertIn(str(opaque.resolve()), carved)
            # I-2 (certified writable) is NOT: carving it would widen the proof until it
            # passed while the profile kept allowing the parent. The root is dropped.
            self.assertFalse(proof["passed"])
            self.assertNotIn(str(mutable.resolve()), carved)
        finally:
            os.chmod(opaque, 0o755)
            os.chmod(root, 0o755)

    def test_t85c_the_invariant_and_the_carve_out_denials_are_asserted_by_name(
        self,
    ) -> None:
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.assert_carve_outs_denied(["/usr/local"], [])
        review_isolation.assert_carve_outs_denied(["/usr/local"], ["/usr/local", "/x"])

        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.assert_no_unscanned_descendant(
                [{"class": "USR", "path": "/x", "scanned": False}]
            )
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.assert_no_unscanned_descendant(
                [{"class": "IMM", "path": "/x",
                  "proof": {"passed": True, "writable_dirs": 1, "writable_files": 0}}]
            )
        with self.assertRaises(review_isolation.IsolationError):
            # The retired iteration-1 class name must fail loudly, not look valid.
            review_isolation.assert_no_unscanned_descendant(
                [{"class": "SYS", "path": "/x", "scanned": False}]
            )

    def test_t99_the_superseded_root_only_rule_is_rejected(self) -> None:
        """T-9.9 -- the regression guard for F-001 itself.

        The iteration-1 rule was *"admit a Class SYS root when `os.access(root, W_OK)` is
        False"*. This asserts that the shape that rule ADMITTED is now REJECTED, without
        depending on a plant surviving on disk. It is the test that fails if someone
        reintroduces the shortcut.
        """
        root = self.base / "f001"
        writable_descendant = root / "T"
        writable_descendant.mkdir(parents=True)
        os.chmod(root, 0o555)
        try:
            # The superseded rule's verdict: admit.
            self.assertFalse(os.access(root, os.W_OK))
            # The current rule's verdict: reject, and reject for the right reason.
            proof = review_isolation.prove_immutable(root)
            self.assertFalse(
                proof["passed"],
                "prove_immutable() must reject a root whose W_OK is False but which "
                "contains a writable descendant -- that is precisely F-001",
            )
            self.assertGreaterEqual(proof["writable_dirs"], 1)
            with self.assertRaises(review_isolation.IsolationError):
                review_isolation.assert_no_unscanned_descendant(
                    [{"class": "IMM", "path": str(root), "scanned": False,
                      "proof": proof}]
                )
        finally:
            os.chmod(root, 0o755)

    def test_private_var_and_library_are_not_admissible_by_habit(self) -> None:
        for forbidden in ("/private/var", "/Library"):
            self.assertIn(forbidden, review_isolation.NEVER_ADMITTED)
            self.assertNotIn(forbidden, review_isolation.DEFAULT_IMM_CANDIDATES)
        key = review_isolation._load_key_with_source(FIXTURE)
        session = self.build()
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.compute_readable_set(
                session, key, imm_candidates=("/private/var",)
            )


@DARWIN_ONLY
class BoundaryEnumerationTests(_IsolationTestCase):
    """T-8.5b: boundaries are found by the flag walk and explained by two authorities."""

    def test_the_two_authorities_are_readable_and_agree_about_the_known_set(self) -> None:
        authorities = review_isolation.boundary_authorities()
        self.assertIn("/System/Volumes/Data", authorities["mount_table"])
        self.assertIn("/usr/local", authorities["firmlinks"])

    def test_usr_boundaries_are_exactly_the_firmlinked_ones(self) -> None:
        found = review_isolation.enumerate_boundaries(Path("/usr"))
        self.assertEqual(found["unexplained"], [], "an unexplained boundary is fatal")
        self.assertIn("/usr/local", found["boundaries"])

    def test_an_unexplained_boundary_is_a_hard_failure(self) -> None:
        key = review_isolation._load_key_with_source(FIXTURE)
        session = self.build()
        with self.assertRaises(review_isolation.IsolationError) as caught:
            review_isolation.compute_readable_set(
                session, key, imm_candidates=("/usr",),
                authorities={"mount_table": (), "firmlinks": ()},
            )
        self.assertIn("named by neither", str(caught.exception))

    def test_system_volumes_is_carved_out_whether_or_not_the_walk_finds_it(self) -> None:
        self.assertIn("/System/Volumes", review_isolation.MANDATORY_CARVE_OUTS)
        # /System/Volumes/Data is a MOUNT POINT, not a symlink, so realpath() does not
        # collapse it and (subpath "/System") would otherwise reach the whole data volume
        # -- including the repository and the answer key -- with nothing planted at all.
        self.assertFalse(Path("/System/Volumes/Data").is_symlink())
        self.assertEqual(
            os.stat("/System/Volumes/Data" + str(REPO_ROOT)).st_ino,
            os.stat(str(REPO_ROOT)).st_ino,
            "the alias must actually reach the repository, or this carve-out is theatre",
        )


class ProfileRenderingTests(_IsolationTestCase):
    """T-8.6: the six clauses, in order, with a closed metadata surface."""

    def profile(self, **overrides) -> str:
        arguments = {
            "session": Path("/session"),
            "imm": ["/bin"],
            "usr": ["/session/review_root"],
            "carve_outs": ["/usr/local"],
            "traversal": ["/", "/usr"],
            "writable": ["/session/review_root"],
            "denied": ["/repo"],
        }
        arguments.update(overrides)
        return review_isolation.render_seatbelt_profile(**arguments)

    def test_t86_the_clauses_appear_in_the_designed_order(self) -> None:
        text = self.profile()
        order = [
            "(deny file-read*)",
            "(allow file-read-metadata",
            "(allow file-read*",
            "(deny file-read* file-read-metadata\n",
            "(deny file-write*)",
            "(deny file-read* file-read-metadata file-write*",
        ]
        positions = [text.index(marker) for marker in order]
        self.assertEqual(
            positions, sorted(positions),
            "seatbelt is last-match-wins, so the order IS the semantics",
        )

    def test_t86b_the_metadata_surface_is_a_closed_set_not_a_global_allow(self) -> None:
        text = self.profile()
        # A generated profile containing a bare `(allow file-read-metadata)` with no
        # operand list is RK-9 realised: measured, it makes os.path.exists() on a planted
        # key copy return True and os.stat().st_size return the key's real size.
        clauses = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith(";;")
        )
        self.assertNotIn("(allow file-read-metadata)", clauses)
        self.assertIn('(allow file-read-metadata\n    (literal "/")', text)

    def test_t86c_the_root_literal_is_present_and_is_never_a_subpath(self) -> None:
        text = self.profile()
        self.assertIn('(literal "/")', text)
        self.assertNotIn('(subpath "/")', text)

    def test_t86d_clause_six_denies_metadata_as_well_as_data(self) -> None:
        text = self.profile()
        tail = text[text.index("(deny file-write*)"):]
        self.assertIn('(deny file-read-metadata\n    (subpath "/repo")', tail)
        # Without this line os.stat() on the key SUCCEEDS -- measured. It is not
        # redundant with clause 1 and must not be dropped as such.

    def test_t86e_every_carve_out_is_denied(self) -> None:
        text = self.profile(carve_outs=["/usr/local", "/System/Volumes"])
        for carve_out in ("/usr/local", "/System/Volumes"):
            self.assertIn(f'(subpath "{carve_out}")', text)

    @NEEDS_SANDBOX
    def test_t86f_a_generated_profile_actually_parses(self) -> None:
        session = self.build()
        key = review_isolation._load_key_with_source(FIXTURE)
        readable = review_isolation.compute_readable_set(
            session, key, imm_candidates=FAST_IMM
        )
        text = review_isolation.render_seatbelt_profile(
            session=session,
            imm=[e["path"] for e in readable["entries"] if e["class"] == "IMM"],
            usr=[e["path"] for e in readable["entries"] if e["class"] == "USR"],
            carve_outs=readable["carve_outs"],
            traversal=review_isolation.compute_traversal_set(
                [e["path"] for e in readable["entries"]], readable["carve_outs"]
            ),
            writable=[str(session / "review_root")],
            denied=review_isolation.discover_key_bearing_roots(FIXTURE),
        )
        path = session / "control" / review_isolation.PROFILE_FILENAME
        path.write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [review_isolation.SANDBOX_EXEC, "-f", str(path), "/usr/bin/true"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)


class TraversalSetTests(_IsolationTestCase):
    """G.4 clause 2: the metadata surface can never exceed the data surface."""

    def test_the_traversal_set_is_ancestors_only(self) -> None:
        traversal = review_isolation.compute_traversal_set(
            ["/usr", "/private/etc"], ["/usr/local"]
        )
        for expected in ("/", "/private"):
            self.assertIn(expected, traversal)
        self.assertNotIn("/usr", traversal, "an admitted root is not also traversal-only")
        self.assertNotIn("/Users", traversal)

    def test_carve_out_ancestors_are_traversable_so_the_deny_can_be_reached(self) -> None:
        traversal = review_isolation.compute_traversal_set(
            ["/System"], ["/System/Library/Caches"]
        )
        self.assertIn("/System/Library", traversal)


class AttestationTests(_IsolationTestCase):
    """T-8.7: what the document may and may not say."""

    def document(self, **overrides) -> dict:
        session = self.build()
        arguments = {
            "run_id": "run_t", "attempt": 1, "terminal": "term_x", "session": session,
            "enforcement": "seatbelt",
            "readable": {
                "entries": [
                    {"class": "IMM", "path": "/bin", "scanned": False,
                     "proof": {"passed": True, "writable_dirs": 0, "writable_files": 0}},
                    {"class": "USR", "path": str(session / "review_root"),
                     "scanned": True, "scan": {"files": 1, "archives": 0, "hits": 0}},
                ],
                "carve_outs": ["/usr/local"],
            },
            "traversal": ["/"], "writable": [str(session / "review_root")],
            "denied": [str(REPO_ROOT)], "profile_digest": "sha256:x",
            "probes": [
                {"id": identifier, "result": "PASS"}
                for identifier in ("NEG-0", "NEG-1", "NEG-2", "NEG-3", "NEG-4",
                                   "NEG-5", "NEG-6", "NEG-7", "NEG-8")
            ],
        }
        arguments.update(overrides)
        return review_isolation.build_attestation(**arguments)

    def test_t87_the_attestation_carries_no_clock_value(self) -> None:
        document = self.document()
        review_isolation.assert_no_clock_value(document)
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.assert_no_clock_value({"generated_at": "2026-01-01"})

    def test_t87b_every_path_field_satisfies_p_path(self) -> None:
        document = self.document()
        for field in ("session_root", "review_root"):
            run_logging_check(self, document[field])
        for entry in document["writable_set"] + document["denied_roots"]:
            run_logging_check(self, entry)

    def test_t87c_scanned_false_requires_imm_with_a_zero_writable_proof(self) -> None:
        with self.assertRaises(review_isolation.IsolationError):
            self.document(readable={
                "entries": [{"class": "IMM", "path": "/bin", "scanned": False,
                             "proof": {"passed": True, "writable_dirs": 1,
                                       "writable_files": 0}}],
                "carve_outs": [],
            })
        with self.assertRaises(review_isolation.IsolationError):
            self.document(readable={
                "entries": [{"class": "USR", "path": "/bin", "scanned": False}],
                "carve_outs": [],
            })

    def test_t87d_the_three_properties_are_independent_verdicts(self) -> None:
        document = self.document()
        self.assertEqual(document["properties"], {"S1": "PASS", "S2": "PASS", "S3": "PASS"})
        self.assertNotIn("isolated", document)
        # A single aggregate boolean invites reading a partial result as a whole one.

        partial = self.document(probes=[
            {"id": "NEG-0", "result": "PASS"}, {"id": "NEG-1", "result": "PASS"},
            {"id": "NEG-2", "result": "FAIL"}, {"id": "NEG-3", "result": "PASS"},
            {"id": "NEG-4", "result": "PASS"}, {"id": "NEG-5", "result": "PASS"},
            {"id": "NEG-6", "result": "PASS"}, {"id": "NEG-7", "result": "PASS"},
            {"id": "NEG-8", "result": "PASS"},
        ])
        self.assertEqual(partial["properties"]["S1"], "PASS")
        self.assertEqual(partial["properties"]["S2"], "FAIL")
        self.assertEqual(partial["properties"]["S3"], "PASS")

    def test_t87e_the_document_names_no_unscanned_descendant_and_the_limitation(
        self,
    ) -> None:
        document = self.document()
        self.assertEqual(document["no_unscanned_descendant"], "PASS")
        self.assertEqual(len(document["limitations"]), 1)
        self.assertIn("privileged (root) writer", document["limitations"][0])
        self.assertIn("not a sandbox-escaping adversary", document["threat_model"])

    def test_the_retired_class_name_is_gone_from_the_module(self) -> None:
        self.assertEqual(review_isolation.ADMISSION_CLASSES, ("IMM", "USR"))
        source = Path(review_isolation.__file__).read_text(encoding="utf-8")
        self.assertNotIn('"SYS"', source)


def run_logging_check(case: unittest.TestCase, value: str) -> None:
    import run_logging

    try:
        run_logging.assert_retained_path_field(value)
    except run_logging.RunLoggingError as error:      # pragma: no cover - failure path
        case.fail(f"P-PATH violation: {error}")


class UnenforcedTests(_IsolationTestCase):
    """T-8.8 / T-8.9: unenforced is a distinct, loud state -- and absent is fatal."""

    def test_t88_enforcement_none_records_unenforced_and_fails_s2(self) -> None:
        completed = run_cli(
            "isolate", "--run-id", "run_t", "--enforcement", "none", "--no-plant",
            "--session-base", str(self.base),
        )
        self.assertEqual(completed.returncode, evaluator.EXIT_OK, completed.stderr)
        self.assertIn("WARNING", completed.stderr)
        result = json.loads(completed.stdout)
        self.sessions.append(Path(result["session"]))
        self.assertEqual(result["scope_enforcement"], "unenforced")
        self.assertEqual(result["properties"]["S2"], "FAIL")
        attestation = json.loads(
            Path(result["attestation"]).read_text(encoding="utf-8")
        )
        for probe in attestation["probes"]:
            if probe["id"] in ("NEG-0", "NEG-1"):
                continue
            # Not SKIP. A skip reads like an absence of EVIDENCE; this reads like what it
            # is -- an absence of ENFORCEMENT.
            self.assertEqual(probe["result"], "NOT_APPLICABLE_UNENFORCED")
            self.assertNotEqual(probe["result"], "SKIP")

    def test_t89_seatbelt_on_a_host_without_the_backend_exits_four(self) -> None:
        original = review_isolation.SANDBOX_EXEC
        review_isolation.SANDBOX_EXEC = str(self.base / "no-such-sandbox-exec")
        try:
            with self.assertRaises(review_isolation.IsolationError) as caught:
                review_isolation.isolate(
                    "run_t", fixture=FIXTURE, session_base=self.base,
                    enforcement="seatbelt", plant=False,
                )
            self.assertIn("FAILS B6", str(caught.exception))
        finally:
            review_isolation.SANDBOX_EXEC = original

    def test_the_backend_choice_is_a_closed_enum(self) -> None:
        with self.assertRaises(review_isolation.IsolationContractError):
            review_isolation.isolate(
                "run_t", fixture=FIXTURE, session_base=self.base,
                enforcement="whatever", plant=False,
            )


class RepatriationAndTeardownTests(_IsolationTestCase):
    """T-8.10 / T-8.11."""

    def prepared(self) -> Path:
        session = self.build()
        (session / "review_root" / "artifacts" / "runs" / "run_t"
         / "FINAL_REVIEW.md").write_text("RESULT: PASS\n", encoding="utf-8")
        (session / "control" / review_isolation.ISOLATION_FILENAME).write_text(
            "{}\n", encoding="utf-8"
        )
        return session

    def test_the_report_the_attestation_and_the_workspace_all_come_back(self) -> None:
        session = self.prepared()
        result = review_isolation.repatriate(session, "run_t", base=self.base)
        root = self.base / "artifacts" / "runs" / "run_t"
        self.assertEqual(
            (root / "FINAL_REVIEW.md").read_text(encoding="utf-8"), "RESULT: PASS\n"
        )
        self.assertTrue((root / "FINAL_REVIEW_ISOLATION.json").is_file())
        # B5 survives because --workspace points at a LIVE path, not a deleted session.
        self.assertTrue(
            (root / "final_review_workspace" / "MANIFEST.json").is_file()
        )
        self.assertEqual(
            result["report_digest"],
            review_isolation.sha256_path(root / "FINAL_REVIEW.md"),
        )

    def test_t810_an_existing_differing_destination_is_refused(self) -> None:
        session = self.prepared()
        root = self.base / "artifacts" / "runs" / "run_t"
        root.mkdir(parents=True)
        (root / "FINAL_REVIEW.md").write_text("a different report\n", encoding="utf-8")
        with self.assertRaises(review_isolation.IsolationContractError):
            review_isolation.repatriate(session, "run_t", base=self.base)
        self.assertEqual(
            (root / "FINAL_REVIEW.md").read_text(encoding="utf-8"),
            "a different report\n",
            "a retry never overwrites the predecessor's evidence",
        )

    def test_a_missing_report_is_refused_rather_than_invented(self) -> None:
        session = self.build()
        with self.assertRaises(review_isolation.IsolationContractError):
            review_isolation.repatriate(session, "run_t", base=self.base)

    def test_t811_teardown_refuses_anything_that_is_not_one_of_our_sessions(self) -> None:
        stranger = self.base / "not_a_session"
        stranger.mkdir()
        with self.assertRaises(review_isolation.IsolationContractError):
            review_isolation.teardown(stranger)
        self.assertTrue(stranger.is_dir(), "a mistyped argument must delete nothing")

        half = self.base / f"{review_isolation.SESSION_PREFIX}half"
        half.mkdir()
        with self.assertRaises(review_isolation.IsolationContractError):
            review_isolation.teardown(half)
        self.assertTrue(half.is_dir())

    def test_teardown_removes_a_completed_session(self) -> None:
        session = self.prepared()
        review_isolation.teardown(session)
        self.assertFalse(session.exists())


class LaunchLineTests(_IsolationTestCase):
    """G.5: the one thing the negative test must not re-implement."""

    def test_the_launch_line_cds_before_it_execs(self) -> None:
        session = self.build()
        line = review_isolation.wrap_command(session, "AGENT")
        self.assertLess(line.index("cd "), line.index("exec "))
        # git inside the sandbox fails with "Unable to read current working directory"
        # when cwd is the denied repository, so the cd must precede the exec.

    def test_the_launch_line_sets_session_scoped_tmpdir_and_home(self) -> None:
        session = self.build()
        line = review_isolation.wrap_command(session, "AGENT")
        self.assertIn(f"TMPDIR={session / 'tmp'}", line)
        self.assertIn(f"HOME={session / 'home'}", line)
        self.assertIn(review_isolation.SANDBOX_EXEC, line)
        self.assertTrue(line.rstrip().endswith("AGENT"),
                        "isolation WRAPS the resolved agent command; it never rewrites it")


@DARWIN_ONLY
@NEEDS_SANDBOX
class Neg5ContractTests(_IsolationTestCase):
    """T-9.5: the NEG-5 contract, asserted at the probe record itself.

    Runs against the real fixture with a deliberately small IMM candidate set: what is
    under test is the per-class pass/vocabulary SELECTION and the record it writes, not
    how long a scan of `/System` takes.
    """

    def neg5_record(self) -> dict:
        session = review_isolation.build_session(
            "run_neg5", fixture=FIXTURE, session_base=self.base
        )
        self.sessions.append(session)
        key = review_isolation._load_key_with_source(FIXTURE)
        readable = review_isolation.compute_readable_set(
            session, key, imm_candidates=FAST_IMM
        )
        denied = review_isolation.discover_key_bearing_roots(FIXTURE)
        traversal = review_isolation.compute_traversal_set(
            [entry["path"] for entry in readable["entries"]], readable["carve_outs"]
        )
        profile = review_isolation.render_seatbelt_profile(
            session=session,
            imm=[e["path"] for e in readable["entries"] if e["class"] == "IMM"],
            usr=[e["path"] for e in readable["entries"] if e["class"] == "USR"],
            carve_outs=readable["carve_outs"],
            traversal=traversal,
            writable=[str(session / "review_root"), str(session / "tmp"),
                      str(session / "home")],
            denied=denied,
        )
        (session / "control" / review_isolation.PROFILE_FILENAME).write_text(
            profile, encoding="utf-8"
        )
        probes = review_isolation.run_probes(
            session, fixture=FIXTURE, readable=readable, denied=denied,
            enforcement="seatbelt", plant=False,
        )
        return next(probe for probe in probes if probe["id"] == "NEG-5")

    def test_t95_every_admitted_root_carries_its_class_pass_set_and_vocabulary(
        self,
    ) -> None:
        record = self.neg5_record()

        self.assertTrue(record["roots"], record)
        classes = {entry["class"] for entry in record["roots"]}
        self.assertIn("IMM", classes, "the selection is untested without an IMM root")
        self.assertIn("USR", classes)
        for entry in record["roots"]:
            with self.subTest(path=entry["path"]):
                if entry["class"] == "IMM":
                    self.assertEqual(entry["passes"], ["A", "B", "C", "D"])
                    self.assertEqual(entry["vocabulary"], "key_material")
                else:
                    self.assertEqual(entry["passes"], ["A", "B", "C", "D", "S"])
                    self.assertEqual(entry["vocabulary"], "key_leak")
                self.assertIsInstance(entry["content_scanned"], int)

    def test_t95_there_is_no_opt_in_imm_content_scan_anywhere(self) -> None:
        """The regression guard against reintroducing a default-off content gate.

        A content-cleanliness gate the DEFAULT capture does not run is not a gate, and
        the section 7 baseline is taken with the default.
        """
        record = self.neg5_record()
        self.assertNotIn("imm_content_scan", json.dumps(record))
        self.assertFalse(hasattr(review_isolation, "SCAN_PASSES_IMM_CONTENT"))

        parser = evaluator.build_parser()
        formatted = parser.format_help()
        for action in parser._subparsers._group_actions:            # noqa: SLF001
            formatted += action.choices["isolate"].format_help()
        self.assertNotIn("--scan-imm-content", formatted)
        self.assertNotIn("scan_imm_content", formatted)


@DARWIN_ONLY
@NEEDS_SANDBOX
class NegativeContractTests(_IsolationTestCase):
    """T-9: NEG-0 .. NEG-8, each denial paired with its own positive control.

    These build a real session against a SYNTHETIC copy of the fixture and run real
    sandboxed processes through the real `wrap_command()`.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls._shared = tempfile.TemporaryDirectory()
        shared = Path(cls._shared.name)
        cls.fixture = shared / "fixture"
        shutil.copytree(str(FIXTURE), str(cls.fixture))
        cls.session = review_isolation.build_session(
            "run_neg", fixture=cls.fixture, session_base=shared
        )
        key = review_isolation._load_key_with_source(cls.fixture)
        readable = review_isolation.compute_readable_set(
            cls.session, key, imm_candidates=review_isolation.DEFAULT_IMM_CANDIDATES
        )
        cls.readable = readable
        cls.denied = review_isolation.discover_key_bearing_roots(cls.fixture)
        traversal = review_isolation.compute_traversal_set(
            [e["path"] for e in readable["entries"]], readable["carve_outs"]
        )
        text = review_isolation.render_seatbelt_profile(
            session=cls.session,
            imm=[e["path"] for e in readable["entries"] if e["class"] == "IMM"],
            usr=[e["path"] for e in readable["entries"] if e["class"] == "USR"],
            carve_outs=readable["carve_outs"],
            traversal=traversal,
            writable=[str(cls.session / "review_root"), str(cls.session / "tmp"),
                      str(cls.session / "home")],
            denied=cls.denied,
        )
        (cls.session / "control" / review_isolation.PROFILE_FILENAME).write_text(
            text, encoding="utf-8"
        )
        cls.key_path = (cls.fixture / "key" / "answer_key.json").resolve()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._shared.cleanup()

    def targets(self) -> dict[str, str]:
        return {"key": str(self.key_path)}

    def control(self) -> dict:
        """NEG-0. Runs FIRST inside every method below, never as a separate test."""
        probes = review_isolation._run_probe(None, self.targets(), sandboxed=False)
        expected = hashlib.sha256(self.key_path.read_bytes()).hexdigest()
        self.assertEqual(
            probes["key.open"]["value"], expected,
            "NEG-0 positive control: the unsandboxed probe MUST read the key, or every "
            "denial below is meaningless",
        )
        self.assertTrue(probes["key.exists"]["value"])
        return probes

    def test_neg2_the_sandboxed_process_cannot_open_the_key(self) -> None:
        self.control()
        probes = review_isolation._run_probe(
            self.session, self.targets(), sandboxed=True
        )
        self.assertFalse(probes["key.open"]["ok"], probes["key.open"])

    def test_neg3_discovery_is_blocked_not_merely_reading(self) -> None:
        self.control()
        probes = review_isolation._run_probe(
            self.session, self.targets(), sandboxed=True
        )
        self.assertIs(probes["key.exists"]["value"], False)
        self.assertFalse(probes["key.stat"]["ok"])
        self.assertFalse(probes["key.listdir"]["ok"])

    def test_neg4_git_cannot_reach_the_key_either(self) -> None:
        self.control()
        relative = "scripts/fixtures/final_review_eval/key/answer_key.json"
        result = review_isolation._command_probe(
            self.session, f"git -C {REPO_ROOT} show HEAD:{relative}"
        )
        self.assertFalse(result["discovered"])
        self.assertFalse(result["leaked_key_content"])

    def test_neg6_the_profile_parses_and_matches_its_digest(self) -> None:
        path = self.session / "control" / review_isolation.PROFILE_FILENAME
        completed = subprocess.run(
            [review_isolation.SANDBOX_EXEC, "-f", str(path), "/usr/bin/true"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            review_isolation.sha256_path(path),
            review_isolation.sha256_bytes(path.read_bytes()),
        )

    def test_the_sandboxed_process_can_still_do_its_job(self) -> None:
        """The other half of the guarantee: a profile that breaks the agent is not a fix.

        RK-4 -- a too-tight profile produces a WORSE review that could be mistaken for a
        detection signal. So the same launch line must still read the subject tree.
        """
        result = review_isolation._command_probe(self.session, "/bin/ls subject")
        self.assertTrue(result["discovered"], "review_root must remain readable")

    def test_f402_the_generated_profile_actually_permits_a_write(self) -> None:
        """F-402, asserted against an ACTUAL generated profile rather than a literal.

        `tempfile.mkdtemp()` hands back `/var/folders/...` on darwin while
        `compute_readable_set()` resolves every Class USR root to
        `/private/var/folders/...`, so one generated profile's read clause and its write
        clause named the same three directories two different ways -- and seatbelt, which
        matches on the RESOLVED path, denied every write the session was supposed to
        allow. An assertion over the profile TEXT would have passed against exactly that
        profile, which is why this one runs the real launch line and looks at the
        filesystem afterwards.
        """
        self.control()
        for relative in ("review_root/probe.txt", "tmp/probe.txt", "home/probe.txt"):
            with self.subTest(relative):
                target = self.session / relative
                if target.exists():
                    target.unlink()
                completed = subprocess.run(
                    ["/bin/sh", "-c", review_isolation.wrap_command(
                        self.session, f"/usr/bin/touch {target}"
                    )],
                    capture_output=True, text=True, check=False, timeout=120,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(target.is_file(),
                                "the writable set must actually be writable")
                target.unlink()

    def test_f402b_every_writable_root_is_denied_outside_the_session(self) -> None:
        """The other half: the corrected spelling must not have widened anything."""
        self.control()
        outside = Path(os.path.realpath(tempfile.gettempdir())) / "frv_f402_probe.txt"
        completed = subprocess.run(
            ["/bin/sh", "-c", review_isolation.wrap_command(
                self.session, f"/usr/bin/touch {outside}"
            )],
            capture_output=True, text=True, check=False, timeout=120,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(outside.exists())

    def test_t97_a_planted_key_copy_in_the_real_temp_dir_is_unreachable(self) -> None:
        """T-9.7 / NEG-7 -- the probe F-001 would have failed, control included."""
        probe = review_isolation._run_neg7(self.session, self.key_path, plant=True)
        self.assertEqual(probe["result"], "PASS", probe)
        self.assertEqual(probe["positive_control"], "PASS")
        for site in review_isolation._plant_sites():
            self.assertFalse(
                (site / review_isolation.NEG7_PLANT_DIRNAME).exists(),
                "leaving a real answer-key copy on disk is worse than the bug",
            )

    def test_t98_every_alias_spelling_is_denied_too(self) -> None:
        self.control()
        probe = review_isolation._run_neg8(
            self.session, self.denied, self.readable["carve_outs"]
        )
        self.assertEqual(probe["result"], "PASS", probe)

    def test_the_data_volume_alias_of_the_repository_is_denied(self) -> None:
        alias = "/System/Volumes/Data" + str(self.key_path)
        control = review_isolation._run_probe(
            None, {"alias": alias}, sandboxed=False
        )
        self.assertTrue(
            control["alias.open"]["ok"],
            "positive control: the alias must actually reach the key unsandboxed",
        )
        probes = review_isolation._run_probe(
            self.session, {"alias": alias}, sandboxed=True
        )
        self.assertFalse(probes["alias.open"]["ok"])
        self.assertIs(probes["alias.exists"]["value"], False)

    def test_a_symlink_out_of_review_root_does_not_bypass_the_profile(self) -> None:
        link = self.session / "review_root" / ".symlink_probe"
        link.unlink(missing_ok=True)
        os.symlink(str(self.key_path), str(link))
        try:
            control = review_isolation._run_probe(
                None, {"link": str(link)}, sandboxed=False
            )
            self.assertTrue(control["link.open"]["ok"])
            probes = review_isolation._run_probe(
                self.session, {"link": str(link)}, sandboxed=True
            )
            self.assertFalse(
                probes["link.open"]["ok"],
                "seatbelt evaluates the RESOLVED target, not the link",
            )
        finally:
            link.unlink(missing_ok=True)


class DiscoveryOracleTests(_IsolationTestCase):
    """The oracle is stated explicitly, because the obvious one is wrong."""

    def test_a_path_on_stderr_is_not_a_discovery(self) -> None:
        source = Path(review_isolation.__file__).read_text(encoding="utf-8")
        body = source.split("def _command_probe")[1].split("def _all_denied")[0]
        self.assertIn("completed.returncode == 0 and bool(completed.stdout.strip())", body)
        self.assertNotIn("completed.stderr", body.split("discovered =")[1].split("\n")[0])
        # Measured: `find <plant dir> -name answer_key.json` under the corrected profile
        # exits 1 and prints "find: <plant dir>: Operation not permitted" ON STDERR --
        # which contains the plant path verbatim. A substring oracle reads that correct
        # denial as a leak.

    def test_the_content_marker_lives_only_inside_the_key(self) -> None:
        self.assertIn(
            review_isolation.KEY_CONTENT_MARKER,
            KEY_PATH.read_text(encoding="utf-8"),
        )


class ProbeSourceTests(unittest.TestCase):
    """The probe program is argv, never a file. That is a security property."""

    def test_the_probe_is_never_written_into_the_readable_set(self) -> None:
        source = Path(review_isolation.__file__).read_text(encoding="utf-8")
        self.assertIn("-c {shlex.quote(_PROBE_SOURCE)}", source)
        # A probe SCRIPT would have to live where the sandboxed process can read it, and
        # it necessarily contains the answer key's absolute path -- the exact string
        # NEG-1 exists to prove absent from anything the Reviewer can read.

    def test_the_probe_uses_the_real_launch_line(self) -> None:
        source = Path(review_isolation.__file__).read_text(encoding="utf-8")
        body = source.split("def _run_probe")[1].split("def _command_probe")[0]
        self.assertIn("wrap_command(session, inner)", body)


class ProbeFailClosedTests(unittest.TestCase):
    """A probe that never ran is an absence of evidence, not a denial."""

    def test_a_probe_that_did_not_run_is_not_a_pass(self) -> None:
        self.assertFalse(review_isolation._all_denied({}))
        self.assertFalse(
            review_isolation._all_denied(
                {"__failed__": {"ok": False, "error": "execvp failed"}}
            )
        )

    def test_a_genuine_denial_is_a_pass(self) -> None:
        self.assertTrue(
            review_isolation._all_denied(
                {
                    "key.open": {"ok": False, "error": "PermissionError"},
                    "key.exists": {"ok": True, "value": False},
                    "key.stat": {"ok": False, "error": "PermissionError"},
                }
            )
        )

    def test_a_successful_read_is_not_a_pass(self) -> None:
        self.assertFalse(
            review_isolation._all_denied({"key.open": {"ok": True, "value": "abc"}})
        )
        self.assertFalse(
            review_isolation._all_denied({"key.exists": {"ok": True, "value": True}})
        )

    def test_the_ancestor_exemption_is_exactly_one_operation_wide(self) -> None:
        # exists/stat on an ancestor of the session are forced by G.4 clause 2 and reveal
        # nothing the process does not know from its own cwd. ENUMERATION is the
        # discovery channel, and it is not exempt.
        self.assertTrue(review_isolation._ancestor_metadata_exempt("temp_root.exists"))
        self.assertTrue(review_isolation._ancestor_metadata_exempt("temp_root.stat"))
        self.assertFalse(review_isolation._ancestor_metadata_exempt("temp_root.listdir"))
        self.assertFalse(review_isolation._ancestor_metadata_exempt("plant.exists"))
        self.assertFalse(review_isolation._ancestor_metadata_exempt("plant_dir.stat"))

    def test_the_probe_interpreter_is_not_the_running_one(self) -> None:
        # sys.executable is frequently a user-installed python under $HOME, which is
        # never admitted; a probe that cannot exec proves nothing.
        self.assertEqual(review_isolation.SYSTEM_PYTHON, "/usr/bin/python3")


# ---- T-10 the seed mechanism (DESIGN D-6.1 .. D-6.9) ---------------------------------
#
# Every test below runs over SYNTHETIC roots. None needs a network, a real credential or
# the operator's own `$CODEX_HOME`, and none writes outside a temporary directory.
#
# The race tests are DETERMINISTIC: the substitution happens between two ordinary function
# calls in the test body, with no threads, no timing and no retries. That is only possible
# because D-6.8 split the mechanism into `read_seed_sources()` and `place_seed_sources()`
# -- the seam the tests drive is the seam that ships.


class _SeedTestCase(_IsolationTestCase):
    """Seed sources live under the REALPATH of the temporary directory.

    `tempfile.mkdtemp()` hands back `/var/folders/...` on darwin and `/var` is a symlink,
    so an unresolved source path is refused by the no-follow walk at component 0. That is
    D-6.8 working exactly as specified rather than a test inconvenience, and it is why
    every test here resolves the base first.
    """

    def setUp(self) -> None:
        super().setUp()
        self.origin = Path(os.path.realpath(self.base)) / "origin"
        self.origin.mkdir()

    def key(self, fixture: Path = FIXTURE) -> dict:
        return review_isolation._load_key_with_source(fixture)

    def source(
        self,
        name: str,
        text: str = '{"token": "synthetic-not-a-real-secret"}\n',
        mode: int = 0o600,
    ) -> Path:
        path = self.origin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)
        return path

    def read_sources(self, *pairs: str, fixture: Path = FIXTURE, key: dict | None = None):
        return review_isolation.read_seed_sources(
            pairs,
            key=self.key(fixture) if key is None else key,
            fixture=fixture,
            repo_root=REPO_ROOT,
        )

    def seed(self, session: Path, *pairs: str, fixture: Path = FIXTURE):
        return review_isolation.seed_session_home(
            session, pairs, key=self.key(fixture), fixture=fixture, repo_root=REPO_ROOT
        )

    def assert_no_key_byte_under_home(self, session: Path, fixture: Path = FIXTURE):
        """The assertion F-001 is actually about: not `an error was raised`."""
        key_bytes = (fixture / "key" / "answer_key.json").read_bytes()
        tokens = evaluator.key_leak_tokens(self.key(fixture))
        for path in sorted((session / "home").rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            raw = path.read_bytes()
            self.assertNotIn(key_bytes, raw, f"{path} carries the answer key verbatim")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            self.assertEqual(
                evaluator.scan_leak_text(path, text, tokens), [],
                f"{path} carries key vocabulary",
            )


class SeedPlacementTests(_SeedTestCase):
    """T-10.1, T-10.7, T-10.8: what a valid pair does, and what an invalid one leaves."""

    def test_t101_a_valid_pair_lands_with_both_identities_and_the_designed_modes(
        self,
    ) -> None:
        source = self.source("auth.json")
        raw = source.read_bytes()
        session = self.build()

        (record,) = self.seed(session, f"{source}:.codex/auth.json")

        dest = session / "home" / ".codex" / "auth.json"
        self.assertEqual(dest.read_bytes(), raw, "content is byte-identical")
        self.assertEqual(dest.stat().st_mode & 0o777, 0o600)
        self.assertEqual(dest.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(record.dest, "home/.codex/auth.json")
        self.assertEqual(record.seeded_bytes, len(raw))
        self.assertEqual(
            record.seeded_sha256, review_isolation.sha256_bytes(raw),
            "the as-copied digest is over the BUFFER, never over the pathname",
        )
        self.assertEqual(record.seeded_mode, "0600")
        self.assertEqual(
            record.source, review_isolation.run_logging.FOREIGN_PATH_PLACEHOLDER
        )

    def test_t101b_the_destination_is_created_by_o_excl(self) -> None:
        # D-4 enforced by the kernel in the same call that creates the file, so the
        # destination has no TOCTOU window either.
        source = self.source("auth.json")
        session = self.build()
        sources = self.read_sources(f"{source}:.codex/auth.json")
        review_isolation.place_seed_sources(session, sources)

        with self.assertRaises(review_isolation.IsolationError) as caught:
            review_isolation.place_seed_sources(session, sources)
        self.assertIsInstance(caught.exception.__cause__, FileExistsError)

    def test_t107_the_per_source_total_and_count_caps_all_bind(self) -> None:
        session = self.build()
        oversized = self.source("big.json", "x" * (review_isolation.MAX_SEED_BYTES + 1))
        with self.assertRaises(review_isolation.IsolationError):
            self.seed(session, f"{oversized}:big.json")

        pairs = []
        for index in range(5):                      # 5 MiB against a 4 MiB total cap
            path = self.source(f"one_mib_{index}.json", "y" * review_isolation.MAX_SEED_BYTES)
            pairs.append(f"{path}:m{index}.json")
        with self.assertRaises(review_isolation.IsolationError):
            self.seed(session, *pairs)

        many = [
            f"{self.source(f'n{index}.json')}:n{index}.json"
            for index in range(review_isolation.MAX_SEEDS + 1)
        ]
        with self.assertRaises(review_isolation.IsolationError):
            self.seed(session, *many)
        self.assertEqual(
            sorted(p.name for p in (session / "home").rglob("*")), [],
            "a refused batch leaves NOTHING in the session HOME",
        )

    def test_t108_validate_all_then_copy_leaves_neither_pair_behind(self) -> None:
        good = self.source("good.json")
        bad = self.source("bad.zip")               # S-6
        session = self.build()

        with self.assertRaises(review_isolation.IsolationError):
            self.seed(session, f"{good}:good.json", f"{bad}:bad.zip")

        self.assertFalse((session / "home" / "good.json").exists(),
                         "pair 1 must not be placed when pair 2 is refused")
        self.assertEqual(list((session / "home").iterdir()), [])


class SeedSourceRefusalTests(_SeedTestCase):
    """T-10.2 .. T-10.5: the source half of the closed refusal list (D-6.5)."""

    def test_t102_a_directory_a_symlink_and_a_fifo_are_each_refused(self) -> None:
        session = self.build()
        directory = self.origin / "adir"
        directory.mkdir()
        real = self.source("real.json")
        link = self.origin / "link.json"
        os.symlink(str(real), str(link))
        fifo = self.origin / "pipe"
        os.mkfifo(str(fifo))

        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{directory}:d.json")
        self.assertIn("not a regular file", str(caught.exception))

        # S-1's symlink case is decided by the NO-FOLLOW OPEN, not by an `lstat` on a
        # pathname that could be re-pointed afterwards: the kernel refuses in the same
        # call that would have handed back the descriptor.
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{link}:l.json")
        self.assertIsInstance(caught.exception.__cause__, OSError)
        self.assertEqual(caught.exception.__cause__.errno, errno.ELOOP)

        # ... and at an INTERMEDIATE component too, which is what makes S-3's lexical
        # containment sound (T-10.15 proves the consequence).
        os.symlink(str(directory), str(self.origin / "alink"))
        (directory / "inner.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{self.origin / 'alink' / 'inner.json'}:i.json")
        # A symlink component raises ELOOP and a non-directory raises ENOTDIR; darwin
        # answers O_DIRECTORY|O_NOFOLLOW over a symlink-to-directory with ENOTDIR.
        # DESIGN's error table names both, and both are exit 4.
        self.assertIn(caught.exception.__cause__.errno, (errno.ELOOP, errno.ENOTDIR))

        # The FIFO must be refused in BOUNDED TIME. `os.open(<fifo>, O_RDONLY)` with no
        # writer blocks forever, which is F-401's defect class at the seed door; the
        # O_NONBLOCK in `_NO_FOLLOW_FILE_FLAGS` is what makes `fstat` reachable at all.
        started = time.monotonic()
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{fifo}:p.json")
        self.assertLess(time.monotonic() - started, 30.0,
                        "S-1 must refuse a FIFO in bounded time")
        self.assertIn("not a regular file", str(caught.exception))
        self.assertEqual(list((session / "home").iterdir()), [])

    def test_t103_the_repository_the_fixture_and_key_names_are_each_refused(self) -> None:
        session = self.build()
        fixture = self.synthetic_fixture()
        cases = {
            "under the fixture": fixture / "README.md",
            "under key/": fixture / "key" / "answer_key.json",
            "a key/ component elsewhere": self.source("key/other.json"),
            "an adjudications/ component": self.source("adjudications/other.json"),
            "under the repository": REPO_ROOT / "VERSION",
            "merely NAMED answer_key.json": self.source("answer_key.json", "{}\n"),
        }
        for label, path in cases.items():
            with self.subTest(label):
                with self.assertRaises(review_isolation.IsolationError):
                    self.seed(session, f"{path}:x.json", fixture=fixture)
        self.assertEqual(list((session / "home").iterdir()), [])

    def test_t104_a_source_carrying_key_vocabulary_is_refused_before_the_copy(
        self,
    ) -> None:
        session = self.build()
        contaminated = self.source(
            "notes.json", f'{{"marker": "{review_isolation.KEY_CONTENT_MARKER}"}}\n'
        )

        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{contaminated}:notes.json")

        self.assertIn("key vocabulary", str(caught.exception))
        self.assertFalse((session / "home" / "notes.json").exists(),
                         "the exit-4 path must have nothing to remove")

    def test_t105_an_executable_an_archive_and_a_non_utf8_source_are_refused(
        self,
    ) -> None:
        session = self.build()
        executable = self.source("run.sh", "#!/bin/sh\nexit 0\n", mode=0o700)
        archive = self.source("bundle.zip", "not really a zip")
        binary = self.origin / "blob.json"
        binary.write_bytes(b"\xff\xfe\x00\x01")
        os.chmod(binary, 0o600)

        for label, path, needle in (
            ("S-5 executable", executable, "executable"),
            ("S-6 archive", archive, "archive"),
            ("S-7 not UTF-8", binary, "UTF-8"),
        ):
            with self.subTest(label):
                with self.assertRaises(review_isolation.IsolationError) as caught:
                    self.seed(session, f"{path}:x.json")
                self.assertIn(needle, str(caught.exception))


class SeedDestinationRefusalTests(_SeedTestCase):
    """T-10.6: the destination half (D-1 .. D-4), plus the argument grammar (D-6.1)."""

    def test_t106_every_refused_destination_form_is_refused(self) -> None:
        session = self.build()
        source = self.source("auth.json")
        for dest in ("../escape", "/abs", "a b/x", "key/x", "subject/x",
                     "adjudications/x", "answer_key.json", "x\\y", "<x>"):
            with self.subTest(dest):
                with self.assertRaises(review_isolation.IsolationError):
                    self.seed(session, f"{source}:{dest}")
        with self.assertRaises(review_isolation.IsolationError):
            self.seed(session, f"{source}:dup.json", f"{source}:dup.json")
        self.assertEqual(list((session / "home").iterdir()), [],
                         "no partial session is left behind")

    def test_t106b_the_argument_grammar_is_exit_one_not_exit_four(self) -> None:
        # A grammar failure builds nothing, so there is nothing to remove -- which is why
        # it is EvalInputError (exit 1) and not IsolationError (exit 4).
        source = self.source("auth.json")
        for pair in (f"{source}", f"{source}:a:b", f"{source}:", "relative:a.json",
                     "/a/../b:a.json", "/a//b:a.json"):
            with self.subTest(pair):
                with self.assertRaises(review_isolation.IsolationSeedGrammarError):
                    review_isolation.read_seed_sources(
                        [pair], key=self.key(), fixture=FIXTURE, repo_root=REPO_ROOT
                    )
        self.assertTrue(
            issubclass(review_isolation.IsolationSeedGrammarError,
                       review_isolation.final_review_eval.EvalInputError)
        )


class _StatShim:
    """A stat result whose `st_size` lies, and nothing else. See T-10.17."""

    def __init__(self, st_mode: int, st_size: int, st_dev: int, st_ino: int) -> None:
        self.st_mode = st_mode
        self.st_size = st_size
        self.st_dev = st_dev
        self.st_ino = st_ino


class SeedCliExitCodeTests(_SeedTestCase):
    """D-6.1/D-6.8's two exit codes, asserted at the PRODUCTION entry point.

    An in-process `assertRaises` proves which exception is raised, not which code an
    operator sees. These run the CLI.
    """

    def test_a_grammar_failure_is_exit_one_with_a_message_not_a_traceback(self) -> None:
        completed = run_cli(
            "isolate", "--run-id", "run_cli_grammar",
            "--seed", "/no/colon/here", "--enforcement", "none",
        )
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertIn("input error:", completed.stderr)
        self.assertNotIn("Traceback", completed.stderr)

    def test_a_refused_source_is_exit_four_with_no_session_left(self) -> None:
        contaminated = self.source(
            "notes.json", f'{{"m": "{review_isolation.KEY_CONTENT_MARKER}"}}\n'
        )
        base = Path(os.path.realpath(self.base)) / "sessions"
        base.mkdir()
        completed = run_cli(
            "isolate", "--run-id", "run_cli_refusal", "--session-base", str(base),
            "--seed", f"{contaminated}:notes.json", "--enforcement", "none",
        )
        self.assertEqual(completed.returncode, 4, completed.stderr)
        self.assertIn("isolation failure:", completed.stderr)
        self.assertEqual(list(base.iterdir()), [],
                         "a refused seed leaves no session behind")


class SeedSubstitutionRaceTests(_SeedTestCase):
    """T-10.13 .. T-10.17: F-001, made deterministic by driving the phase seam."""

    def test_t1013_a_substitution_between_the_phases_changes_nothing_observable(
        self,
    ) -> None:
        fixture = self.synthetic_fixture()
        key_file = fixture / "key" / "answer_key.json"
        for label, substitute in (
            ("replaced by a different regular file",
             lambda p: p.write_text('{"token": "SWAPPED"}\n', encoding="utf-8")),
            ("replaced by a symlink into the fixture's key/",
             lambda p: (p.unlink(), os.symlink(str(key_file), str(p)))),
            ("replaced by a directory", lambda p: (p.unlink(), p.mkdir())),
            ("deleted outright", lambda p: p.unlink()),
        ):
            with self.subTest(label):
                session = self.build()
                source = self.source(
                    "auth.json", '{"token": "the-original-and-only-bytes"}\n'
                )
                original = source.read_bytes()

                sources = self.read_sources(
                    f"{source}:.codex/auth.json", fixture=fixture
                )
                substitute(source)                        # <-- the race, deterministic
                (record,) = review_isolation.place_seed_sources(session, sources)

                dest = session / "home" / ".codex" / "auth.json"
                self.assertEqual(dest.read_bytes(), original,
                                 "phase 2 writes the BUFFER, never a re-read source")
                self.assertEqual(
                    record.seeded_sha256, review_isolation.sha256_bytes(original)
                )
                self.assertFalse(dest.is_symlink())
                self.assert_no_key_byte_under_home(session, fixture)
                self.origin_reset()

    def origin_reset(self) -> None:
        shutil.rmtree(self.origin, ignore_errors=True)
        self.origin.mkdir()

    def test_t1014_a_substitution_can_never_bypass_a_refusal(self) -> None:
        fixture = self.synthetic_fixture()
        key_file = fixture / "key" / "answer_key.json"
        session = self.build()

        # (i) a source S-3 refuses stays refused whatever it is replaced with afterwards:
        # phase 1 raised, so there is no `sources` tuple for phase 2 to be handed.
        with self.assertRaises(review_isolation.IsolationError):
            self.read_sources(f"{key_file}:.codex/auth.json", fixture=fixture)
        replacement = self.source("innocuous.json")
        self.assertEqual(list((session / "home").iterdir()), [])

        # (ii) a source valid at phase 1 cannot be turned into one that should have been
        # refused, because phase 2 NEVER LOOKS -- not even at the answer key itself.
        sources = self.read_sources(f"{replacement}:.codex/auth.json", fixture=fixture)
        original = replacement.read_bytes()
        replacement.write_bytes(key_file.read_bytes())    # byte-identical to the key
        (record,) = review_isolation.place_seed_sources(session, sources)

        dest = session / "home" / ".codex" / "auth.json"
        self.assertEqual(dest.read_bytes(), original)
        self.assertEqual(record.seeded_bytes, len(original))
        self.assert_no_key_byte_under_home(session, fixture)

    def test_t1015_the_walk_decides_over_components_not_over_a_pathname(self) -> None:
        fixture = self.synthetic_fixture()
        session = self.build()
        inside = fixture / "adjudications"
        (inside / "auth.json").write_text('{"token": "x"}\n', encoding="utf-8")
        os.chmod(inside / "auth.json", 0o600)

        os.symlink(str(inside), str(self.origin / "a"))
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{self.origin / 'a' / 'auth.json'}:auth.json",
                      fixture=fixture)
        self.assertIn(caught.exception.__cause__.errno, (errno.ELOOP, errno.ENOTDIR))
        self.assertFalse((session / "home" / "auth.json").exists())

        real = self.origin / "b"
        real.mkdir()
        (real / "auth.json").write_text('{"token": "y"}\n', encoding="utf-8")
        os.chmod(real / "auth.json", 0o600)
        self.seed(session, f"{real / 'auth.json'}:auth.json", fixture=fixture)
        self.assertTrue((session / "home" / "auth.json").is_file())

    def test_t1016_s8_refuses_a_hard_link_alias_of_key_material(self) -> None:
        fixture = self.synthetic_fixture()
        session = self.build()

        # (a) a hard link to the answer key, planted OUTSIDE every refused root. S-4 and
        # S-8 each refuse it; each is asserted with the OTHER disabled, so neither is
        # carrying the other.
        alias = self.origin / "alias.json"
        os.link(str(fixture / "key" / "answer_key.json"), str(alias))
        os.chmod(alias, 0o600)

        with mock.patch.object(review_isolation, "_key_bearing_inodes",
                               return_value=set()):
            with self.assertRaises(review_isolation.IsolationError) as caught:
                self.seed(session, f"{alias}:a.json", fixture=fixture)
        self.assertNotIn("S-8", str(caught.exception))       # S-4 alone

        with mock.patch.object(review_isolation, "_answer_key_digest",
                               return_value=None), \
             mock.patch.object(review_isolation.final_review_eval,
                               "key_leak_tokens", return_value=set()):
            with self.assertRaises(review_isolation.IsolationError) as caught:
                self.seed(session, f"{alias}:a.json", fixture=fixture)
        self.assertIn("S-8", str(caught.exception))          # S-8 alone

        # (b) the case S-4 ALONE would pass: a hard link to a non-key regular file under
        # `key/` whose content carries no key vocabulary at all.
        plain = fixture / "key" / "state.json"
        plain.write_text('{"session": "nothing secret here"}\n', encoding="utf-8")
        os.chmod(plain, 0o600)
        plain_alias = self.origin / "state.json"
        os.link(str(plain), str(plain_alias))
        self.assertEqual(
            evaluator.scan_leak_text(
                plain_alias, plain_alias.read_text(encoding="utf-8"),
                evaluator.key_leak_tokens(self.key(fixture)),
            ),
            [], "the control: S-4 alone would let this through",
        )
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.seed(session, f"{plain_alias}:s.json", fixture=fixture)
        self.assertIn("S-8", str(caught.exception))

        # (c) the cap fails CLOSED rather than running against a truncated set.
        with mock.patch.object(review_isolation, "MAX_KEY_INODES", 0):
            with self.assertRaises(review_isolation.IsolationError) as caught:
                review_isolation._key_bearing_inodes(fixture)
        self.assertIn("MAX_KEY_INODES", str(caught.exception))
        self.assertEqual(list((session / "home").iterdir()), [])

    def test_t1017_the_by_construction_guarantees_hold(self) -> None:
        source = self.source("auth.json")
        (seed_source,) = self.read_sources(f"{source}:.codex/auth.json")
        session = self.build()
        (record,) = review_isolation.place_seed_sources(session, [seed_source])

        for frozen, field in ((seed_source, "dest"), (record, "seeded_sha256")):
            with self.subTest(type(frozen).__name__):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(frozen, field, "rewritten")

        self.assertEqual(
            seed_source.source, review_isolation.run_logging.FOREIGN_PATH_PLACEHOLDER
        )
        for name in ("dest", "source", "sha256"):
            value = getattr(seed_source, name)
            self.assertFalse(
                os.path.exists(value),
                f"SeedSource.{name} must not be a path phase 2 could re-open",
            )
        self.assertNotIn(str(self.origin), repr(seed_source))

        # The READ CEILING, not the advisory `st_size`, is what binds. Simulated at the
        # one seam where a real grow-after-fstat would show: `fstat` under-reports, the
        # descriptor then yields more than the cap, and step 5 refuses.
        big = self.source("grew.json", "z" * (review_isolation.MAX_SEED_BYTES + 64))
        real_fstat = os.fstat

        def lying_fstat(fd):
            st = real_fstat(fd)
            return _StatShim(st.st_mode, 12, st.st_dev, st.st_ino)

        key = self.key()
        with mock.patch.object(os, "fstat", lying_fstat):
            with self.assertRaises(review_isolation.IsolationError) as caught:
                self.read_sources(f"{big}:grew.json", key=key)
        self.assertIn("read ceiling", str(caught.exception))

    def test_the_seed_path_never_names_copyfile_or_sha256_path(self) -> None:
        # The single-open-descriptor contract is the whole of F-001's answer, so the two
        # pathname-oriented operations it removes are asserted absent by inspection.
        text = (REPO_ROOT / "scripts" / "review_isolation.py").read_text(encoding="utf-8")
        start = text.index("def read_seed_sources(")
        end = text.index("def inventory_session_home(")
        seed_path = text[start:end]
        self.assertNotIn("shutil.copyfile", seed_path)
        self.assertNotIn("sha256_path", seed_path)


class SeedAttestationTests(_SeedTestCase):
    """T-10.9 .. T-10.11, T-10.18, T-10.19: the two identities, and the single reader."""

    def attest(self, session: Path, manifest):
        inventory = review_isolation.inventory_session_home(session)
        return review_isolation.attest_seeds(manifest, inventory), inventory

    def test_t109_the_session_home_is_not_exempt_from_the_admission_scan(self) -> None:
        # The seed door refuses key vocabulary (T-10.4), and the admission scan refuses it
        # again over the root the seed lands in. This plants DIRECTLY, so what is under
        # test is the scan's coverage of `<SESSION>/home` rather than the seed door.
        session = self.build()
        (session / "home" / "leak.json").write_text(
            f'{{"m": "{review_isolation.KEY_CONTENT_MARKER}"}}\n', encoding="utf-8"
        )
        with self.assertRaises(review_isolation.IsolationError) as caught:
            review_isolation.compute_readable_set(
                session, self.key(), imm_candidates=()
            )
        self.assertIn("key material is reachable", str(caught.exception))

    def test_t1010_the_record_shape_is_the_two_identity_one(self) -> None:
        source = self.source("auth.json")
        session = self.build()
        manifest = self.seed(session, f"{source}:.codex/auth.json")
        seeded, inventory = self.attest(session, manifest)

        (row,) = seeded
        for field in ("seeded_bytes", "seeded_sha256", "seeded_mode",
                      "observed_bytes", "observed_sha256", "observed_mode", "state"):
            self.assertIn(field, row)
        for retired in ("bytes", "sha256", "mode"):
            self.assertNotIn(retired, row, "iteration 2's single field is REMOVED")
        review_isolation.run_logging.assert_retained_path_field(row["dest"])
        for entry in inventory["entries"]:
            review_isolation.run_logging.assert_retained_path_field(entry["path"])
        self.assertEqual(
            row["source"], review_isolation.run_logging.FOREIGN_PATH_PLACEHOLDER
        )

        document = review_isolation.build_attestation(
            run_id="run_t", attempt=1, terminal="", session=session,
            enforcement="none",
            readable={"entries": [], "carve_outs": []},
            traversal=[], writable=[], denied=[], profile_digest=None,
            probes=[{"id": "NEG-1", "result": "PASS"}],
            session_home={
                "seed_policy": review_isolation.SEED_POLICY_STATEMENT,
                "seeded": seeded, "inventory": inventory,
                "scanned_by": ["compute_readable_set:USR", "NEG-5"],
            },
        )
        review_isolation.assert_no_clock_value(document)
        self.assertEqual(document["schema_version"], "1.1")
        self.assertIn(review_isolation.SEED_DIGEST_LIMITATION, document["limitations"])

    def test_t1011_the_inventory_is_the_single_reader_and_opens_nothing_it_should_not(
        self,
    ) -> None:
        source = self.source("auth.json")
        session = self.build()
        manifest = self.seed(session, f"{source}:.codex/auth.json")
        (session / "home" / "history.jsonl").write_text("{}\n", encoding="utf-8")
        fifo = session / "home" / "agent.sock"
        os.mkfifo(str(fifo))

        started = time.monotonic()
        seeded, inventory = self.attest(session, manifest)
        self.assertLess(time.monotonic() - started, 30.0,
                        "the walk lstat()s and NEVER opens a non-regular entry")

        by_path = {entry["path"]: entry for entry in inventory["entries"]}
        self.assertEqual(by_path["home/.codex/auth.json"]["origin"], "seed")
        self.assertEqual(by_path["home/history.jsonl"]["origin"], "session")
        self.assertEqual(by_path["home/agent.sock"]["kind"], "fifo")
        self.assertNotIn("sha256", by_path["home/agent.sock"])
        self.assertEqual(inventory["seeded_unmodified"], 1)
        self.assertEqual(inventory["seeded_modified"], 0)
        self.assertEqual(inventory["unseeded"], inventory["files"] - 1)
        self.assertEqual(
            by_path["home/.codex/auth.json"]["sha256"], seeded[0]["observed_sha256"],
            "one read, so the two views cannot disagree",
        )

        with mock.patch.object(review_isolation, "MAX_HOME_INVENTORY", 1):
            with self.assertRaises(review_isolation.IsolationError) as caught:
                review_isolation.inventory_session_home(session)
        self.assertIn("MAX_HOME_INVENTORY", str(caught.exception))

    def test_t1011b_the_tree_digest_is_stable_across_two_identical_sessions(self) -> None:
        digests = []
        for _ in range(2):
            session = self.build()
            (session / "home" / "a.json").write_text("alpha\n", encoding="utf-8")
            (session / "home" / "b").mkdir()
            (session / "home" / "b" / "c.json").write_text("beta\n", encoding="utf-8")
            digests.append(review_isolation.inventory_session_home(session)["tree_digest"])
        self.assertEqual(digests[0], digests[1])

    def test_t1018_a_modified_seed_keeps_both_identities(self) -> None:
        source = self.source("auth.json", '{"token": "before"}\n')
        before = source.read_bytes()
        session = self.build()
        manifest = self.seed(session, f"{source}:.codex/auth.json")

        dest = session / "home" / ".codex" / "auth.json"
        after = b'{"token": "after-the-agent-refreshed-it"}\n'
        dest.write_bytes(after)                    # the agent rewriting its credential

        seeded, inventory = self.attest(session, manifest)
        (row,) = seeded
        self.assertEqual(row["seeded_bytes"], len(before))
        self.assertEqual(row["seeded_sha256"], review_isolation.sha256_bytes(before))
        self.assertEqual(row["observed_bytes"], len(after))
        self.assertEqual(row["observed_sha256"], review_isolation.sha256_bytes(after))
        self.assertEqual(row["state"], "modified")
        self.assertEqual(inventory["seeded_unmodified"], 0)
        self.assertEqual(inventory["seeded_modified"], 1)
        self.assertEqual(
            inventory["entries"][0]["sha256"], row["observed_sha256"]
        )

        # A MODE-only change is not a `state` change: `state` answers "are these the bytes
        # we supplied", which is B6's question.
        dest.write_bytes(before)
        os.chmod(dest, 0o644)
        (row,) = self.attest(session, manifest)[0]
        self.assertEqual(row["observed_mode"], "0644")
        self.assertEqual(row["seeded_mode"], "0600")
        self.assertEqual(row["state"], "unmodified")

    def test_t1019_the_unmodified_case_and_the_immutability_guard(self) -> None:
        source = self.source("auth.json")
        session = self.build()
        manifest = self.seed(session, f"{source}:.codex/auth.json")

        seeded, inventory = self.attest(session, manifest)
        (row,) = seeded
        self.assertEqual(row["state"], "unmodified")
        self.assertEqual(row["observed_sha256"], row["seeded_sha256"])
        self.assertEqual(row["observed_bytes"], row["seeded_bytes"])
        self.assertEqual(inventory["seeded_unmodified"], 1)

        for field in ("seeded_bytes", "seeded_sha256", "seeded_mode", "dest", "source"):
            with self.subTest(field):
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(manifest[0], field, "rewritten")

        # A declared destination absent from the inventory is exit 4 -- never a dropped
        # row and never an invented one.
        (session / "home" / ".codex" / "auth.json").unlink()
        with self.assertRaises(review_isolation.IsolationError) as caught:
            self.attest(session, manifest)
        self.assertIn("absent from the session HOME", str(caught.exception))


class AgentPathTests(_SeedTestCase):
    """T-10.12: `--agent-path` can never exceed what `--allow-read` scanned."""

    def test_t1012_an_unadmitted_entry_is_refused_and_an_admitted_one_leads_path(
        self,
    ) -> None:
        session = self.build()
        elsewhere = self.origin / "bin"
        elsewhere.mkdir()
        readable = review_isolation.compute_readable_set(
            session, self.key(), imm_candidates=(), allow_read=[str(elsewhere)]
        )

        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.assert_agent_path_admitted([str(self.origin)], readable)

        admitted = review_isolation.assert_agent_path_admitted(
            [str(elsewhere)], readable
        )
        line = review_isolation.wrap_command(session, "AGENT", admitted)
        self.assertIn(f"PATH={str(elsewhere)}:/usr/bin:", line)
        self.assertLess(line.index(str(elsewhere)), line.index("/usr/bin"))
        self.assertIn(f"TMPDIR={session / 'tmp'}", line)
        self.assertIn(f"HOME={session / 'home'}", line)

    def test_the_launch_line_is_byte_identical_with_no_agent_path(self) -> None:
        session = self.build()
        self.assertEqual(
            review_isolation.wrap_command(session, "AGENT"),
            review_isolation.wrap_command(session, "AGENT", ()),
        )
        self.assertNotIn("PATH=", review_isolation.wrap_command(session, "AGENT"))


class WritableSetSpellingTests(_IsolationTestCase):
    """F-402 at the unit level: one path, one spelling, decided where it enters.

    The integration half lives in `NegativeContractTests.test_f402_*`, which runs the
    real launch line against a real generated profile. This half is what makes the
    regression cheap to catch on every run.
    """

    def test_build_session_returns_a_resolved_path(self) -> None:
        session = self.build()
        self.assertEqual(str(session), os.path.realpath(session))

    def test_the_readable_and_writable_spellings_of_one_root_agree(self) -> None:
        session = self.build()
        readable = review_isolation.compute_readable_set(
            session, review_isolation._load_key_with_source(FIXTURE), imm_candidates=()
        )
        admitted = {entry["path"] for entry in readable["entries"]}
        for relative in ("review_root", "tmp", "home"):
            with self.subTest(relative):
                writable = str(review_isolation._realpath(session / relative))
                self.assertEqual(writable, str(session / relative),
                                 "the session path is already resolved, so these are the "
                                 "same string and seatbelt sees one directory")
                self.assertIn(writable, admitted,
                              "a write clause naming a directory the read clause spells "
                              "differently is a profile that allows nothing")


class NonRegularScanTests(_IsolationTestCase):
    """F-401: the scan COUNTS a non-regular entry and never opens one.

    The finding as filed is `/dev`, whose 459 character and block devices SIGKILLed the
    section 7 capture at 17m44s. `/dev/zero` cannot be reproduced in a unit test without
    root, but a FIFO reproduces the same defect exactly -- `read_text()` on one never
    returns -- and the fix is the same general policy for both.
    """

    def test_a_fifo_under_an_admitted_root_is_counted_and_not_opened(self) -> None:
        root = Path(os.path.realpath(self.base)) / "root"
        root.mkdir()
        (root / "plain.txt").write_text("nothing to see\n", encoding="utf-8")
        os.mkfifo(str(root / "pipe"))

        started = time.monotonic()
        scan = review_isolation.scan_readable_set(
            review_isolation._load_key_with_source(FIXTURE), root
        )
        self.assertLess(time.monotonic() - started, 30.0)
        self.assertEqual(scan["hits"], [])
        self.assertEqual(scan["files"], 2, "a non-regular entry is still COUNTED")
        self.assertEqual(scan["non_regular"], 1)
        self.assertEqual(scan["content_scanned"], 1, "only the regular file was opened")

    def test_a_non_regular_entry_named_like_an_archive_is_not_handed_to_tarfile(
        self,
    ) -> None:
        # Pass D opens what it is handed too, so the S_ISREG gate has to precede it.
        root = Path(os.path.realpath(self.base)) / "root_d"
        root.mkdir()
        os.mkfifo(str(root / "bundle.tar"))
        started = time.monotonic()
        scan = review_isolation.scan_readable_set(
            review_isolation._load_key_with_source(FIXTURE), root
        )
        self.assertLess(time.monotonic() - started, 30.0)
        self.assertEqual(scan["archives"], 0)
        self.assertEqual(scan["non_regular"], 1)
        self.assertEqual(scan["hits"], [])

    def test_the_production_entry_point_terminates_on_a_non_regular_entry(self) -> None:
        # BOUNDED, and at the entry point a capture actually uses: `compute_readable_set`
        # over an `--allow-read` root. Run in a subprocess with a hard timeout so a
        # regression FAILS rather than hanging the suite forever.
        root = Path(os.path.realpath(self.base)) / "allowed"
        root.mkdir()
        (root / "notes.txt").write_text("ordinary\n", encoding="utf-8")
        os.mkfifo(str(root / "agent.sock"))
        session = self.build()

        program = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(REPO_ROOT / "scripts")!r})
            sys.path.insert(0, {str(REPO_ROOT)!r})
            from pathlib import Path
            import review_isolation as ri
            readable = ri.compute_readable_set(
                Path({str(session)!r}),
                ri._load_key_with_source(Path({str(FIXTURE)!r})),
                imm_candidates=(), allow_read=[{str(root)!r}],
            )
            print(len(readable["entries"]))
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", program], cwd=REPO_ROOT, capture_output=True,
            text=True, check=False, timeout=180,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "4")


class AttemptDomainTests(_IsolationTestCase):
    """T-13.1 / T-13.3 / T-13.4' / T-13.5 / T-13.6 -- DESIGN D-A.7 and INV-ATTEMPT-2.

    `attempt` becomes a path component through
    `suffix = "" if attempt == 1 else f"_iteration{attempt}"`, and an f-string accepts
    ANY object while `== 1` is a value comparison, so `0`, `-1`, `False` and `2.0` all
    produced real, unexempted, digest-bound destinations before D-A.7 (DESIGN M-14/M-24).
    """

    # DESIGN M-14: `attempt=False` wrote FINAL_REVIEW_iterationFalse.md and
    # `attempt=True` silently aliased attempt 1, because `True == 1`. The bool exclusion
    # is that measurement, not pedantry.
    OUT_OF_RANGE = (0, -1, -12)
    WRONG_TYPE = (False, True, 2.0, "2", None)

    def prepared(self) -> Path:
        session = self.build()
        (session / "review_root" / "artifacts" / "runs" / "run_t"
         / "FINAL_REVIEW.md").write_text("RESULT: PASS\n", encoding="utf-8")
        (session / "control" / review_isolation.ISOLATION_FILENAME).write_text(
            "{}\n", encoding="utf-8"
        )
        return session

    def attestation_arguments(self, session: Path, **overrides) -> dict:
        arguments = {
            "run_id": "run_t", "attempt": 1, "terminal": "term_x", "session": session,
            "enforcement": "seatbelt",
            "readable": {
                "entries": [
                    {"class": "IMM", "path": "/bin", "scanned": False,
                     "proof": {"passed": True, "writable_dirs": 0, "writable_files": 0}},
                ],
                "carve_outs": [],
            },
            "traversal": ["/"], "writable": [str(session / "review_root")],
            "denied": [str(REPO_ROOT)], "profile_digest": "sha256:x",
            "probes": [
                {"id": identifier, "result": "PASS"}
                for identifier in ("NEG-0", "NEG-1", "NEG-2", "NEG-3", "NEG-4",
                                   "NEG-5", "NEG-6", "NEG-7", "NEG-8")
            ],
        }
        arguments.update(overrides)
        return arguments

    def assertNothingCreated(self) -> None:
        """The half a bare assertRaises would miss.

        DESIGN M-14 measured the shipped `repatriate()` creating
        `artifacts/runs/<run>/` BEFORE it looked at `attempt`, so this is what pins the
        check ahead of the mkdir rather than merely present.
        """
        self.assertFalse(
            (self.base / "artifacts").exists(),
            "a refused attempt must leave no run directory behind",
        )

    # ---- T-13.1 -------------------------------------------------------------------

    def test_t131_repatriate_refuses_zero_and_negatives_and_creates_nothing(self) -> None:
        session = self.prepared()
        for attempt in self.OUT_OF_RANGE:
            with self.subTest(attempt=attempt):
                with self.assertRaises(
                    review_isolation.IsolationAttemptDomainError
                ) as caught:
                    review_isolation.repatriate(
                        session, "run_t", attempt=attempt, base=self.base
                    )
                self.assertEqual(
                    str(caught.exception), f"attempt must be >= 1, got {attempt!r}"
                )
                self.assertNothingCreated()

    def test_t131_isolate_refuses_zero_and_negatives_and_builds_no_session(self) -> None:
        before = set(Path(tempfile.gettempdir()).glob(
            f"{review_isolation.SESSION_PREFIX}*"
        ))
        for attempt in self.OUT_OF_RANGE:
            with self.subTest(attempt=attempt):
                with self.assertRaises(
                    review_isolation.IsolationAttemptDomainError
                ) as caught:
                    review_isolation.isolate(
                        "run_t", fixture=FIXTURE, session_base=self.base,
                        attempt=attempt, enforcement="none", plant=False,
                    )
                self.assertEqual(
                    str(caught.exception), f"attempt must be >= 1, got {attempt!r}"
                )
                self.assertNothingCreated()
        self.assertEqual(
            set(Path(tempfile.gettempdir()).glob(
                f"{review_isolation.SESSION_PREFIX}*"
            )),
            before,
            "a session is expensive to build and must not be built on a bad argument",
        )

    # ---- T-13.3 -------------------------------------------------------------------

    def test_t133a_non_integer_objects_are_refused_at_the_function_boundary(self) -> None:
        session = self.prepared()
        for attempt in self.WRONG_TYPE:
            with self.subTest(attempt=attempt):
                for call in (
                    lambda: review_isolation.repatriate(
                        session, "run_t", attempt=attempt, base=self.base
                    ),
                    lambda: review_isolation.isolate(
                        "run_t", fixture=FIXTURE, session_base=self.base,
                        attempt=attempt, enforcement="none", plant=False,
                    ),
                ):
                    with self.assertRaises(
                        review_isolation.IsolationAttemptDomainError
                    ) as caught:
                        call()
                    self.assertEqual(
                        str(caught.exception),
                        f"attempt must be an int >= 1, got {attempt!r}",
                    )
                    self.assertNothingCreated()

    def test_t133b_non_integer_text_at_the_cli_keeps_argparses_own_exit_two(self) -> None:
        # PRE-EXISTING AND DELIBERATE (D-A.7.5). An out-of-DOMAIN integer is a
        # program-level input error (exit 1, G.7); unparseable TEXT never reaches the
        # program at all and takes argparse's usage exit, the same one `--enforcement
        # bogus` already takes. Both codes are pinned so a future unification is
        # deliberate rather than accidental.
        for text in ("abc", "1.5", "0x2", "1e3"):
            with self.subTest(text=text):
                completed = run_cli(
                    "isolate", "--run-id", "run_t", "--teardown", str(self.base),
                    "--attempt", text,
                )
                self.assertEqual(completed.returncode, 2, completed.stderr)
                self.assertIn("invalid int value", completed.stderr)

    def test_t133c_lenient_integer_spellings_parse_and_are_accepted(self) -> None:
        # M-16: spelling leniency cannot produce an out-of-domain name, because argparse
        # normalises the text to an int before the domain check ever sees it.
        parser = evaluator.build_parser()
        for text, expected in (("001", 1), ("+2", 2), ("1_0", 10), (" 3 ", 3)):
            with self.subTest(text=text):
                arguments = parser.parse_args(
                    ["isolate", "--run-id", "run_t", "--attempt", text]
                )
                self.assertEqual(arguments.attempt, expected)
                self.assertIsNone(
                    run_logging.attempt_domain_violation(arguments.attempt)
                )

    # ---- T-13.4' ------------------------------------------------------------------

    def test_t134_every_gated_boundary_checks_before_it_does_anything_else(self) -> None:
        """D-A.7.6's census made executable, corrected for INV-ATTEMPT-2.

        D-A.7's T-13.4 asserted a REACHABILITY claim about `build_attestation()`; that
        clause is deleted, because GATE 4 now makes the claim unnecessary.
        """
        for function, expression in (
            (review_isolation.repatriate, "assert_attempt_in_domain(attempt)"),
            (review_isolation.isolate, "assert_attempt_in_domain(attempt)"),
            (review_isolation.build_attestation, "assert_attempt_in_domain(attempt)"),
            (run_logging.final_review_report_ladder_path,
             "assert_attempt_in_domain(attempt"),
            (run_logging.read_final_review_attempt_provenance,
             "assert_attempt_in_domain(attempt"),
            (e2e_harness.final_review_artifact_path,
             "assert_attempt_in_domain(attempt)"),
        ):
            with self.subTest(function=function.__qualname__):
                body = _function_body_statements(function)
                self.assertTrue(
                    body, f"{function.__qualname__} has no executable body"
                )
                self.assertIn(
                    expression, body[0],
                    f"{function.__qualname__}'s domain gate must PRECEDE every other "
                    "statement, not merely be present",
                )

    def test_t134_review_isolation_still_declares_no_cli_door_of_its_own(self) -> None:
        source = (REPO_ROOT / "scripts" / "review_isolation.py").read_text(
            encoding="utf-8"
        )
        for marker in ("__main__", "import argparse", "\ndef main("):
            self.assertNotIn(
                marker, source,
                "review_isolation is library-only; it is reached from the command line "
                "only through final_review_eval.py isolate (C-7)",
            )

    def test_t134_the_shipped_run_logging_mirror_is_byte_identical(self) -> None:
        # C-10 / RK-22: a global or project-local Skill install never copies this
        # repository's scripts/, so the Skill ships its own copy. validate_skills.py
        # fails on drift; this is the faster tripwire inside the suite.
        self.assertEqual(
            (REPO_ROOT / "scripts" / "run_logging.py").read_bytes(),
            (REPO_ROOT / "orca-worker-reviewer-orchestration" / "tools"
             / "run_logging.py").read_bytes(),
            "any attempt-domain edit to run_logging.py must be mirrored in the same "
            "commit",
        )

    # ---- T-13.5 -------------------------------------------------------------------

    def test_t135_valid_attempts_are_unaffected_in_every_respect(self) -> None:
        session = self.prepared()
        source = (session / "review_root" / "artifacts" / "runs" / "run_t"
                  / "FINAL_REVIEW.md")
        root = self.base / "artifacts" / "runs" / "run_t"
        # 100 is in this list deliberately: D-A.7.2 declines an upper bound and D-A.6"
        # leaves the path unexempted (RK-19). This asserts it still WORKS; T-12.3
        # asserts it is still unexempted. Together they make the undermatch a bounded
        # choice rather than a gap.
        for attempt in (1, 2, 3, 9, 10, 42, 99, 100):
            with self.subTest(attempt=attempt):
                result = review_isolation.repatriate(
                    session, "run_t", attempt=attempt, base=self.base
                )
                suffix = "" if attempt == 1 else f"_iteration{attempt}"
                self.assertEqual(
                    result["report"], str(root / f"FINAL_REVIEW{suffix}.md")
                )
                self.assertEqual(
                    result["workspace"],
                    str(root / f"{review_isolation.REPATRIATED_WORKSPACE_DIRNAME}"
                        f"{suffix}"),
                )
                self.assertEqual(
                    result["report_digest"], review_isolation.sha256_path(source),
                    "the existing digest-verification path is reached unchanged",
                )

    # ---- T-13.6 -- GATE 4 ---------------------------------------------------------

    def test_t136_build_attestation_refuses_every_out_of_domain_attempt(self) -> None:
        session = self.build()
        for attempt in self.OUT_OF_RANGE + self.WRONG_TYPE:
            with self.subTest(attempt=attempt):
                # `type(...) is int`, not `attempt in self.OUT_OF_RANGE`: `False == 0`,
                # so membership would classify the bool as an out-of-RANGE value and
                # assert the wrong half of the message contract.
                expected = (
                    f"attempt must be >= 1, got {attempt!r}"
                    if type(attempt) is int
                    else f"attempt must be an int >= 1, got {attempt!r}"
                )
                with self.assertRaises(
                    review_isolation.IsolationAttemptDomainError
                ) as caught:
                    review_isolation.build_attestation(
                        **self.attestation_arguments(session, attempt=attempt)
                    )
                self.assertEqual(str(caught.exception), expected)

    def test_t136_the_domain_error_wins_before_any_document_field_is_built(self) -> None:
        # The half a bare assertRaises would miss: an otherwise-invalid `readable` that
        # would raise IsolationError if the body ran (see T-8.7c). The DOMAIN error must
        # win, which is what places the gate before `verdicts = {...}`.
        session = self.build()
        broken = {
            "entries": [{"class": "USR", "path": "/bin", "scanned": False}],
            "carve_outs": [],
        }
        with self.assertRaises(review_isolation.IsolationError):
            review_isolation.build_attestation(
                **self.attestation_arguments(session, attempt=1, readable=broken)
            )
        with self.assertRaises(review_isolation.IsolationAttemptDomainError):
            review_isolation.build_attestation(
                **self.attestation_arguments(session, attempt=0, readable=broken)
            )

    def test_t136_valid_attempts_round_trip_as_json_numbers(self) -> None:
        # The direct counter-assertion to M-24c, which measured shipped code serializing
        # 0, -1, 2.0, false, true, "2" and null into ISOLATION.json's
        # final_review_attempt field.
        session = self.build()
        for attempt in (1, 2, 100):
            with self.subTest(attempt=attempt):
                document = review_isolation.build_attestation(
                    **self.attestation_arguments(session, attempt=attempt)
                )
                self.assertIs(type(document["final_review_attempt"]), int)
                self.assertEqual(document["final_review_attempt"], attempt)
                self.assertEqual(
                    json.loads(json.dumps(document))["final_review_attempt"], attempt
                )


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
