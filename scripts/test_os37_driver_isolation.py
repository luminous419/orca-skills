"""OS-37 V-9 / D4.1.  STATIC proofs that CLI knowledge is contained and nothing was vendored.

Two isolation tests rather than one, because one is not enough: a token sweep proves no
module outside the driver layer names a CLI, and a pinned digest proves the standalone work
added no branch to the workflow, decision or review policy.

**A reconciliation between two clauses of the approved design, recorded rather than
absorbed.**  DESIGN D4.1 excludes ``standalone_drivers.py`` and ``standalone_profile.py``
from the token sweep, while DESIGN D1.1/D7.2 assign ``FORBIDDEN_CHILD_ENV_PREFIXES`` -- a
denylist of parent-session marker prefixes that necessarily spells ``CLAUDE``, ``CODEX``
and ``ANTHROPIC_`` -- to ``standalone_env.py``.  Both are binding, so the sweep excludes
three modules and this file pays for the third exclusion with a STRICTER assertion: in
``standalone_env.py`` and ``standalone_profile.py`` a CLI token may appear only inside a
module-level closed data constant, never in a conditional, a comparison or an f-string.
That keeps the property the containment rule exists for -- no module outside the drivers
BRANCHES on which CLI it is talking to -- checkable rather than waived.
"""
from __future__ import annotations

import ast
import hashlib
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "scripts" / "deterministic_workflow"
MIRROR = REPO / "orca-worker-reviewer-orchestration" / "tools" / "deterministic_workflow"

#: The only modules in which a CLI-shaped token may appear at all.
DRIVER_LAYER = frozenset({"standalone_drivers.py"})
#: Excluded from the token sweep, and held to the stricter data-only rule below instead.
DATA_ONLY = frozenset({"standalone_profile.py", "standalone_env.py"})
#: `orca_adapter.py` names Orca by design and predates this ticket; it is not a standalone
#: module and the containment rule is about CLI *agents*, not about the Orca runtime.
PREEXISTING = frozenset({"orca_adapter.py"})

CLI_TOKEN = re.compile(r"(?i)\b(claude|codex|anthropic|openai)\b")

#: `tools/` siblings a standalone module may reach, and the ONLY ones.  They are not new
#: dependencies -- they ship in the same archive -- but they are not intra-package either, so
#: they must be imported through the lazy installed-flat-layout idiom.  `decision_contract`
#: is here because the settlement RESULT vocabulary is workflow policy: the standalone path
#: derives it through the same parser the Orca and fake paths use, and a standalone-specific
#: parser would be the per-runtime divergence AC-37-20 forbids.
TOOLS_SIBLINGS = frozenset({"decision_contract"})

#: The module-level constants a CLI token may live inside, in the DATA_ONLY modules.
ALLOWED_DATA_CONSTANTS = frozenset({
    "DRIVER_KINDS", "DriverKind", "FORBIDDEN_CHILD_ENV_PREFIXES", "ALLOWED_EXCEPTIONS",
})

#: The policy modules DESIGN D4.1 pins.  Their digests are the values these files hold at
#: `BASELINE_REV` -- that is, UNCHANGED by this ticket -- recorded here as LITERALS.  A
#: later edit to any of them fails `test_policy_modules_unchanged`, which is the point: it
#: makes "the standalone work added no branch to workflow, decision or review policy" a
#: gate rather than a claim.  Regenerate deliberately, never to make the test pass.
POLICY_MODULES = ("graph.py", "routing.py", "executor.py", "state.py")
POLICY_MODULE_DIGESTS = {
    "graph.py": "aeca4df8e3fa68c91bdc5d85f04e9bdb4f591272f84c87cdce3f73b38d84da9c",
    "routing.py": "b361e4fc747e39596861f78c1221d9c6c4636d4ac8a5804fa510dbbfbb509324",
    "executor.py": "f14374204745521b48c54fba6a0c8597a81cab1800762ea22acce64c6000316e",
    "state.py": "a95d370120a4a4f1191bf517a8f9f089dfc341245d17eea2322816ce1a9bd636",
}

#: `contracts.py`'s ADDITIVE region, by line range in the CURRENT file (1-based, inclusive)
#: -- D4.1's "excluded by line range".  Everything OUTSIDE these ranges is the POLICY
#: REGION, and it must be byte-identical to `BASELINE_REV`'s.  The ranges are:
#:   (9, 9)      the added `collections.abc` import
#:   (88, 105)   the STANDALONE_CAPABILITIES block, ending at the one pre-existing policy
#:               line this ticket edits -- the `CAPABILITIES` union.  It is inside the
#:               excluded range precisely BECAUSE it changed; the assertion below pins what
#:               it changed FROM, so the exclusion cannot be used to hide anything else.
#:   (110, 198)  the ownership-axis vocabularies and their validator
#: The additive region itself is checked by V-5, per D4.1.
CONTRACTS_ADDITIVE_RANGES = ((9, 9), (88, 105), (110, 198))

#: The digest of `contracts.py`'s policy region.  Equal, byte for byte, to `BASELINE_REV`'s
#: `contracts.py` minus the single line below -- which the test PROVES rather than asserts,
#: so this pin cannot drift into being merely "whatever the file says today".
CONTRACTS_POLICY_REGION_DIGEST = (
    "be5ac3d429d3b9ff5853b9e6158bf3e65218710360a8f6a8ed196db140d25886")

#: The one pre-existing policy line this ticket edits, AS IT READS AT `BASELINE_REV`.  The
#: edit is the widening of a set that is only ever read as an allowed SUPERSET, so it
#: forbids nothing that was previously allowed.
BASELINE_CAPABILITIES_LINE = (
    "CAPABILITIES = BASE_CAPABILITIES | RECOVERY_CAPABILITIES | frozenset({\n")

#: The revision this branch is based on.  The "added no CLI token" assertion below compares
#: against it rather than against a transcribed count, so it stays true as unrelated prose
#: in unrelated modules changes.
BASELINE_REV = "d13b7fa"

#: The digest ``ports.py`` holds at ``origin/main`` = d13b7fa, recorded here as a LITERAL.
#: Recomputing it from the file would make the test tautological -- it would pass whatever
#: the file said.  Verified equal to ``git show HEAD:scripts/deterministic_workflow/ports.py``
#: at IMPLEMENTATION time, so this pin is the unedited baseline and not this ticket's output.
PORTS_PY_DIGEST = "ea4dbf0e76b3668163d71dba4ef317bf28bac148d55e21c882d0b807d3246ae9"


def _standalone_modules() -> tuple[Path, ...]:
    return tuple(sorted(ENGINE.glob("standalone_*.py")))


def _string_and_comment_tokens(path: Path) -> list[tuple[int, str]]:
    """Every string literal and comment in ``path``, with its line number."""
    import io
    import tokenize
    found: list[tuple[int, str]] = []
    with open(path, "rb") as handle:
        for token in tokenize.tokenize(handle.readline):
            if token.type in (tokenize.STRING, tokenize.COMMENT):
                found.append((token.start[0], token.string))
    return found


class DriverContainmentTests(unittest.TestCase):
    """D4.1: CLI names live in the driver layer, and nowhere above it."""

    def test_no_cli_token_in_any_standalone_module_above_the_driver(self) -> None:
        """ZERO CLI-shaped tokens in every standalone module above the driver layer.

        Code, docstrings and comments alike.  A comment saying "for <CLI>, do X" documents
        a branch somebody will eventually write, so prose is swept too -- and the two
        data-only modules are held to the stricter rule below instead of being waived.
        """
        offenders: list[str] = []
        for path in _standalone_modules():
            if path.name in DRIVER_LAYER | DATA_ONLY:
                continue
            for line, text in _string_and_comment_tokens(path):
                if CLI_TOKEN.search(text):
                    offenders.append(f"{path.name}:{line}: {text[:90]!r}")
        self.assertEqual(
            offenders, [],
            "a CLI-shaped token appears in a standalone module above the driver layer; "
            "per-CLI knowledge must be absorbed by standalone_drivers so that no lifecycle, "
            "journal, adapter, workflow, decision or review policy branches on it:\n"
            + "\n".join(offenders))

    def test_the_standalone_work_added_no_cli_token_to_any_existing_module(self) -> None:
        """No PRE-EXISTING engine module gained a CLI-shaped token from this ticket.

        Some existing modules legitimately name a CLI in prose already -- ``turn_boundary``
        and ``launcher`` document this repository's own Claude Code ``Stop``-hook
        integration, which is the harness the Coordinator runs INSIDE and not an agent CLI
        this runtime drives, and ``lease_keeper`` names the two agents only to say how long
        a real dispatch blocks for.  Asserting zero there would be asserting something
        false, and excluding those files outright would make the sweep unable to notice a
        NEW branch added to one of them.

        So the assertion is the property that is actually true and actually load-bearing:
        the count in every pre-existing module is unchanged from ``origin/main``.  A
        standalone edit that introduced a per-CLI branch into ``launcher`` or ``executor``
        would raise a count and fail here.
        """
        import subprocess
        for path in sorted(ENGINE.glob("*.py")):
            if path.name.startswith("standalone_"):
                continue
            rel = f"scripts/deterministic_workflow/{path.name}"
            baseline = subprocess.run(["git", "show", f"{BASELINE_REV}:{rel}"],
                                      cwd=REPO, capture_output=True, text=True,
                                      check=False)
            if baseline.returncode != 0:
                continue          # a file that did not exist at the baseline
            before = len(CLI_TOKEN.findall(baseline.stdout))
            after = len(CLI_TOKEN.findall(path.read_text()))
            self.assertLessEqual(
                after, before,
                f"{path.name} gained {after - before} CLI-shaped token(s) relative to "
                f"{BASELINE_REV}; the standalone work must add no per-CLI knowledge to any "
                "module above the driver layer")

    def test_data_only_modules_never_branch_on_a_cli(self) -> None:
        """In the two data-only modules a CLI token appears ONLY in closed data.

        This is the stricter assertion that pays for their exclusion above.  It walks the
        AST rather than the text, so a token inside an ``if``, a comparison, an f-string or
        a function body is a failure even though the same token in a module-level tuple is
        not.
        """
        for name in sorted(DATA_ONLY):
            path = ENGINE / name
            tree = ast.parse(path.read_text())
            allowed_nodes: set[int] = set()
            for node in tree.body:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = ([node.target] if isinstance(node, ast.AnnAssign)
                               else list(node.targets))
                    names = {t.id for t in targets if isinstance(t, ast.Name)}
                    if names & ALLOWED_DATA_CONSTANTS:
                        for child in ast.walk(node):
                            allowed_nodes.add(id(child))
            offenders: list[str] = []
            for node in ast.walk(tree):
                if id(node) in allowed_nodes:
                    continue
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and CLI_TOKEN.search(node.value):
                    # A docstring is prose about the policy, not a branch on it.
                    if _is_docstring(tree, node):
                        continue
                    offenders.append(f"{name}:{node.lineno}: {node.value[:80]!r}")
            self.assertEqual(
                offenders, [],
                f"{name} references a CLI outside its declared closed data constants "
                f"{sorted(ALLOWED_DATA_CONSTANTS)}; only standalone_drivers may branch on "
                "which CLI is in play:\n" + "\n".join(offenders))

    def _policy_region(self, source: str) -> str:
        """`contracts.py` minus its additive line ranges -- D4.1's POLICY REGION."""
        lines = source.splitlines(keepends=True)
        excluded = {n for first, last in CONTRACTS_ADDITIVE_RANGES
                    for n in range(first, last + 1)}
        return "".join(line for number, line in enumerate(lines, 1)
                       if number not in excluded)

    def test_policy_modules_unchanged(self) -> None:
        """DESIGN D4.1: a PINNED DIGEST over the policy modules and contracts.py's region.

        This is the approved mechanism and not a stand-in for it.  A substring assertion --
        "these files do not contain the word 'standalone'" -- was tried and is not equal to
        it: it proves the absence of one spelling, so a per-runtime branch that avoids that
        word passes, and it says nothing at all about `contracts.py`, which this ticket
        genuinely edits.  A digest says what the design asked for: these bytes, exactly.

        Both trees, because a divergence between them is the same defect arriving by a
        different route.
        """
        for tree in (ENGINE, MIRROR):
            for name, pinned in POLICY_MODULE_DIGESTS.items():
                actual = hashlib.sha256((tree / name).read_bytes()).hexdigest()
                self.assertEqual(
                    actual, pinned,
                    f"{tree.name}/{name} no longer matches its pinned digest.  The "
                    "standalone work must add NO branch to workflow, decision or review "
                    "policy; if this file changed for an unrelated and approved reason, "
                    "re-pin it deliberately -- never to make this test pass")
            region = self._policy_region((tree / "contracts.py").read_text())
            self.assertEqual(
                hashlib.sha256(region.encode()).hexdigest(),
                CONTRACTS_POLICY_REGION_DIGEST,
                f"{tree.name}/contracts.py's POLICY REGION changed.  Only the additive "
                f"region {CONTRACTS_ADDITIVE_RANGES} may differ from {BASELINE_REV}; "
                "everything else is runtime-neutral policy and is pinned")

    def test_the_policy_pins_are_the_baseline_and_not_this_tickets_output(self) -> None:
        """The pins above are proven equal to `BASELINE_REV`, not merely to today's files.

        Without this, `test_policy_modules_unchanged` would be tautological the moment a
        pin were regenerated from a modified file: it would assert that the file equals
        itself.  Here the authority is the git object at the branch's base, so "unchanged"
        means unchanged from something this ticket did not write.
        """
        import subprocess
        for name, pinned in POLICY_MODULE_DIGESTS.items():
            shown = subprocess.run(
                ["git", "show", f"{BASELINE_REV}:scripts/deterministic_workflow/{name}"],
                cwd=REPO, capture_output=True, check=False)
            if shown.returncode != 0:
                self.skipTest(f"{BASELINE_REV} is not available in this checkout")
            self.assertEqual(
                hashlib.sha256(shown.stdout).hexdigest(), pinned,
                f"the pinned digest for {name} is not the one it holds at {BASELINE_REV}")

        shown = subprocess.run(
            ["git", "show", f"{BASELINE_REV}:scripts/deterministic_workflow/contracts.py"],
            cwd=REPO, capture_output=True, text=True, check=False)
        if shown.returncode != 0:
            self.skipTest(f"{BASELINE_REV} is not available in this checkout")
        baseline = shown.stdout.splitlines(keepends=True)
        self.assertEqual(
            baseline.count(BASELINE_CAPABILITIES_LINE), 1,
            "the one policy line this ticket edits could not be located at the baseline")
        without = "".join(line for line in baseline
                          if line != BASELINE_CAPABILITIES_LINE)
        self.assertEqual(
            hashlib.sha256(without.encode()).hexdigest(),
            CONTRACTS_POLICY_REGION_DIGEST,
            "the pinned policy region is not the baseline's; the excluded line range is "
            "hiding a change to policy beyond the one widened CAPABILITIES declaration")

    def test_policy_modules_carry_no_standalone_branch(self) -> None:
        """A second, INDEPENDENT reading of the same property, not a substitute for it.

        The digest gate above is D4.1's mechanism and the authority.  This one survives a
        deliberate re-pin and names the specific thing a re-pin might be smuggling, so the
        two fail for different reasons and neither can quietly stand in for the other.
        """
        for name in POLICY_MODULES:
            source = (ENGINE / name).read_text()
            self.assertNotIn(
                "standalone", source,
                f"{name} references the standalone runtime; the CLI-specific difference "
                "must be absorbed by the driver, and workflow/decision/review policy must "
                "carry no per-runtime branch")

    def test_no_excluded_layer_import(self) -> None:
        """AC-37-18: no GUI, Electron, renderer, mobile, relay or desktop import."""
        forbidden = ("electron", "renderer", "mobile", "relay", "desktop", "webview",
                     "browser_window")
        for path in _standalone_modules():
            source = path.read_text().lower()
            for token in forbidden:
                self.assertNotIn(
                    f"import {token}", source,
                    f"{path.name} imports an excluded layer {token!r}; GUI, Electron, "
                    "mobile and remote hosts are out of the MVP")

    def test_no_standalone_module_imports_the_orca_adapter(self) -> None:
        """``StandaloneAdapter`` is a SIBLING of ``OrcaAdapter``, never a subclass.

        Asserted structurally: no standalone module imports ``orca_adapter`` at all, so
        there is no path by which Orca-specific behaviour could be inherited into the
        standalone runtime and no way for the standalone runtime to invoke an Orca verb.
        """
        for path in _standalone_modules():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""] + [alias.name for alias in node.names]
                self.assertNotIn(
                    "orca_adapter", names,
                    f"{path.name} imports orca_adapter; the standalone adapter is a "
                    "sibling of it, not a subclass, and copies no Orca code")

    def test_no_new_runtime_dependency(self) -> None:
        """AC-37-19: every standalone import is stdlib or an intra-package sibling."""
        stdlib = {
            "__future__", "ast", "collections", "contextlib", "copy", "ctypes",
            "dataclasses",
            "datetime", "errno", "fcntl", "hashlib", "hmac", "importlib", "inspect", "io",
            "json", "math", "os", "pathlib", "pty", "re", "resource", "select", "shlex",
            "shutil", "signal", "struct", "subprocess", "termios", "time", "tokenize",
            "typing", "unittest", "uuid",
        }
        for path in _standalone_modules():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split(".")[0]
                        self.assertIn(
                            root, stdlib | TOOLS_SIBLINGS,
                            f"{path.name} imports {alias.name!r}, which is neither stdlib, "
                            "an intra-package sibling, nor a declared tools/ sibling; the "
                            "standalone runtime adds no runtime dependency")
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    root = (node.module or "").split(".")[0]
                    self.assertIn(
                        root, stdlib | {"scripts"} | TOOLS_SIBLINGS,
                        f"{path.name} imports from {node.module!r}, which is neither "
                        "stdlib, an intra-package sibling, nor a declared tools/ sibling")

    def test_every_tools_sibling_import_uses_the_installed_flat_layout_idiom(self) -> None:
        """A ``tools/`` sibling must be imported the way ``orca_adapter`` imports one.

        The engine package ships INSIDE ``tools/``, so at run time a sibling like
        ``decision_contract`` is a top-level module and ``scripts.decision_contract`` does
        not exist.  The mandatory idiom is a LAZY, in-function ``try: from scripts.X import Y
        / except ImportError: import X`` -- lazy because a module-scope import would make the
        whole package unimportable whenever the sibling is absent, which is what
        ``test_os42_installed_e2e`` exists to catch.
        """
        for path in _standalone_modules():
            source = path.read_text()
            for sibling in sorted(TOOLS_SIBLINGS):
                if sibling not in source:
                    continue
                with self.subTest(module=path.name, sibling=sibling):
                    self.assertIn(
                        f"from scripts import {sibling}", source,
                        f"{path.name} imports {sibling} without the repository-layout half "
                        "of the idiom")
                    self.assertIn(
                        f"import {sibling}", source.split(
                            f"from scripts import {sibling}", 1)[1],
                        f"{path.name} has no flat-layout fallback for {sibling}; the "
                        "installed package would fail to import it")
                    tree = ast.parse(source)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom) and node.level == 0 \
                                and (node.module or "").startswith("scripts"):
                            self.assertGreater(
                                node.col_offset, 0,
                                f"{path.name} imports {sibling} at module scope; it must be "
                                "lazy, or the package becomes unimportable without it")

    def test_no_vendored_orca_file(self) -> None:
        """No standalone module is a copy of an existing engine module.

        Compared by digest over the whole file: "Orca 코드를 복사하지 않는다" is a property
        about bytes, and a byte comparison is how it becomes checkable.
        """
        existing = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in ENGINE.glob("*.py")
                    if not path.name.startswith("standalone_")}
        for path in _standalone_modules():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertNotIn(
                digest, set(existing.values()),
                f"{path.name} is a byte-for-byte copy of an existing engine module")

    def test_ports_py_is_unedited(self) -> None:
        """AC-37-22: ``ports.py`` is not edited by this ticket -- a PINNED DIGEST.

        The strongest available form of "no existing signature moved": not a comparison of
        the six signatures against a transcription of them, but a digest over the whole
        file.  A default, an annotation or a whitespace change all fail it.
        """
        digest = hashlib.sha256((ENGINE / "ports.py").read_bytes()).hexdigest()
        self.assertEqual(
            digest, PORTS_PY_DIGEST,
            "ports.py changed.  AC-37-22 freezes the six AgentExecutionPort signatures and "
            "OS-37 is additive: every addition is a new KEY inside the mappings those six "
            "methods already exchange, never a new method, a re-signed one or a new port. "
            f"observed={digest}")

    def test_delivery_mode_is_never_reassigned(self) -> None:
        """D4.2b.  ``delivery_mode`` is assigned in EXACTLY ONE place: profile parsing.

        This is what makes the four capability-mismatch outcomes genuinely unrecoverable.
        USER DIRECTIVE D-C requires a declared-vs-actual disagreement to FAIL CLOSED, and
        the one way a later refactor could quietly undo that is by "repairing" a mismatch --
        `resolve_unknown` learning a branch that flips the mode, or a driver recomputing its
        own.  A runtime that can change its own delivery mode has no fail-closed outcome at
        all, only a retry.

        Checked over the AST of every engine module, so a docstring mentioning the name does
        not trip it and a real assignment cannot hide behind one.
        """
        import ast as _ast

        def _forwards_the_profiles_value(value) -> bool:
            """True when the value READ is the profile's own field, not a new one.

            Forwarding `profile.delivery_mode` into a record or an intent is not a second
            author of the value -- it is the single author being quoted.  A literal, a
            conditional, or anything computed IS a second author, and that is what this
            case exists to refuse.
            """
            if value is None:                      # a bare annotation declares no value
                return True
            if isinstance(value, _ast.Attribute) and value.attr == "delivery_mode":
                return True
            if isinstance(value, _ast.Subscript):  # `capabilities["delivery_mode"]`
                index = getattr(value, "slice", None)
                return (isinstance(index, _ast.Constant)
                        and index.value == "delivery_mode")
            if isinstance(value, _ast.Call):       # `str(profile.delivery_mode)`
                return any(_forwards_the_profiles_value(arg) for arg in value.args)
            return False

        offenders = []
        for path in sorted(ENGINE.glob("*.py")):
            if path.name == "standalone_profile.py":
                continue                     # the ONE place: the profile owns the value
            tree = _ast.parse(path.read_text())
            for node in _ast.walk(tree):
                targets, value = [], None
                if isinstance(node, _ast.Assign):
                    targets, value = list(node.targets), node.value
                elif isinstance(node, (_ast.AnnAssign, _ast.AugAssign)):
                    targets, value = [node.target], getattr(node, "value", None)
                for target in targets:
                    name = (target.id if isinstance(target, _ast.Name)
                            else target.attr if isinstance(target, _ast.Attribute)
                            else "")
                    if name == "delivery_mode" and not _forwards_the_profiles_value(value):
                        offenders.append(f"{path.name}:{node.lineno}")
                # A keyword argument `delivery_mode=` carrying anything but the profile's
                # own field would be a second author of the value by another route.
                if isinstance(node, _ast.Call):
                    for keyword in node.keywords:
                        if (keyword.arg == "delivery_mode"
                                and not _forwards_the_profiles_value(keyword.value)):
                            offenders.append(f"{path.name}:{node.lineno} (keyword)")
        self.assertEqual(
            offenders, [],
            "delivery_mode is assigned outside profile parsing: " + ", ".join(offenders)
            + ".  A runtime that can change its own delivery mode can 'recover' from a "
            "capability mismatch instead of failing closed, which is exactly what USER "
            "DIRECTIVE D-C forbids")

    def test_resolve_unknown_has_no_branch_that_changes_a_delivery_mode(self) -> None:
        """The same rule, from the other side: no unknown resolves INTO a mode change."""
        from scripts.deterministic_workflow import standalone_lifecycle as lifecycle
        import inspect as _inspect
        source = _inspect.getsource(lifecycle.resolve_unknown)
        self.assertNotIn("delivery_mode", source,
                         "resolve_unknown mentions delivery_mode; the fail-closed outcomes "
                         "must not be resolvable by switching modes")
        for reason in lifecycle.CAPABILITY_FAILURE_REASONS:
            self.assertIsInstance(reason, str)
        self.assertEqual(len(set(lifecycle.CAPABILITY_FAILURE_REASONS)), 5,
                         "the capability-outcome vocabulary changed size; D4.2b names "
                         "exactly five and each one fails closed")

    def test_no_policy_module_reads_the_delivery_mode_axis(self) -> None:
        """The axis is DRIVER capability, not workflow policy.

        The ticket's constraint is that per-CLI differences are absorbed by the driver and
        that no CLI branch is added to workflow, decision or review policy.  A
        `delivery_mode` branch in `graph.py` would be that branch wearing a different name,
        so it is refused by name as well as by the CLI-token sweep.
        """
        for name in ("graph.py", "routing.py", "executor.py", "state.py",
                     "fake_adapter.py", "orca_adapter.py"):
            path = ENGINE / name
            if not path.exists():
                continue
            with self.subTest(name):
                self.assertNotIn(
                    "delivery_mode", path.read_text(),
                    f"{name} reads the delivery_mode capability axis; per-CLI differences "
                    "belong to the driver and this is a policy module")

    def test_both_trees_hold_identical_standalone_modules(self) -> None:
        """B-2: every standalone module is byte-identical in both skill trees."""
        for path in _standalone_modules():
            mirror = MIRROR / path.name
            self.assertTrue(mirror.exists(),
                            f"{path.name} is missing from the mirrored tree")
            self.assertEqual(
                path.read_bytes(), mirror.read_bytes(),
                f"{path.name} differs between the two trees; every engine edit is a PAIRED "
                "edit and validate_skills.py compares the bytes")
        for mirror in sorted(MIRROR.glob("standalone_*.py")):
            self.assertTrue(
                (ENGINE / mirror.name).exists(),
                f"{mirror.name} exists only in the mirrored tree; a stray .py in either "
                "tree fails validate_skills.py's file-set comparison")


def _is_docstring(tree: ast.AST, node: ast.Constant) -> bool:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            body = getattr(parent, "body", [])
            if body and isinstance(body[0], ast.Expr) and body[0].value is node:
                return True
    return False




if __name__ == "__main__":
    unittest.main()
