#!/usr/bin/env python3
"""Final Review evaluation: materialize a subject workspace, and score a review.

OS-22 sections 5 and 6. Five subcommands, deliberately separate:

    materialize      build a reviewable workspace out of scripts/fixtures/final_review_eval
    verify-fixture   prove the fixture is what the key says it is, before anyone reviews it
    scan-leak        prove no key material reached a target -- a tree, a workspace, or the
                     retained reviewer input OS-22 now keeps
    parse-report     turn a section 11/17 shaped review into a normalized findings document
    score            compute the metric block from findings + key + optional adjudications

`parse-report` and `score` are separate commands on purpose: section 5 requires reviewer
execution and scoring to be separated, and separating PARSING from scoring additionally
makes the parse auditable -- the normalized findings JSON is an artifact a human can read
before any metric is computed.

Two properties this file exists to hold, and neither is a matter of care:

* **An unmatched finding is never a false positive.** There is no code path, flag or
  config that maps one. Precision and false-positive rate are REFUSED, with a
  machine-readable reason, until an independent adjudication says otherwise.
* **The metrics document contains no clock-derived value.** Identical inputs produce
  byte-identical metrics output, with no excepted keys. "When was this scored?" is
  answered by a separate provenance sidecar that the default invocation does not write.

Repository-side tooling. Standard library only, CPython >= 3.11.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_DIR = REPO_ROOT / "scripts" / "fixtures" / "final_review_eval"

FINDINGS_SCHEMA_VERSION = "1.0"
METRICS_SCHEMA_VERSION = "1.0"
ADJUDICATION_SCHEMA_VERSION = "1.0"
KEY_SCHEMA_VERSION = "1.0"
PROVENANCE_SCHEMA_VERSION = "1.0"

# One archetype per ticket section 5 category, spelled exactly.
ARCHETYPES = (
    "value_vs_presence",
    "omitted_call_site_propagation",
    "equality_boundary",
    "losing_precedence_fallback",
    "validation_scope_gap",
)

# The one classification an unmatched finding gets by default, and the only two an
# adjudication can change it to. There is no fourth value and no inference rule.
UNADJUDICATED = "UNADJUDICATED"
ADJUDICATED_TRUE_POSITIVE = "ADJUDICATED_TRUE_POSITIVE"
ADJUDICATED_FALSE_POSITIVE = "ADJUDICATED_FALSE_POSITIVE"
VERDICT_VALUES = ("true_positive", "false_positive")
# DEC-8 rule 4 made structural: a verdict object may carry NOTHING else, so a
# historical-corpus signal ("was corrected", "was not disputed") is unrepresentable
# rather than merely discouraged.
VERDICT_KEYS = frozenset({"finding_id", "verdict", "rationale"})
ADJUDICATION_KEYS = frozenset(
    {
        "schema_version",
        "adjudicator",
        "adjudicated_at",
        "closed_world",
        "exhaustive_attestation",
        "verdicts",
    }
)
ATTESTATION_KEYS = ("scope", "statement", "attested_by", "attested_at")

UNMATCHED_REASONS = ("no_key_match", "unresolvable_location", "ambiguous_match")

EXIT_OK = 0
EXIT_INPUT_ERROR = 1
EXIT_CONTRACT_VIOLATION = 2
EXIT_PRECISION_REFUSED = 3
EXIT_LEAK_OR_FIXTURE = 4


class EvalInputError(Exception):
    """A file is missing, unreadable, not JSON, or carries an unknown schema MAJOR."""


class EvalContractError(Exception):
    """The inputs are readable but violate the contract they claim to follow."""


class FixtureError(Exception):
    """The fixture, the workspace, or a leak scan failed its own check."""


# ---- digests and the manifest ------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_text(files: dict[str, str]) -> str:
    """`<relpath>\\0<sha256-hex>\\n` per file, sorted.

    Stable across filesystems and directory orderings, which is what makes the digest
    a property of the CONTENT rather than of the machine that computed it.
    """
    return "".join(f"{name}\0{files[name]}\n" for name in sorted(files))


def fixture_digest(files: dict[str, str]) -> str:
    return "sha256:" + sha256_text(manifest_text(files))


def read_tree(root: Path) -> dict[str, str]:
    """Every file under `root`, keyed by POSIX-relative path."""
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


def build_diff(base: dict[str, str], head: dict[str, str]) -> str:
    """The `base` -> `head` unified diff, derived rather than stored.

    Deterministic by construction: files in sorted order, three lines of context,
    `a/<path>` vs `b/<path>`, LF endings. Deriving it at materialize time is what
    keeps it from drifting away from the trees -- and a STORED patch would be a
    description of the change rather than the change itself.
    """
    chunks: list[str] = []
    for name in sorted(set(base) | set(head)):
        before = base.get(name, "").splitlines(keepends=True)
        after = head.get(name, "").splitlines(keepends=True)
        if before == after:
            continue
        chunks.extend(
            difflib.unified_diff(
                before, after, fromfile=f"a/{name}", tofile=f"b/{name}", n=3
            )
        )
    return "".join(chunks)


def changed_head_lines(before: str, after: str) -> set[int]:
    """1-based `after` line numbers the diff touches."""
    matcher = difflib.SequenceMatcher(
        None, before.splitlines(), after.splitlines(), autojunk=False
    )
    touched: set[int] = set()
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        touched.update(range(j1 + 1, j2 + 1))
    return touched


# ---- the key ------------------------------------------------------------------------


def load_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EvalInputError(f"{label} not found: {path}") from None
    except (OSError, UnicodeDecodeError) as error:
        raise EvalInputError(f"{label} cannot be read: {error}") from None
    except json.JSONDecodeError as error:
        raise EvalInputError(f"{label} is not JSON: {error}") from None


def require_major(document: Any, expected: str, *, label: str) -> None:
    if not isinstance(document, dict):
        raise EvalInputError(f"{label} is not a JSON object")
    version = document.get("schema_version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+", version):
        raise EvalInputError(f"{label} has no readable schema_version")
    if version.split(".")[0] != expected.split(".")[0]:
        raise EvalInputError(
            f"{label} schema_version {version} has an unknown MAJOR; this tool reads "
            f"{expected.split('.')[0]}.x"
        )


def load_key(path: Path) -> dict:
    key = load_json(path, label="answer key")
    require_major(key, KEY_SCHEMA_VERSION, label="answer key")
    defects = key.get("seeded_defects")
    if not isinstance(defects, list) or not defects:
        raise EvalContractError("the answer key lists no entries")
    seen: set[str] = set()
    for entry in defects:
        identifier = entry.get("id")
        if not identifier or identifier in seen:
            raise EvalContractError(f"duplicate or missing key entry id: {identifier!r}")
        seen.add(identifier)
        if entry.get("archetype") not in ARCHETYPES:
            raise EvalContractError(
                f"{identifier}: unknown archetype {entry.get('archetype')!r}"
            )
        location = entry.get("location") or {}
        if not location.get("file") or not location.get("symbol"):
            raise EvalContractError(f"{identifier}: incomplete location")
        line_range = location.get("line_range")
        if (
            not isinstance(line_range, list)
            or len(line_range) != 2
            or not all(isinstance(value, int) for value in line_range)
            or line_range[0] > line_range[1]
        ):
            raise EvalContractError(f"{identifier}: malformed line_range")
        criterion = entry.get("match_criterion") or {}
        groups = (criterion.get("claim_requirements") or {}).get("all_of")
        if not isinstance(groups, list) or not groups:
            raise EvalContractError(f"{identifier}: malformed match_criterion")
        for group in groups:
            forms = (group or {}).get("any_of")
            if not isinstance(forms, list) or not forms:
                raise EvalContractError(f"{identifier}: malformed claim group")
        if not isinstance(criterion.get("location_tolerance_lines"), int):
            raise EvalContractError(f"{identifier}: missing location_tolerance_lines")
    # Nothing anywhere reads a target finding count. The literal below is a guard that
    # says so in the document itself, so section 5's "expected finding count" cannot
    # leak through the key even by accident.
    if key.get("expected_finding_count_is_not_a_contract") is not True:
        raise EvalContractError(
            "the answer key must carry expected_finding_count_is_not_a_contract: true"
        )
    return key


def key_fixture_digest(path: Path) -> str:
    """The one field `materialize` reads out of the key, for one comparison."""
    document = load_json(path, label="answer key")
    digest = document.get("fixture_digest")
    if not isinstance(digest, str) or not digest:
        raise EvalContractError("the answer key carries no fixture_digest")
    return digest


# ---- leak scanning --------------------------------------------------------------------

FIXED_LEAK_MARKERS = (
    "answer key",
    "answer_key",
    "seeded defect",
    "seeded_defect",
    "expected finding",
    "expected_finding_count",
    "seeded",
    "정답",
    "시드",
)
_EXPECTED_COUNT = re.compile(
    r"(?is)(?=.{0,40}?(?:finding|defect|issue|bug|결함|발견))"
    r".{0,40}?\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|하나|둘|셋|넷|다섯)\b.{0,40}?"
    r"(?:expect|should find|must find|총|개의)"
)
_EXPECTED_COUNT_REVERSE = re.compile(
    r"(?is)(?:expect|should find|must find|총|개의).{0,40}?"
    r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten"
    r"|하나|둘|셋|넷|다섯)\b.{0,40}?(?:finding|defect|issue|bug|결함|발견)"
)


def _shingles(text: str, size: int = 6) -> set[str]:
    words = text.split()
    return {
        " ".join(words[index : index + size])
        for index in range(0, max(len(words) - size + 1, 0))
    }


def key_leak_tokens(key: dict) -> set[str]:
    """What must not appear in a reviewer's scope.

    Deliberately NOT "every string in the key": the key names real symbols
    (`resolve_tier`) and real files that MUST appear in the subject tree. What may not
    appear is the key's own vocabulary -- its ids, its archetype names, its fixture id,
    long verbatim runs of its prose, and the fixed marker set.
    """
    tokens: set[str] = set(FIXED_LEAK_MARKERS)
    fixture_id = key.get("fixture_id")
    if isinstance(fixture_id, str) and fixture_id:
        tokens.add(fixture_id.casefold())
    for entry in key.get("seeded_defects", []):
        identifier = entry.get("id")
        if identifier:
            tokens.add(str(identifier).casefold())
        archetype = entry.get("archetype")
        if archetype:
            tokens.add(str(archetype).casefold())
        for field in ("summary", "negative_space_argument"):
            text = entry.get(field) or ""
            tokens.update(shingle.casefold() for shingle in _shingles(" ".join(text.split())))
    return {token for token in tokens if token}


def scan_leak(
    key: dict, targets: list[Path], *, exclude_names: tuple[str, ...] = ()
) -> list[dict]:
    """Every hit, as `{path, token}` / `{path, expected_count_statement}` records.

    `exclude_names` exists for exactly one caller and is named rather than hard-coded:
    `materialize` writes its own MANIFEST.json, whose entire content is relative paths,
    digests and the fixture id -- and the fixture id is itself a leak token, so a
    workspace could never pass its own scan otherwise. Nothing about a seeded entry can
    reach that file, because this module generates every byte of it. The `scan-leak`
    command applies no exclusion at all: it scans exactly what it is pointed at.
    """
    tokens = key_leak_tokens(key)
    hits: list[dict] = []
    for target in targets:
        files = (
            [path for path in sorted(target.rglob("*")) if path.is_file()]
            if target.is_dir()
            else [target]
        )
        for path in files:
            if "__pycache__" in path.parts or path.name in exclude_names:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            haystack = " ".join(raw.split()).casefold()
            for token in sorted(tokens):
                if token in haystack:
                    hits.append({"path": str(path), "token": token})
            for pattern in (_EXPECTED_COUNT, _EXPECTED_COUNT_REVERSE):
                match = pattern.search(haystack)
                if match is not None:
                    hits.append(
                        {
                            "path": str(path),
                            "expected_count_statement": match.group(0)[:120],
                        }
                    )
                    break
    return hits


# ---- materialize -----------------------------------------------------------------------


def materialize(dest: Path, fixture: Path) -> dict:
    """Build the workspace a reviewer is pointed at. Refuses to reuse a non-empty one.

    `key/` and `adjudications/` are never read by this function beyond the single
    `fixture_digest` comparison below: the code path does not open their contents, so a
    mistake cannot copy them.

    No `.git` is created and none is copied. The reviewer gets DIFF.patch, which is what
    section 17 hands it anyway.
    """
    if dest.exists() and any(dest.iterdir()):
        raise FixtureError(
            f"{dest} is not empty; materialize never overwrites, merges or partially "
            "reuses a destination"
        )
    base = read_tree(fixture / "subject" / "base")
    head = read_tree(fixture / "subject" / "head")
    if not base or not head:
        raise FixtureError(f"{fixture}/subject is missing a base or head tree")
    diff = build_diff(base, head)
    files = {name: sha256_text(text) for name, text in head.items()}
    files["DIFF.patch"] = sha256_text(diff)
    digest = fixture_digest(files)

    staging = Path(tempfile.mkdtemp(prefix="final_review_eval_"))
    try:
        for name, text in head.items():
            path = staging / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        (staging / "DIFF.patch").write_text(diff, encoding="utf-8")
        manifest = {
            "fixture_id": _fixture_id(fixture),
            "fixture_digest": digest,
            "files": dict(sorted(files.items())),
        }
        (staging / "MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        # Post-copy assertions, all before anything is visible at --dest.
        for path in staging.rglob("*"):
            for part in path.relative_to(staging).parts:
                if part in ("key", "adjudications", ".git"):
                    raise FixtureError(
                        f"a path component named {part!r} reached the workspace"
                    )
        key = load_key(fixture / "key" / "answer_key.json")
        hits = scan_leak(key, [staging], exclude_names=("MANIFEST.json",))
        if hits:
            raise FixtureError(
                "key material reached the materialized workspace: "
                + json.dumps(hits[:5], ensure_ascii=False)
            )
        expected = key_fixture_digest(fixture / "key" / "answer_key.json")
        if expected != digest:
            raise EvalContractError(
                f"fixture digest mismatch: the key expects {expected}, the tree "
                f"computes {digest}. Update the key deliberately -- there is no flag "
                "that rewrites the value it is checking against."
            )
        dest.mkdir(parents=True, exist_ok=True)
        for entry in sorted(staging.iterdir()):
            shutil.move(str(entry), str(dest / entry.name))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {"dest": str(dest), "fixture_digest": digest, "files": len(files)}


def _fixture_id(fixture: Path) -> str:
    key_path = fixture / "key" / "answer_key.json"
    if key_path.is_file():
        document = load_json(key_path, label="answer key")
        identifier = document.get("fixture_id")
        if isinstance(identifier, str) and identifier:
            return identifier
    return fixture.name


# ---- verify-fixture ---------------------------------------------------------------------


def _run_suite(tree: dict[str, str]) -> tuple[bool, str]:
    """Run a subject tree's own suite in a scratch copy, so nothing is left behind."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in tree.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", ".", "-p", "test_*.py"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0, (completed.stderr or completed.stdout)[-800:]


def verify_fixture(fixture: Path, key_path: Path) -> list[str]:
    """Every failure, as a list. Empty means the fixture is what the key says it is."""
    problems: list[str] = []
    key = load_key(key_path)
    base = read_tree(fixture / "subject" / "base")
    head = read_tree(fixture / "subject" / "head")

    files = {name: sha256_text(text) for name, text in head.items()}
    files["DIFF.patch"] = sha256_text(build_diff(base, head))
    digest = fixture_digest(files)
    if digest != key.get("fixture_digest"):
        problems.append(
            f"fixture digest mismatch: key {key.get('fixture_digest')}, tree {digest}"
        )

    for entry in key["seeded_defects"]:
        identifier = entry["id"]
        location = entry["location"]
        name, symbol = location["file"], location["symbol"]
        start, end = location["line_range"]
        text = head.get(name)
        if text is None:
            problems.append(f"{identifier}: {name} does not exist in head/")
            continue
        lines = text.splitlines()
        defined = [
            number
            for number, line in enumerate(lines, start=1)
            if re.match(rf"\s*(?:def|class)\s+{re.escape(symbol)}\b", line)
        ]
        if not any(start <= number <= end for number in defined):
            problems.append(
                f"{identifier}: {symbol} is not defined within lines {start}-{end} of "
                f"{name} (found at {defined or 'nowhere'})"
            )
        touched = changed_head_lines(base.get(name, ""), text)
        if not touched & set(range(start, end + 1)):
            # A key entry that describes a range the feature diff never touched is a
            # key entry about something the feature did not introduce.
            problems.append(
                f"{identifier}: the base -> head diff does not touch lines "
                f"{start}-{end} of {name}"
            )

    for label, tree in (("base", base), ("head", head)):
        passed, output = _run_suite(tree)
        if not passed:
            # A green head suite is the point: a failing test would localize a defect
            # for free, and the fixture would stop measuring search at all.
            problems.append(f"the {label} tree's own test suite does not pass: {output}")

    hits = scan_leak(key, [fixture / "subject"])
    if hits:
        problems.append(
            "key material is present in subject/: "
            + json.dumps(hits[:5], ensure_ascii=False)
        )
    return problems


# ---- parse-report -------------------------------------------------------------------------

FINDING_FIELDS = (
    ("ID", "id"),
    ("Quality Attribute", "quality_attribute"),
    ("Severity", "severity"),
    ("Blocking", "blocking"),
    ("Responsible Phase", "responsible_phase"),
    ("Location", "location_raw"),
    ("Issue", "issue"),
    ("Reason / Evidence", "reason"),
    ("Required Action", "required_action"),
)
_FIELD_LINE = re.compile(
    r"^(" + "|".join(re.escape(label) for label, _ in FINDING_FIELDS) + r")\s*:\s*(.*)$"
)
_ID_LINE = re.compile(r"(?m)^ID:\s*(\S+)\s*$")

_LOCATION_PATTERNS = (
    re.compile(r"^(?P<path>[\w./\-]+):(?P<line>\d+)\b"),
    re.compile(r"^(?P<path>[\w./\-]+):(?P<line>\d+)-\d+\b"),
    re.compile(r"^(?P<path>[\w./\-]+)\s*(?:line|L|:)\s*(?P<line>\d+)\b", re.IGNORECASE),
    re.compile(r"^(?P<path>[\w./\-]+\.(?:py|md))\b"),
)


def parse_location(raw: str, workspace: Path | None) -> tuple[str | None, int | None]:
    """`(file, line)` -- first match wins, and an unresolvable location stays null."""
    text = raw.strip().strip("`").lstrip("- ")
    for pattern in _LOCATION_PATTERNS:
        match = pattern.match(text)
        if match is None:
            continue
        candidate = match.group("path")
        line = match.groupdict().get("line")
        if workspace is not None:
            if not (workspace / candidate).is_file():
                continue
        elif not re.fullmatch(r"[\w./\-]+\.(?:py|md)", candidate):
            continue
        return candidate, int(line) if line else None
    return None, None


def parse_report(text: str, workspace: Path | None) -> list[dict]:
    """The section 11/17 Finding Contract blocks, verbatim plus a parsed location."""
    findings: list[dict] = []
    matches = list(_ID_LINE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        block = text[match.start() : end]
        fields: dict[str, str] = {}
        current: str | None = None
        for line in block.splitlines():
            field = _FIELD_LINE.match(line)
            if field is not None:
                label, value = field.group(1), field.group(2)
                current = dict(FINDING_FIELDS)[label]
                fields[current] = value.strip()
            elif current is not None and line.strip():
                fields[current] = (fields.get(current, "") + " " + line.strip()).strip()
        location_file, location_line = parse_location(
            fields.get("location_raw", ""), workspace
        )
        findings.append(
            {
                "id": fields.get("id", match.group(1)),
                "location_raw": fields.get("location_raw", ""),
                "location_file": location_file,
                "location_line": location_line,
                "severity": fields.get("severity", ""),
                "blocking": fields.get("blocking", "").upper() == "YES",
                "quality_attribute": fields.get("quality_attribute", ""),
                "responsible_phase": fields.get("responsible_phase", ""),
                "issue": fields.get("issue", ""),
                "reason": fields.get("reason", ""),
                "required_action": fields.get("required_action", ""),
                "raw": block.rstrip(),
            }
        )
    return findings


# ---- matching ---------------------------------------------------------------------------------

_MARKDOWN = re.compile(r"[`*_#>\[\]]")


def normalize_claim(text: str) -> str:
    """The one normalization both sides of every comparison go through."""
    return " ".join(_MARKDOWN.sub("", text).split()).casefold()


def _claim_text(finding: dict) -> str:
    return normalize_claim(
        " ".join(
            (
                finding.get("issue") or "",
                finding.get("reason") or "",
                finding.get("required_action") or "",
            )
        )
    )


def _groups_satisfied(claim: str, entry: dict) -> int:
    groups = entry["match_criterion"]["claim_requirements"]["all_of"]
    return sum(
        1
        for group in groups
        if any(normalize_claim(form) in claim for form in group["any_of"])
    )


def match_findings(findings: list[dict], key: dict) -> tuple[list[dict], list[dict]]:
    """One-to-one assignment, deterministic, with an explicit ambiguity guard.

    An unmatched finding is UNADJUDICATED. It is never, by any path, a false positive.
    """
    entries = key["seeded_defects"]
    candidates: dict[str, list[dict]] = {finding["id"]: [] for finding in findings}
    for finding in findings:
        claim = _claim_text(finding)
        raw_location = normalize_claim(finding.get("location_raw") or "")
        for entry in entries:
            groups = entry["match_criterion"]["claim_requirements"]["all_of"]
            satisfied = _groups_satisfied(claim, entry)
            if satisfied != len(groups):
                continue
            location = entry["location"]
            tolerance = entry["match_criterion"]["location_tolerance_lines"]
            if finding["location_file"] != location["file"]:
                continue
            line = finding["location_line"]
            start, end = location["line_range"]
            if line is not None and not (start - tolerance <= line <= end + tolerance):
                continue
            symbol = normalize_claim(location["symbol"])
            midpoint = (start + end) / 2
            candidates[finding["id"]].append(
                {
                    "entry": entry,
                    "claim_groups_satisfied": satisfied,
                    "symbol_hit": symbol in claim or symbol in raw_location,
                    "line_distance": abs(line - midpoint) if line is not None else 0.0,
                }
            )

    ambiguous: dict[str, str] = {}
    for finding in findings:
        options = candidates[finding["id"]]
        by_file: dict[str, list[dict]] = {}
        for option in options:
            by_file.setdefault(option["entry"]["location"]["file"], []).append(option)
        for name, group in by_file.items():
            if len(group) < 2:
                continue
            with_symbol = [option for option in group if option["symbol_hit"]]
            if len(with_symbol) == 1:
                candidates[finding["id"]] = with_symbol
                break
            if finding["location_line"] is not None:
                nearest = min(
                    group,
                    key=lambda option: (
                        option["line_distance"],
                        option["entry"]["id"],
                    ),
                )
                candidates[finding["id"]] = [nearest]
                break
            candidates[finding["id"]] = []
            ambiguous[finding["id"]] = "ambiguous_match"
            break

    pairs = [
        (finding, option)
        for finding in findings
        for option in candidates[finding["id"]]
    ]
    pairs.sort(
        key=lambda pair: (
            -pair[1]["claim_groups_satisfied"],
            not pair[1]["symbol_hit"],
            pair[1]["line_distance"],
            pair[1]["entry"]["id"],
            pair[0]["id"],
        )
    )
    matched: list[dict] = []
    used_findings: set[str] = set()
    used_entries: set[str] = set()
    for finding, option in pairs:
        if finding["id"] in used_findings or option["entry"]["id"] in used_entries:
            continue
        used_findings.add(finding["id"])
        used_entries.add(option["entry"]["id"])
        matched.append(
            {
                "finding_id": finding["id"],
                "seeded_defect_id": option["entry"]["id"],
                "claim_groups_satisfied": option["claim_groups_satisfied"],
                "symbol_hit": option["symbol_hit"],
                "line_distance": option["line_distance"],
            }
        )
    matched.sort(key=lambda item: (item["seeded_defect_id"], item["finding_id"]))

    unmatched: list[dict] = []
    for finding in findings:
        if finding["id"] in used_findings:
            continue
        if finding["id"] in ambiguous:
            reason = "ambiguous_match"
        elif finding["location_file"] is None:
            reason = "unresolvable_location"
        else:
            reason = "no_key_match"
        unmatched.append({"finding_id": finding["id"], "reason": reason})
    return matched, unmatched


# ---- scoring -------------------------------------------------------------------------------------


def load_adjudications(path: Path) -> dict:
    document = load_json(path, label="adjudications")
    require_major(document, ADJUDICATION_SCHEMA_VERSION, label="adjudications")
    unknown = set(document) - ADJUDICATION_KEYS
    if unknown:
        raise EvalContractError(
            f"unknown top-level adjudication key(s): {sorted(unknown)}"
        )
    verdicts = document.get("verdicts") or []
    if not isinstance(verdicts, list):
        raise EvalContractError("verdicts must be a list")
    seen: set[str] = set()
    for verdict in verdicts:
        if not isinstance(verdict, dict):
            raise EvalContractError("a verdict must be an object")
        extra = set(verdict) - VERDICT_KEYS
        if extra:
            raise EvalContractError(
                f"unknown key(s) in a verdict object: {sorted(extra)}; a verdict "
                "carries a finding id, a verdict and a rationale, and nothing that "
                "could encode a historical-corpus signal"
            )
        finding_id = verdict.get("finding_id")
        if not finding_id or finding_id in seen:
            raise EvalContractError(f"duplicate or missing finding_id: {finding_id!r}")
        seen.add(finding_id)
        if verdict.get("verdict") not in VERDICT_VALUES:
            raise EvalContractError(
                f"{finding_id}: verdict must be one of {VERDICT_VALUES}"
            )
        if not str(verdict.get("rationale") or "").strip():
            raise EvalContractError(
                f"{finding_id}: rationale is required and must be non-empty"
            )
    if document.get("closed_world"):
        attestation = document.get("exhaustive_attestation")
        if not isinstance(attestation, dict) or not all(
            str(attestation.get(field) or "").strip() for field in ATTESTATION_KEYS
        ):
            raise EvalContractError(
                "closed_world requires a complete exhaustive_attestation "
                f"({', '.join(ATTESTATION_KEYS)})"
            )
    return document


def score(
    findings_document: dict,
    key: dict,
    *,
    adjudications: dict | None = None,
    workspace: Path | None = None,
    run_verdicts: list[str] | None = None,
) -> dict:
    """The metric block. NO key of this document is derived from the clock."""
    findings = findings_document.get("findings") or []
    identifiers = [finding["id"] for finding in findings]
    if len(set(identifiers)) != len(identifiers):
        raise EvalContractError("duplicate finding id in the findings document")

    if workspace is not None:
        manifest_path = workspace / "MANIFEST.json"
        manifest = load_json(manifest_path, label="workspace MANIFEST.json")
        if manifest.get("fixture_digest") != key.get("fixture_digest"):
            raise EvalContractError(
                "workspace fixture_digest does not match the key's; metrics computed "
                "against a different tree than the key describes are not metrics"
            )

    matched, unmatched = match_findings(findings, key)
    entries = key["seeded_defects"]
    total_defects = len(entries)
    detected = len(matched)
    missed = sorted(
        entry["id"]
        for entry in entries
        if entry["id"] not in {item["seeded_defect_id"] for item in matched}
    )

    verdicts = {
        verdict["finding_id"]: verdict["verdict"]
        for verdict in (adjudications or {}).get("verdicts", [])
    }
    classified = []
    true_positives = 0
    false_positives = 0
    for item in unmatched:
        verdict = verdicts.get(item["finding_id"])
        if verdict == "true_positive":
            classification = ADJUDICATED_TRUE_POSITIVE
            true_positives += 1
        elif verdict == "false_positive":
            classification = ADJUDICATED_FALSE_POSITIVE
            false_positives += 1
        else:
            classification = UNADJUDICATED
        classified.append({**item, "classification": classification})
    unadjudicated = [
        item for item in classified if item["classification"] == UNADJUDICATED
    ]

    closed_world = bool((adjudications or {}).get("closed_world"))
    if adjudications is None:
        adjudication_status = "none"
    elif not unadjudicated:
        adjudication_status = "complete"
    else:
        adjudication_status = "partial"

    grounded, ungrounded = _evidence_grounding(findings, workspace)

    precision: float | None = None
    false_positive_rate: float | None = None
    if closed_world or not unadjudicated:
        status = "COMPUTED"
        reason = ""
        total = len(findings)
        if total:
            precision = (len(matched) + true_positives) / total
            false_positive_rate = false_positives / total
        else:
            precision = None
            false_positive_rate = None
            status = "REFUSED"
            reason = "no_findings: the report carried no finding to compute a rate over"
    else:
        status = "REFUSED"
        reason = (
            f"adjudication_incomplete: {len(unadjudicated)} unmatched finding(s) carry "
            "no independent adjudication verdict, and no closed_world exhaustive "
            "attestation is present"
        )

    metrics: dict[str, Any] = {
        "schema_version": METRICS_SCHEMA_VERSION,
        "fixture_id": key.get("fixture_id", ""),
        "fixture_digest": key.get("fixture_digest", ""),
        "key_digest": "sha256:"
        + sha256_text(json.dumps(key, sort_keys=True, ensure_ascii=False)),
        "findings_source": findings_document.get("source_report", ""),
        "findings_source_digest": findings_document.get("source_report_digest", ""),
        "findings_total": len(findings),
        "adjudication_status": adjudication_status,
        "closed_world": closed_world,
        "seeded_defects_total": total_defects,
        "detected_seeded_defects": detected,
        "seeded_recall": {
            "value": detected / total_defects if total_defects else None,
            "numerator": detected,
            "denominator": total_defects,
            "population": "seeded_defects_only",
        },
        "miss_count": len(missed),
        "miss_rate": {
            "value": len(missed) / total_defects if total_defects else None,
            "numerator": len(missed),
            "denominator": total_defects,
            "population": "seeded_defects_only",
        },
        "missed_defect_ids": missed,
        "matched_findings": matched,
        "unmatched_findings": classified,
        "unadjudicated_count": len(unadjudicated),
        "adjudicated_true_positives": true_positives,
        "adjudicated_false_positives": false_positives,
        "precision": precision,
        "precision_status": status,
        "precision_refusal_reason": reason,
        "precision_definition": (
            "a matched finding counts as a true positive by construction of the key: "
            "it identified an entry the key describes"
        ),
        "false_positive_rate": false_positive_rate,
        "false_positive_rate_status": status,
        "false_positive_rate_refusal_reason": reason,
        "evidence_grounding": grounded,
        "verdict_reproducibility": _verdict_reproducibility(run_verdicts or []),
    }
    metrics["evidence_grounding"]["ungrounded_finding_ids"] = ungrounded
    return metrics


def _evidence_grounding(
    findings: list[dict], workspace: Path | None
) -> tuple[dict, list[str]]:
    ungrounded: list[str] = []
    for finding in findings:
        name = finding.get("location_file")
        if not name:
            ungrounded.append(finding["id"])
            continue
        if workspace is None:
            continue
        path = workspace / name
        if not path.is_file():
            ungrounded.append(finding["id"])
            continue
        line = finding.get("location_line")
        if line is not None:
            try:
                count = len(path.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                ungrounded.append(finding["id"])
                continue
            if not 1 <= line <= count:
                ungrounded.append(finding["id"])
    total = len(findings)
    numerator = total - len(ungrounded)
    return (
        {
            "value": numerator / total if total else None,
            "numerator": numerator,
            "denominator": total,
            "definition": (
                "fraction of findings whose Location resolves to a file that exists in "
                "the materialized subject workspace and, when a line is given, to a "
                "line within that file"
            ),
            "ungrounded_finding_ids": [],
        },
        ungrounded,
    )


def _verdict_reproducibility(run_verdicts: list[str]) -> dict:
    """Observed, never asserted from one run."""
    if len(run_verdicts) < 2:
        return {
            "status": "SINGLE_RUN_NOT_ASSERTED",
            "run_count": len(run_verdicts),
            "verdicts": list(run_verdicts),
            "agreement": None,
        }
    counts: dict[str, int] = {}
    for verdict in run_verdicts:
        counts[verdict] = counts.get(verdict, 0) + 1
    modal = max(sorted(counts), key=lambda verdict: counts[verdict])
    return {
        "status": "OBSERVED",
        "run_count": len(run_verdicts),
        "verdicts": list(run_verdicts),
        "agreement": counts[modal] / len(run_verdicts),
    }


# ---- CLI -------------------------------------------------------------------------------------------


def _dump(document: Any, out: Path | None) -> None:
    text = json.dumps(document, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    if out is None:
        sys.stdout.write(text)
    else:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="final_review_eval.py",
        description="Materialize, verify and score the Final Review evaluation fixture.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    command = subparsers.add_parser("materialize", help="build a reviewable workspace")
    command.add_argument("--dest", required=True)
    command.add_argument("--fixture", default=str(DEFAULT_FIXTURE_DIR))

    command = subparsers.add_parser(
        "verify-fixture", help="prove the fixture is what the key says it is"
    )
    command.add_argument("--fixture", default=str(DEFAULT_FIXTURE_DIR))
    command.add_argument("--key", default="")

    command = subparsers.add_parser(
        "scan-leak", help="prove no key material reached a target"
    )
    command.add_argument("--key", required=True)
    command.add_argument("--target", required=True, action="append")

    command = subparsers.add_parser(
        "parse-report", help="normalize a section 11/17 review into a findings document"
    )
    command.add_argument("--report", required=True)
    command.add_argument("--workspace", default="")
    command.add_argument("--out", default="")

    command = subparsers.add_parser("score", help="compute the metric block")
    command.add_argument("--findings", required=True)
    command.add_argument("--key", required=True)
    command.add_argument("--adjudications", default="")
    command.add_argument("--workspace", default="")
    command.add_argument("--out", default="")
    command.add_argument("--require-precision", action="store_true")
    command.add_argument("--run-verdict", action="append", default=[])
    command.add_argument(
        "--provenance-out",
        default="",
        help=(
            "write a SEPARATE provenance sidecar. This is the only place `score` reads "
            "a clock, and the sidecar is never merged into the metrics document."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except EvalInputError as error:
        print(f"input error: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    except EvalContractError as error:
        print(f"contract violation: {error}", file=sys.stderr)
        return EXIT_CONTRACT_VIOLATION
    except FixtureError as error:
        print(f"fixture error: {error}", file=sys.stderr)
        return EXIT_LEAK_OR_FIXTURE


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "materialize":
        result = materialize(Path(args.dest), Path(args.fixture))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return EXIT_OK

    if args.command == "verify-fixture":
        fixture = Path(args.fixture)
        key_path = Path(args.key) if args.key else fixture / "key" / "answer_key.json"
        problems = verify_fixture(fixture, key_path)
        if problems:
            for problem in problems:
                print(f"- {problem}", file=sys.stderr)
            return EXIT_LEAK_OR_FIXTURE
        print("fixture verification PASSED")
        return EXIT_OK

    if args.command == "scan-leak":
        key = load_key(Path(args.key))
        hits = scan_leak(key, [Path(target) for target in args.target])
        if hits:
            print(json.dumps(hits, indent=2, ensure_ascii=False), file=sys.stderr)
            return EXIT_LEAK_OR_FIXTURE
        print("leak scan PASSED")
        return EXIT_OK

    if args.command == "parse-report":
        report_path = Path(args.report)
        try:
            raw = report_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise EvalInputError(f"report cannot be read: {error}") from None
        workspace = Path(args.workspace) if args.workspace else None
        document = {
            "schema_version": FINDINGS_SCHEMA_VERSION,
            "source_report": str(report_path),
            "source_report_digest": "sha256:" + sha256_text(raw),
            "findings": parse_report(raw, workspace),
        }
        _dump(document, Path(args.out) if args.out else None)
        return EXIT_OK

    findings_document = load_json(Path(args.findings), label="findings")
    require_major(findings_document, FINDINGS_SCHEMA_VERSION, label="findings")
    key = load_key(Path(args.key))
    adjudications = (
        load_adjudications(Path(args.adjudications)) if args.adjudications else None
    )
    metrics = score(
        findings_document,
        key,
        adjudications=adjudications,
        workspace=Path(args.workspace) if args.workspace else None,
        run_verdicts=list(args.run_verdict),
    )
    out = Path(args.out) if args.out else None
    _dump(metrics, out)
    if args.provenance_out:
        _write_provenance(Path(args.provenance_out), metrics, args)
    if metrics["precision_status"] == "REFUSED" and args.require_precision:
        print(
            f"precision refused: {metrics['precision_refusal_reason']}", file=sys.stderr
        )
        return EXIT_PRECISION_REFUSED
    return EXIT_OK


def _write_provenance(path: Path, metrics: dict, args: argparse.Namespace) -> None:
    """The ONLY clock read in this module, and it writes a different file.

    B5 is stated over the scoring metrics output, which is the document written to
    --out. This sidecar is a different artifact at a different path that the default
    invocation does not produce at all, and it is never embedded in, appended to, or
    merged into the metrics document. Its metrics_digest is what lets an auditor prove
    WHICH metrics bytes a given timestamp describes without putting the timestamp
    inside them.
    """
    from datetime import datetime, timezone

    serialized = json.dumps(metrics, indent=2, sort_keys=False, ensure_ascii=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": PROVENANCE_SCHEMA_VERSION,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "scorer_source_digest": "sha256:"
                + sha256_text(Path(__file__).read_text(encoding="utf-8")),
                "argv": [
                    "score",
                    "--findings",
                    args.findings,
                    "--key",
                    args.key,
                    *(["--adjudications", args.adjudications] if args.adjudications else []),
                    *(["--workspace", args.workspace] if args.workspace else []),
                    *(["--out", args.out] if args.out else []),
                ],
                "metrics_digest": "sha256:" + sha256_text(serialized),
            },
            indent=2,
            sort_keys=False,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
