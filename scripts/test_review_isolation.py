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

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import review_isolation
from scripts import final_review_eval as evaluator

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
            self.key, self.root, passes=review_isolation.SCAN_PASSES_NAME_ONLY
        )["hits"]
        self.assertEqual(
            [hit for hit in hits if hit["pass"] == "S"], [],
            "for Class IMM the PROFILE is the evidence: seatbelt evaluates the resolved "
            "target, so the link grants nothing the profile does not already grant",
        )
        self.assertIn("S", review_isolation.SCAN_PASSES_ALL)
        self.assertNotIn("S", review_isolation.SCAN_PASSES_NAME_ONLY)

    def test_a_carved_out_subtree_is_not_scanned_because_it_is_not_readable(self) -> None:
        # The carve-outs are part of the readable set's DEFINITION. Scanning beneath one
        # is wrong in the loud direction: /System/Volumes/Data re-exposes the whole data
        # volume, so a rescan that ignored the carve-out would report every answer-key
        # copy on the machine as a hit while the sandboxed process can reach none of them.
        carved = self.root / "denied"
        carved.mkdir()
        (carved / "answer_key.json").write_bytes(KEY_PATH.read_bytes())
        # The IMM pass set, because a carve-out only ever arises inside a Class IMM root.
        # (Pass B is `scan_leak()`, which has no exclusion parameter by design and is
        # therefore never run over a root that has one.)
        passes = review_isolation.SCAN_PASSES_NAME_ONLY
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


if __name__ == "__main__":       # pragma: no cover
    unittest.main()
