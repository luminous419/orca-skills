# Worker Result

STATUS: COMPLETE

Run: `run_db374a3fd83a` · Phase: design · Iteration: 7 · Role: worker
Feature: Jira OS-30 Structured Human Clarification and Decision Protocol

## Summary / Requirements

Implement OS-30 as a separate, standard-library-only clarification protocol linked to, but never
embedded in, the unchanged OS-29 decision ledger. A settled OS-29 `NEEDS_INPUT` or `CONFLICT`
record may cause the runtime adapter to publish an immutable structured request; an explicit,
non-interactive CLI submission may later create immutable response and normalized-decision
evidence. Neither publication nor response ingestion resumes a run, changes its terminal
`BLOCKED` state, admits a dispatch, increments a phase/correction counter, or consumes the stored
decision; those are OS-31 responsibilities.

The design fixes the approved PLAN's repository-local bounds:

- request and response records written after this correction use matched schema generation `2`;
  decision and lineage records remain version `1`, and historical version-1 artifacts are immutable;
- `MAX_BUNDLE_ITEMS = 3`;
- `MAX_RECLARIFICATION_REVISIONS = 2` after revision 0;
- one authoritative raw response copy, maximum `65536` bytes, mode `0600`;
- UTF-8 JSON, closed object field sets, exact enum values, strict integers (a boolean is not an
  integer), RFC 3339 UTC timestamps ending in `Z`, and rejection of unknown fields.

The design directly addresses REVIEW_PLAN N-001 through N-004: semantic parity gets explicit
`validate_skills.py` and `test_validate_skills.py` anchors; installed self-containment gets a
positive-control AST import test as well as isolated execution; the installed twin is re-synced
after every source change; and push/PR remains a delivery action while merge, release publication,
and Jira mutation remain excluded.

Iteration 5 retains the REVIEW_DESIGN iteration-2 corrections for F-001 through F-003: response JSON and ordinary channels carry
safe metadata/digests only; one logical `(run, phase, open_item)` retains one stable
`decision_item_id` across agreeing Worker/Reviewer judgements; and source-key parsing delegates run
identity to the repository's separator/dot-segment rule instead of imposing a narrower character
class. It resolves F-004 by making `open_item` explicitly nullable and using the canonical producer
ledger key as the deterministic identity fallback. It resolves REVIEW_DESIGN iteration-3 F-005 by
first folding every bound Reviewer B3 to its validated Worker B2 producer, then deriving the tag
solely from that producer's label or ledger key; the Reviewer's label is never identity-bearing.
Unrelated null-labelled producers remain distinct. It resolves FINAL_REVIEW FA-002 by making each
response target one stable `decision_item_id`, defining matched request/response schema generation
2, per-item normalization and lineage, whole-request cancellation, and fail-closed version mixing. It also
specifies crash recovery, request currency, cancellation mode, the exact import/AST contract,
publisher parity tests, and one unambiguous harness publication seam.

Iteration 6 resolves REVIEW_IMPLEMENTATION iteration-8 N-802 and R8-002 at the design boundary.
Effective authority is now derived only from a validated per-item lineage graph: timestamps,
sequence numbers, directory order, and an unlinked `later` decision can never select a head. The
historical-v1 reader and v2 writer have separate admissibility rules, including a generation-aware
raw-binding requirement, while existing v1 bytes remain untouched.

## Current Architecture

- `scripts/decision_gate.py` owns OS-29 judgement. Its `CLOSED_LEDGER_RECORD_FIELDS` deliberately
  excludes OS-30 fields and `OS30_RESERVED_FIELDS` names eight examples. Its immutable key is
  `run/phase/iteration/boundary#sequence`; it is already run-qualified.
- `scripts/run_logging.py` owns append-only directory publication using same-filesystem staging,
  file fsync, directory fsync, atomic directory rename, collision retry, and no overwrite. It is
  byte-identical to `orca-worker-reviewer-orchestration/tools/run_logging.py` and must remain usable
  outside the repository.
- `scripts/orca_runtime_harness.py` stores `_last_settled` only in process memory. It records B2/B3
  after a settled attempt, and an unresolved decision is terminal even after the one already
  scheduled verification Reviewer. That is the correct OS-31 boundary.
- `scripts/e2e_harness.py` mirrors the decision-gate topology deterministically: two agents, two
  subprocess sites, four B1 sites, and no resume path.
- The direct-session loop Skill shares OS-28 policy text but has no run artifact root, installed
  orchestration tool, or OS-29 execution gate. It receives semantic documentation, not a false
  claim of executable feature parity.
- `scripts/release_manifest.py` currently permits only `tools/run_logging.py` in the orchestration
  Skill. A second installed tool must be enumerated or package verification will reject it.
- Historical runs may lack `clarifications/`. Absence means “OS-30 artifacts were never created,”
  not malformed history and not an approved decision. No migration or backfill is performed.

## Proposed Design

### 1. Module boundary and constants

Add `scripts/clarification_protocol.py`, then maintain a byte-identical installed twin at
`orca-worker-reviewer-orchestration/tools/clarification_protocol.py`. The module imports only the
standard library plus sibling `run_logging` through the same dual-location pattern as
`decision_gate.py`:

```python
try:
    from scripts.run_logging import <required names>
except ModuleNotFoundError:
    from run_logging import <required names>
```

It must not import `decision_gate`, either harness, Orca, or any transport. Callers pass validated
OS-29 source data as plain values; the one-way dependency is therefore
`harness -> clarification_protocol -> run_logging`, never the reverse.

Public constants and enums are closed:

```text
REQUEST_SCHEMA_VERSION = 2
RESPONSE_SCHEMA_VERSION = 2
BINDING_SCHEMA_VERSION = 1
DECISION_SCHEMA_VERSION = 1
LINEAGE_SCHEMA_VERSION = 1
MAX_BUNDLE_ITEMS = 3
MAX_RECLARIFICATION_REVISIONS = 2
MAX_RAW_RESPONSE_BYTES = 65536
ID_PATTERN = ^[a-z][a-z0-9]*(?:_[a-z0-9]+)*_[0-9a-f]{24}$

SOURCE_STATES = NEEDS_INPUT | CONFLICT
RESPONSE_KINDS = OPTION_ID | TEXT | CANCEL
NORMALIZATION_OUTCOMES = OPTION | CUSTOM | AMBIGUOUS | CANCELLED
DECISION_KINDS = OPTION | CUSTOM
LINEAGE_EVENT_TYPES = request_reclarified | ambiguity_limit_reached |
                      decision_superseded | decision_cancelled |
                      decision_scope_expanded
ACTOR_TYPES = human | service
SENSITIVITY = normal | sensitive
REQUEST_STATUS = current | stale
ITEM_EFFECTIVE_STATUS = unresolved | effective | cancelled | invalid
```

All validator entry points return frozen dataclasses or plain immutable mappings; storage entry
points return IDs/paths/status values and raise typed `ClarificationError` subclasses. Error text
contains identifiers and closed reason codes only, never raw response or normalized secret text.

### 2. Exact artifact layout

```text
artifacts/runs/<run-id>/clarifications/
  requests/<request-id>/record.json
  responses/<response-id>/record.json
  responses/<response-id>/raw_response.txt
  response_bindings/<binding-id>/record.json
  decisions/<decision-id>/record.json
  lineage/<000000>/event.json
  lineage/<000001>/event.json
  .staging/<random-private-name>/...
```

`<run-id>` continues through `run_logging`'s existing run-root validation. Every remaining path
component must match `ID_PATTERN` or the fixed six-digit lineage sequence grammar; callers never
concatenate unvalidated input into a path. Readers ignore `.staging` but do not ignore malformed
published directories or records: they return a typed unreadable sentinel to the state validator,
which makes the item `invalid` and blocks any new authoritative transition.

Publication uses a new small generic helper in `clarification_protocol.py`, deliberately matching
the existing directory-publication behavior rather than changing the OS-29 writer. It creates a
mode-`0700` staging directory beneath `clarifications/.staging`, opens files with `O_EXCL`, fsyncs
each file and directory, applies final modes, then atomically renames the whole directory. An
existing target is first-writer-wins. Matching replay returns the existing object; non-matching
reuse raises `CLARIFICATION_ID_CONFLICT`. No published file is edited.

Because OS-30 must publish byte-exact binary content with restrictive modes that the text-only
OS-29 helper does not support, its helper remains separate. A behavioral parity test runs both
helpers through their shared guarantees—same-filesystem staging, exclusive creation, file and
directory fsync ordering, atomic rename, collision/idempotent replay, and no overwrite—plus
OS-30-only assertions for byte preservation and `0600`/`0700` modes. Any intentional future
divergence must update that parity matrix.

The response directory is published in one rename containing both `record.json` and
`raw_response.txt`, so metadata never points to a missing raw file. JSON records use mode `0600`
because even redacted metadata may be sensitive; directories use `0700`. Request, decision, and
lineage records also use `0600`. Raw files are never included in release packages or generalized
artifact exports.

`response_bindings/` is an intentional published artifact type, not staging data. Its closed record
has exactly `schema="orca.clarification.response-raw-binding"`, `schema_version=1`, `binding_id`,
`response_id`, and `raw_sha256`; its identity formula is defined below. It is required exactly once
for every v2 response and prohibited from being used to retrofit authority into a v1 response.
Section 6 defines generation-aware read admission.

### 3. Deterministic identities and source binding

Identifiers are SHA-256-derived, truncated to 96 bits (24 lower-case hex characters), with a type
prefix. Canonical inputs are UTF-8 JSON encoded with sorted keys, compact separators, and no NaN.
The digest is stable across processes and Python 3.11-3.13.

```text
canonical_producer_record(r) = the Worker B2 record named by
                               r.verifies.worker_record_key when r is a Reviewer B3
                               whose binding to that record passed OS-29 validation;
                               otherwise r
logical_item_key(r) = canonical(["named", producer.run, producer.phase,
                                 producer.open_item])
                      when producer.open_item is a non-empty string
                    = canonical(["producer", decision_gate.ledger_key(producer)])
                      when producer.open_item is null or empty
                      where producer = canonical_producer_record(r)
decision_item_id = "item_" + H("os30-item-v1\0" + logical_item_key)[0:24]
bundle_id        = "bundle_" + H("os30-bundle-v1\0" + sorted item IDs)[0:24]
request_id       = "request_" + H("os30-request-v2\0" + item IDs + revision +
                                   canonical request contract)[0:24]
response_id      = "response_" + H("os30-response-v2\0" + request_id +
                                    decision_item_id + submission_id)[0:24]
decision_id      = "decision_" + H("os30-decision-v1\0" + response_id +
                                    canonical normalized payload)[0:24]
binding_id       = "binding_" + H("os30-response-raw-binding-v1\0" +
                                   canonical([response_id, raw_sha256]))[0:24]
```

These are unkeyed content addresses, not signatures or capabilities. The security claim is
**structural integrity against partial/in-place mutation and unlinked append forgery**: closed
schemas, re-derived identities, raw bindings, and the lineage graph detect non-first decisions
appended without a valid transition and edits that do not consistently replace every dependent object. The design
does **not** claim cryptographic authenticity or unforgeability against a writer able to replace or
fabricate every response, binding, decision, and lineage event and recompute all public hashes. That
stronger adversary requires an external trust primitive outside OS-30's approved baseline; neither
content addressing nor folding the raw digest into `response_id` supplies it.

`source_ledger_key` is split structurally from the right as
`<run-id>/<phase>/<iteration>/(B2|B3)#<sequence>`. Phase and numeric components retain their closed
OS-29 grammars. `<run-id>` must equal the caller's run ID and is validated by the same
`run_logging` rule used for the run artifact root: one non-empty path component, neither `.` nor
`..`, containing no `/` or platform path separator. No `run_` prefix or additional character
class is imposed, so existing repository and actual Orca IDs remain valid while traversal fails
closed. The adapter obtains the complete key only from `decision_gate.ledger_key(record)`; OS-30
never writes a reverse link into OS-29.

OS-29 keys identify judgements, not necessarily named questions, so identity evaluation has two
ordered steps. First, `canonical_producer_record(r)` unconditionally folds a Reviewer B3 whose
`verifies` binding passed OS-29 validation to the Worker B2 record named by
`verifies.worker_record_key`; every other record is its own producer. The adapter never follows an
unvalidated or cross-run/cross-phase claim. Second, and only after that fold, it selects the tagged
key from the canonical producer record: a non-empty producer `open_item` uses the exact validated
`(producer.run, producer.phase, producer.open_item)` tuple, while a null or empty producer label
uses `decision_gate.ledger_key(producer)`. The reading Reviewer's own `open_item` is descriptive
evidence only and never affects identity. This one-hop rule is deterministic because OS-29 permits
a Reviewer B3 to verify exactly one Worker B2 key and rejects duplicate verification; no prose,
sequence adjacency, or nullable Reviewer label is used to infer identity.

The first blocking judgement for the resulting logical key becomes `source_ledger_key`; later
blocking judgements for that key join the immutable sorted `source_ledger_keys` evidence set and
never mint another item or request. Thus an agreeing Worker B2 and its bound Reviewer B3 share one
stable item even when their labels disagree or both carry `open_item: null`, while two independent null/empty producers have
different producer ledger keys and cannot coalesce. Judgements sharing a key must agree on the
state/reason and request contract; disagreement fails closed for Coordinator resolution instead of
splitting identity. The same named `open_item` in another run or phase is a different item.

`submission_id` is a required caller-generated idempotency token matching
`^[a-z][a-z0-9_-]{0,63}$`; it is not raw answer content. Repeating the same submission ID with
byte-identical raw content and identical actor/provenance returns the existing response/decision.
Reusing it with different bytes or metadata fails closed. A request is current iff it is the
highest fully published revision reachable from revision 0 through valid `request_reclarified`
events and matching request records; an event whose successor request is absent does not advance
currency. An effective decision does not make its request stale, so a later explicit changed
answer can supersede it. A response with a new submission ID to a non-current request is retained
as `stale=true` evidence but creates no decision or lineage head.

### 4. Closed schema: request record v2

Every key below is required unless marked nullable. New writes use `schema` exactly
`orca.clarification.request` and `schema_version` exactly `2`. Version 2 has the same closed fields
as the previously specified version 1; the bump creates a non-mixable generation boundary for
item-addressed response v2. Historical v1 request bytes are never rewritten.

| Field | Exact type / constraint |
| --- | --- |
| `schema`, `schema_version` | fixed string; strict integer `2` |
| `request_id` | request ID; equals deterministic derivation |
| `bundle_id` | bundle ID or `null`; required non-null for 2-3 items, null for 1 |
| `revision` | strict integer `0..2` |
| `reclarifies_request_id` | request ID or `null`; null iff revision 0 |
| `ambiguity_response_id` | response ID or `null`; non-null iff revision > 0 |
| `created_at` | UTC timestamp |
| `items` | array of 1..3 `RequestItem` objects, sorted by `decision_item_id` |
| `bundle_rationale` | non-empty string for bundle; empty string for one item |
| `independence_declared_by` | actor/provenance object; must be Coordinator adapter |
| `accepted_response_modes` | exact non-empty subset of `option_id`, `response_file`, `cancel` |
| `default_applicable` | literal `false` |
| `on_timeout` | literal `no selection; run remains blocked` |
| `sensitivity_guidance` | non-empty string, maximum 1000 Unicode scalars |

Each closed `RequestItem` has exactly:

| Field | Exact type / constraint |
| --- | --- |
| `decision_item_id` | deterministic item ID, unique in bundle |
| `open_item` | non-empty OS-29 logical question identity or `null`; copied only from the canonical producer record defined in §3, never from a bound Reviewer's label; when null, the producer ledger key supplies identity |
| `source_ledger_key` | first immutable OS-29 judgement key for this item |
| `source_ledger_keys` | sorted unique non-empty array containing first and later agreeing judgement keys |
| `source_state` | `NEEDS_INPUT` or `CONFLICT` |
| `source_reason_code` | OS-28 lowercase reason token |
| `phase`, `iteration` | validated phase token; strict positive integer |
| `question` | 1..1000 Unicode scalars |
| `context` | 1..4000 Unicode scalars |
| `what_is_blocked` | 1..2000 Unicode scalars |
| `options` | 1..8 closed `Option` objects |
| `recommended_option_id` | exactly one option ID present in `options` |
| `recommendation_rationale` | 1..2000 Unicode scalars |
| `deadline_at` | UTC timestamp or `null`; informational only |
| `depends_on` | sorted unique array of item IDs; no self/cycle |
| `independent_with` | sorted unique IDs of every co-bundled item |
| `custom_decision` | closed envelope below |
| `narrowing_rationale` | empty string at revision 0; non-empty only when a later revision narrows options |

An `Option` has exactly `option_id`, `label`, `action`, and `tradeoff`; IDs match
`^[a-z][a-z0-9_-]{0,63}$`, are unique per item, and all three text fields are non-empty with maxima
200/2000/2000. A recommendation is descriptive evidence only; it is never a response, default,
timeout action, or authority.

`accepted_response_modes` gates all three CLI alternatives. `cancel` is explicit rather than an
implicit escape hatch; when omitted, `--cancel` is rejected. Runtime-created requests include it
when lifecycle cancellation is permitted. Cancellation always requires actor/provenance evidence
and never arises from timeout, EOF, or process exit.

`custom_decision` has exactly `allowed`, `subject`, `value_type`, `max_length`, `pattern`,
`allowed_values`, and `sensitive`. When `allowed=false`, subject is empty, value type is `none`,
max length is `0`, pattern is null, allowed values is empty, and sensitive is false. When true,
subject is non-empty; value type is one of
`text|integer|boolean|enum`; max length is `1..4096` for text/enum and `0` otherwise; pattern is a
full-match regular expression only for text, or null; an enum additionally carries a required
closed `allowed_values` array. To keep the object closed, `allowed_values` is required for all
forms and is empty unless `value_type=enum`.

The create input contains the same item/business fields plus adapter provenance; the module stamps
schema, IDs, revision 0, and time. Re-clarification is module-generated only. It copies the option
set exactly unless the ambiguity result proves a strict subset; any subset requires
`narrowing_rationale` in the new item. No new option, wider custom envelope, changed recommendation,
or changed dependency may enter through re-clarification.

### 5. Bundle and dependency contract

The Coordinator/adapter is the sole producer of `depends_on`, `independent_with`, and
`independence_declared_by`; these declarations are never inferred from question prose or from
per-key declarations in isolation. The adapter first constructs the complete known-item DAG from
all validated Coordinator declarations in the terminal blocking set and persisted effective
predecessors, derives the transitive dependency relation, and then computes an antichain. For every
chosen pair it writes both item IDs into their symmetric `independent_with` lists; the module checks
that derivation rather than expecting one per-key declaration to predict a peer's content-derived
ID. Before any
request publication the module validates the complete known-item DAG:

1. item and source keys are unique and source-bound;
2. every dependency exists in the provided graph or is an already effective persisted item;
3. the graph is acyclic;
4. each co-bundled pair declares symmetric independence;
5. no co-bundled pair is ancestor/descendant;
6. every dependency outside the bundle has an effective, non-cancelled decision;
7. bundle size is 1..3.

A dependency-ready set is sorted by `decision_item_id`; the adapter takes at most three items from
the first independent antichain. This must be a genuine antichain in the complete DAG: no item may
be bundled with any direct or transitive predecessor or successor. In particular, a DEPENDENT
question can never share a request with the question it depends on. A dependent item receives no request until all predecessors are
effective. Cancellation of a predecessor makes successors non-ready. Scope expansion adds new
nodes and edges only; it cannot mutate the old graph.

### 6. Closed schema: response record v2 and raw evidence

New `record.json` writes have exactly the following closed schema. `decision_item_id` is used rather
than an index or a new identity field because it is already the stable, content-derived logical-item
identity from §3: it survives request revisions and is unique within a bundle. Positional indices
would change when sorting, partitioning, or revising a bundle.

| Field | Exact type / constraint |
| --- | --- |
| `schema`, `schema_version` | `orca.clarification.response`, strict integer `2` |
| `response_id`, `submission_id`, `request_id` | validated IDs/tokens |
| `request_revision` | exact revision observed by submitter |
| `decision_item_id` | string matching `ID_PATTERN`, exactly one member of the named current request; stable across every revision of that logical item |
| `response_kind` | `OPTION_ID`, `TEXT`, or `CANCEL` |
| `actor` | closed `{actor_id, actor_type}`; non-empty ID, closed actor type |
| `provenance` | closed `{source, capture_mechanism, where_recorded}`; source literal `explicit_user_reply`, mechanism literal `cli`, non-empty location |
| `responded_at`, `recorded_at` | caller timestamp and module timestamp, UTC |
| `raw` | closed `{path, sha256, byte_count, sensitivity, redaction_policy_version}` |
| `stale` | boolean derived at ingestion |
| `normalization_outcome` | one of the four closed outcomes |
| `normalization_reason` | closed code, never raw text |
| `decision_id` | decision ID or `null` |

`raw.path` is the fixed relative `raw_response.txt`, digest is 64 lower-case hex, count is
`0..65536`, and redaction version is safe policy-version metadata only. No preview, excerpt,
normalized secret, or other response-derived string exists in response JSON. For
`--option-id`, raw bytes are the exact UTF-8 option token. For `--response-file`, bytes are copied
exactly; normalization requires strict UTF-8 but preservation does not transform newlines. Empty,
oversized, unreadable, non-regular, or unsafe files fail before publication. Symlinks are rejected.
`CANCEL` records a fixed UTF-8 cancellation token plus explicit actor evidence; absence, EOF, CLI
timeout, or process termination never synthesizes it.

Schema generations are non-destructive and fail closed, with distinct read and write admissibility.
Readers accept only two homogeneous shapes for one request lineage: (a) a historical
request-v1/response-v1 single-item lineage, for which the absent response item field is resolved to
the sole request item in memory and raw evidence is verified directly against the response's
`raw.sha256`; no `response_bindings/` record is required or synthesized; or (b) a
request-v2/response-v2 lineage, for which each response requires exactly one closed, identity-valid
binding record whose `response_id` and `raw_sha256` equal the response and the digest of its raw
bytes. In both shapes every request, response, decision, and lineage link must otherwise validate.
No writer creates v1. A historical v1 response remains effective on read only under shape (a). A
v1 response under any bundled request is `SCHEMA_VERSION_MIXED`, because its target is unknowable.
A tree may contain disjoint historical-v1 and v2 request lineages; each is admitted independently.
Any request lineage or logical item that crosses generations, a request with responses from both
generations, a v2 response against v1, or a v1 response against v2 is
`SCHEMA_VERSION_MIXED`: every affected item is invalid and no transition is authorized. A binding
for a v1 response neither makes it v2 nor makes it invalid by itself; it is non-authoritative extra
evidence and must not be consulted. Conversely, a missing, duplicate, mismatched, or malformed
binding for v2 is `SCHEMA_MALFORMED`. Existing v1 files and directories are never rewritten,
migrated, or backfilled. Any unknown version is `SCHEMA_UNSUPPORTED`; it is never coerced,
defaulted, copied forward, or skipped. Checks precede normalization and head replay.

### 7. Normalization and closed decision record v1

Normalization is deterministic, locale-independent, and applied independently to the one
`decision_item_id` named by each v2 response:

1. `--option-id` must exactly equal one option ID. Otherwise reject without guessing.
2. Text is decoded as strict UTF-8, Unicode NFC-normalized for matching only, stripped at both ends,
   and compared by exact option ID, then case-folded exact option label. Exactly one match produces
   `OPTION`; zero continues; more than one is `AMBIGUOUS`.
3. If custom is allowed, parse only the declared value type, enforce the declared full-match/enum/
   length boundary, and reject trailing material. A wholly in-envelope value produces `CUSTOM`.
4. A precise but out-of-envelope value, non-executable prose, zero match with custom disabled, or
   multiple plausible option matches produces `AMBIGUOUS`. It creates no decision.
5. `CANCEL` is a lifecycle instruction, not a normalized decision.

`decisions/<decision-id>/record.json` remains version 1 and has exactly:

| Field | Exact type / constraint |
| --- | --- |
| `schema`, `schema_version` | `orca.clarification.decision`, `1` |
| `decision_id`, `decision_item_id`, `request_id`, `response_id` | validated and mutually bound |
| `source_ledger_key` | exact request item binding |
| `kind` | `OPTION` or `CUSTOM` |
| `option` | closed `{option_id, action}` or `null`; non-null iff OPTION |
| `custom` | closed `{value_type, value, bounded_by, redacted, raw_response_sha256}` or `null`; non-null iff CUSTOM |
| `actor`, `provenance` | copied closed evidence from response |
| `responded_at`, `normalized_at` | UTC timestamps |
| `resolves` | exact `source_ledger_key` |
| `scope` | exact request custom subject or option action boundary |

For sensitive custom values, `custom.value` is null, `redacted=true`, and authority is the typed
boundary plus raw digest/reference; for normal values it holds only the bounded normalized scalar.
`bounded_by` is a copy of the request envelope, so later readers need not trust mutable code or
prose. No decision is valid unless its response, request, raw digest, item, and source key reconcile.

### 8. Bounded re-clarification

An ambiguous current response affects only its named item and is published first. If its request revision is 0 or 1, append
`request_reclarified`, then publish revision 1 or 2 with the same decision item IDs and direct
`reclarifies_request_id`/`ambiguity_response_id` link. Creation is deterministic and replay-safe.
If revision 2 is ambiguous, append `ambiguity_limit_reached`; keep the item unresolved and create no
revision 3. The request writer must not loop internally or prompt. The CLI returns status
`RECLARIFICATION_CREATED` or `AMBIGUITY_LIMIT_REACHED` plus IDs only.

An ambiguous changed answer does not supersede the effective decision. Supersession occurs only
after a later response successfully produces a new decision.

### 9. Closed lineage event schema v1 and effective-head derivation

Each `lineage/<sequence>/event.json` has the common exact fields:

```text
schema="orca.clarification.lineage", schema_version=1, sequence,
event_id, event_type, run_id, decision_item_id, request_id, response_id,
actor, provenance, occurred_at, prior_decision_id, next_decision_id,
related_item_ids, details
```

`event_id = "event_" + H(canonical event without event_id)[0:24]`. Nullable identifiers remain
present as null. `related_item_ids` is always a sorted array. `details` is a closed tagged object:

- `request_reclarified`: prior/next request IDs, ambiguity reason code;
- `ambiguity_limit_reached`: final request/response IDs and literal limit `2`;
- `decision_superseded`: prior and next decision IDs, both same item, successful replacement
  response required. The top-level `prior_decision_id` is the predecessor and
  `next_decision_id` is the successor; the same values must appear in `details` and reconcile with
  the referenced decision records;
- `decision_cancelled`: prior decision ID, nullable only when the item has no decisions and no
  effective head, next null, explicit CANCEL response required;
- `decision_scope_expanded`: prior decision retained, next null, non-empty new item IDs plus exact
  new dependency edges and bounded scope statements.

Lineage sequence allocation uses bounded collision retry like OS-29. Sequence determines only event
replay order; it never confers decision authority. Readers first validate every record and event,
then derive one graph independently per `decision_item_id` as follows:

1. Let `D` be all fully validated decisions for the item. A decision whose request, response, raw
   evidence (under §6's generation rule), item, or source binding fails validation makes the item
   invalid before graph construction. If `D` is empty, the state is `cancelled` when exactly one
   valid null-predecessor cancellation marker from step 4 exists, `unresolved` when none exists,
   and `invalid` when a malformed or competing transition purports to reference it. This is a
   terminal empty-set classification: return that status without attempting the root replay in
   steps 2-5. If a decision later appears beside a previously valid null-predecessor marker, apply
   step 5's explicit post-cancellation rejection instead of reclassifying the decision as a root.
2. For every validated `decision_superseded` event, require non-null
   `prior_decision_id=P` and `next_decision_id=N`, require distinct `P,N` in `D`, require both records
   to name the event's item and to have a `source_ledger_key` whose §3 structural split yields the
   event's run, and require `N.response_id` to equal the event's successful replacement
   `response_id`. Add directed edge `P -> N`. No timestamp comparison is performed.
3. Every non-root decision must be the `next_decision_id` of exactly one such edge. There
   must be exactly one root in `D` (indegree zero), and every member of `D` must be reachable from
   it. A second root or any unreachable decision is an **orphan decision** and rejects the item as
   `ORPHAN_DECISION`; it is never treated as a candidate head.
4. A `decision_cancelled` event requires `next_decision_id=null` and a validated explicit CANCEL
   response for this run/item/request. When `D` is non-empty, it requires non-null
   `prior_decision_id=P` naming the then-effective decision; it resets the effective head to null
   and records `P` as the cancelled reset anchor. When `D` is empty and the item has no effective
   head, request-level cancellation instead emits exactly one such event with
   `prior_decision_id=null`. This null-predecessor cancellation marker is valid only for that empty
   `D` case, derives item status `cancelled` rather than `unresolved`, and neither creates nor
   consumes a reset anchor. A legitimate
   later redecision still requires a `decision_superseded` edge with `prior_decision_id=P` and the
   new decision as `next_decision_id`; that edge is admissible from a null head only when `P` is the
   most recent unmatched cancelled reset anchor. Applying it consumes the anchor and establishes
   the successor as head. Thus cancel-then-redecide has an explicit, validated transition and no
   fallback.
5. Replay the validated transition events in sequence order from the unique root. Before the first
   head-changing event, the root is effective. A supersession must name the current head, except
   for the precisely defined cancelled-anchor case above; cancellation must name the current head
   except for the empty-`D` null-predecessor marker defined in step 4. If that marker is present,
   the cancelled item admits no first-decision root: any later decision for the item makes it
   `invalid` with `LINEAGE_INVALID`; request abandonment cannot be reversed by answering an item
   that had no prior decision. A repeated byte-identical marker is idempotent, while any competing
   cancellation marker or head-changing event is invalid.
   Scope expansion does not change it. After replay, the single remaining head is effective, or a
   final unconsumed cancellation anchor yields `cancelled` with no effective decision.

A **conflicting fork** exists if a predecessor has more than one distinct supersession successor, a
successor has more than one predecessor, graph traversal cycles, two non-identical head-changing
events compete for the same replay state, a transition bypasses the current head/reset anchor, or
replay otherwise yields more than one possible head. It rejects the item as `LINEAGE_FORK`; no
branch is selected by timestamp, event sequence, path order, lexical ID, or latest-write rule.
Missing lineage events therefore surface as `ORPHAN_DECISION`; malformed/missing referenced targets
surface as `LINEAGE_INVALID`; forks surface as `LINEAGE_FORK`. Each makes the item `invalid`, blocks
new authoritative transitions, and preserves every published byte. Exact replay of an already
applied event is idempotent only when every byte and semantic field matches.

`MULTIPLE_EFFECTIVE_HEADS` is not a separate condition: every construction or replay state that
could yield multiple effective heads is the `LINEAGE_FORK` condition above, so the latter subsumes
that condition.

This graph algorithm is the only effective-head derivation. In particular, there is no `later`
fallback and `normalized_at`, `occurred_at`, directory order, and decision/event sequence values
cannot promote a decision. A stale response/event remains evidence but has zero head effect.
The §6 v1/v2 split governs evidence admissibility only; this graph derivation applies identically to
both generations. A hypothetical historical v1 tree whose head depended on the removed `later`
fallback now fails `ORPHAN_DECISION` with all historical bytes untouched.

Consequently one request may have effective, unresolved, cancelled, and invalid items
simultaneously. It is fully resolved only when every item is effective; any unresolved item keeps
the run blocked, any invalid item fails the request closed, and cancellation never supplies
authority or satisfies dependencies.

Per-item rejection and replay contracts are exact:

- **Partial:** accepted. Each valid response advances only its named item's head; unanswered items
  remain `unresolved`, the request remains not fully resolved, and the run remains blocked.
- **Duplicate:** byte/metadata-identical replay of the same `submission_id` for the same item is
  idempotent and returns existing IDs. A new submission for an item with an effective head is
  accepted only as the specified changed-answer supersession. Reuse of a submission ID with a
  different item or content is rejected as `CLARIFICATION_ID_CONFLICT`, with no mutation.
- **Missing:** v2 `decision_item_id` is required; omission is rejected as `SCHEMA_MALFORMED`. An ID
  absent from the named request is rejected as `ITEM_NOT_IN_REQUEST`. A bundle item with no response
  remains unresolved and keeps the request blocked.
- **Stale:** a response naming a non-current revision is retained as `stale=true` evidence but
  creates no decision or event and returns exit/status `3`/`STALE_REQUEST`. If its named item no
  longer exists in the current revision, ingestion is rejected before publication as `STALE_ITEM`.
  It is never coerced to a surviving item. Re-clarification must retain item IDs, so a revision that
  silently removes an item is `SCHEMA_MALFORMED` and cannot become current.

### 10. Runtime-neutral port

Expose plain runtime-neutral types in `clarification_protocol.py`:

```python
@dataclass(frozen=True)
class ClarificationSource:
    open_item: str | None
    source_ledger_key: str
    source_ledger_keys: tuple[str, ...]
    state: str
    reason_code: str
    phase: str
    iteration: int
    request_input: Mapping[str, object]

@dataclass(frozen=True)
class PublishResult:
    request_ids: tuple[str, ...]
    item_ids: tuple[str, ...]
    status: str

@dataclass(frozen=True)
class IngestResult:
    response_id: str
    decision_id: str | None
    request_id: str
    decision_item_id: str | None
    status: str

class HumanApprovalPort(Protocol):
    def publish(self, *, run_id: str,
                sources: Sequence[ClarificationSource]) -> PublishResult: ...
    def show(self, *, run_id: str, request_id: str) -> Mapping[str, object]: ...
    def ingest(self, *, run_id: str, request_id: str,
               decision_item_id: str | None,
               submission: ResponseSubmission) -> IngestResult: ...
```

The `status` members use `ITEM_EFFECTIVE_STATUS` for per-item results, and `show` includes an
`item_statuses` mapping from every `decision_item_id` to that closed status alongside the separate
effective-decision-ID mapping. Thus `unresolved` and `cancelled` remain distinguishable even though
both have a null effective decision; this status is derived view data and is never persisted into
an existing record.

`ArtifactHumanApprovalPort` is the only initial implementation. It receives `artifact_base` in its
constructor and owns no runtime callbacks. No method accepts a dispatch function, terminal handle,
Orca client, resume token, run-status writer, or iteration mutator. This makes the OS-31 exclusion a
type/API boundary rather than documentation alone.

### 11. CLI contract

The byte-identical module is directly executable:

```text
python clarification_protocol.py create --artifact-base PATH --run-id RUN \
  --ledger-key KEY --input REQUEST.json

python clarification_protocol.py respond --artifact-base PATH --run-id RUN \
  --request-id ID [--decision-item-id ITEM] --submission-id TOKEN --actor-id ID --actor-type human \
  --where-recorded TEXT (--option-id ID | --response-file PATH | --cancel)

python clarification_protocol.py show --artifact-base PATH --run-id RUN \
  --request-id ID [--json]
```

`create` accepts one item or a pre-validated bundle input; repeated `--ledger-key` values must match
the input items exactly. It verifies the named published OS-29 record through an adapter-supplied
record for library use and through read-only `run_logging.read_decision_ledger` for CLI use. Only a
valid open B2/B3 `NEEDS_INPUT`/`CONFLICT` source is eligible.

For `--option-id` and `--response-file`, `--decision-item-id` is required even for a one-item v2
request and must name exactly one item in that request. `--cancel` is a request-level lifecycle
instruction and never depends on item count: it forbids `--decision-item-id`, atomically publishes
one CANCEL response per item. Each child uses
`submission_id = "cancel_" + H(canonical([caller_submission_id, decision_item_id]))[0:24]`, which
meets the closed token bound, and appends cancellation lineage for every non-cancelled item. The
caller token and complete sorted item set participate in replay validation. Validation and commit
are all-or-nothing; exact replay is idempotent. Any invalid item/version/head rejects the entire
operation as `CANCEL_REQUEST_INVALID` without publication. Request-level cancel makes every item
cancelled, removes every effective head, leaves dependents non-ready, and does not resolve or
approve the run. Individual-item cancellation is intentionally not exposed: cancellation expresses
abandonment of the whole human request.
For an item cancelled before any decision existed, a later answer is not admissible: validation
detects the null-predecessor marker before authority publication and returns `LINEAGE_INVALID`
without creating a response, binding, decision, or lineage event. This is distinct from the
already-decided cancel-then-redecide transition admitted only through §9's unmatched reset anchor.

CLI stdout is one compact JSON object containing schema version, operation, status, and identifiers.
stderr contains closed error code plus identifiers. Exit codes are `0` success/idempotent replay,
`2` usage/schema error, `3` stale/ambiguous unresolved result, `4` conflict/corruption/security
failure. It never calls `input()`, reads stdin, prompts, accesses a TTY, invokes `orca orchestration
ask`, or treats a command timeout as approval. `--response-file` is the only free-text path; no
`--response` flag is provided, avoiding shell history/process-list leakage.

`show` returns the redacted request/status/effective-decision metadata only. It never prints raw
response bytes or sensitive custom values; raw inspection remains an explicit filesystem action by
an authorized operator.

### 12. Harness integration: create, never resume

Add an optional `human_approval_port` constructor dependency to both harnesses, defaulting to
`ArtifactHumanApprovalPort` rooted at the existing artifact base. Add one adapter method
`_publish_clarifications_for_terminal_block(records, coordinator_input)`. In
`orca_runtime_harness.py` its sole call site is the final `log_run_status` path after
`decision_gate.unresolved_block_reason(...)` has converted the completed attempt to terminal
`BLOCKED`, immediately before the final BLOCKED artifact is logged/returned. It receives the
complete authoritative open B2/B3 record set, not individual settlement callbacks. The analogous
single seam in `e2e_harness.py` is final BLOCKED-result assembly, before result serialization.

The adapter first folds every OS-29-validated bound Reviewer B3 to its canonical Worker B2 producer,
then groups records by the producer-derived tagged logical-item rule in §3. It orders each group's keys by
ledger sequence, retains the canonical producer/first key as `source_ledger_key`, and supplies all
agreeing keys as source evidence. It publishes once per open question or unlabeled producer,
never once per judgement record; deterministic
identity makes retries idempotent. Publication failure is logged as a closed OS-30
artifact error and the run remains `BLOCKED`; it cannot convert a block to error, CLEAR, or a new
dispatch. A missing Coordinator request declaration is fail-closed and produces no vague fallback
question.

There is no publication call at the B2/B3 settlement sites. The final-block seam ensures an
agreeing Worker/Reviewer pair is coalesced before publication and removes the prior ambiguity
between settlement and terminal-status calculation. It does not
alter B1 admission, `_last_settled`, `_pending_verification`, `gate_attempts`, the correction loop,
phase transition code, subprocess sites, task/dispatch creation, or final-review routing. The
deterministic harness accepts an injected fake port for assertions; the runtime harness uses the
artifact port. CLI response ingestion is tested after `finish()` in a separate invocation and no
harness instance consumes its result.

Measured invariant for response ingestion:

```text
delta(run status, OS-29 ledger head, task count, dispatch count, subprocess count,
      phase iteration, correction count, role/round/status vocabularies) == 0
```

### 13. Raw preservation, redaction, and fail-closed boundary

The module never creates a response preview and never passes response-derived text to
`run_logging.redact_text` as a substitute for secrecy: that policy does not recognize every bare
secret. Ordinary channels receive only validated identifiers, byte counts, fixed outcome/reason
codes, sensitivity flags, and SHA-256 digests. Safe order is:

1. validate IDs, source request, actor/provenance, file type, size, and target paths;
2. read at most `MAX_RAW_RESPONSE_BYTES + 1`, compute digest, and compute deterministic
   normalization entirely in memory (strict UTF-8 where normalization requires it);
3. prepare sparse safe metadata and, when normalization succeeds, its deterministic decision ID;
4. publish raw plus response JSON together at `0600` inside a `0700` directory;
5. re-open with no symlink following, verify mode/digest/count;
6. only then commit authority by publishing the already-computed decision and any lineage event.

A crash after response publication but before step 6 leaves a safe unresolved response naming a
deterministic absent decision. Replaying the same submission ID with byte-identical raw bytes,
actor, provenance, and timestamps validates the existing response, recomputes the same normalized
payload/decision ID, and resumes at step 6; it does not republish or edit the response. Any mismatch
is `CLARIFICATION_ID_CONFLICT`. Readers derive authority only from a present, fully validated
decision and lineage head, never from the response's pointer, so every partial state fails closed.

If permission setting, fsync, digest verification, schema validation, redaction policy availability,
UTF-8 decoding, or atomic publication is uncertain, stop before normalization/authority. The raw
response may exist in a private staging directory after a crash but readers ignore staging; startup
cleanup may remove only validated stale staging directories owned by this protocol, never published
objects. Error construction, CLI output, ordinary logs, OS-29 ledger, task specs, lineage details,
and exported summaries receive safe metadata/digests only; response bytes, previews, excerpts, and
normalized sensitive values are forbidden in all of them.

## Components / Interfaces / Data Flow

```text
terminal authoritative open Worker/Reviewer record set
  -> unchanged OS-29 validator and append-only decision ledger
  -> valid open NEEDS_INPUT/CONFLICT ledger key
  -> final-BLOCKED harness adapter coalesces logical questions + Coordinator declarations
  -> HumanApprovalPort.publish
  -> immutable request record(s)
  -> run remains BLOCKED; harness ends

explicit later CLI response
  -> HumanApprovalPort.ingest
  -> private raw + sparse response record
  -> deterministic normalization
     -> OPTION/CUSTOM -> immutable decision + optional supersede lineage
     -> AMBIGUOUS     -> immutable narrowed request or limit event
     -> CANCEL        -> cancellation lineage
  -> no harness callback, no dispatch, no resume
```

Responsibility boundaries:

| Component | Owns | Must not own |
| --- | --- | --- |
| `decision_gate.py` | unchanged OS-28/29 judgement and ledger key | OS-30 schemas/lineage |
| `run_logging.py` | existing run root, redaction, OS-29 writer | OS-30 normalization |
| `clarification_protocol.py` | schemas, IDs, store, normalization, lineage, CLI/port | OS-29 admission, transport, resume |
| harness adapters | translate authoritative block + Coordinator declarations | infer dependencies/answers, consume decisions |
| orchestration Skill | executable artifact/CLI rules | claim OS-31 or UI transport |
| loop Skill | matching OS-28/OS-30 human-authority semantics | claim installed artifact runtime |

## Error Handling / Compatibility

Closed error reasons include `SCHEMA_UNSUPPORTED`, `SCHEMA_VERSION_MIXED`, `SCHEMA_MALFORMED`, `SOURCE_NOT_OPEN`,
`SOURCE_BINDING_MISMATCH`, `DEPENDENCY_INVALID`, `BUNDLE_NOT_READY`, `ID_CONFLICT`,
`STALE_REQUEST`, `AMBIGUOUS_RESPONSE`, `AMBIGUITY_LIMIT_REACHED`, `LINEAGE_INVALID`,
`LINEAGE_FORK`, `ORPHAN_DECISION`, `ITEM_NOT_IN_REQUEST`, `STALE_ITEM`, `CANCEL_REQUEST_INVALID`,
`RAW_RESPONSE_UNSAFE`, `RAW_RESPONSE_TOO_LARGE`,
`REDACTION_UNAVAILABLE`, and `PERSISTENCE_UNSAFE`. Errors are typed and carry safe identifiers;
unexpected exceptions are wrapped at the CLI boundary without echoing exception operands that may
contain secrets.

Compatibility rules:

- Do not change `LEDGER_RECORD_SCHEMA_VERSION`, `CLOSED_LEDGER_RECORD_FIELDS`,
  `OS30_RESERVED_FIELDS`, OS-28 state transitions, workflow statuses, roles, rounds, B1/B2/B3, or
  iteration accounting. The existing invalid OS-29 supersession fixture stays invalid.
- A missing `clarifications/` directory is a valid legacy state. Readers return an empty OS-30 view
  without creating directories. They never infer CLEAR/approval from that absence.
- Published version 1 objects are immutable. Historical homogeneous v1 single-item lineages are
  interpreted exactly as specified in §6 without requiring a v2 raw-binding artifact; all new
  request/response writes are matched v2 and require exactly one raw binding. Disjoint v1 and v2
  lineages may coexist in one tree, but a lineage/item may not cross generations. Unknown or mixed
  versions fail closed; there is no in-place upgrade, migration, or historical backfill.
- Repository and installed module bytes are equal. After every later source edit, copy/sync the
  installed twin again; equality tests verify procedure rather than compensate for it.
- Direct-session Skill parity is semantic only: recommendation is not approval, timeout/no response
  is not approval, responses require explicit human evidence, ambiguity is bounded, and history is
  retained. Its lack of executable run artifacts is stated explicitly.

## Expected Changed Files / Implementation Steps

1. Add `scripts/clarification_protocol.py` with constants, dataclasses/protocol, closed validators,
   deterministic IDs, immutable store, normalization/lineage reader, and CLI.
2. Add byte-identical
   `orca-worker-reviewer-orchestration/tools/clarification_protocol.py`; re-sync after every change.
3. Update `scripts/orca_runtime_harness.py` and `scripts/e2e_harness.py` only at the port injection,
   authoritative blocking-settlement adapter, and request-publication seam.
4. Add `scripts/fixtures/clarification_protocol/valid/` and `invalid/`, plus
   `scripts/test_clarification_protocol.py`; extend `test_e2e_harness.py` and
   `test_orca_runtime_contract.py` for no-resume integration.
5. Extend `scripts/test_os29_decision_gate.py` with unchanged-schema/reserved-field locks and the
   installed-tool AST import invariant. Parse `ImportFrom.module` so the primary
   `from scripts.run_logging import ...` yields both `scripts` and `run_logging`; allow exactly that
   primary module and the fallback `from run_logging import ...`. A synthetic forbidden
   `from scripts.decision_gate import ...` positive control must fail, proving the walker is not
   accidentally satisfied only by the fallback branch.
6. Update both `SKILL.md` files. Add explicit shared OS-30 semantic anchors to
   `scripts/validate_skills.py`, with deletion/drift/false-feature-parity cases in
   `scripts/test_validate_skills.py` (REVIEW_PLAN N-001).
7. Update `scripts/release_manifest.py` to require both orchestration tools; extend
   `scripts/test_release_package.py` and package/archive verification for exact inclusion, mode,
   parity, and no unexpected loop tool. Run the installed module from a copied Skill with repository
   `scripts/` removed from `sys.path` (N-002).
8. Update `README.md`, `INSTALL.md`, `CHANGELOG.md`, `docs/ROADMAP.md`, and
   `docs/COMPATIBILITY.md` with schemas/layout, CLI, bounds, security, legacy behavior, two-Skill
   boundary, OS-31 exclusion, and MINOR-compatible unreleased capability.
9. Preserve all historical/untracked artifacts. Validate, inspect the exact diff, commit intended
   OS-30 files, push the named branch, and open the requested PR. Do not merge, publish a release,
   deploy, or mutate Jira.

Direct consistency consequences for later owning phases are named, not edited here: implementation
must update both `scripts/clarification_protocol.py` and its byte-identical installed twin for the
matched v2 schemas, generation-aware binding validation, graph-only head derivation, explicit
cancel-reset supersession, per-item replay, and request-level cancel. In particular it must remove
the timestamp-based `later` fallback and emit the specified linkage for cancel-then-redecide. Test must update
`scripts/test_clarification_protocol.py` plus relevant fixtures and harness tests for the matrix
below. Documentation must update `README.md`, `INSTALL.md`, `CHANGELOG.md`, `docs/ROADMAP.md`,
`docs/COMPATIBILITY.md`, `orca-worker-reviewer-orchestration/SKILL.md`, and
`orca-worker-reviewer-loop/SKILL.md`: every executable respond example must include
`--decision-item-id ITEM` for answer modes, while `--cancel` remains request-wide and omits it;
schema text must say new request/response v2 and immutable historical v1. ANALYSIS.md and PLAN.md
need no change: their retained 1..3 independent-bundle requirement is implemented, not reduced.

## Testing Strategy

### Schema and identity fixtures

- One valid fixture for each record/event kind and every allowed tagged-union arm; canonical
  round-trip retains exact schema/version and deterministic IDs.
- Invalid fixtures for every missing/extra field, wrong enum, wrong version, bool-as-int, malformed
  timestamp/ID/path, source/run mismatch, unsorted/duplicate membership, and oversized value.
- Repeated create/submission/event with identical content is idempotent; conflicting reuse fails;
  simultaneous writers publish one immutable object or distinct sequences without overwrite. A
  crash after response publication and before decision publication is replayed with the same
  submission and completes the identical deterministic decision; mismatched replay fails closed.
- Run IDs including `run_e2e_blocking_attribute`, `run_from_scenario`, `run_mdup`, and a valid
  externally supplied Orca-style segment pass; empty, `.`, `..`, `/` and platform-separator forms
  fail before path construction.

### Request, bundle, and dependency behavior

- Both `NEEDS_INPUT` and `CONFLICT` produce requests with `what_is_blocked`, actionable option(s),
  tradeoffs, explicit recommendation/rationale, literal false default, and literal timeout text.
- Bundle sizes 1 and 3 pass; 4 fails. Missing/symmetric independence, duplicate item, cycle,
  ancestor/descendant co-membership, unresolved/cancelled dependency, and out-of-order publication
  fail. Ready dependent items appear sequentially after effective predecessors.
- A Worker B2 block followed by an agreeing Reviewer B3 block with the same `(run, phase,
  open_item)` produces exactly one decision item and request, retains both source ledger keys, and
  cannot be answered as two independent questions. A disagreement for the same logical key fails
  closed.
- A bound Worker B2/Reviewer B3 verification pair publishes exactly one item and one request in
  each cross-label case: Worker named/Reviewer null, Worker null/Reviewer named, and two different
  non-empty labels. Its item ID and `RequestItem.open_item` always derive solely from the Worker B2
  canonical producer; changing only the Reviewer label never changes identity or publication
  cardinality.
- A single valid `NEEDS_INPUT` block with `open_item: null` publishes exactly one structured
  request (AC1). An agreeing bound B2/B3 verification pair with null labels also publishes exactly
  one stable item by following `verifies.worker_record_key`; two independent null-labelled
  producer records in one `(run, phase)` derive different item IDs and never coalesce.

### Normalization and lifecycle

- A valid 2- and 3-item request is answered item by item with `--decision-item-id`; partial answers
  advance only their named heads, all items can become effective independently, and a missing or
  foreign item designator produces the exact closed error without mutation.
- Same-item identical replay is idempotent; conflicting duplicate submission is
  `CLARIFICATION_ID_CONFLICT`; stale-revision and removed-item cases produce respectively
  `STALE_REQUEST` evidence/no authority and pre-publication `STALE_ITEM` rejection.
- Request-level `--cancel` succeeds for 1, 2, and 3 items without an item argument, cancels every
  item atomically, is replay-safe, and rejects a supplied item argument or any invalid member with
  the specified no-partial-commit error. For each size 1, 2, and 3, a request with zero prior
  answers appends exactly one §9 null-predecessor cancellation marker per item; every item derives
  `cancelled`, no reset anchor exists, and a subsequent first answer fails `LINEAGE_INVALID` with
  no effective head and no mutation.
- Historical v1 single-item request/response sets remain readable without a binding and without
  byte changes; v2 requires exactly one digest-matching binding. One tree containing disjoint valid
  v1 and v2 lineages reads both, while mixed-generation lineages/items, mixed response versions,
  v2-response/v1-request, v1-response/v2-request, bundled v1 responses, and unknown versions all
  fail closed with no effective authority.
- A hypothetical homogeneous v1 tree with two otherwise-valid decisions and no supersession event
  applies §9 identically to v2: it fails `ORPHAN_DECISION`, never uses `later`, and preserves every
  historical byte.
- Exact option ID, unique case-folded label, bounded custom text/integer/boolean/enum, ambiguous
  duplicate label, out-of-envelope custom, empty/non-executable answer, invalid UTF-8, and explicit
  cancel each reach only their specified outcome.
- Ambiguity at revisions 0 and 1 creates revisions 1 and 2; revision 2 appends limit reached and
  remains unresolved. No recommendation/default/timeout supplies authority.
- Changed valid response supersedes with a single head; ambiguous change leaves the old head;
  cancellation requires explicit response and removes authority; cancel-then-redecide requires a
  supersession edge from the most recent cancelled reset anchor; scope expansion keeps old scope,
  creates fresh child IDs/edges, and never widens old approval.
- Append a second otherwise-valid response/binding/decision without a lineage event and assert
  `ORPHAN_DECISION`, no effective-head change, no mutation, and no timestamp/sequence fallback.
  Delete the required supersession/cancel transition and assert the same fail-closed result.
- Cross-run/item, missing/self target, competing successors, multiple predecessors, bypassed reset
  anchor, cycle, multiple head, malformed/out-of-order event, stale revision, duplicate conflict,
  traversal, and partial staging all fail closed; fork cases assert `LINEAGE_FORK` exactly.
- Currency tests cover a fully published successor, a lineage event with a missing successor
  request, and a changed answer after an effective decision; only the highest complete revision is
  current, and the effective decision does not itself stale that request. Mode tests prove
  `--cancel` is accepted iff `cancel` is declared.

### Security and portability

- Submit a unique secret through `--response-file`; exact bytes occur exactly once in the published
  raw file, whose mode is `0600`, and nowhere in stdout/stderr, response `record.json`, any other
  JSON, exception text, OS-29 ledger,
  orchestration/timing logs, task specs, lineage summaries, exports, or package archives.
- Permission/fsync/redaction/digest failures publish no decision. Oversize/symlink/non-regular files
  fail before artifact publication.
- AST test with positive control enforces the installed dependency rule; isolated copied-Skill CLI
  execution proves runtime self-containment. Source/installed bytes remain identical.
- Publication-helper parity tests exercise the shared atomicity/fsync/collision/no-overwrite
  matrix, while OS-30-specific cases prove binary preservation and restrictive modes.
- Static tests reject `input(`, stdin/TTY access, Orca imports/commands, and transport/resume APIs.

### Harness and regression gates

- Fake port captures request creation at valid B2/B3 blocks. Missing/invalid request declarations
  never create vague requests and never un-block the run.
- Snapshot before/after post-settlement CLI ingestion proves zero delta across run status, OS-29
  head, dispatch/task/command/subprocess counts, phase/correction counters, and vocabulary constants.
- Existing OS-28 policy tests, OS-29 gate fixtures (including
  `record_carries_os30_supersession.json`), B1/B2/B3 tests, dispatch-cardinality tests, and all
  historical run readers remain unchanged/green.
- `validate_skills.py` enforces shared semantics while rejecting false executable parity for the
  loop Skill; package tests require the second installed tool and no repository dependency.

Repository gate commands:

```text
python3 -m unittest scripts.test_clarification_protocol scripts.test_e2e_harness \
  scripts.test_orca_runtime_contract scripts.test_os29_decision_gate \
  scripts.test_validate_skills scripts.test_release_package
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 scripts/validate_skills.py
python3 scripts/verify_package.py
# build and verify the deterministic archive exactly as docs/RELEASING.md specifies
git diff --check
```

The full suite must remain Python 3.11-3.13 compatible and standard-library-only.

## Risks / Open Issues

- **Secret duplication:** controlled by response-directory atomicity, one raw copy, no previews,
  sparse safe metadata, mode verification, canary scans, and fail-closed ordering.
- **Lineage races:** controlled by immutable IDs, directory atomicity, bounded sequence collision
  retry, full graph/replay validation, and rejection of orphan decisions and forks rather than
  timestamp/sequence/last-write selection.
- **Trust boundary:** unkeyed content addressing detects partial mutation and unlinked appends but
  cannot authenticate a fully fabricated/recomputed tree; §3 bounds the structural-integrity claim.
- **OS-29 boundary erosion:** controlled by a separate namespace and one-way existing ledger key;
  existing closed-field tests remain authoritative.
- **Accidental OS-31 behavior:** controlled by the port's lack of lifecycle callbacks and measured
  zero-delta harness tests.
- **Two-copy drift/install failure:** controlled by continuous re-sync, byte equality, AST
  positive-control import validation, and isolated installed execution.
- **No open design issue remains.** Numeric bounds, the matched request/response v2 generation, and
  validated-lineage-only head derivation are settled; the design does not invent a cryptographic
  authenticity guarantee unavailable from the approved primitives. The bounds are reversible repository-local
  decisions already settled by the approved PLAN; no user authority is required.

## Resolution Trace — Design Iteration 5

| Finding / note | Resolution in this design | Verification anchor |
| --- | --- | --- |
| F-001 | Removed `redacted_preview`; response JSON and every ordinary channel permit only safe metadata/digests, and authority fails closed if restricted raw publication cannot be verified. | §6, §13; canary scan explicitly includes response `record.json`. |
| F-002 | For named questions, `decision_item_id` derives from `(run_id, phase, open_item)`; first and later agreeing B2/B3 ledger keys bind to one item/request. | §3, §12; Worker+Reviewer agreement fixture asserts exactly one request. |
| F-003 | Ledger-key parsing validates the run component with `run_logging`'s actual non-empty, non-dot, separator-free contract and accepts existing/Orca IDs. | §3; named valid-ID and traversal fixtures. |
| N-001 | Normalization/decision ID are computed before response publication; identical replay completes a decision missing after a crash. | §13; crash-window replay test. |
| N-002 | Currency is the highest completely published reachable revision; an effective decision does not stale its request. | §3; partial-successor and changed-answer tests. |
| N-003 | `cancel` is an explicit accepted response mode and gates `--cancel`. | §4, §11; accepted/omitted mode tests. |
| N-004 | Uses the literal submodule/fallback import shape and defines the AST walk, with a forbidden-import positive control. | §1; implementation step 5 and isolated installed execution. |
| N-005 | Keeps the binary/mode-capable helper separate and adds behavioral parity coverage for shared publication guarantees. | §2; security/portability parity matrix. |
| N-006 | Names one final-BLOCKED publication seam in each harness; no settlement-site call remains. | §12; fake-port cardinality and zero-delta tests. |
| F-004 | Made source `open_item` nullable and replaced label-only identity with a tagged fallback to the canonical producer ledger key. A validated B3 verification follows `verifies.worker_record_key`, preserving B2/B3 request identity; independent null-labelled producers retain distinct keys. | §3, §4, §12; null single-block AC1, null bound B2/B3, and independent-null non-coalescence fixtures. |
| F-005 | Made the validated B3-to-B2 verification fold unconditional and prior to tag selection. Both named and producer tags, plus published `RequestItem.open_item`, now derive solely from the canonical Worker B2 producer; a Reviewer's label never affects identity. | §3, §4, §12; cross-label fixture covers both null/named directions and differing non-empty labels, each with exactly one request. |
| FA-002 | Retained bounded bundles and made `decision_item_id` the stable per-item response designator; introduced matched request/response v2 without rewriting v1; specified per-item heads, partial/duplicate/missing/stale behavior, genuine dependency antichains, and item-count-independent whole-request cancel. | §4-§11; multi-item answer/cancel and version-matrix tests in Testing Strategy. |

## Resolution Trace — Design Iteration 6

| Finding / note | Resolution in this design | Verification anchor |
| --- | --- | --- |
| N-801 | Adopted `response_bindings/` into the exact layout and specified its closed schema, identity, and v2-only authority rule. | §2, §3, §6. |
| N-802 | Replaced timestamp/`later` head selection with a validated per-item lineage graph; specified roots, supersession edges, cancel reset anchors, orphan/fork rejection, and the bounded structural-integrity threat model. | §3, §9; append-only orphan, fork, and cancel-redecision tests. |
| R8-002 | Split historical-v1 read admission from v2 write/read admission: v1 verifies its raw digest without a binding and is never rewritten; v2 requires exactly one binding; disjoint generations may coexist but may not mix within a lineage/item. | §6, compatibility rules, historical-v1 byte-identity test. |

## Review Feedback Resolution

- **D6-001 (MAJOR, design): RESOLVED.** An unanswered item receives exactly one validated
  `decision_cancelled` event with `prior_decision_id=null`; this nullable shape is exclusive to
  empty `D`, derives `cancelled`, and neither creates nor consumes a reset anchor. Such an item
  admits no later first-decision root: an attempted answer fails closed as `LINEAGE_INVALID`.
  Section 11 remains request-wide and atomic, `show.item_statuses` exposes the derived distinction,
  and the 1/2/3-item zero-answer cancellation tests pin event count, status, and post-cancel outcome.
- **D6-N01 (LOW, design): RESOLVED.** Removed unreachable `MULTIPLE_EFFECTIVE_HEADS` from the closed
  vocabulary and stated that every multi-head construction/replay condition is subsumed by
  `LINEAGE_FORK`.
- **D6-N02 through D6-N07 (non-blocking specification tightening): RESOLVED.** Added the binding
  version constant; defined run reconciliation through `source_ledger_key`; replaced ordering
  language with `non-root`; exposed derived per-item status through `show.item_statuses`; made §9
  generation-uniform and added its v1 test; and narrowed the structural-integrity claim to
  non-first decisions. These edits tighten only already-settled behavior.

- **FA-002 (CRITICAL, design): RESOLVED.** The accepted user decision is implemented exactly:
  bundles remain bounded at three; v2 responses and answer-mode CLI calls name the stable
  `decision_item_id`; response processing, normalization, and lineage are per item; `--cancel`
  cancels the whole request without depending on item count; v1 history is never rewritten; mixed
  and unknown versions fail closed; and partial, duplicate, missing, and stale cases have explicit
  testable contracts. No production file was edited in this design phase.
- **N-802 (HIGH, design): RESOLVED.** The effective decision head is reachable only through the
  unique validated lineage graph. Every non-first decision requires a valid
  `decision_superseded(prior_decision_id, next_decision_id)` edge; cancel-then-redecide uses the most
  recent validated `decision_cancelled` predecessor as a reset anchor and still requires that edge.
  Orphans fail `ORPHAN_DECISION`, forks fail `LINEAGE_FORK`, and no timestamp, sequence, or `later`
  fallback can create authority. Section 3 states the obtainable structural-integrity guarantee and
  explicitly disclaims authenticity against an adversary able to fabricate the complete tree.
- **R8-002 (MAJOR, implementation regression routed through design): RESOLVED IN DESIGN.** A
  historical homogeneous v1 single-item lineage needs no `response_bindings/` artifact and remains
  byte-identical; every v2 response requires exactly one valid binding. Disjoint v1/v2 lineages may
  coexist, but generation mixing within a lineage/item fails closed. The production and test changes
  named in Expected Changed Files remain implementation/test-owned; no file outside `DESIGN.md` was
  edited here.

## Decision Record

DECISION_STATE: CLEAR
REASON_CODE: none
EVIDENCE: The approved analysis/plan, prior design findings, FINAL_REVIEW FA-002,
REVIEW_IMPLEMENTATION iteration-8 N-802/R8-002, the user's explicit validated-lineage contract and
choice to retain bundles with a stable per-item designator, and existing OS-28/29 contracts determine
an implementable design. Iteration 7 resolves D6-001 and the safe D6-N01..D6-N07 precision findings
while retaining all prior corrections without changing scope;
no user-owned choice remains open.

DECISION_GATE_STATE: CLEAR

```decision-gate
{
  "assumption": null,
  "boundary": "B2",
  "evidence": {},
  "grounds": "The approved analysis and plan, Jira OS-30 requirements, existing OS-28/OS-29 contracts, FINAL_REVIEW FA-002, REVIEW_IMPLEMENTATION iteration-8 N-802/R8-002, and D6-001 determine that request-wide cancellation emits a null-predecessor marker for each unanswered item, derives cancelled, and rejects a subsequent first decision; no user-owned decision remains open.",
  "iteration": 7,
  "ledger_schema_version": 1,
  "open_decision_item": false,
  "open_item": null,
  "phase": "design",
  "prior_open_decision_items": [],
  "reason_code": null,
  "recorded_at": "2026-09-01T23:50:00+09:00",
  "responsible_phase": "design",
  "role": "worker",
  "run": "run_db374a3fd83a",
  "scope": "Corrected design iteration 7 for Jira OS-30 only, resolving D6-001 through a null-predecessor cancellation marker for unanswered items, derived cancelled status, and fail-closed post-cancellation first-answer admission, plus non-behavioral D6-N01..D6-N07 tightening, while preserving every accepted iteration-6 contract and excluding OS-31 resume and transport-specific UI.",
  "sequence": 26,
  "source": "worker",
  "source_binding": "artifacts/runs/run_db374a3fd83a/DESIGN.md",
  "state": "CLEAR",
  "verdict": "",
  "verifies": null
}
```
