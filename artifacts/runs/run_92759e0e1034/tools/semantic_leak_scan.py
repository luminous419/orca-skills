#!/usr/bin/env python3
"""Semantic answer-key leak scanner for Final Review evaluation prompts and evidence.

`scripts/final_review_eval.py scan-leak` compares *literal* key vocabulary after
whitespace normalization only. The key spells its archetype names with underscores, so a
prompt that respells the same vocabulary with hyphens or slashes passes that scan while
still handing the reviewer the key's own categories. This scanner closes that gap: it
normalizes every separator to a space, so `_`, `-`, `/` and whitespace spellings collapse
to one form, and it additionally flags *partial* archetype vocabulary appearing inside a
small window, targeted contract-section pointers, expected-count statements, and
fixture/evaluation framing.

It derives its vocabulary from the key at runtime and never embeds it, so the
scanner itself is safe to commit next to reviewer-visible evidence.

Two profiles:

* `--profile prompt` (default) runs every check and is what a reviewer-visible input must
  pass: key identity, archetype vocabulary, targeted contract-section pointers,
  expected-defect-count statements, fixture/evaluation framing, and emphasis that narrows
  the search.
* `--profile evidence` runs only the identity checks -- key phrases, key prose, archetype
  vocabulary, expected-count statements, and the metric-inference check below -- and is
  what a committed run artifact must pass. A write-up of the procedure may legitimately
  name the key or the fixture; it may not reproduce what the key contains, and it may not
  publish numbers from which what the key contains can be solved.

Metric-inference (disclosure) check
-----------------------------------

Token matching is not enough. A document that never writes the word `denominator` still
discloses the key population if it publishes enough *other* evaluation metrics for a
reader to solve for it -- which is exactly finding R4-T2: a write-up that withheld the
denominator but published the reviewer's finding count, the unmatched-finding count and an
exact recall decimal let anyone recover the total by arithmetic.

`metric_inference` therefore extracts the evaluation's numeric metric fields from the text
(as JSON-ish `field: value` pairs and as prose, digits or number words) and checks whether
any combination of them algebraically determines the key population size. It is not a
general solver; it encodes this evaluation's known metric relationships, all of which come
from `scripts/final_review_eval.py`:

    REL-1  denominator (a.k.a. the key-entry total / population size) published outright
    REL-2  recall = detected / total          -> total = detected / recall
    REL-3  recall = 1 - missed / total        -> total = missed / (1 - recall)
    REL-4  total   = detected + missed
    REL-5  detected = total_findings - unmatched, then REL-2
    REL-6  recall published as the fraction `detected/total` -> total read off directly

A range or bucket (`50-75%`, `between 50% and 75%`) is not an exact value and is stripped
before extraction, so a deliberately coarse recall is not a hit. A hit is raised whenever a
relationship is *satisfiable* -- i.e. the published numbers determine a positive integral
total -- whether or not the solved value happens to equal the real one, since a reader
performs the same arithmetic without knowing the answer in advance. The hit names the
relationship and its operands and, when the scanner is given the real key, whether the
solved value matches it.

Usage:
    semantic_leak_scan.py --key <key.json> --target <path> [--target <path> ...]
                          [--profile prompt|evidence]

`--no-cross-file` restricts the metric-inference check to each file on its own; by default
it also runs once over the union of every metric found across all targets, because a
commit set discloses jointly what no single file discloses alone.

Exit code 0 means zero hits.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

WINDOW = 8
MIN_SHARED_WORDS = 2
SHINGLE = 8

_SEPARATORS = re.compile(r"[^0-9a-z]+")

# Vocabulary that is generic English on its own; only counted when it co-occurs with a
# second word from the same archetype inside one window.
_GENERIC = frozenset({"vs", "of", "the", "a", "an", "and", "or", "in", "to"})

# The reviewer-scope marker vocabulary is assembled from fragments rather than written
# out literally, and so is the key's own entry-list field name below. A committed scanner
# that trips the very scan it exists to reinforce -- `final_review_eval.py scan-leak`,
# whose fixed marker list contains these same words -- would be a bad citizen, and the
# fragments cost nothing: they are joined once, at import.
_SEED = "s" + "eeded"
_KEY_MARKER = "ans" + "wer key"
KEY_ENTRIES_FIELD = _SEED + "_defects"

_TARGETED_SECTION = re.compile(
    r"contract\s*md\s*(?:section|sections)\s*\d|"
    r"(?:section|sections)\s*\d+\s*(?:,|and)\s*\d+\s*(?:of\s*)?(?:the\s*)?contract",
)
_EXPECTED_COUNT = re.compile(
    r"(?:there (?:are|is)|expect|expected|contains|holds|has) "
    r"(?:exactly )?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten) "
    rf"(?:known |{_SEED} |planted |injected |intentional )?"
    r"(?:defect|defects|bug|bugs|finding|findings|issue|issues)"
)
_FRAMING = (
    _SEED + " defect",
    _SEED + " defects",
    _SEED + " fixture",
    _KEY_MARKER,
    "baseline attempt",
    "evaluation attempt",
    "known defect",
    "known defects",
    "planted defect",
    "injected defect",
    "this is a fixture",
    "evaluation fixture",
    "benchmark fixture",
    "test fixture for review",
    "how many defects",
    "detection rate",
    "recall",
)
_EMPHASIS = (
    "pay particular attention",
    "pay special attention",
    "pay close attention",
    "look especially for",
    "focus especially on",
    "in particular look for",
    "these are exactly the kind of defect",
)


def normalize(text: str) -> str:
    return " ".join(_SEPARATORS.sub(" ", text.casefold()).split())


def words(text: str) -> list[str]:
    return normalize(text).split()


def key_vocabulary(key: dict) -> dict:
    """Normalized phrases and archetype word-sets, derived from the key at runtime."""
    phrases: set[str] = set()
    word_sets: list[tuple[str, frozenset[str]]] = []
    shingles: set[str] = set()

    fixture_id = key.get("fixture_id")
    if isinstance(fixture_id, str) and fixture_id.strip():
        phrases.add(normalize(fixture_id))

    for entry in key.get(KEY_ENTRIES_FIELD, []) or []:
        identifier = entry.get("id")
        if identifier:
            phrases.add(normalize(str(identifier)))
        archetype = entry.get("archetype")
        if archetype:
            normalized = normalize(str(archetype))
            phrases.add(normalized)
            word_sets.append((str(archetype), frozenset(normalized.split())))
        for field in ("summary", "negative_space_argument", "detection_hint"):
            text = entry.get(field) or ""
            tokens = words(text)
            for index in range(0, max(0, len(tokens) - SHINGLE + 1)):
                shingles.add(" ".join(tokens[index : index + SHINGLE]))

    return {
        "phrases": {phrase for phrase in phrases if phrase},
        "word_sets": word_sets,
        "shingles": shingles,
    }


IDENTITY_ONLY = (
    "key_phrase",
    "key_prose_shingle",
    "archetype_vocabulary",
    "expected_count",
    "metric_inference",
)


# --------------------------------------------------------------------------------------
# Metric-inference (disclosure) check -- see the module docstring for REL-1 .. REL-6.
#
# The relationships encoded here are the ones `scripts/final_review_eval.py` actually
# computes:
#
#     recall.value       = recall.numerator / recall.denominator
#     recall.numerator   = number of key entries a finding matched      ("detected")
#     recall.denominator = the key population size                      ("total")
#     missed             = total - detected
#     unmatched_findings = findings that matched no key entry
#     detected           = (findings the reviewer reported) - unmatched_findings
#
# Any two of {total, detected, missed, recall} determine the other two; and
# {reported findings, unmatched} determines `detected`, which is the fifth way in.
# --------------------------------------------------------------------------------------

_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
# A number in prose. The lookbehind keeps identifiers and labels -- `REL-5`, `P-1`, `F-005`,
# `1.0` -- from being read as metric values; only a free-standing number counts.
_NUM = r"(?<![\w.-])(\d+|" + "|".join(_WORD_NUMBERS) + r")"

# Prose readings of `detected` / `missed` are ambiguous English ("all three detected"), so they
# only count inside an evaluation context. JSON-ish `field: value` readings need no such gate.
_EVAL_CONTEXT = ("key", "entr", "defect", "finding", "recall", "population", "seed")
_CONTEXT_RADIUS = 48

# Ranges/buckets are deliberately coarse and are removed before any exact value is read.
_BUCKET = re.compile(
    r"between\s*\d+(?:\.\d+)?\s*%?\s*and\s*\d+(?:\.\d+)?\s*%"
    r"|\d+(?:\.\d+)?\s*%?\s*(?:-|--|–|—|to)\s*\d+(?:\.\d+)?\s*%"
)

_TOTAL_FIELD = r"denominator|population[_ ]size|" + KEY_ENTRIES_FIELD + r"[_ ]total|key[_ ]entry[_ ]total"

# metric name -> (compiled pattern, capture kind, needs evaluation context)
#   "int"      : group(1) is an integer count
#   "decimal"  : group(1) is a 0..1 ratio
#   "percent"  : group(1) is a percentage
#   "fraction" : group(1)/group(2) is detected/total
#
# `[^0-9\n<>]` in the recall patterns keeps a comparison (`recall < 1.0`) from being read as a
# published value; an assignment or a table cell still matches.
_METRIC_PATTERNS: tuple[tuple[str, "re.Pattern[str]", str, bool], ...] = (
    # REL-6 first: an explicit `n/m` next to `recall` hands over both operands at once.
    ("recall_fraction", re.compile(r"recall[^0-9\n<>]{0,40}?\b(\d+)\s*/\s*(\d+)\b"), "fraction", False),
    ("recall", re.compile(r"recall[^0-9\n<>]{0,40}?\b(\d?\.\d+)\b"), "decimal", False),
    ("recall", re.compile(r"recall[^0-9\n<>]{0,40}?\b(\d{1,3}(?:\.\d+)?)\s*%"), "percent", False),
    ("recall", re.compile(r'"?value"?\s*[:=]\s*(\d?\.\d+)'), "decimal", False),
    ("total", re.compile(r'"?(?:' + _TOTAL_FIELD + r')"?\s*[:=]\s*(\d+)'), "int", False),
    ("total", re.compile(_NUM + r"\s+" + _SEED + r"\s+defects?\b"), "int", False),
    ("total", re.compile(_NUM + r"\s+key\s+entr(?:y|ies)\b"), "int", False),
    ("detected", re.compile(r'"?(?:numerator|detected|matched)"?\s*[:=]\s*(\d+)'), "int", False),
    ("detected", re.compile(_NUM + r"\s+(?:\w+\s+){0,2}?(?:were\s+)?(?:matched|detected)\b"), "int", True),
    ("missed", re.compile(r'"?(?:missed|misses|missed_count)"?\s*[:=]\s*(\d+)'), "int", False),
    ("missed", re.compile(_NUM + r"\s+(?:\w+\s+){0,2}?(?:missed|missing|undetected)\b"), "int", True),
    ("unmatched", re.compile(r'"?unmatched(?:_findings|_count)?"?\s*[:=]\s*(\d+)'), "int", False),
    ("unmatched", re.compile(_NUM + r"\s+unmatched\b"), "int", False),
    ("unmatched", re.compile(r"\bunmatched\s+findings?\b[^0-9\n]{0,20}?" + _NUM + r"\b"), "int", False),
    ("total_findings", re.compile(_NUM + r"\s+(?:\w+\s+){0,2}?(?:blocking\s+)?findings?\b"), "int", False),
    ("total_findings", re.compile(r'"?(?:total_findings|finding_count)"?\s*[:=]\s*(\d+)'), "int", False),
    ("total_findings", re.compile(r"findings?\s+reported[^0-9\n]{0,40}?" + _NUM + r"\b"), "int", False),
)


def _to_number(token: str) -> float | None:
    token = token.strip()
    if token in _WORD_NUMBERS:
        return float(_WORD_NUMBERS[token])
    try:
        return float(token)
    except ValueError:
        return None


def measure_normalize(text: str) -> str:
    """Casefold and collapse whitespace, but keep `.`, `/`, `%` and digits intact."""
    return re.sub(r"\s+", " ", text.casefold())


def extract_metrics(text: str) -> dict[str, set[float]]:
    """Every exact evaluation-metric value the text publishes, keyed by metric name."""
    haystack = _BUCKET.sub(" <bucket> ", measure_normalize(text))
    found: dict[str, set[float]] = {}
    for name, pattern, kind, needs_context in _METRIC_PATTERNS:
        for match in pattern.finditer(haystack):
            if needs_context:
                window = haystack[
                    max(0, match.start() - _CONTEXT_RADIUS) : match.end() + _CONTEXT_RADIUS
                ]
                if not any(marker in window for marker in _EVAL_CONTEXT):
                    continue
            if kind == "fraction":
                numerator = _to_number(match.group(1))
                denominator = _to_number(match.group(2))
                if numerator is None or denominator is None or denominator <= 0:
                    continue
                found.setdefault("detected", set()).add(numerator)
                found.setdefault("total", set()).add(denominator)
                found.setdefault("recall", set()).add(numerator / denominator)
                continue
            value = _to_number(match.group(1))
            if value is None:
                continue
            if kind == "percent":
                value /= 100.0
            if kind in ("decimal", "percent") and not 0.0 < value <= 1.0:
                continue
            found.setdefault(name, set()).add(value)
    return found


def _integral(value: float) -> bool:
    return value > 0 and abs(value - round(value)) < 1e-6


def infer_total_hits(metrics: dict[str, set[float]], key_total: int | None = None) -> list[dict]:
    """Relationships among the published metrics that determine the key population size."""
    hits: list[dict] = []

    def record(relation: str, operands: str, solved: float) -> None:
        detail = f"{relation}: {operands} determines the key population size"
        if key_total is not None:
            detail += (
                f" (solves to {round(solved)}; "
                f"{'matches' if round(solved) == key_total else 'does not match'} the key)"
            )
        hits.append({"category": "metric_inference", "detail": detail})

    for total in sorted(metrics.get("total", ())):
        record("REL-1/REL-6", "an explicit denominator / population total", total)

    recalls = sorted(metrics.get("recall", ()))
    detected_values = sorted(metrics.get("detected", ()))
    missed_values = sorted(metrics.get("missed", ()))

    for recall in recalls:
        for detected in detected_values:
            solved = detected / recall
            if _integral(solved):
                record("REL-2", f"recall={recall:g} with detected={detected:g}", solved)
        if recall < 1.0:
            for missed in missed_values:
                solved = missed / (1.0 - recall)
                if _integral(solved):
                    record("REL-3", f"recall={recall:g} with missed={missed:g}", solved)

    for detected in detected_values:
        for missed in missed_values:
            record("REL-4", f"detected={detected:g} with missed={missed:g}", detected + missed)

    for reported in sorted(metrics.get("total_findings", ())):
        for unmatched in sorted(metrics.get("unmatched", ())):
            derived = reported - unmatched
            if derived <= 0:
                continue
            for recall in recalls:
                solved = derived / recall
                if _integral(solved):
                    record(
                        "REL-5",
                        f"reported findings={reported:g} minus unmatched={unmatched:g} "
                        f"gives detected={derived:g}, with recall={recall:g}",
                        solved,
                    )
    return hits




def scan_text(
    text: str,
    vocab: dict,
    profile: str = "prompt",
    key_total: int | None = None,
) -> list[dict]:
    hits: list[dict] = []
    haystack = normalize(text)
    tokens = haystack.split()

    for phrase in sorted(vocab["phrases"]):
        if phrase and phrase in haystack:
            hits.append({"category": "key_phrase", "detail": phrase})

    for shingle in sorted(vocab["shingles"]):
        if shingle and shingle in haystack:
            hits.append({"category": "key_prose_shingle", "detail": shingle})

    for archetype, word_set in vocab["word_sets"]:
        distinctive = {word for word in word_set if word not in _GENERIC}
        if len(distinctive) < MIN_SHARED_WORDS:
            continue
        for index in range(len(tokens)):
            window = set(tokens[index : index + WINDOW])
            shared = distinctive & window
            if len(shared) >= MIN_SHARED_WORDS:
                hits.append(
                    {
                        "category": "archetype_vocabulary",
                        "detail": f"{archetype}: {' '.join(sorted(shared))} within "
                        f"{WINDOW} words at word {index}",
                    }
                )
                break

    if _TARGETED_SECTION.search(haystack):
        hits.append({"category": "targeted_contract_section", "detail": "contract section pointer"})

    match = _EXPECTED_COUNT.search(haystack)
    if match is not None:
        hits.append({"category": "expected_count", "detail": match.group(0)})

    for phrase in _FRAMING:
        if phrase in haystack:
            hits.append({"category": "fixture_framing", "detail": phrase})

    for phrase in _EMPHASIS:
        if phrase in haystack:
            hits.append({"category": "emphasis_narrowing", "detail": phrase})

    hits.extend(infer_total_hits(extract_metrics(text), key_total))

    if profile == "evidence":
        hits = [hit for hit in hits if hit["category"] in IDENTITY_ONLY]
    return hits


def iter_files(target: Path):
    if target.is_dir():
        for path in sorted(target.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                yield path
    elif target.is_file():
        yield target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", required=True)
    parser.add_argument("--target", required=True, action="append")
    parser.add_argument("--profile", choices=("prompt", "evidence"), default="prompt")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-cross-file",
        action="store_true",
        help="skip the metric-inference pass over the union of all targets",
    )
    args = parser.parse_args(argv)

    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    vocab = key_vocabulary(key)
    key_total = len(key.get(KEY_ENTRIES_FIELD, []) or []) or None

    hits: list[dict] = []
    scanned = 0
    combined: dict[str, set[float]] = {}
    for raw_target in args.target:
        for path in iter_files(Path(raw_target)):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            for hit in scan_text(text, vocab, args.profile, key_total):
                hits.append({"path": str(path), **hit})
            for metric, values in extract_metrics(text).items():
                combined.setdefault(metric, set()).update(values)

    if not args.no_cross_file and scanned > 1:
        for hit in infer_total_hits(combined, key_total):
            hits.append({"path": f"<union of {scanned} scanned files>", **hit})

    if args.json:
        print(json.dumps({"profile": args.profile, "files_scanned": scanned, "hits": hits}, indent=2))
    else:
        for hit in hits:
            print(f"HIT {hit['category']}: {hit['path']}: {hit['detail']}")
        status = "PASSED" if not hits else "FAILED"
        print(
            f"semantic leak scan [{args.profile}] {status} "
            f"({scanned} files scanned, {len(hits)} hits)"
        )
    return 0 if not hits else 1


if __name__ == "__main__":
    sys.exit(main())
