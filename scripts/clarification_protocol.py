#!/usr/bin/env python3
"""OS-30 structured human clarification artifacts and non-interactive CLI.

This module deliberately has no runtime, terminal, transport, or resume dependency.
Published records are immutable; effective state is derived from append-only lineage.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Collection, Mapping, Protocol, Sequence

try:
    from scripts.run_logging import FINAL_REVIEW_REDACTION_POLICY_VERSION, read_decision_ledger
except ModuleNotFoundError:
    from run_logging import FINAL_REVIEW_REDACTION_POLICY_VERSION, read_decision_ledger

REQUEST_SCHEMA_VERSION = 2
RESPONSE_SCHEMA_VERSION = 2
DECISION_SCHEMA_VERSION = 1
LINEAGE_SCHEMA_VERSION = 1
CLARIFICATION_SCHEMA_VERSION = REQUEST_SCHEMA_VERSION
MAX_BUNDLE_ITEMS = 3
MAX_RECLARIFICATION_REVISIONS = 2
MAX_RAW_RESPONSE_BYTES = 65536
ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{24}$")
TOKEN_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
PHASE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
UTC_PATTERN = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z$")
SOURCE_STATES = frozenset({"NEEDS_INPUT", "CONFLICT"})


class ClarificationError(ValueError):
    code = "CLARIFICATION_INVALID"

    def __init__(self, message: str = "invalid clarification artifact") -> None:
        super().__init__(message)


class SchemaUnsupported(ClarificationError): code = "SCHEMA_UNSUPPORTED"
class SchemaVersionMixed(ClarificationError): code = "SCHEMA_VERSION_MIXED"
class SchemaMalformed(ClarificationError): code = "SCHEMA_MALFORMED"
class ItemNotInRequest(ClarificationError): code = "ITEM_NOT_IN_REQUEST"
class StaleItem(ClarificationError): code = "STALE_ITEM"
class CancelRequestInvalid(ClarificationError): code = "CANCEL_REQUEST_INVALID"
class SourceNotOpen(ClarificationError): code = "SOURCE_NOT_OPEN"
class LineageInvalid(ClarificationError): code = "LINEAGE_INVALID"
class LineageFork(ClarificationError): code = "LINEAGE_FORK"
class OrphanDecision(ClarificationError): code = "ORPHAN_DECISION"


class ClarificationConflict(ClarificationError):
    code = "CLARIFICATION_ID_CONFLICT"


class ClarificationSecurityError(ClarificationError):
    code = "CLARIFICATION_SECURITY_FAILURE"


@dataclasses.dataclass(frozen=True)
class ClarificationSource:
    open_item: str | None
    source_ledger_key: str
    source_ledger_keys: tuple[str, ...]
    state: str
    reason_code: str
    phase: str
    iteration: int
    request_input: Mapping[str, object]


@dataclasses.dataclass(frozen=True)
class PublishResult:
    request_ids: tuple[str, ...]
    item_ids: tuple[str, ...]
    status: str


@dataclasses.dataclass(frozen=True)
class ResponseSubmission:
    submission_id: str
    actor_id: str
    actor_type: str
    where_recorded: str
    responded_at: str
    option_id: str | None = None
    response_file: Path | None = None
    cancel: bool = False
    sensitivity: str = "normal"


@dataclasses.dataclass(frozen=True)
class IngestResult:
    response_id: str
    decision_id: str | None
    request_id: str
    decision_item_id: str | None
    status: str


class HumanApprovalPort(Protocol):
    def publish(self, *, run_id: str, sources: Sequence[ClarificationSource]) -> PublishResult: ...
    def show(self, *, run_id: str, request_id: str) -> Mapping[str, object]: ...
    def ingest(self, *, run_id: str, request_id: str, decision_item_id: str | None,
               submission: ResponseSubmission) -> IngestResult: ...


def terminal_block_sources(
    *,
    run_id: str,
    records: Sequence[Mapping[str, object]],
    coordinator_input: Mapping[str, Mapping[str, object]],
    ledger_key,
    valid_reviewer_binding,
) -> tuple[ClarificationSource, ...]:
    """Translate an authoritative OS-29 open set without importing OS-29.

    The harness supplies the OS-29 key and binding validators.  A validated B3 is
    always folded onto its B2 producer before the producer's label is inspected;
    consequently a Reviewer's free-text label can never split one question.
    """
    by_key = {ledger_key(record): record for record in records}
    groups: dict[tuple[object, ...], list[Mapping[str, object]]] = {}
    producers: dict[tuple[object, ...], Mapping[str, object]] = {}
    for record in records:
        if record.get("state") not in SOURCE_STATES or record.get("open_decision_item") is not True:
            continue
        producer = record
        verifies = record.get("verifies")
        if record.get("role") == "reviewer" and isinstance(verifies, Mapping):
            worker_key = verifies.get("worker_record_key")
            worker = by_key.get(worker_key) if isinstance(worker_key, str) else None
            if worker is not None and valid_reviewer_binding(record, worker):
                producer = worker
        producer_key = ledger_key(producer)
        label = producer.get("open_item")
        identity = ("named", run_id, producer.get("phase"), label) if isinstance(label, str) and label else ("producer", producer_key)
        groups.setdefault(identity, []).append(record)
        producers[identity] = producer
    sources = []
    for identity in sorted(groups, key=lambda value: _canonical(value)):
        producer = producers[identity]
        judgements = {(record.get("state"), record.get("reason_code")) for record in groups[identity]}
        if judgements != {(producer.get("state"), producer.get("reason_code"))}:
            raise ClarificationError("folded judgements disagree")
        keys = sorted({ledger_key(record) for record in groups[identity]}, key=_ledger_sort_key)
        declaration = coordinator_input.get(ledger_key(producer))
        if declaration is None:
            continue  # fail closed: no invented question or option set
        sources.append(ClarificationSource(
            open_item=producer.get("open_item") if isinstance(producer.get("open_item"), str) else None,
            source_ledger_key=ledger_key(producer), source_ledger_keys=tuple(keys),
            state=str(producer["state"]), reason_code=str(producer["reason_code"]),
            phase=str(producer["phase"]), iteration=int(producer["iteration"]),
            request_input=declaration,
        ))
    return tuple(sources)


def publication_batches(run_id: str, sources: Sequence[ClarificationSource], *,
                        resolved: Collection[str] = (),
                        already_published: Collection[str] = ()) -> tuple[tuple[ClarificationSource, ...], ...]:
    """Return every dependency-ready item in deterministic antichain bundles.

    `resolved` names decision items that already carry an effective decision, so a
    dependent whose predecessors are all resolved becomes ready.  Without it the
    first call would be the only one that can ever publish anything: a dependent's
    predecessor is in the source set, so it would be filtered out forever and the
    Jira requirement that dependent questions are asked in dependency order would
    have no executable path.  `already_published` names items whose request exists,
    which is what makes promotion publish each item exactly once.
    """
    resolved=set(resolved); already_published=set(already_published)
    prepared=[]
    for source in sources:
        merged=dict(source.request_input)
        merged.update(open_item=source.open_item,source_ledger_key=source.source_ledger_key,
                      source_ledger_keys=list(source.source_ledger_keys),source_state=source.state,
                      source_reason_code=source.reason_code,phase=source.phase,iteration=source.iteration)
        prepared.append((source,_validate_item(merged,run_id)))
    by_id={item["decision_item_id"]:(source,item) for source,item in prepared}
    graph={item_id:set(item["depends_on"]) for item_id,(_,item) in by_id.items()}
    def ancestors(item_id):
        found=set(); stack=list(graph.get(item_id,()))
        while stack:
            value=stack.pop()
            if value in found: continue
            found.add(value); stack.extend(graph.get(value,()))
        return found
    closure={item_id:ancestors(item_id) for item_id in graph}
    # A node is ready when every predecessor inside this source set already carries
    # an effective decision. At initial terminal publication `resolved` is empty, so
    # this is exactly "no known predecessor"; after answers land, promotion passes
    # the resolved set and the next dependency-ready antichain becomes selectable.
    # Items whose request already exists are never re-selected.
    unresolved_graph=set(graph)-resolved
    remaining=sorted((item_id for item_id in graph
                      if item_id not in already_published and not (graph[item_id] & unresolved_graph)),key=str)
    batches=[]
    while remaining:
        chosen=[]
        for item_id in remaining:
            if all(item_id not in closure[other] and other not in closure[item_id] for other in chosen):
                chosen.append(item_id)
                if len(chosen)==MAX_BUNDLE_ITEMS: break
        member_set=set(chosen); batch=[]
        for item_id in chosen:
            source,item=by_id[item_id]; request_input=dict(source.request_input)
            request_input["independent_with"]=sorted(member_set-{item_id})
            batch.append(dataclasses.replace(source,request_input=request_input))
        batches.append(tuple(batch)); remaining=[value for value in remaining if value not in member_set]
    return tuple(batches)


def canonical_ledger_key(record: Mapping[str, object]) -> str:
    return f"{record.get('run')}/{record.get('phase')}/{record.get('iteration')}/{record.get('boundary')}#{record.get('sequence')}"


# The OS-29 binding contract, restated here because clarification_protocol must not
# import decision_gate. `verifies` is a CLOSED four-field object and every field is
# authority-bearing: checking only worker_record_key lets a schema-valid B3 carry
# forged inner run/phase/iteration and still be folded into a request's provenance.
VERIFIES_FIELDS: tuple[str, ...] = ("run", "phase", "iteration", "worker_record_key")


def canonical_reviewer_binding(reviewer: Mapping[str, object], worker: Mapping[str, object]) -> bool:
    """The one B3 -> B2 binding rule, shared by the CLI and the harness seams.

    This matches `decision_gate.verification_binding_defect()` field for field: the
    closed `verifies` set, and all of run / phase / iteration / worker_record_key
    compared against the worker's own identity rather than the reviewer's outer copy.
    """
    verifies = reviewer.get("verifies")
    if not isinstance(verifies, Mapping) or set(verifies) != set(VERIFIES_FIELDS):
        return False
    if not (worker.get("role") == "worker" and worker.get("boundary") == "B2"
            and reviewer.get("role") == "reviewer" and reviewer.get("boundary") == "B3"):
        return False
    if any(reviewer.get(key) != worker.get(key) for key in ("run", "phase", "iteration")):
        return False
    actual = (verifies.get("run"), verifies.get("phase"), verifies.get("iteration"),
              verifies.get("worker_record_key"))
    expected = (worker.get("run"), worker.get("phase"), worker.get("iteration"),
                canonical_ledger_key(worker))
    return actual == expected


def derive_source_ledger_keys(primary: str, records: Sequence[Mapping[str, object]]) -> tuple[str, ...]:
    """Derive the authenticated key set a request may assert for `primary`.

    Every asserted key must be a real open ledger record, the primary must be the
    canonical B2 producer, and only Reviewer records validly bound to that producer
    may join it.  The set is DERIVED, never taken from caller input: trusting the
    input is what let a fabricated secondary key ride along on a genuine primary.
    """
    by_key = {canonical_ledger_key(record): record for record in records}
    record = by_key.get(primary)
    if record is None or record.get("boundary") not in {"B2", "B3"} or \
            record.get("open_decision_item") is not True or record.get("state") not in SOURCE_STATES:
        raise SourceNotOpen("ledger source is not a published open decision")
    producer = record
    if record.get("role") == "reviewer":
        verifies = record.get("verifies")
        worker_key = verifies.get("worker_record_key") if isinstance(verifies, Mapping) else None
        worker = by_key.get(worker_key) if isinstance(worker_key, str) else None
        if worker is None or not canonical_reviewer_binding(record, worker):
            raise SourceNotOpen("reviewer source is not validly bound to a producer")
        # A validly bound B3 folds onto its B2; the request must name the producer.
        raise SourceNotOpen("source_ledger_key must name the canonical producer")
    keys = {primary}
    for candidate in records:
        if candidate.get("role") != "reviewer" or candidate.get("boundary") != "B3":
            continue
        if candidate.get("open_decision_item") is not True or candidate.get("state") not in SOURCE_STATES:
            continue
        if canonical_reviewer_binding(candidate, producer):
            if (candidate.get("state"), candidate.get("reason_code")) != (producer.get("state"), producer.get("reason_code")):
                raise ClarificationError("folded judgements disagree")
            keys.add(canonical_ledger_key(candidate))
    return tuple(sorted(keys, key=_ledger_sort_key))


def _ledger_sort_key(key: str) -> tuple[str, int, int]:
    match = re.fullmatch(r"(.+)/([a-z][a-z0-9_-]{0,63})/([1-9][0-9]*)/(?:B2|B3)#([1-9][0-9]*)", key)
    return (match.group(2), int(match.group(3)), int(match.group(4))) if match else (key, 0, 0)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _identifier(prefix: str, domain: str, value: object) -> str:
    digest = hashlib.sha256(domain.encode() + b"\0" + _canonical(value)).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _strict_int(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _closed(value: object, fields: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise ClarificationError(f"{label}: closed schema mismatch")
    return value


def _text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ClarificationError(f"{label}: invalid text")
    return value


def _run_root(base: Path, run_id: str) -> Path:
    if not run_id or run_id in {".", ".."} or "/" in run_id or "\\" in run_id:
        raise ClarificationError("run_id: invalid path component")
    return base / "artifacts" / "runs" / run_id


def _clarification_root(base: Path, run_id: str) -> Path:
    root = _run_root(base, run_id) / "clarifications"
    for name in ("requests", "responses", "decisions", "lineage", ".staging"):
        path = root / name
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)
    return root


def _write_directory(root: Path, relative: Path, files: Mapping[str, tuple[bytes, int]]) -> bool:
    target = root / relative
    expected = {name: data for name, (data, _) in files.items()}
    if target.exists():
        actual = {p.name: p.read_bytes() for p in target.iterdir() if p.is_file()}
        if actual == expected:
            return False
        raise ClarificationConflict(f"identifier conflict: {target.name}")
    staging_root = root / ".staging"
    staging = Path(tempfile.mkdtemp(prefix="publish_", dir=staging_root))
    os.chmod(staging, 0o700)
    try:
        for name, (data, mode) in files.items():
            fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(staging / name, mode)
        fd = os.open(staging, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.rename(staging, target)
        except FileExistsError:
            return _write_directory(root, relative, files)
        fd = os.open(target.parent, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        return True
    finally:
        if staging.exists():
            try: staging.rmdir()
            except OSError: pass


def _json_bytes(record: Mapping[str, object]) -> bytes:
    return json.dumps(record, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def _ledger_parts(key: str, run_id: str) -> tuple[str, int]:
    match = re.fullmatch(r"(.+)/([a-z][a-z0-9_-]{0,63})/([1-9][0-9]*)/(B2|B3)#[1-9][0-9]*", key)
    if not match or match.group(1) != run_id:
        raise ClarificationError("source_ledger_key: invalid or cross-run")
    return match.group(2), int(match.group(3))


def decision_item_id(run_id: str, phase: str, open_item: str | None, source_key: str) -> str:
    tag = ["named", run_id, phase, open_item] if open_item else ["producer", source_key]
    return _identifier("item", "os30-item-v1", tag)


OPTION_FIELDS = {"option_id", "label", "action", "tradeoff"}
CUSTOM_FIELDS = {"allowed", "subject", "value_type", "max_length", "pattern", "allowed_values", "sensitive"}
ITEM_INPUT_FIELDS = {"open_item", "source_ledger_key", "source_ledger_keys", "source_state", "source_reason_code",
                     "phase", "iteration", "question", "context", "what_is_blocked", "options",
                     "recommended_option_id", "recommendation_rationale", "deadline_at", "depends_on",
                     "independent_with", "custom_decision", "narrowing_rationale"}
PUBLISHED_ITEM_FIELDS = ITEM_INPUT_FIELDS | {"decision_item_id"}
REQUEST_FIELDS = {"schema", "schema_version", "request_id", "bundle_id", "revision",
                  "reclarifies_request_id", "ambiguity_response_id", "created_at", "items",
                  "bundle_rationale", "independence_declared_by", "accepted_response_modes",
                  "sensitivity_guidance", "default_applicable", "on_timeout"}


def _validate_item(raw: object, run_id: str) -> dict:
    item = _closed(raw, ITEM_INPUT_FIELDS, "item")
    phase, iteration = _ledger_parts(_text(item["source_ledger_key"], "source_ledger_key", 300), run_id)
    keys = item["source_ledger_keys"]
    if not isinstance(keys, list) or keys != sorted(set(keys)) or item["source_ledger_key"] not in keys:
        raise ClarificationError("source_ledger_keys: invalid")
    for key in keys: _ledger_parts(key, run_id)
    if item["phase"] != phase or item["iteration"] != iteration or not _strict_int(item["iteration"], minimum=1):
        raise ClarificationError("source binding mismatch")
    if item["source_state"] not in SOURCE_STATES or not TOKEN_PATTERN.fullmatch(str(item["source_reason_code"])):
        raise ClarificationError("source decision invalid")
    open_item = item["open_item"]
    if open_item is not None: _text(open_item, "open_item", 1000)
    if item["deadline_at"] is not None and not UTC_PATTERN.fullmatch(str(item["deadline_at"])):
        raise ClarificationError("deadline_at: invalid UTC timestamp")
    options = item["options"]
    if not isinstance(options, list) or not 1 <= len(options) <= 8:
        raise ClarificationError("options: requires 1..8")
    option_ids = []
    for raw_option in options:
        option = _closed(raw_option, OPTION_FIELDS, "option")
        oid = _text(option["option_id"], "option_id", 64)
        if not TOKEN_PATTERN.fullmatch(oid): raise ClarificationError("option_id: invalid")
        option_ids.append(oid)
        _text(option["label"], "option.label", 200); _text(option["action"], "option.action", 2000); _text(option["tradeoff"], "option.tradeoff", 2000)
    if len(set(option_ids)) != len(option_ids) or item["recommended_option_id"] not in option_ids:
        raise ClarificationError("recommendation: invalid option")
    custom = _closed(item["custom_decision"], CUSTOM_FIELDS, "custom_decision")
    if type(custom["allowed"]) is not bool or type(custom["sensitive"]) is not bool or not _strict_int(custom["max_length"]):
        raise ClarificationError("custom_decision: invalid types")
    if not custom["allowed"]:
        if custom != {"allowed": False, "subject": "", "value_type": "none", "max_length": 0,
                      "pattern": None, "allowed_values": [], "sensitive": False}:
            raise ClarificationError("custom_decision: disabled envelope mismatch")
    elif custom["value_type"] not in {"text", "integer", "boolean", "enum"} or not custom["subject"]:
        raise ClarificationError("custom_decision: invalid envelope")
    elif (not isinstance(custom["subject"], str) or len(custom["subject"]) > 1000 or
          custom["max_length"] > MAX_RAW_RESPONSE_BYTES or
          not isinstance(custom["allowed_values"], list) or
          any(not isinstance(value, str) for value in custom["allowed_values"]) or
          custom["allowed_values"] != sorted(set(custom["allowed_values"])) or
          (custom["pattern"] is not None and not isinstance(custom["pattern"], str))):
        raise ClarificationError("custom_decision: invalid bounds")
    for field, maximum in (("question",1000),("context",4000),("what_is_blocked",2000),("recommendation_rationale",2000)):
        _text(item[field], field, maximum)
    for field in ("depends_on", "independent_with"):
        if (not isinstance(item[field], list) or
                any(not isinstance(value, str) or not ID_PATTERN.fullmatch(value) for value in item[field]) or
                item[field] != sorted(set(item[field]))):
            raise ClarificationError(f"{field}: invalid")
    result = dict(item)
    result["decision_item_id"] = decision_item_id(run_id, phase, open_item, item["source_ledger_key"])
    return result


def _validate_request_record(raw: object, run_id: str, expected_request_id: str | None = None) -> dict:
    """Validate the complete authority-bearing request envelope before any use."""
    request = _closed(raw, REQUEST_FIELDS, "request")
    if request["schema"] != "orca.clarification.request":
        raise SchemaMalformed("request: schema")
    if type(request["schema_version"]) is not int or request["schema_version"] not in {1, 2}:
        raise SchemaUnsupported("request: unsupported schema")
    request_id = request["request_id"]
    if not isinstance(request_id, str) or not ID_PATTERN.fullmatch(request_id) or (expected_request_id and request_id != expected_request_id):
        raise ClarificationError("request_id: invalid")
    if not _strict_int(request["revision"]): raise ClarificationError("request revision: invalid")
    if not isinstance(request["created_at"], str) or not UTC_PATTERN.fullmatch(request["created_at"]):
        raise ClarificationError("created_at: invalid UTC timestamp")
    if request["default_applicable"] is not False or request["on_timeout"] != "no selection; run remains blocked":
        raise ClarificationError("request: implicit authority forbidden")
    items_raw = request["items"]
    if not isinstance(items_raw, list) or not 1 <= len(items_raw) <= MAX_BUNDLE_ITEMS:
        raise ClarificationError("bundle: requires 1..3 items")
    items = []
    for raw_item in items_raw:
        published = _closed(raw_item, PUBLISHED_ITEM_FIELDS, "published item")
        expected_item_id = published["decision_item_id"]
        item = _validate_item({field: published[field] for field in ITEM_INPUT_FIELDS}, run_id)
        if expected_item_id != item["decision_item_id"]: raise ClarificationError("decision_item_id: invalid")
        items.append(item)
    if items != sorted(items, key=lambda value: value["decision_item_id"]):
        raise ClarificationError("request items: noncanonical order")
    ids = [item["decision_item_id"] for item in items]
    if len(set(ids)) != len(ids): raise ClarificationError("bundle: duplicate item")
    expected_bundle = _identifier("bundle", "os30-bundle-v1", ids) if len(ids) > 1 else None
    if request["bundle_id"] != expected_bundle: raise ClarificationError("bundle_id: invalid")
    member_set = set(ids)
    for item in items:
        if set(item["independent_with"]) != member_set - {item["decision_item_id"]}:
            raise ClarificationError("bundle: symmetric independence required")
        if item["decision_item_id"] in item["depends_on"] or member_set.intersection(item["depends_on"]):
            raise ClarificationError("bundle: dependency conflict")
    actor = _closed(request["independence_declared_by"], {"actor_id", "actor_type"}, "independence_declared_by")
    if not isinstance(actor["actor_id"], str) or not actor["actor_id"] or actor["actor_type"] not in {"human", "service"}:
        raise ClarificationError("independence_declared_by: invalid")
    modes = request["accepted_response_modes"]
    if (not isinstance(modes, list) or not modes or modes != list(dict.fromkeys(modes)) or
            not set(modes) <= {"option_id", "response_file", "cancel"}):
        raise ClarificationError("accepted_response_modes: invalid")
    for field, maximum in (("bundle_rationale", 4000), ("sensitivity_guidance", 4000)):
        if not isinstance(request[field], str) or len(request[field]) > maximum:
            raise ClarificationError(f"{field}: invalid")
    prior = request["reclarifies_request_id"]; ambiguity = request["ambiguity_response_id"]
    if request["revision"] == 0:
        if prior is not None or ambiguity is not None: raise ClarificationError("request lineage: invalid initial revision")
    elif (not isinstance(prior, str) or not ID_PATTERN.fullmatch(prior) or
          not isinstance(ambiguity, str) or not ID_PATTERN.fullmatch(ambiguity)):
        raise ClarificationError("request lineage: invalid revision")
    contract = {"items":items, "bundle_rationale":request["bundle_rationale"],
                "independence_declared_by":actor, "accepted_response_modes":modes,
                "sensitivity_guidance":request["sensitivity_guidance"]}
    version = request["schema_version"]
    expected_id = _identifier("request", f"os30-request-v{version}", {"items":ids,"revision":request["revision"],"contract":contract})
    if request_id != expected_id: raise ClarificationError("request_id: content mismatch")
    return dict(request)


class ArtifactHumanApprovalPort:
    def __init__(self, artifact_base: Path | str = Path(".")) -> None:
        self.artifact_base = Path(artifact_base)

    # The blocked source set is the only OS-30 input that lives outside the artifact
    # tree: it is assembled in coordinator memory at terminal BLOCKED. Without it on
    # disk, nothing after the run terminates can know that B exists, so promotion had
    # no reachable entry point. Persisting it once, immutably, is what makes `promote`
    # a real operation rather than a library call needing a live coordinator.
    BLOCKED_SOURCES_SCHEMA = "orca.clarification.blocked_sources"
    BLOCKED_SOURCES_SCHEMA_VERSION = 2

    def _blocked_sources_path(self, run_id: str) -> Path:
        return _clarification_root(self.artifact_base, run_id) / "blocked_sources" / "record.json"

    def persist_blocked_sources(self, run_id: str, sources: Sequence[ClarificationSource]) -> bool:
        """Publish the declared blocked source set write-once. Returns True if written.

        Re-publishing an identical set is a no-op; a DIFFERENT set for the same run is
        a conflict, because the declarations a request was derived from are immutable.
        """
        payload = {"schema": self.BLOCKED_SOURCES_SCHEMA,
                   "schema_version": self.BLOCKED_SOURCES_SCHEMA_VERSION,
                   "run_id": run_id,
                   "sources": [{"open_item": source.open_item,
                                "source_ledger_key": source.source_ledger_key,
                                "source_ledger_keys": list(source.source_ledger_keys),
                                "state": source.state, "reason_code": source.reason_code,
                                "phase": source.phase, "iteration": source.iteration,
                                "request_input": source.request_input}
                               for source in sorted(sources, key=lambda value: value.source_ledger_key)]}
        root = _clarification_root(self.artifact_base, run_id)
        (root / ".staging").mkdir(parents=True, exist_ok=True, mode=0o700)
        return _write_directory(root, Path("blocked_sources"), {"record.json": (_json_bytes(payload), 0o600)})

    def load_blocked_sources(self, run_id: str) -> tuple[ClarificationSource, ...]:
        """Reload the persisted declarations, validating the closed envelope."""
        path = self._blocked_sources_path(run_id)
        if not path.exists():
            return ()
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaMalformed("blocked_sources: unreadable") from exc
        if not isinstance(record, dict) or set(record) != {"schema", "schema_version", "run_id", "sources"}:
            raise SchemaMalformed("blocked_sources: closed schema")
        if record["schema"] != self.BLOCKED_SOURCES_SCHEMA:
            raise SchemaMalformed("blocked_sources: schema")
        if record["schema_version"] != self.BLOCKED_SOURCES_SCHEMA_VERSION:
            raise SchemaUnsupported("blocked_sources: unsupported generation")
        if record["run_id"] != run_id or not isinstance(record["sources"], list):
            raise SchemaMalformed("blocked_sources: run binding")
        sources = []
        for value in record["sources"]:
            if not isinstance(value, dict) or set(value) != {
                    "open_item", "source_ledger_key", "source_ledger_keys", "state",
                    "reason_code", "phase", "iteration", "request_input"}:
                raise SchemaMalformed("blocked_sources: closed source schema")
            sources.append(ClarificationSource(
                open_item=value["open_item"], source_ledger_key=value["source_ledger_key"],
                source_ledger_keys=tuple(value["source_ledger_keys"]), state=value["state"],
                reason_code=value["reason_code"], phase=value["phase"],
                iteration=int(value["iteration"]), request_input=value["request_input"]))
        return tuple(sources)

    def promote_pending(self, run_id: str) -> PublishResult:
        """Promote from the PERSISTED declarations, with no live coordinator.

        This is the post-response entry point: after a human answers, the next
        dependency-ready antichain becomes askable without resuming the run.
        """
        sources = self.load_blocked_sources(run_id)
        if not sources:
            return PublishResult((), (), "EXISTING")
        return self.promote(run_id=run_id, sources=sources, persist=False)

    def resolved_items(self, run_id: str) -> frozenset[str]:
        """Decision items that already carry an effective decision.

        `cancelled` is deliberately excluded: abandonment is irreversible, so a
        dependent of a cancelled question must never be promoted.
        """
        root = _clarification_root(self.artifact_base, run_id)
        return frozenset(item_id for item_id in self._known_items(run_id)
                         if self._lineage_state(root, item_id)[2] == "effective")

    def promote(self, *, run_id: str, sources: Sequence[ClarificationSource],
                persist: bool = True) -> PublishResult:
        """Publish the dependency-ready antichains unlocked by effective decisions.

        This is the operation that lets a dependent question be asked after its
        predecessor is answered, without resuming the run and without republishing
        anything already asked.  It is a no-op returning EXISTING when nothing new
        is ready, so it is safe to call on every terminal boundary.

        `persist` records the declared source set so `promote_pending()` can run the
        same computation later with no coordinator alive.
        """
        if persist:
            self.persist_blocked_sources(run_id, sources)
        published = frozenset(self._known_items(run_id))
        batches = publication_batches(run_id, sources, resolved=self.resolved_items(run_id),
                                      already_published=published)
        request_ids: list[str] = []
        item_ids: list[str] = []
        for batch in batches:
            result = self.publish(run_id=run_id, sources=batch)
            request_ids.extend(result.request_ids)
            item_ids.extend(result.item_ids)
        return PublishResult(tuple(request_ids), tuple(item_ids), "CREATED" if request_ids else "EXISTING")

    def publish(self, *, run_id: str, sources: Sequence[ClarificationSource]) -> PublishResult:
        items = []
        request_meta = None
        for source in sources:
            merged = dict(source.request_input)
            merged.update(open_item=source.open_item, source_ledger_key=source.source_ledger_key,
                          source_ledger_keys=list(source.source_ledger_keys), source_state=source.state,
                          source_reason_code=source.reason_code, phase=source.phase, iteration=source.iteration)
            items.append(_validate_item(merged, run_id))
            request_meta = source.request_input
        return self._publish_items(run_id, items, request_meta or {})

    def create(self, *, run_id: str, data: Mapping[str, object]) -> PublishResult:
        fields = {"items", "bundle_rationale", "independence_declared_by", "accepted_response_modes", "sensitivity_guidance"}
        obj = _closed(dict(data), fields, "create input")
        items = [_validate_item(item, run_id) for item in obj["items"]] if isinstance(obj["items"], list) else []
        return self._publish_items(run_id, items, obj)

    def _publish_items(self, run_id: str, items: list[dict], meta: Mapping[str, object], *, revision: int = 0,
                       prior: str | None = None, ambiguity_response: str | None = None) -> PublishResult:
        if not 1 <= len(items) <= MAX_BUNDLE_ITEMS:
            raise ClarificationError("bundle: requires 1..3 items")
        items.sort(key=lambda x: x["decision_item_id"])
        ids = [x["decision_item_id"] for x in items]
        if len(set(ids)) != len(ids): raise ClarificationError("bundle: duplicate item")
        member_set = set(ids)
        for item in items:
            if set(item["independent_with"]) != member_set - {item["decision_item_id"]}:
                raise ClarificationError("bundle: symmetric independence required")
            if item["decision_item_id"] in item["depends_on"] or member_set.intersection(item["depends_on"]):
                raise ClarificationError("bundle: dependency conflict")
        self._validate_known_dag(run_id, items)
        modes = meta.get("accepted_response_modes", ["option_id", "response_file", "cancel"])
        if not isinstance(modes, list) or not modes or not set(modes) <= {"option_id", "response_file", "cancel"}:
            raise ClarificationError("accepted_response_modes: invalid")
        actor = meta.get("independence_declared_by", {"actor_id":"coordinator", "actor_type":"service"})
        _closed(actor, {"actor_id", "actor_type"}, "independence_declared_by")
        contract = {"items": items, "bundle_rationale": meta.get("bundle_rationale", ""),
                    "independence_declared_by": actor, "accepted_response_modes": modes,
                    "sensitivity_guidance": meta.get("sensitivity_guidance", "Treat responses as run artifacts.")}
        bundle_id = _identifier("bundle", "os30-bundle-v1", ids) if len(ids) > 1 else None
        request_id = _identifier("request", "os30-request-v2", {"items":ids,"revision":revision,"contract":contract})
        record = {"schema":"orca.clarification.request", "schema_version":2, "request_id":request_id,
                  "bundle_id":bundle_id, "revision":revision, "reclarifies_request_id":prior,
                  "ambiguity_response_id":ambiguity_response, "created_at":_now(), **contract,
                  "default_applicable":False, "on_timeout":"no selection; run remains blocked"}
        root = _clarification_root(self.artifact_base, run_id)
        existing_path = root / "requests" / request_id / "record.json"
        if existing_path.exists():
            try:
                existing = _validate_request_record(json.loads(existing_path.read_text(encoding="utf-8")), run_id, request_id)
            except (OSError, json.JSONDecodeError) as exc:
                raise ClarificationConflict(f"identifier conflict: {request_id}") from exc
            comparable = dict(existing)
            comparable["created_at"] = record["created_at"]
            if comparable == record:
                return PublishResult((request_id,), tuple(ids), "EXISTING")
        created = _write_directory(root, Path("requests") / request_id, {"record.json":(_json_bytes(record),0o600)})
        return PublishResult((request_id,), tuple(ids), "CREATED" if created else "EXISTING")

    def _known_items(self, run_id: str) -> dict[str, dict]:
        root = _clarification_root(self.artifact_base, run_id)
        known: dict[str, dict] = {}
        for path in (root / "requests").glob("request_*/record.json"):
            try:
                request = _validate_request_record(json.loads(path.read_text(encoding="utf-8")), run_id, path.parent.name)
                for item in request["items"]:
                    item_id = item["decision_item_id"]
                    if item_id in known:
                        immutable = ("open_item", "source_ledger_key", "source_ledger_keys",
                                     "source_state", "source_reason_code", "phase", "iteration",
                                     "depends_on", "independent_with")
                        if any(known[item_id].get(field) != item.get(field) for field in immutable):
                            raise ClarificationError("known item mutated")
                    known[item_id] = item
            except (OSError, KeyError, json.JSONDecodeError, TypeError) as exc:
                raise ClarificationError("known item graph unreadable") from exc
        return known

    def _validate_known_dag(self, run_id: str, incoming: Sequence[dict]) -> None:
        known = self._known_items(run_id)
        for item in incoming:
            prior = known.get(item["decision_item_id"])
            if prior is not None:
                immutable = ("decision_item_id", "open_item", "source_ledger_key",
                             "source_ledger_keys", "source_state", "source_reason_code",
                             "phase", "iteration", "depends_on", "independent_with")
                if any(prior.get(field) != item.get(field) for field in immutable):
                    raise ClarificationError("known item mutation")
            known[item["decision_item_id"]] = item
        graph = {item_id: tuple(item["depends_on"]) for item_id, item in known.items()}
        for item_id, dependencies in graph.items():
            for dependency in dependencies:
                if dependency not in graph:
                    raise ClarificationError("dependency: unknown item")
                if dependency == item_id:
                    raise ClarificationError("dependency: self cycle")
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(item_id: str) -> None:
            if item_id in visiting: raise ClarificationError("dependency: cycle")
            if item_id in visited: return
            visiting.add(item_id)
            for dependency in graph[item_id]: visit(dependency)
            visiting.remove(item_id); visited.add(item_id)
        for item_id in graph: visit(item_id)
        incoming_ids = {item["decision_item_id"] for item in incoming}
        root = _clarification_root(self.artifact_base, run_id)
        for item in incoming:
            for dependency in item["depends_on"]:
                if dependency not in incoming_ids and self._effective_decision(root, dependency) is None:
                    raise ClarificationError("dependency: predecessor not effective")

    def expand_scope(self, *, run_id: str, decision_item_id: str, request_id: str,
                     child_items: Sequence[Mapping[str, object]], edges: Sequence[tuple[str, str]],
                     actor: Mapping[str, object], provenance: Mapping[str, object],
                     scope_statements: Sequence[str]) -> PublishResult:
        """Append child identities and dependency edges while retaining authority."""
        root = _clarification_root(self.artifact_base, run_id)
        prior = self._effective_decision(root, decision_item_id)
        if prior is None: raise ClarificationError("scope expansion requires effective decision")
        known_before = self._known_items(run_id)
        validated = [_validate_item(dict(item), run_id) for item in child_items]
        child_ids = {item["decision_item_id"] for item in validated}
        if not validated or child_ids.intersection(known_before):
            raise ClarificationError("scope expansion requires new child identities")
        normalized_edges = sorted([list(edge) for edge in edges])
        declared_edges = sorted([ [dependency, item["decision_item_id"]] for item in validated for dependency in item["depends_on"] ])
        if normalized_edges != declared_edges or any(edge[1] not in child_ids for edge in normalized_edges):
            raise ClarificationError("scope expansion edge mismatch")
        result = self._publish_items(run_id, validated, {"bundle_rationale":"Scope expansion children.",
            "independence_declared_by":dict(actor),"accepted_response_modes":["option_id","response_file","cancel"],
            "sensitivity_guidance":"Treat responses as run artifacts."})
        self._append_event(root, run_id, "decision_scope_expanded", decision_item_id, request_id, None,
            dict(actor), dict(provenance), prior, None, sorted(child_ids),
            {"new_item_ids":sorted(child_ids),"dependency_edges":normalized_edges,
             "scope_statements":list(scope_statements)})
        return result

    def _request(self, run_id: str, request_id: str) -> dict:
        if not ID_PATTERN.fullmatch(request_id): raise ClarificationError("request_id: invalid")
        path = _clarification_root(self.artifact_base, run_id) / "requests" / request_id / "record.json"
        try: record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise ClarificationError("request: unreadable") from exc
        return _validate_request_record(record, run_id, request_id)

    def _current_request(self, run_id: str, request: dict) -> bool:
        root = _clarification_root(self.artifact_base, run_id) / "requests"
        item_ids = {x["decision_item_id"] for x in request["items"]}
        revisions = []
        for path in root.glob("request_*/record.json"):
            try: other_raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc: raise ClarificationError("request: unreadable") from exc
            other = _validate_request_record(other_raw, run_id, path.parent.name)
            if {x["decision_item_id"] for x in other["items"]} == item_ids: revisions.append(other["revision"])
        return request["revision"] == max(revisions, default=request["revision"])

    def _current_item_ids(self, run_id: str, request: dict) -> set[str]:
        records = []
        for path in (_clarification_root(self.artifact_base, run_id) / "requests").glob("request_*/record.json"):
            records.append(_validate_request_record(json.loads(path.read_text(encoding="utf-8")), run_id, path.parent.name))
        related = [value for value in records if set(x["decision_item_id"] for x in value["items"]).intersection(
            x["decision_item_id"] for x in request["items"])]
        current = max(related, key=lambda value: value["revision"], default=request)
        return {item["decision_item_id"] for item in current["items"]}

    @staticmethod
    def _validate_response_record(raw: object, request: dict) -> dict:
        common = {"schema","schema_version","response_id","submission_id","request_id","request_revision",
                  "decision_item_id","response_kind","actor","provenance","responded_at","recorded_at","raw",
                  "stale","normalization_outcome","normalization_reason","decision_id"}
        if not isinstance(raw,dict): raise SchemaMalformed("response object")
        version=raw.get("schema_version")
        fields=common if version==2 else common-{"decision_item_id"}
        response = _closed(raw, fields, "response")
        version = response["schema_version"]
        if response["schema"] != "orca.clarification.response": raise SchemaMalformed("response schema")
        if type(version) is not int or version not in {1, 2}: raise SchemaUnsupported("response version")
        if version != request["schema_version"]: raise SchemaVersionMixed("request/response generation")
        if version==1:
            if len(request["items"])!=1: raise SchemaVersionMixed("v1 bundled response")
            response=dict(response); response["decision_item_id"]=request["items"][0]["decision_item_id"]
        if response["decision_item_id"] not in {x["decision_item_id"] for x in request["items"]}:
            raise ItemNotInRequest("response item")
        identity=([response["request_id"],response["submission_id"]] if version==1 else
                  [response["request_id"],response["decision_item_id"],response["submission_id"]])
        expected = _identifier("response", f"os30-response-v{version}", identity)
        if response["response_id"] != expected: raise SchemaMalformed("response_id content mismatch")
        return response

    @staticmethod
    def _response_binding(response_id: str, digest: str) -> tuple[str, dict]:
        binding_id = _identifier("binding", "os30-response-raw-binding-v1", [response_id, digest])
        return binding_id, {"schema":"orca.clarification.response-raw-binding","schema_version":1,
                            "binding_id":binding_id,"response_id":response_id,"raw_sha256":digest}

    def _validate_response_evidence(self, root: Path, response: dict) -> None:
        raw_bytes=(root/"responses"/response["response_id"]/"raw_response.txt").read_bytes()
        raw_digest=hashlib.sha256(raw_bytes).hexdigest()
        if raw_digest!=response["raw"]["sha256"] or len(raw_bytes)!=response["raw"]["byte_count"]:
            raise SchemaMalformed("raw digest mismatch")
        # Historical v1 authority is its directly verified raw digest.  Bindings
        # are a v2 write/read requirement and never retrofit or invalidate v1.
        if response["schema_version"]==1:
            return
        matches=[]
        for path in (root/"response_bindings").glob("binding_*/record.json"):
            record=_closed(json.loads(path.read_text(encoding="utf-8")),
                           {"schema","schema_version","binding_id","response_id","raw_sha256"},"response binding")
            if record["schema"]!="orca.clarification.response-raw-binding" or record["schema_version"]!=1:
                raise SchemaMalformed("response binding schema")
            expected_id,_=self._response_binding(record["response_id"],record["raw_sha256"])
            if record["binding_id"]!=path.parent.name or record["binding_id"]!=expected_id:
                raise SchemaMalformed("response binding identity")
            if record["response_id"]==response["response_id"]: matches.append(record)
        if len(matches)!=1 or matches[0]["raw_sha256"]!=response["raw"]["sha256"]:
            raise SchemaMalformed("response raw binding mismatch")
        if raw_digest!=matches[0]["raw_sha256"]:
            raise SchemaMalformed("raw digest mismatch")

    def ingest(self, *, run_id: str, request_id: str, decision_item_id: str | None = None,
               submission: ResponseSubmission) -> IngestResult:
        request = self._request(run_id, request_id)
        if not TOKEN_PATTERN.fullmatch(submission.submission_id): raise ClarificationError("submission_id: invalid")
        if submission.actor_type not in {"human","service"} or not submission.actor_id or not submission.where_recorded:
            raise ClarificationError("actor/provenance: invalid")
        if not UTC_PATTERN.fullmatch(submission.responded_at): raise ClarificationError("responded_at: invalid UTC timestamp")
        selected = sum((submission.option_id is not None, submission.response_file is not None, submission.cancel))
        if selected != 1: raise ClarificationError("response: exactly one explicit mode required")
        if submission.cancel:
            if decision_item_id is not None: raise CancelRequestInvalid("cancel forbids item selector")
            results = []
            # Validate every item/head before the first write.  Cancellation child
            # tokens are stable and item-bound, making whole-request replay exact.
            root = _clarification_root(self.artifact_base, run_id)
            for item in request["items"]:
                self._effective_decision(root, item["decision_item_id"])
            for item in request["items"]:
                child = dataclasses.replace(submission, submission_id=_identifier(
                    "cancel", "os30-cancel-v1", [submission.submission_id, item["decision_item_id"]]),
                    cancel=False, option_id=None)
                results.append(self._ingest_one(run_id, request, item["decision_item_id"], child,
                                                forced_cancel=True))
            return IngestResult(results[0].response_id if results else "", None, request_id, None, "CANCELLED")
        if decision_item_id is None:
            raise SchemaMalformed("decision_item_id required")
        return self._ingest_one(run_id, request, decision_item_id, submission)

    def _ingest_one(self, run_id: str, request: dict, decision_item_id: str,
                    submission: ResponseSubmission, *, forced_cancel: bool = False) -> IngestResult:
        request_id = request["request_id"]
        item_by_id = {item["decision_item_id"]: item for item in request["items"]}
        if decision_item_id not in item_by_id: raise ItemNotInRequest("item absent from request")
        if not self._current_request(run_id, request):
            current_ids = self._current_item_ids(run_id, request)
            if decision_item_id not in current_ids: raise StaleItem("item absent from current revision")
        mode = "cancel" if submission.cancel else "option_id" if submission.option_id is not None else "response_file"
        if forced_cancel: mode = "cancel"
        if mode not in request["accepted_response_modes"]: raise ClarificationError("response mode not accepted")
        if mode == "cancel": raw = b"CANCEL"; kind = "CANCEL"
        elif mode == "option_id": raw = submission.option_id.encode("utf-8"); kind = "OPTION_ID"
        else:
            path = submission.response_file
            assert path is not None
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode): raise ClarificationSecurityError("response file must be regular")
                raw = path.read_bytes()
            except OSError as exc: raise ClarificationSecurityError("response file unreadable") from exc
            kind = "TEXT"
        if not raw or len(raw) > MAX_RAW_RESPONSE_BYTES: raise ClarificationSecurityError("response size invalid")
        if submission.sensitivity not in {"normal","sensitive"}: raise ClarificationError("sensitivity: invalid")
        stale = not self._current_request(run_id, request)
        item = item_by_id[decision_item_id]
        root = _clarification_root(self.artifact_base, run_id)
        outcome, reason, normalized, item = self._normalize(request, decision_item_id, kind, raw)
        if outcome in {"OPTION","CUSTOM"} and not stale:
            _, reset_anchor, item_status = self._lineage_state(root, item["decision_item_id"])
            if item_status == "cancelled" and reset_anchor is None:
                raise LineageInvalid("cancelled item cannot receive a first decision")
        response_id = _identifier("response", "os30-response-v2", [request_id, decision_item_id, submission.submission_id])
        decision_id = None
        if outcome in {"OPTION","CUSTOM"} and not stale:
            decision_id = _identifier("decision", "os30-decision-v1", [response_id, normalized])
        digest = hashlib.sha256(raw).hexdigest()
        actor = {"actor_id":submission.actor_id,"actor_type":submission.actor_type}
        provenance = {"source":"explicit_user_reply","capture_mechanism":"cli","where_recorded":submission.where_recorded}
        response = {"schema":"orca.clarification.response","schema_version":2,"response_id":response_id,
                    "submission_id":submission.submission_id,"request_id":request_id,"request_revision":request["revision"],
                    "decision_item_id":decision_item_id,
                    "response_kind":kind,"actor":actor,"provenance":provenance,"responded_at":submission.responded_at,
                    "recorded_at":_now(),"raw":{"path":"raw_response.txt","sha256":digest,"byte_count":len(raw),
                    "sensitivity":submission.sensitivity,"redaction_policy_version":FINAL_REVIEW_REDACTION_POLICY_VERSION},
                    "stale":stale,"normalization_outcome":outcome,"normalization_reason":reason,"decision_id":decision_id}
        if not stale and outcome == "AMBIGUOUS":
            self._reclarification_items(run_id, request, decision_item_id)
        response_dir = root / "responses" / response_id
        if response_dir.exists():
            try:
                existing = self._validate_response_record(json.loads((response_dir/"record.json").read_text(encoding="utf-8")), request)
                self._validate_response_evidence(root,existing)
                same = ((response_dir/"raw_response.txt").read_bytes() == raw and
                        existing["submission_id"] == submission.submission_id and
                        existing["request_id"] == request_id and existing["actor"] == actor and
                        existing["provenance"] == provenance and existing["responded_at"] == submission.responded_at and
                        existing["raw"]["sensitivity"] == submission.sensitivity)
            except (OSError, KeyError, json.JSONDecodeError):
                same = False
            if not same:
                raise ClarificationConflict(f"identifier conflict: {response_id}")
            response = existing
            decision_id = existing["decision_id"]
        else:
            _write_directory(root, Path("responses")/response_id,
                             {"record.json":(_json_bytes(response),0o600),"raw_response.txt":(raw,0o600)})
            binding_id,binding=self._response_binding(response_id,digest)
            _write_directory(root,Path("response_bindings")/binding_id,{"record.json":(_json_bytes(binding),0o600)})
        if stat.S_IMODE((response_dir/"raw_response.txt").stat().st_mode) != 0o600 or (response_dir/"raw_response.txt").read_bytes() != raw:
            raise ClarificationSecurityError("raw verification failed")
        status = "STALE"
        if not stale and outcome in {"OPTION","CUSTOM"}:
            prior, reset_anchor, item_status = self._lineage_state(root, item["decision_item_id"])
            if item_status == "cancelled" and reset_anchor is None:
                raise LineageInvalid("cancelled item cannot receive a first decision")
            decision = self._decision_record(decision_id, request, response, normalized, item, submission.sensitivity)
            decision_path = root/"decisions"/decision_id/"record.json"
            if not decision_path.exists():
                _write_directory(root, Path("decisions")/decision_id,{"record.json":(_json_bytes(decision),0o600)})
            predecessor = prior or reset_anchor
            if predecessor and predecessor != decision_id:
                self._append_event(root, run_id, "decision_superseded", item["decision_item_id"], request_id,
                                   response_id, actor, provenance, predecessor, decision_id, [],
                                   {"prior_decision_id":predecessor,"next_decision_id":decision_id})
            status = "DECIDED"
        elif not stale and outcome == "CANCELLED":
            prior = self._effective_decision(root, item["decision_item_id"])
            existing_marker = any(
                event["event_type"] == "decision_cancelled" and event["decision_item_id"] == item["decision_item_id"]
                and event["response_id"] == response_id
                for event in self._lineage_events(root)
            )
            if not existing_marker:
                self._append_event(root, run_id, "decision_cancelled", item["decision_item_id"], request_id,
                                   response_id, actor, provenance, prior, None, [], {"cancelled_decision_id":prior})
            status = "CANCELLED"
        elif not stale:
            status = self._reclarify(root, run_id, request, decision_item_id, response_id, actor, provenance)
        return IngestResult(response_id, decision_id, request_id, decision_item_id, "STALE_REQUEST" if stale else status)

    def _normalize(self, request: dict, decision_item_id: str, kind: str, raw: bytes) -> tuple[str,str,object,dict]:
        item = next(value for value in request["items"] if value["decision_item_id"] == decision_item_id)
        if kind == "CANCEL": return "CANCELLED","explicit_cancel",None,item
        try: value = unicodedata.normalize("NFC", raw.decode("utf-8")).strip()
        except UnicodeDecodeError: return "AMBIGUOUS","invalid_utf8",None,item
        matches = [o for o in item["options"] if value == o["option_id"] or value.casefold() == o["label"].casefold()]
        if kind == "OPTION_ID":
            matches = [o for o in item["options"] if value == o["option_id"]]
            if len(matches) != 1: raise ClarificationError("option_id: unknown")
        if len(matches) == 1:
            return "OPTION","exact_option",{"kind":"OPTION","option_id":matches[0]["option_id"],"action":matches[0]["action"]},item
        if len(matches) > 1: return "AMBIGUOUS","multiple_option_matches",None,item
        custom = item["custom_decision"]
        parsed = self._custom(value, custom) if custom["allowed"] else None
        if parsed is not None: return "CUSTOM","bounded_custom",{"kind":"CUSTOM","value":parsed,"envelope":custom},item
        return "AMBIGUOUS","outside_declared_choices",None,item

    @staticmethod
    def _custom(value: str, envelope: dict) -> object | None:
        kind = envelope["value_type"]
        if kind in {"text","enum"} and (not value or len(value) > envelope["max_length"]): return None
        if kind == "text":
            try: return value if envelope["pattern"] is None or re.fullmatch(envelope["pattern"], value) else None
            except re.error: return None
        if kind == "enum": return value if value in envelope["allowed_values"] else None
        if kind == "integer":
            try: return int(value) if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value) else None
            except ValueError: return None
        if kind == "boolean": return {"true":True,"false":False}.get(value.casefold())
        return None

    def _decision_record(self, decision_id: str, request: dict, response: dict, normalized: dict, item: dict, sensitivity: str) -> dict:
        option = custom = None
        if normalized["kind"] == "OPTION": option = {"option_id":normalized["option_id"],"action":normalized["action"]}; scope=normalized["action"]
        else:
            envelope=normalized["envelope"]; sensitive=envelope["sensitive"] or sensitivity=="sensitive"
            custom={"value_type":envelope["value_type"],"value":None if sensitive else normalized["value"],
                    "bounded_by":envelope,"redacted":sensitive,"raw_response_sha256":response["raw"]["sha256"]}; scope=envelope["subject"]
        return {"schema":"orca.clarification.decision","schema_version":1,"decision_id":decision_id,
                "decision_item_id":item["decision_item_id"],"request_id":request["request_id"],"response_id":response["response_id"],
                "source_ledger_key":item["source_ledger_key"],"kind":normalized["kind"],"option":option,"custom":custom,
                "actor":response["actor"],"provenance":response["provenance"],"responded_at":response["responded_at"],
                "normalized_at":_now(),"resolves":item["source_ledger_key"],"scope":scope}

    def _validate_decision_record(self, raw: object, root: Path, expected_id: str) -> dict:
        fields={"schema","schema_version","decision_id","decision_item_id","request_id","response_id",
                "source_ledger_key","kind","option","custom","actor","provenance","responded_at",
                "normalized_at","resolves","scope"}
        record=_closed(raw,fields,"decision")
        if record["schema"]!="orca.clarification.decision": raise SchemaMalformed("decision schema")
        if type(record["schema_version"]) is not int or record["schema_version"]!=DECISION_SCHEMA_VERSION:
            raise SchemaUnsupported("decision version")
        if record["decision_id"]!=expected_id: raise SchemaMalformed("decision directory binding")
        request=self._request_from_root(root,record["request_id"])
        response=self._validate_response_record(json.loads((root/"responses"/record["response_id"]/"record.json").read_text()),request)
        self._validate_response_evidence(root,response)
        item=next((x for x in request["items"] if x["decision_item_id"]==record["decision_item_id"]),None)
        if item is None or response["decision_item_id"]!=record["decision_item_id"] or record["source_ledger_key"]!=item["source_ledger_key"]:
            raise SchemaMalformed("decision binding")
        raw_bytes=(root/"responses"/record["response_id"]/"raw_response.txt").read_bytes()
        outcome,_,normalized,_=self._normalize(request,record["decision_item_id"],response["response_kind"],raw_bytes)
        if outcome!=record["kind"]: raise SchemaMalformed("decision normalized shape")
        if _identifier("decision","os30-decision-v1",[record["response_id"],normalized])!=record["decision_id"]:
            raise SchemaMalformed("decision_id content mismatch")
        expected=self._decision_record(record["decision_id"],request,response,normalized,item,response["raw"]["sensitivity"])
        for field in ("option","custom","scope","resolves","actor","provenance","responded_at"):
            if record[field]!=expected[field]: raise SchemaMalformed("decision authority mismatch")
        return record

    def _request_from_root(self, root: Path, request_id: str) -> dict:
        raw=json.loads((root/"requests"/request_id/"record.json").read_text(encoding="utf-8"))
        return _validate_request_record(raw,root.parent.name,request_id)

    @staticmethod
    def _validate_lineage_event(raw: object, run_id: str, sequence: int) -> dict:
        fields={"schema","schema_version","sequence","event_id","event_type","run_id","decision_item_id",
                "request_id","response_id","actor","provenance","occurred_at","prior_decision_id",
                "next_decision_id","related_item_ids","details"}
        event=_closed(raw,fields,"lineage")
        if event["schema"]!="orca.clarification.lineage": raise SchemaMalformed("lineage schema")
        if type(event["schema_version"]) is not int or event["schema_version"]!=LINEAGE_SCHEMA_VERSION:
            raise SchemaUnsupported("lineage version")
        if event["run_id"]!=run_id or event["sequence"]!=sequence: raise SchemaMalformed("lineage path binding")
        if event["event_type"] not in {"request_reclarified","ambiguity_limit_reached","decision_superseded",
                                      "decision_cancelled","decision_scope_expanded"}:
            raise SchemaMalformed("lineage event type")
        body=dict(event); event_id=body.pop("event_id")
        if event_id!=_identifier("event","os30-event-v1",body): raise SchemaMalformed("event_id content mismatch")
        return event

    def _append_event(self, root: Path, run_id: str, event_type: str, item_id: str, request_id: str | None,
                      response_id: str | None, actor: dict, provenance: dict, prior: str | None, next_id: str | None,
                      related: list[str], details: dict) -> str:
        lineage=root/"lineage"; existing=sorted(path for path in lineage.iterdir() if re.fullmatch(r"[0-9]{6}", path.name)); sequence=len(existing)
        body={"schema":"orca.clarification.lineage","schema_version":1,"sequence":sequence,"event_type":event_type,
              "run_id":run_id,"decision_item_id":item_id,"request_id":request_id,"response_id":response_id,"actor":actor,
              "provenance":provenance,"occurred_at":_now(),"prior_decision_id":prior,"next_decision_id":next_id,
              "related_item_ids":sorted(related),"details":details}
        body["event_id"]=_identifier("event","os30-event-v1",body)
        _write_directory(root,Path("lineage")/f"{sequence:06d}",{"event.json":(_json_bytes(body),0o600)})
        return body["event_id"]

    def _lineage_events(self, root: Path) -> list[dict]:
        events=[]
        for path in sorted((root/"lineage").glob("[0-9]*/event.json")):
            try:
                events.append(self._validate_lineage_event(
                    json.loads(path.read_text(encoding="utf-8")),root.parent.name,int(path.parent.name)))
            except Exception as exc:
                raise LineageInvalid("lineage record malformed") from exc
        return events

    def _lineage_state(self, root: Path, item_id: str) -> tuple[str | None, str | None, str]:
        decisions=[]
        for path in (root/"decisions").glob("decision_*/record.json"):
            try: rec=self._validate_decision_record(json.loads(path.read_text(encoding="utf-8")),root,path.parent.name)
            except Exception as exc: raise SchemaMalformed("decision: malformed") from exc
            if rec.get("decision_item_id")==item_id: decisions.append(rec)
        by_id={rec["decision_id"]:rec for rec in decisions}
        transitions=[event for event in self._lineage_events(root)
                     if event["decision_item_id"]==item_id and event["event_type"] in {"decision_superseded","decision_cancelled"}]
        superseded=[event for event in transitions if event["event_type"]=="decision_superseded"]
        cancelled=[event for event in transitions if event["event_type"]=="decision_cancelled"]

        # Validate event-to-record and event-to-response bindings before graph use.
        for event in transitions:
            try:
                request=self._request_from_root(root,event["request_id"])
                response=self._validate_response_record(
                    json.loads((root/"responses"/event["response_id"]/"record.json").read_text()),request)
                self._validate_response_evidence(root,response)
            except Exception as exc:
                raise LineageInvalid("lineage response binding") from exc
            if response["decision_item_id"]!=item_id:
                raise LineageInvalid("lineage response item")
            prior,next_id=event["prior_decision_id"],event["next_decision_id"]
            if event["event_type"]=="decision_superseded":
                if (not prior or not next_id or prior==next_id or prior not in by_id or next_id not in by_id or
                        response["decision_id"]!=next_id or response["normalization_outcome"] not in {"OPTION","CUSTOM"} or
                        event["details"]!={"prior_decision_id":prior,"next_decision_id":next_id}):
                    raise LineageInvalid("supersession linkage")
            elif (next_id is not None or response["normalization_outcome"]!="CANCELLED" or
                  event["details"]!={"cancelled_decision_id":prior}):
                raise LineageInvalid("cancellation linkage")

        if not decisions:
            if not cancelled: return None,None,"unresolved"
            if len(cancelled)!=1 or cancelled[0]["prior_decision_id"] is not None or superseded:
                raise LineageFork("competing empty-decision transitions")
            return None,None,"cancelled"
        if any(event["prior_decision_id"] is None for event in cancelled):
            raise LineageInvalid("decision appended after null-predecessor cancellation")

        incoming={decision_id:[] for decision_id in by_id}; outgoing={decision_id:[] for decision_id in by_id}
        for event in superseded:
            outgoing[event["prior_decision_id"]].append(event["next_decision_id"])
            incoming[event["next_decision_id"]].append(event["prior_decision_id"])
        if any(len(set(values))>1 for values in outgoing.values()) or any(len(set(values))>1 for values in incoming.values()):
            raise LineageFork("conflicting supersession fork")
        roots=[decision_id for decision_id,values in incoming.items() if not values]
        if len(roots)>1:
            raise OrphanDecision("unlinked decision")
        if not roots:
            raise LineageFork("lineage has no unique root")
        reachable=set(); cursor=roots[0]
        while cursor not in reachable:
            reachable.add(cursor); successors=set(outgoing[cursor])
            if not successors: break
            cursor=next(iter(successors))
        if len(reachable)!=len(by_id):
            if any(decision_id in reachable for decision_id in set(by_id)-reachable):
                raise LineageFork("lineage cycle")
            raise OrphanDecision("unlinked decision")

        head=roots[0]; reset_anchor=None
        for event in transitions:
            prior,next_id=event["prior_decision_id"],event["next_decision_id"]
            if event["event_type"]=="decision_cancelled":
                if prior!=head: raise LineageFork("cancellation bypasses current head")
                head=None; reset_anchor=prior
            else:
                if head is not None:
                    if prior!=head: raise LineageFork("supersession bypasses current head")
                elif prior!=reset_anchor:
                    raise LineageFork("supersession bypasses cancelled anchor")
                head=next_id; reset_anchor=None
        return head,reset_anchor,"cancelled" if head is None else "effective"

    def _effective_decision(self, root: Path, item_id: str) -> str | None:
        return self._lineage_state(root,item_id)[0]

    def _reclarification_items(self, run_id: str, request: dict, decision_item_id: str) -> list[dict]:
        values=[]
        for published in request["items"]:
            value={key:published[key] for key in ITEM_INPUT_FIELDS}
            if published["decision_item_id"]==decision_item_id:
                value["narrowing_rationale"]="Ambiguous explicit response; choose one listed option or a bounded custom value."
            values.append(_validate_item(value,run_id))
        if {item["decision_item_id"] for item in values}!={item["decision_item_id"] for item in request["items"]}:
            raise SchemaMalformed("reclarification item membership")
        self._validate_known_dag(run_id,values)
        return values

    def _reclarify(self, root: Path, run_id: str, request: dict, decision_item_id: str,
                   response_id: str, actor: dict, provenance: dict) -> str:
        revision=request["revision"]
        item=next(value for value in request["items"] if value["decision_item_id"]==decision_item_id)
        if revision >= MAX_RECLARIFICATION_REVISIONS:
            self._append_event(root,run_id,"ambiguity_limit_reached",item["decision_item_id"],request["request_id"],response_id,
                               actor,provenance,None,None,[],{"final_request_id":request["request_id"],"final_response_id":response_id,"limit":2})
            return "AMBIGUITY_LIMIT_REACHED"
        validated=self._reclarification_items(run_id,request,decision_item_id)
        meta={"items":validated,"bundle_rationale":request["bundle_rationale"],"independence_declared_by":request["independence_declared_by"],
              "accepted_response_modes":request["accepted_response_modes"],"sensitivity_guidance":request["sensitivity_guidance"]}
        result=self._publish_items(run_id,validated,meta,revision=revision+1,prior=request["request_id"],ambiguity_response=response_id)
        self._append_event(root,run_id,"request_reclarified",item["decision_item_id"],request["request_id"],response_id,
                           actor,provenance,None,None,[],{"prior_request_id":request["request_id"],"next_request_id":result.request_ids[0],"ambiguity_reason":"outside_declared_choices"})
        return "RECLARIFICATION_CREATED"

    def show(self, *, run_id: str, request_id: str) -> Mapping[str, object]:
        request=self._request(run_id,request_id); root=_clarification_root(self.artifact_base,run_id)
        # Validate the complete authority set, not only records whose forged item
        # field happens to match the requested item.
        for path in (root/"responses").glob("response_*/record.json"):
            response_raw=json.loads(path.read_text(encoding="utf-8"))
            bound_request=self._request_from_root(root,response_raw.get("request_id",""))
            response=self._validate_response_record(response_raw,bound_request)
            self._validate_response_evidence(root,response)
        for path in (root/"decisions").glob("decision_*/record.json"):
            self._validate_decision_record(json.loads(path.read_text(encoding="utf-8")),root,path.parent.name)
        for path in (root/"lineage").glob("[0-9]*/event.json"):
            self._validate_lineage_event(json.loads(path.read_text(encoding="utf-8")),run_id,int(path.parent.name))
        states={item["decision_item_id"]:self._lineage_state(root,item["decision_item_id"]) for item in request["items"]}
        heads={item_id:value[0] for item_id,value in states.items()}
        statuses={item_id:value[2] for item_id,value in states.items()}
        return {"schema_version":1,"operation":"show","request":request,"current":self._current_request(run_id,request),
                "effective_decisions":heads,"item_statuses":statuses}


def _parser() -> argparse.ArgumentParser:
    parser=argparse.ArgumentParser(prog="clarification",add_help=True); sub=parser.add_subparsers(dest="operation",required=True)
    create=sub.add_parser("create"); respond=sub.add_parser("respond"); show=sub.add_parser("show")
    promote=sub.add_parser("promote")  # post-response entry point; needs no live coordinator
    for p in (create,respond,show,promote): p.add_argument("--artifact-base",type=Path,default=Path(".")); p.add_argument("--run-id",required=True)
    create.add_argument("--ledger-key",action="append",required=True); create.add_argument("--input",type=Path,required=True)
    respond.add_argument("--request-id",required=True); respond.add_argument("--submission-id",required=True); respond.add_argument("--actor-id",required=True)
    respond.add_argument("--actor-type",choices=("human","service"),required=True); respond.add_argument("--where-recorded",required=True)
    respond.add_argument("--responded-at",default=None); respond.add_argument("--sensitivity",choices=("normal","sensitive"),default="normal")
    respond.add_argument("--decision-item-id")
    group=respond.add_mutually_exclusive_group(required=True); group.add_argument("--option-id"); group.add_argument("--response-file",type=Path); group.add_argument("--cancel",action="store_true")
    show.add_argument("--request-id",required=True); show.add_argument("--json",action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args=_parser().parse_args(argv); port=ArtifactHumanApprovalPort(args.artifact_base)
        if args.operation=="create":
            data=json.loads(args.input.read_text(encoding="utf-8")); keys=args.ledger_key
            if sorted(keys)!=sorted(item.get("source_ledger_key") for item in data.get("items",[])): raise ClarificationError("ledger-key/input mismatch")
            ledger=read_decision_ledger(args.run_id,base=args.artifact_base)
            by_key={canonical_ledger_key(r):r for r in ledger}
            for item in data.get("items",[]):
                record=by_key.get(item.get("source_ledger_key"))
                if (record is None or record.get("boundary") not in {"B2","B3"} or
                        record.get("open_decision_item") is not True or record.get("state") not in SOURCE_STATES or
                        any(item.get(field)!=record.get(source) for field,source in
                            (("phase","phase"),("iteration","iteration"),("source_state","state"),("source_reason_code","reason_code")))):
                    raise SourceNotOpen("ledger source is not a published open decision")
                # The primary key alone is not the request's provenance claim: every
                # member of source_ledger_keys is authority-bearing, so derive the
                # authenticated set and require the input to match it exactly.
                derived=derive_source_ledger_keys(item["source_ledger_key"],ledger)
                if tuple(item.get("source_ledger_keys") or ()) != derived:
                    raise SourceNotOpen("source_ledger_keys are not the authenticated set")
            result=port.create(run_id=args.run_id,data=data); output={"schema_version":1,"operation":"create",**dataclasses.asdict(result)}
        elif args.operation=="respond":
            submission=ResponseSubmission(args.submission_id,args.actor_id,args.actor_type,args.where_recorded,args.responded_at or _now(),args.option_id,args.response_file,args.cancel,args.sensitivity)
            result=port.ingest(run_id=args.run_id,request_id=args.request_id,decision_item_id=args.decision_item_id,submission=submission); output={"schema_version":1,"operation":"respond",**dataclasses.asdict(result)}
            # An answer is what unlocks the next question, so this is where promotion
            # belongs. The response is already durable; a promotion failure is reported
            # in the output rather than swallowed, and never unwinds the response.
            try:
                promoted=port.promote_pending(run_id=args.run_id)
                output["promoted"]={"request_ids":list(promoted.request_ids),
                                    "item_ids":list(promoted.item_ids),"status":promoted.status}
            except ClarificationError as exc:
                output["promoted"]={"request_ids":[],"item_ids":[],"status":"ERROR","code":exc.code}
        elif args.operation=="promote":
            result=port.promote_pending(run_id=args.run_id)
            output={"schema_version":1,"operation":"promote",**dataclasses.asdict(result)}
        else: output=port.show(run_id=args.run_id,request_id=args.request_id)
        print(json.dumps(output,sort_keys=True,separators=(",",":"),ensure_ascii=False))
        return 3 if output.get("status") in {"STALE_REQUEST","RECLARIFICATION_CREATED","AMBIGUITY_LIMIT_REACHED"} else 0
    except ClarificationError as exc:
        print(json.dumps({"schema_version":1,"status":"ERROR","code":exc.code},separators=(",",":")),file=sys.stderr)
        return 4 if isinstance(exc,(ClarificationConflict,ClarificationSecurityError)) else 2
    except (OSError,json.JSONDecodeError) as exc:
        print('{"schema_version":1,"status":"ERROR","code":"CLARIFICATION_INPUT_INVALID"}',file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
