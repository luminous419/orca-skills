# Worker Result

STATUS: COMPLETE

## Summary / Requirements

This is the implementable design for the three findings raised by the external GitHub review on
Draft PR #20: **MAJOR-1** (the seeded answer key is not isolated from the Final Reviewer as an
*execution* property), **MAJOR-2** (`FINAL_REVIEW_EVIDENCE_BUNDLE.json` re-introduces unredacted
text by inlining `ORCHESTRATOR_LOG.md` raw), and **MINOR-1** (`COMPATIBILITY.md` still describes
the superseded `redaction/1.0` and states a packaging claim that must be reconciled with whatever
isolation MAJOR-1 produces).

It **extends** `artifacts/runs/run_804e35d29531/DESIGN.md` and contradicts nothing in it. D-A
(audit record), D-B (provenance), D-C (`redaction/1.1` + P-PATH), D-D (fixture), D-E (scorer) and
N-1 (neutrality) are treated as settled and are not reopened. Two of that document's subsections
are *superseded in a stated, narrow way* and the supersession is spelled out here rather than left
to be inferred:

* **D.5 rule set** gains a sixth rule (isolation-session materialization) — additive.
* **D-F (export bundle schema)** goes from `1.0` to `2.0`; the `orchestrator_log` object's shape
  changes. Stated in D-H.5.
* **Baseline procedure B-1 … B-5** is replaced by **B-1′ … B-7** in `## Testing Strategy`; the
  five acceptance criteria B1-B5 keep their meaning and gain a sixth, **B6 scope enforcement**.

### What the IMPLEMENTATION phase is being handed

| id | deliverable | finding it discharges |
|---|---|---|
| **D-G** | Reviewer execution isolation: the isolation session layout, the computed readable set, the generated kernel-enforced scope profile, the launch line, the `ISOLATION.json` attestation, report repatriation, and the negative-test contract with a positive control | MAJOR-1 |
| **D-H** | Evidence-bundle sanitization: `safe_embedded_text()`, the new `orchestrator_log` object, the residue fail-closed rule, embedded-artifact residue policy, export schema `2.0` | MAJOR-2 |
| **D-I** | `COMPATIBILITY.md` exact replacement wording for the redaction paragraph and the packaging paragraph | MINOR-1 |

### Requirements this design must satisfy, and where each is satisfied

| requirement (quoted from the external review) | designed in |
|---|---|
| "make answer-key isolation an execution property, not only a prompt/materialization property" | D-G.1, D-G.3, D-G.4 |
| "run the Reviewer in a workspace/environment from which the key/adjudication tree is not readable at all … or otherwise enforce and verify filesystem scope" | D-G.2 (workspace), D-G.4 (enforcement) |
| "Add a negative test proving the Reviewer-visible/executable filesystem cannot read or discover the key" | D-G.9 (**NEG-1 … NEG-6**, incl. **NEG-0** positive control) |
| "Do not claim any baseline uncontaminated until this is fixed and re-run" | D-G.7 fail-closed rule, B6 in `## Testing Strategy`, and the mandatory re-run in Step 6 |
| "redact/sanitize the ORCHESTRATOR_LOG content before embedding it (carrying policy version/digest/redaction metadata alongside it)" | D-H.2, D-H.3 |
| "Preserve the authoritative raw local log's own lifecycle/immutability untouched" | D-H.1 rule 1 |
| "The export must make the sanitized-vs-original-log identity/digest relationship auditable" | D-H.3 (`digest_pre_redaction` / `digest_post_redaction` / determinism statement) |
| "Add tests with synthetic secret/path-bearing log cells … proving the exported bundle cannot leak them" | `## Testing Strategy` **T-7** |
| MINOR-1 doc wording, and the packaging paragraph reconciled | D-I |
| "if the isolation mechanism requires changing the fixture layout or export procedure, specify exactly what changes and why it is still reproducible/byte-for-byte re-scoreable" | D-G.8, and `## Error Handling / Compatibility` → **Reproducibility** |

### What must not regress (restated from the review, with where each is held)

| invariant | held by |
|---|---|
| accepted-vs-voided provenance is fail-closed | D-B, untouched. D-G writes no provenance field and D-H reads them without changing them. |
| per-dispatch publication is immutable/atomic | D-A.3, untouched. D-H never writes into `final_review_audit/`; D-G writes only into the isolation session and, at B-5, *adds* files to the run artifact root. |
| retry identity is separate | D-A.2, untouched. An isolation-session failure is a dispatch-layer failure and takes the existing B-3R retry path under a new Task/Dispatch identity (B-4R below). |
| the scorer refuses to classify unmatched findings as false positives without adjudication/closed-world evidence | D-E, untouched. No new subcommand touches `score`. |
| H-1/H-2/H-4/H-5 out of scope | Nothing in this design mentions detection or search policy. See `## Risks / Open Issues` → **Explicitly not designed**. |
| the follow-up run preserved the predecessor ESCALATED run | Unrelated to these three findings; untouched. |

### Non-negotiable ordering rules this design anchors

1. **D-H before D-G's re-run.** The §7 baseline must be re-captured under isolation (MAJOR-1), and
   the capture produces an evidence bundle. Exporting a bundle whose log is still inlined raw would
   produce, as the *first artifact of the fix*, exactly the artifact MAJOR-2 objects to. So
   `run_logging.py` lands first, then the isolation tooling, then the re-run.
2. **The negative test lands with the mechanism, in the same commit.** A scope profile without
   NEG-1 … NEG-6 is an assertion, not a guarantee; splitting them lets an unverified profile exist
   in history as if it were verified.
3. **NEG-0 (positive control) runs before every NEG-1 … NEG-4 assertion in the same test method.**
   A negative test that passes because the probe used the wrong path proves nothing. The control
   is not a separate test that could be skipped independently.
4. **The old §7 baseline artifacts are not deleted and not edited.** They are re-labelled by a new
   `scope_enforcement` field on the *new* capture, and the old capture's own record keeps saying
   what it said. Rewriting a settled record is the thing D-A.3 exists to prevent.

---

## Current Architecture

Every path and line reference below was read in this working copy on branch
`agent/final-review-observability-evaluation`.

### MAJOR-1 — what exists today, and precisely where the gap is

* `materialize()` (`scripts/final_review_eval.py:371`) builds the reviewable workspace out of
  `subject/head` + a generated `DIFF.patch` + `MANIFEST.json`, in a staging directory, and only
  moves it to `--dest` after three post-copy assertions: no path component named `key`,
  `adjudications` or `.git`; `scan_leak()` over the whole staging tree returns zero hits; and the
  computed `fixture_digest` equals the key's. `key/` and `adjudications/` are opened for exactly
  one field (`key_fixture_digest()`, `scripts/final_review_eval.py:259`).
* `scan_leak()` (`scripts/final_review_eval.py:327`) has no exclusion parameter, by design.
* **The gap.** All of the above is a property of the *workspace tree*. The Final Review dispatch
  itself creates its terminal with `orca terminal create --worktree current`
  (`scripts/orca_runtime_harness.py:1689-1699`, and `:1510` for the run-owner terminal), i.e. in
  the main repository checkout. Nothing constrains the reviewer process's filesystem scope, so
  `scripts/fixtures/final_review_eval/key/answer_key.json` — a tracked file, additionally present
  as a blob in `.git/objects` and inside `dist/orca-skills-0.9.0.tar.gz` — is readable by absolute
  path, by `git show`, and by `grep -r`. `SKILL.md`'s `REVIEWER_DRILL_DOWN =
  mandatory_and_unrestricted` makes "the prompt does not name it" a non-argument.
  `run_804e35d29531/DESIGN.md` D.6's closing paragraph states this limitation honestly; MAJOR-1's
  position is that stating it is no longer sufficient.

### MAJOR-2 — what exists today

* `export_final_review_evidence()` (`scripts/run_logging.py:2389`) inlines each attempt's
  `record.json`, `input.md` and `report.md` through `_embedded_artifact()`
  (`scripts/run_logging.py:2366`), all three of which are written post-redaction and are digest
  bound.
* At `scripts/run_logging.py:2484-2503` it then does:

  ```python
  log_path = root / ORCHESTRATOR_LOG_FILENAME
  ...
  log_content = log_path.read_text(encoding="utf-8")
  log_digest  = sha256_text(log_content)
  ...
  "orchestrator_log": {"path": ..., "digest": log_digest, "content": log_content},
  ```

  `assert_retained_path_field()` is applied to `["path"]` only. **`["content"]` never passes
  through `redact_text()`.** The log's `detail` and `result` columns are free-form strings written
  by `log_orchestrator_event()` (`scripts/run_logging.py:346`) from runtime/CLI values, so a
  foreign absolute path, a `file://` URL, a `dcap_…` capability or a `*_TOKEN=…` assignment can
  reach them and be copied verbatim into the bundle.
* Only one existing test reads that key: `scripts/test_run_logging.py:3181`.
* `redact_text()` (`scripts/run_logging.py:1123`) is a pure, deterministic function of
  `(text, policy_version)` over the five ordered categories at `scripts/run_logging.py:1078`.

### MINOR-1 — what exists today

`COMPATIBILITY.md:120-122` still says "**Redaction policy v1.0 covers POSIX paths only.**"
`COMPATIBILITY.md:124-127` states the packaging fact and then draws the claim
"not 'the key was unreachable.'", which this design changes for the baseline-capture environment
specifically and must therefore be re-worded rather than left standing.

### Host facts, verified live on this machine before designing against them

These were executed, not assumed. Darwin 25.5.0.

| fact | how verified | result |
|---|---|---|
| `sandbox-exec(1)` exists and is usable without privilege | `which sandbox-exec` → `/usr/bin/sandbox-exec` | present |
| a `(deny file-read*)` + narrow-allowlist profile lets an ordinary binary run | `sandbox-exec -f p.sb /bin/cat <allowed file>` | prints the file, exit 0 |
| the same profile blocks *content* reads outside the allowlist | `sandbox-exec -f p.sb /bin/cat <repo>/VERSION` | `Operation not permitted`, exit 1 |
| it blocks `git` reaching into the repository | `sandbox-exec -f p.sb git -C <repo> show HEAD:VERSION` | `fatal: Unable to read …`, exit 128 |
| a *global* `(allow file-read-metadata)` is required or even writing to stdout fails | profile without it → `cat: stdout: Operation not permitted` | required |
| an explicit later `(deny file-read-metadata (subpath <root>))` still hides existence | `os.path.exists(key)` inside the sandbox | `False`; `stat`/`listdir` → `Operation not permitted` |
| `(literal "/")` must be allowed for reads or dyld aborts the process | profile without it → `Abort trap: 6` | required |
| ordering is last-match-wins | the metadata deny only takes effect when placed *after* the global metadata allow | confirmed |
| `orca terminal create` accepts an arbitrary startup command line | `orca terminal create --help` → `--command <text>` | usable to wrap the agent |
| no stray copy of the key exists under `$HOME` today | `find "$HOME" -maxdepth 8 -name answer_key.json` | zero hits (the in-repo copy is under the working tree, and `dist/*.tar.gz` holds one as an archive member) |

### Existing conventions this design must not fork

* Standard library only, CPython ≥ 3.11 (`scripts/final_review_eval.py` module docstring).
* `final_review_eval.py` may import `run_logging`; `run_logging.py` may import **nothing** from
  `scripts/` (`run_logging.py` module docstring, OS-17 round 3 MAJOR-1). D-G therefore lives on the
  `final_review_eval.py` side of that line, and D-H entirely inside `run_logging.py`.
* Subcommand-per-responsibility with distinct exit codes (`EXIT_OK`, `EXIT_INPUT_ERROR`,
  `EXIT_CONTRACT_VIOLATION`, `EXIT_LEAK_OR_FIXTURE`, `EXIT_PRECISION_REFUSED`).
* Every schema-bearing document carries an explicit `schema_version` and is read through
  `require_major()`.
* No clock value inside a document whose byte-identity is asserted; clocks go to a sidecar.

---

## Proposed Design

### The shape in one picture

```text
      ┌─────────────────────────── main repository checkout (the Coordinator's world) ─────────┐
      │ scripts/fixtures/final_review_eval/{subject,key,adjudications}                          │
      │ artifacts/runs/<run>/{ORCHESTRATOR_LOG.md, final_review_audit/, FINAL_REVIEW.md}        │
      └────────────────────────────────────────────────────────────────────────────────────────┘
                    │  (1) isolate: build session, compute readable set, generate profile
                    ▼
   ISOLATION SESSION  <SESSION>/            (mkdtemp, no path relationship to the repo)
      ├── review_root/                      ← the ONLY tree the Reviewer can read or write
      │     ├── subject/                    materialize() output: CONTRACT.md src/ tests/
      │     │                               DIFF.patch MANIFEST.json
      │     ├── policy/                     closed copy list of review-policy files
      │     └── artifacts/runs/<run>/       the Reviewer's write target (FINAL_REVIEW.md)
      └── control/                          ← DENIED to the Reviewer; never inside review_root
            ├── scope.sb                    generated seatbelt profile (names the denied roots)
            ├── ISOLATION.json              attestation: readable set, digests, probe results
            └── probes/                     NEG-0 … NEG-6 evidence
                    │  (2) dispatch: terminal cwd = review_root, agent exec'd under scope.sb
                    ▼
              Final Reviewer  ── writes ──▶ <SESSION>/review_root/artifacts/runs/<run>/FINAL_REVIEW.md
                    │  (3) repatriate: Coordinator copies the report back into the repo artifact root
                    ▼
      main checkout: existing capture → final_review_audit/<key>/{input.md,report.md,record.json}
                    │  (4) score (unchanged), (5) export (now sanitized by D-H)
                    ▼
              metrics.json (byte-identical on re-score)   FINAL_REVIEW_EVIDENCE_BUNDLE.json
```

### Three principles the whole design is derived from

1. **Isolation is a property of the executing process, not of a tree.** The load-bearing claim is
   "the Reviewer process could not read the key", and the only thing that can make that true on a
   host with no container is a kernel-enforced scope on that process. Everything else in D-G —
   the session layout, the copy list, the repatriation step — exists to make the enforced scope
   *small enough to prove things about*.
2. **Allowlist the readable set; never denylist the secret.** A profile that denies the repository
   is defeated by any copy of the key outside the repository (an installed skill, an unpacked
   tarball, a second worktree). So the profile denies *all* reads and then names the readable set,
   and that named set is what the negative test scans exhaustively. This is the same reasoning
   D3-001 applied to P-PATH: "the policy left it unchanged" must not be an accepted answer.
3. **When the mechanism is unavailable, the label is withheld — not the run.** Nothing here
   prevents an ordinary Final Review dispatch on a host without `sandbox-exec`. What is prevented
   is *calling such a capture a §7 baseline*. Fail-closed on the claim, not on the workflow.

---

## Components / Interfaces / Data Flow

### D-G — Reviewer execution isolation (MAJOR-1)

New module **`scripts/review_isolation.py`** (standard library only), plus three new
`final_review_eval.py` subcommands that are thin CLI wrappers over it. It is a separate module
because it is the only code in this repository that generates an OS security policy, and because
`final_review_eval.py` must stay readable as "materialize / verify / scan / parse / score".

#### G.1 Threat model, and the three properties that together define "isolated"

The adversary modelled is **an unconstrained, well-behaved reviewer agent with normal repository
and diff inspection authority** — one that reads absolute paths, runs `git`, and greps broadly
because that is what `REVIEWER_DRILL_DOWN = mandatory_and_unrestricted` tells it to do. Not
modelled: a reviewer that deliberately breaks out of a kernel sandbox, a compromised host, or a
malicious operator. That boundary is stated in `ISOLATION.json` and in D-I's wording; a guarantee
whose threat model is unstated is not a guarantee.

| id | property | enforced by | verified by |
|---|---|---|---|
| **S1 — scope** | The Reviewer's cwd and every path reachable by relative traversal from it contain no key material and no path relationship to `key/` or `adjudications/`. | G.2 session layout + `materialize()`'s existing assertions | NEG-1 |
| **S2 — unreadability** | No absolute path outside the computed readable set is readable *or stat-able* by the Reviewer process, including via `git`, and the key's own paths are in neither. | G.4 seatbelt profile | NEG-2, NEG-3, NEG-4 |
| **S3 — cleanliness of the readable set** | Every path the Reviewer *can* read has been exhaustively scanned and contains zero key material. | G.3 readable-set computation + scan | NEG-5, NEG-6 |

S1 alone is what the previous design had. S2 without S3 is defeated by a stray copy. S3 without S2
is unbounded. The three are required together and `ISOLATION.json` records all three verdicts
separately, so a partial result can never be read as a whole one.

#### G.2 The isolation session

```bash
python3 scripts/final_review_eval.py isolate \
    --run-id <orca run id> \
    [--fixture scripts/fixtures/final_review_eval] \
    [--session-base <dir>]          # default: tempfile.gettempdir()
    [--policy-file <repo-relative path>]...   # repeatable; default list below
    [--allow-read <abs path>]...    # repeatable; extra Class USR roots the agent needs
    [--enforcement seatbelt|none]   # default: seatbelt
    [--out <path>]                  # where to print the session descriptor JSON
```

Creates, via `tempfile.mkdtemp(prefix="frv_iso_")`, and prints a descriptor:

```text
<SESSION>/
    review_root/
        subject/                    <- materialize(dest=<SESSION>/review_root/subject, fixture=…)
        policy/                     <- the closed copy list, flattened one level
            REVIEW_COMMON.md        <- orca-worker-reviewer-orchestration/reviews/common.md
            REVIEW_DESIGN.md        <- orca-worker-reviewer-orchestration/reviews/design.md
            …one per --policy-file
        artifacts/runs/<run-id>/    <- created empty; the Reviewer's only write target
    control/
        scope.sb
        ISOLATION.json
        probes/
```

Rules, all enforced by the command before it exits 0:

1. **`<SESSION>` has no path relationship to the fixture or the repository.** Checked, not assumed:
   `Path(session).resolve()` must be neither an ancestor nor a descendant of
   `Path(fixture).resolve()`, of `REPO_ROOT.resolve()`, or of any root discovered by G.3. If
   `TMPDIR` were ever configured inside the repository, this check fails the command (exit 2)
   rather than producing a session that silently satisfies nothing.
2. **`review_root/` is built only by copying.** No symlink is created and no symlink is followed:
   every copy uses `shutil.copy2(..., follow_symlinks=False)` and a source that `is_symlink()` is a
   hard error (exit 4). A symlink into the repository would defeat S1 while passing a naive walk.
3. **`policy/` is a closed, explicit list**, defaulted to `reviews/common.md` plus the one
   `reviews/<phase>.md` the dispatch's phase needs, and overridable only by repeated
   `--policy-file`. There is no glob and no "copy the skill directory": the skill directory
   contains `tools/run_logging.py` and, in a packaged install, could contain the fixture.
4. **Every file that lands in `review_root/` is leak-scanned** with the existing
   `scan_leak(key, [review_root])` — the same function, no exclusions, so the policy files are
   scanned exactly as the subject tree is. Non-zero hits → exit 4, session removed.
5. **`control/` is a sibling of `review_root/`, never a child.** It holds the generated profile,
   which necessarily *names* the denied roots — i.e. it contains the repository path and hence the
   key's directory path. Putting it inside `review_root/` would hand the Reviewer the exact path
   NEG-1 exists to prove absent.
6. **The session is ephemeral and its removal is the Coordinator's explicit step** (`isolate
   --teardown <SESSION>`, G.7). `isolate` never removes a session it did not create in this
   invocation.

`materialize()` itself is unchanged. This is the sixth rule added to
`run_804e35d29531/DESIGN.md` D.5: *when a materialized workspace is built for a dispatch that
claims scope enforcement, it is built inside an isolation session under these six rules.* The
fixture layout on disk does not change at all.

#### G.3 The readable set — computation, classification, and the discovery scan

`compute_readable_set()` returns an ordered, deduplicated list of **realpaths** (symlinks resolved
before anything else — `/tmp` → `/private/tmp` on darwin; an unresolved spelling in a seatbelt
profile does not match).

**Class SYS** — the closed default list, all read-only to the run user:

```text
/          (literal only, not subpath — dyld requires it; see the host-facts table)
/usr  /bin  /sbin  /System  /Library  /private/etc  /private/var  /dev
```

Each Class SYS root is admitted only if `os.access(root, os.W_OK)` is **False**. A root that is
user-writable is *moved to Class USR* and scanned. (`/opt/homebrew` is user-writable on a typical
darwin install and is therefore Class USR by this rule, not by a special case.)

**Class USR** — everything else that must be readable for the agent to function:

* `<SESSION>/review_root` (always);
* the agent's own state/config roots, supplied by `--allow-read` and defaulted to the values the
  pre-flight probe of G.5 discovers to be necessary — the design does **not** hard-code a
  particular agent's dotfile layout, because routing can select a different agent per phase
  (`agent_profile.py`);
* `$TMPDIR` is **not** added wholesale. Only `<SESSION>/review_root`.

**Every Class USR root is scanned exhaustively** by `scan_readable_set()`:

| pass | what it does | a hit means |
|---|---|---|
| **A — name** | walk the root; any file whose basename is `answer_key.json`, or whose path contains a component named `key` or `adjudications` **under a directory that also contains a `subject/`** | a fixture tree is reachable |
| **B — content** | `scan_leak(key, [root])` — the existing D.6 token/shingle/expected-count scan, no exclusions | key material is reachable |
| **C — key digest** | any file whose sha256 equals the answer key file's sha256 | a renamed copy is reachable |
| **D — archive members** | for every `*.tar`, `*.tar.gz`, `*.tgz`, `*.zip` under the root, list members with `tarfile`/`zipfile` (**names only — members are never extracted and never read**) and match member names by pass A's rule | a packaged copy is reachable (this is what catches `dist/orca-skills-*.tar.gz`) |

A hit in any pass is a **hard failure** (exit 4) with the offending path printed. There is
deliberately no `--ignore` flag: the operator's remedy is to remove the copy, or to stop allowing
that root and re-run the pre-flight probe. Pass D reads only the archive's index; if an archive is
unreadable or malformed it counts as a hit, because an archive whose contents cannot be enumerated
cannot be certified clean.

Class SYS roots are **not** content-scanned (they are gigabytes and not user-writable). Instead
`ISOLATION.json` records, for each, the `W_OK=False` evidence and the assertion that it is neither
an ancestor nor a descendant of any key-bearing path found by G.3. That asymmetry is a stated
limitation, recorded in the attestation and in D-I, not a silent one.

Bounding: pass A/B/C walks skip nothing and follow no symlinks (`os.walk(..., followlinks=False)`),
and every symlink encountered whose realpath escapes the root is itself a hit — an escaping symlink
inside an allowed root is an allowed read path that the walk did not cover.

#### G.4 The scope profile — exact generated text, and the ordering rule

Backend `seatbelt` (darwin). `render_seatbelt_profile(readable, writable, denied)` emits exactly
this, in exactly this order; **seatbelt is last-match-wins, so the order is the semantics**:

```scheme
(version 1)
;; Generated by scripts/review_isolation.py -- do not edit. Session: <SESSION>
(allow default)

;; 1. Deny every file read, then name the readable set. Allowlist, not denylist:
;;    a profile that denied only the repository is defeated by any copy of the key
;;    outside it.
(deny file-read*)
;; 2. Metadata must be globally allowed or the process cannot even fstat its own
;;    stdout (verified: "cat: stdout: Operation not permitted"). Narrowed again in 5.
(allow file-read-metadata)
;; 3. The readable set. "(literal \"/\")" is required or dyld aborts at exec.
(allow file-read*
    (literal "/")
    (subpath "/usr")
    …one (subpath "<realpath>") per Class SYS and Class USR root…
    (subpath "<SESSION>/review_root"))
;; 4. Writes: deny everything, then name the writable set.
(deny file-write*)
(allow file-write*
    (subpath "<SESSION>/review_root")
    (subpath "/dev")
    …one per --allow-write root the pre-flight probe proved necessary…)
;; 5. The key-bearing roots, denied for BOTH data and metadata, after the global
;;    metadata allow so that existence itself is hidden. Redundant with (1) for
;;    reads; kept because a future edit that widens (3) must not silently widen
;;    these, and because it makes the profile self-documenting about what it is
;;    protecting.
(deny file-read-metadata
    (subpath "<repo realpath>")
    …one per key-bearing root discovered in G.3…)
(deny file-read* file-write*
    (subpath "<repo realpath>")
    …one per key-bearing root discovered in G.3…)
```

Verified live: with clause 5 present, `os.path.exists(<key path>)` inside the sandbox returns
`False`, `os.stat` and `os.listdir` return `Operation not permitted`, `open()` returns
`Operation not permitted`, and `git -C <repo> show HEAD:<path>` fails. Without clause 5's
`file-read-metadata` line, `os.stat` succeeded — so that line is load-bearing and must not be
dropped as "redundant".

Backend `none`: no profile is generated, the launch line is the unchanged one, and
`ISOLATION.json` records `scope_enforcement: "unenforced"`. **This is the only supported way to
run without enforcement, and it fails B6.**

A `bwrap` backend for Linux is an obvious additive slot behind the same
`render_profile()`/`wrap_command()` interface. It is **not** implemented here: `COMPATIBILITY.md`
claims no Linux support for the runtime path, and an untested backend is worse than a stated gap —
the same judgement `redaction/1.1` already made about Windows paths.

#### G.5 The launch line, and how it reaches Orca

`orca terminal create` takes a `--worktree` *selector*, and an ephemeral session directory is not
an Orca-managed worktree. It also takes `--command <text>`, which is arbitrary. The design uses the
second, and does **not** register the session as a worktree (registering it would create Orca
metadata that outlives the ephemeral directory).

```bash
orca terminal create --worktree current \
  --title "final-review-isolated-<attempt>" \
  --command "cd <SESSION>/review_root && exec /usr/bin/sandbox-exec -f <SESSION>/control/scope.sb <resolved agent command>"
```

Why this is correct rather than a trick:

* The shell that runs the `--command` text starts in the main checkout and is **not** sandboxed;
  the `cd` therefore succeeds, and `exec` replaces that shell with the sandboxed agent. The agent
  process — the only process that ever sees the review — has cwd `review_root` and the profile
  applied. Verified: `git` inside the sandbox fails with *"Unable to read current working
  directory"* when cwd is the denied repository, which is exactly why the `cd` must precede the
  `exec` and why the design states the order explicitly.
* `<resolved agent command>` is `resolved_agent_command(role, phase)`
  (`scripts/orca_runtime_harness.py`, the W-20 reuse-gate value), unchanged. Isolation wraps the
  command; it never rewrites it. The reuse gate compares the *resolved role command*, so wrapping
  must be applied at launch and must **not** be folded into `agent_command`, or every isolated
  dispatch would look like a different agent to the gate. Stated as an implementation constraint in
  `## Expected Changed Files`.
* **Terminal reuse is forbidden for an isolated dispatch.** A sandbox is applied at exec; a reused
  terminal is already past its exec. `register_terminal(..., origin="self_created")` with reuse
  disabled, and `ISOLATION.json` records the terminal handle so the pairing is auditable.

**Pre-flight probe (mandatory, before the real dispatch).** `isolate` runs the *actual* resolved
agent command under the *actual* generated profile with a trivial non-review prompt and a short
timeout, from `review_root`. Purpose: discover the Class USR roots the agent genuinely needs (an
`Abort trap: 6` or a startup error means the readable set is too small) and prove the launch line
works before a Task is dispatched into it. Its stdout/stderr go to `control/probes/preflight.log`.
A failing pre-flight is exit 4 with the message printed — never a silently widened profile. Each
root added in response is added by an explicit `--allow-read` on the next invocation, so every
widening is a recorded operator decision and is then subject to the G.3 scan.

`orca orchestration send/check/ask` must keep working from inside the sandbox: the `orca`
executable lives outside the repository, `(allow default)` leaves network and process rights
untouched, and the dispatch capability is passed in the preamble rather than read from the repo.
The pre-flight probe asserts this concretely by running `orca orchestration check --terminal
<handle>` inside the sandbox and requiring exit 0. If it fails, that is a blocking finding for
IMPLEMENTATION, not something to work around silently — see `## Risks / Open Issues` **O-1**.

#### G.6 `ISOLATION.json` — the attestation

Written to `<SESSION>/control/ISOLATION.json` after the readable-set scan and pre-flight probe pass,
and **copied into the run artifact root at repatriation time** (G.8) as
`artifacts/runs/<run>/FINAL_REVIEW_ISOLATION.json`, so the evidence survives the ephemeral session.

```json
{
  "schema_version": "1.0",
  "document_kind": "final_review_isolation_attestation",
  "run_id": "run_…",
  "final_review_attempt": 1,
  "terminal": "term_…",
  "scope_enforcement": "seatbelt",
  "enforcement_backend_version": "sandbox-exec; darwin <uname -r>",
  "session_root": "<REDACTED:foreign_absolute_path>",
  "review_root": "<REDACTED:foreign_absolute_path>",
  "profile_digest": "sha256:…",
  "readable_set": [
    {"class": "SYS", "path": "/usr", "user_writable": false, "scanned": false},
    {"class": "USR", "path": "<REDACTED:foreign_absolute_path>",
     "user_writable": true, "scanned": true,
     "scan": {"files": 412, "archives": 0, "hits": 0}}
  ],
  "writable_set": ["<REDACTED:foreign_absolute_path>"],
  "denied_roots": ["<REDACTED:foreign_absolute_path>"],
  "key_bearing_roots_discovered": 2,
  "properties": {"S1": "PASS", "S2": "PASS", "S3": "PASS"},
  "probes": [
    {"id": "NEG-0", "kind": "positive_control", "result": "PASS"},
    {"id": "NEG-1", "kind": "review_root_walk",  "result": "PASS", "hits": 0}
  ],
  "threat_model": "unconstrained well-behaved reviewer agent; not a sandbox-escaping adversary",
  "limitations": ["Class SYS roots are asserted non-writable, not content-scanned"]
}
```

Rules:

* **Every path-bearing field goes through the existing P-PATH treatment**
  (`run_logging._relative_artifact_path()` / `assert_retained_path_field()`), so the attestation is
  itself environment-safe. A session path is by construction a foreign absolute path and lands as
  the placeholder — which is why `profile_digest`, not the profile text, is what is recorded.
* **No clock value.** Like the metrics document, this file must be byte-reproducible from the same
  session. A `--provenance-out` sidecar (identical shape to `_write_provenance()`) carries
  `generated_at` and the attestation digest. Same rule, same reason, same code shape.
* `properties.S1/S2/S3` are three separate verdicts. There is no aggregate "isolated: true" field —
  a single boolean invites reading a partial result as a whole one.
* `readable_set[].scanned: false` is only ever legal for `class: "SYS"`, enforced when the document
  is written.

#### G.7 Fail-closed rules and exit codes

Reusing `final_review_eval.py`'s existing codes, no new ones:

| condition | exit | effect |
|---|---|---|
| session path relates to the repo/fixture; key digest mismatch | `2` (contract violation) | no session left behind |
| any G.3 scan hit; symlink in the copy list; leak-scan hit in `review_root`; pre-flight failure; `--enforcement seatbelt` on a host with no `sandbox-exec` | `4` (leak/fixture) | no session left behind, nothing dispatched |
| bad arguments | `1` | — |
| `--enforcement none` | `0`, with `scope_enforcement: "unenforced"` on stdout **and** a stderr warning line | dispatch may proceed; **B6 fails**, so the capture may not be recorded as a §7 baseline |

`isolate --teardown <SESSION>` removes a session and is the only removal path; it refuses a path
that is not a `frv_iso_` session containing `control/ISOLATION.json`, so a mistyped argument cannot
delete an unrelated tree.

#### G.8 Report repatriation, audit capture, and why re-scoring stays byte-identical

The Reviewer writes `FINAL_REVIEW.md` inside the session, because the repository is not writable to
it. The Coordinator therefore performs one explicit step between settlement and capture:

```bash
python3 scripts/final_review_eval.py isolate --repatriate <SESSION> --run-id <run> [--attempt N]
```

which:

1. copies `<SESSION>/review_root/artifacts/runs/<run>/FINAL_REVIEW.md` to
   `artifacts/runs/<run>/FINAL_REVIEW.md` in the main checkout — **byte-for-byte**, no
   normalization, no re-wrapping (the retained report is a digest-bound snapshot; see D-A.6 and the
   `.gitattributes` whitespace exemption already in place);
2. copies `<SESSION>/control/ISOLATION.json` to
   `artifacts/runs/<run>/FINAL_REVIEW_ISOLATION.json`;
3. records the copied report's sha256 **before and after** the copy and refuses (exit 2) on any
   difference;
4. refuses (exit 2) if the destination `FINAL_REVIEW.md` already exists with different content —
   the existing "a retry never overwrites the predecessor's evidence" rule, applied at the one new
   place a file crosses the session boundary. Attempt N ≥ 2 repatriates under the existing
   attempt-suffixed path convention, exactly as the non-isolated path already does.

Then the **existing, unchanged** capture runs: `final-review-audit-record` reads
`artifacts/runs/<run>/FINAL_REVIEW.md`, redacts, and publishes `input.md`/`report.md`/`record.json`
per D-A. Nothing in the audit family changes shape.

**Why byte-for-byte re-scoreability is preserved.** `score` consumes `--findings` (from
`parse-report` over the retained report), `--key`, optional `--adjudications`, and `--workspace`.
Under isolation:

* the retained report is byte-identical to what the Reviewer wrote (step 3 proves it);
* the key and adjudications are read in the main checkout, by the Coordinator, *after* the dispatch
  settled — the §5 execution/scoring separation is strengthened, not weakened, because the scorer
  now provably runs in a different filesystem scope than the reviewer did;
* `--workspace` is used by `parse_location()`/`_evidence_grounding()` to resolve reported file
  paths. It must be given the **repatriated copy** of the subject tree, not the deleted session
  path. So repatriation also copies `<SESSION>/review_root/subject/` to
  `artifacts/runs/<run>/final_review_workspace/` and that path is what `--workspace` receives.
  The tree is byte-identical to the materialized one (same `MANIFEST.json`, same `fixture_digest`),
  so every digest and every metric is unchanged;
* the fixture itself is untouched, so `verify-fixture` and `fixture_digest` are unchanged;
* `metrics.json` still contains no clock value, so re-running `score` on the stored inputs
  reproduces the file byte-for-byte. **B5 is unaffected by this design.**

The only new artifacts in the run root are `FINAL_REVIEW_ISOLATION.json` and
`final_review_workspace/`. Both are additive; no existing artifact changes name, shape or content.

#### G.9 The negative-test contract

All of these are executed by `isolate` itself (results recorded in `ISOLATION.json`) **and**
asserted by `scripts/test_review_isolation.py` against a synthetic fixture, so the guarantee is
tested in CI and re-proved at every capture.

| id | probe | passes when |
|---|---|---|
| **NEG-0** | *Positive control.* The exact same read the negative probes make — `open(<key path>)` and `git -C <repo> show HEAD:<key repo-relative path>` — executed **without** the sandbox. | Both **succeed** and the bytes read equal the key file's bytes. If NEG-0 fails, every NEG-1…NEG-4 result is discarded and the command exits 4: the probe was pointed at the wrong path. |
| **NEG-1** | Exhaustive walk of `review_root` from *inside* it: every file, no exclusions. Checks (a) `scan_leak(key, [review_root])`, (b) the literal absolute path of the key file, of `key/`, of `adjudications/`, and of the repository root as substrings, (c) the fixture's real `fixture_id`. | zero hits in all three |
| **NEG-2** | Inside the sandbox: `open(<key abs path>)`, `open(<adjudications file>)`. | both raise `PermissionError` |
| **NEG-3** | Inside the sandbox: `os.path.exists(<key abs path>)`, `os.stat`, `os.listdir(<key dir>)`, `os.listdir(<repo root>)`. | `exists` is `False`; the other three raise `PermissionError` — *discovery*, not just reading, is blocked |
| **NEG-4** | Inside the sandbox: `git -C <repo> show HEAD:scripts/fixtures/final_review_eval/key/answer_key.json`; `git -C <repo> grep seeded_defects`; `tar -tzf <repo>/dist/*.tar.gz`. | all exit non-zero and none prints key content |
| **NEG-5** | The G.3 readable-set scan over every Class USR root, re-run from inside the sandbox using only paths the profile permits. | zero hits in passes A–D |
| **NEG-6** | Profile integrity: `sha256(scope.sb)` equals `ISOLATION.json.profile_digest`, and the profile parses (`sandbox-exec -f scope.sb /usr/bin/true` exits 0). | both hold |

NEG-2 … NEG-5 run the probe as a *separate process launched by exactly the same
`wrap_command()` used for the real dispatch* — not by re-implementing the launch line in the test.
A negative test that builds its own launch line proves something about the test, not about the
dispatch.

Under `--enforcement none`, NEG-2 … NEG-6 are **not** recorded as `SKIP`; they are recorded as
`"result": "NOT_APPLICABLE_UNENFORCED"` and `properties.S2` is `"FAIL"`. A skip reads like an
absence of evidence; this reads like what it is.

---

### D-H — Evidence-bundle sanitization (MAJOR-2)

All of this is inside `scripts/run_logging.py`. No new module, no new dependency, and the raw local
`ORCHESTRATOR_LOG.md` is never opened for writing.

#### H.1 The rule, stated once

> **No text enters `FINAL_REVIEW_EVIDENCE_BUNDLE.json` that has not been proved residue-free under
> the current redaction policy. Text that cannot be made residue-free is omitted with a stated
> reason and a digest, never embedded and never silently truncated.**

Three consequences:

1. **The authoritative local log is untouched.** `export_final_review_evidence()` opens it
   read-only, exactly as today. Its append-only lifecycle, its digests and its immutability are
   unchanged. Only the *exported copy* is sanitized.
2. **Sanitization is the existing `redact_text()`**, not a second policy. A second copy of a
   redaction policy is precisely the drift R1 punished.
3. **Residue is a hard stop for the affected value, not for the export.** The bundle is still
   written, still lists every attempt, and says explicitly which content it withheld and why — the
   same posture `_embedded_artifact()` already takes toward digest mismatches (report, don't deny).

#### H.2 `safe_embedded_text()` — the one gate every embedded string passes

```python
def safe_embedded_text(
    raw: str, *, redact: bool
) -> tuple[str | None, tuple[dict[str, int], ...], str]:
    """Return (text_or_None, redactions, omission_reason).

    `redact=True`  -- the source is NOT already a post-redaction artifact
                      (ORCHESTRATOR_LOG.md). Apply redact_text() first.
    `redact=False` -- the source IS a digest-bound post-redaction artifact
                      (input.md / report.md). Re-redacting would break the
                      recorded digest identity, so this path VERIFIES ONLY.
    """
```

Steps, in order:

1. `candidate, redactions = redact_text(raw)` when `redact` is true; otherwise
   `candidate, redactions = raw, ()`.
2. **Residue check (fixed point).** `again, extra = redact_text(candidate)`. Require
   `again == candidate` **and** `extra == ()`. Because category 5 (`foreign_absolute_path`) matches
   *every* absolute POSIX path with no minimum segment count and replaces it whole, and category 4
   owns the three home spellings, a fixed point means **no absolute path, no `dcap_…`, no
   `scheme://user:pass@`, and no `SECRET`/`TOKEN`/`PASSWORD`/`API_KEY`-named environment assignment
   survives**. That is a closed statement over the policy's own categories, not an inspection.
   Verified for the concrete cases: `/Users/<u>/x/y`, `/private/tmp/claude-501/foo`, `/luminous`,
   `file:///Users/<u>/a`, `AWS_SECRET_ACCESS_KEY=…`, `https://u:p@h/p`, `dcap_…` are all fixed
   points after one pass, and `<REPO>/scripts/x.py` is untouched.
3. **Structure check (log only, `redact=True`).** `candidate.count("\n") == raw.count("\n")`, and
   for every line, `candidate` line's `|` count equals `raw` line's `|` count. Placeholders contain
   no `|` and no newline today; this makes a future policy that introduced one fail loudly instead
   of silently corrupting the exported table. Verified on table rows: `| a | /Users/u/x/y | b |`
   → `| a | /Users/<REDACTED:absolute_local_path>/x/y | b |`, four pipes before and after.
4. On any failure of 2 or 3: return `(None, redactions, reason)` where `reason` is one of the
   closed set `"redaction_residue"`, `"table_structure_changed"`. On success:
   `(candidate, redactions, "")`.

The reason vocabulary is closed and enumerated in a module constant, the same discipline
`REDACTION_CATEGORIES` and `FINAL_REVIEW_RETAINED_PATH_FIELDS` already use.

#### H.3 The new `orchestrator_log` object

```jsonc
"orchestrator_log": {
  "path": "ORCHESTRATOR_LOG.md",                 // unchanged, P-PATH asserted
  "redaction_policy_version": "redaction/1.1",
  "digest_pre_redaction":  "sha256-of-the-raw-local-file",
  "digest_post_redaction": "sha256-of-content_redacted",   // null when omitted
  "redactions": [{"category": "foreign_absolute_path", "count": 3}],
  "content_redacted": "| timestamp | event | …",           // null when omitted
  "content_omitted_reason": ""                             // "" when embedded
}
```

* The key is **`content_redacted`, not `content`.** A reader that has not been updated must not be
  able to keep reading a key whose meaning changed; a rename makes the change impossible to miss
  and is why the schema bump in H.5 is MAJOR.
* **The auditable identity relationship**, stated in the exporter docstring and in `SKILL.md` §9:
  `digest_pre_redaction` identifies *which* log this is — an auditor holding the local file
  recomputes it and compares. `digest_post_redaction` identifies the embedded bytes.
  `redact_text()` is documented as a pure deterministic function of `(text, policy_version)`, so
  `digest_post_redaction == sha256(redact_text(local_file, policy_version=<recorded>))` is a
  re-derivable equality, on any machine, at any time. That is the whole chain: raw → recorded
  policy → embedded bytes, with no unverifiable link.
* `redactions` uses the existing category+count shape — deliberately no offsets and no
  per-occurrence digest, for the reason `redact_text()`'s docstring already gives (offsets plus
  retained text localize and size the removed value).
* When omitted: `content_redacted: null`, `digest_post_redaction: null`,
  `content_omitted_reason: "redaction_residue"`, and `digest_pre_redaction` still present — the log
  remains *identifiable* and locally readable; only its text is withheld.
  `integrity.omitted_content[]` gains `{"path": "ORCHESTRATOR_LOG.md", "reason": …}`.

#### H.4 The embedded `input.md` / `report.md` — verify, never re-redact

`_embedded_artifact()` gains the `redact=False` path:

* the bytes are read exactly as today, `digest_recomputed` and `digest_verified` keep their present
  meaning (they compare against `artifact_digest_post_redaction`, which is a digest of *these*
  bytes — re-redacting would make `digest_verified` false for a clean artifact, which is why
  `redact=False` verifies rather than transforms);
* `safe_embedded_text(raw, redact=False)` runs the residue check anyway. A pass changes nothing —
  the normal case is byte-identical to today's output plus one new field. A **fail** means a
  retained artifact written under an older policy (or a policy bug) carries residue: the content is
  **omitted** with `content_omitted_reason: "redaction_residue"`, the digests are kept, and
  `integrity.omitted_content[]` records it.
* The alternative — embedding known-residual content, or aborting the export permanently — are both
  worse: the first knowingly ships the leak, the second makes a legacy record permanently
  un-exportable with no remedy that does not violate D-A.3 immutability. Omission is the only option
  that is simultaneously safe, non-destructive, and honest about what happened.

`entry[name]` therefore gains `content_omitted_reason` and `redaction_residue_detected` (bool).
`digest_recorded` / `digest_recomputed` / `digest_verified` keep their existing names and meanings.

#### H.5 Schema version, reader rule, and the CLI

* `FINAL_REVIEW_EXPORT_SCHEMA_VERSION` `"1.0"` → **`"2.0"`** (`scripts/run_logging.py:863`).
  `orchestrator_log.content` is removed and `orchestrator_log.digest` is renamed; both are breaking
  for a reader, so the MAJOR component of the version moves. `component_versions.export_schema`
  follows automatically (it reads the same constant).
* Reader rule, matching the existing `require_major()` discipline: a consumer of the bundle accepts
  `2.x` and refuses `1.x` and `3.x`. The bundle is opt-in and fully re-derivable from immutable
  records, so there is no migration to write — an old bundle is regenerated, not upgraded. Stated in
  `CHANGELOG.md`.
* `component_versions` gains nothing: `redaction_policy` is already there and is now genuinely
  load-bearing for the log as well as the records.
* The CLI subcommand `final-review-audit-export` (`scripts/run_logging.py:2739`) is unchanged in
  name, arguments and exit behaviour.
* `export_final_review_evidence()`'s docstring gains the H.1 rule verbatim, because the current
  docstring's "Content is INLINED rather than referenced" is exactly the sentence a future
  contributor would cite to re-introduce the defect.

---

### D-I — `COMPATIBILITY.md` (MINOR-1)

Replace `COMPATIBILITY.md:120-122` with:

```markdown
**Redaction policy `redaction/1.1` covers POSIX paths only.** The policy has five ordered
categories: Orca dispatch capability, URL credential, secret-named environment assignment,
home-rooted absolute path (the user-name segment is replaced, the rest stays readable), and
— added in 1.1 — every other absolute POSIX path, replaced whole with no minimum segment
count, so an unanticipated shape fails closed rather than being left unchanged. Windows
`C:\Users\<name>` is deliberately not a category: this document does not claim Windows
support for the runtime path, and an untested pattern is worse than a stated gap. Adding it
is a MINOR policy bump. The same policy governs the exported evidence bundle, including the
copy of `ORCHESTRATOR_LOG.md` embedded in it; text that is not residue-free under the policy
is omitted from the bundle with a stated reason and a digest rather than embedded. The
authoritative local log is never rewritten.
```

Replace `COMPATIBILITY.md:124-127` with:

```markdown
**Packaging, and what the baseline capture guarantees.** `scripts/` is included in the release
archive, so a downloaded tarball contains
`scripts/fixtures/final_review_eval/key/answer_key.json`. That is unchanged and is stated rather
than worked around: the key must ship for a downstream user to score anything.

What changed is the claim, which is now about the *execution environment* rather than only about
the retained input. A §7 baseline capture dispatches the Final Reviewer under an enforced
filesystem scope (`scripts/review_isolation.py`, `sandbox-exec` on darwin): its working directory
is an ephemeral session containing only the materialized subject and a closed list of review-policy
files, every path it can read has been exhaustively scanned for key material, and the key-bearing
roots — the repository checkout, its `.git`, and any release archive found by the scan — are denied
for both content and metadata, so the key cannot be read *or discovered*. A negative test with a
positive control proves this per capture and its result is recorded in
`artifacts/runs/<run>/FINAL_REVIEW_ISOLATION.json`.

Two boundaries, stated rather than implied. First, the guarantee is scoped to a capture whose
attestation says `scope_enforcement: seatbelt`; a capture on a host without an enforcement backend
records `scope_enforcement: unenforced`, fails the baseline's B6 criterion, and may not be called a
baseline. Second, the threat model is an unconstrained but well-behaved reviewer agent — one that
reads absolute paths, runs `git` and greps broadly — not an adversary that escapes a kernel
sandbox. Ordinary (non-baseline) Final Review dispatches are unaffected and are not claimed to be
isolated; for those, the older and narrower claim still holds and is still verified per run: no key
material appears in the reviewer's retained input.
```

---

## Error Handling / Compatibility

### Failure posture, by surface

| surface | failure | posture |
|---|---|---|
| `isolate` (session build, scan, profile, pre-flight) | any | **fail closed**, non-zero exit, session removed, nothing dispatched. A half-built isolation session is worse than none, because its existence would be read as a guarantee. |
| `isolate --repatriate` | digest differs, or destination exists with different content | **fail closed** (exit 2). The report is the evidence; a silently overwritten or altered one is not. |
| the isolated dispatch itself | terminal dies, agent fails to start, no report produced | existing dispatch-layer failure path (D-A/D-B): the failure and its evidence are captured, provenance is `voided`, and the retry runs under a **new** Task/Dispatch identity **and a new isolation session**. A session is never reused across attempts — its `ISOLATION.json` names one attempt. |
| `export_final_review_evidence` | residue or structure change in the log | content omitted with reason; bundle still written; `integrity.omitted_content[]` populated. |
| `export_final_review_evidence` | residue in a retained `input.md`/`report.md` | same: omit, record, keep digests. |
| `export_final_review_evidence` | log file missing / unreadable | as today: `digest_pre_redaction: null`, `content_redacted: null`, and `content_omitted_reason: "unreadable"`. |
| `redact_text` raising on an unknown policy version | propagates | unchanged; a digest under an unknown policy is comparable to nothing. |

### Compatibility

**Unchanged on disk:** the fixture layout (`subject/`, `key/`, `adjudications/`), the answer-key
schema, `MANIFEST.json`, `fixture_digest`, the findings/metrics/adjudication schemas, the audit
record schema `1.0`, the redaction policy `redaction/1.1` and its five categories, P-PATH and its
field list, `ORCHESTRATOR_LOG.md`'s columns and row format, `TIMING_LOG.md`.

**Changed:** the export bundle schema (`1.0` → `2.0`, D-H.5) — an opt-in, re-derivable artifact
that nothing else in the repository reads. One existing test
(`scripts/test_run_logging.py:3181`) asserts on `bundle["orchestrator_log"]["content"]` and must be
updated to `["content_redacted"]` in the same commit.

**Added:** `scripts/review_isolation.py`; three `final_review_eval.py` subcommands (`isolate`,
`isolate --repatriate`, `isolate --teardown`); two new run artifacts
(`FINAL_REVIEW_ISOLATION.json`, `final_review_workspace/`); `scripts/test_review_isolation.py`.

**Not additive, and named as such:** the `orchestrator_log` key rename. It is a deliberate breaking
change of a bundle key, taken because leaving `content` in place with new semantics is the more
dangerous option.

### Reproducibility

Byte-for-byte re-scoreability (B5) is preserved for the reasons enumerated in G.8. The one thing
that would have broken it — pointing `--workspace` at a deleted session path — is closed by
repatriating the subject tree into `artifacts/runs/<run>/final_review_workspace/`. `metrics.json`
still contains no clock-derived value; `ISOLATION.json` follows the same rule and puts its clock in
a sidecar.

---

## Expected Changed Files / Implementation Steps

Order is the ordering rules from `## Summary / Requirements`. `**HARD**` means the step boundary is
a commit boundary and may not be merged with its neighbours.

### Step 1 — `scripts/run_logging.py` (D-H) (**HARD**)

* add `EMBED_OMISSION_REASONS` (closed tuple) and `safe_embedded_text()` next to `redact_text()`;
* rewrite `_embedded_artifact()` to take `redact: bool` and to emit `content_omitted_reason` /
  `redaction_residue_detected`;
* rewrite the `orchestrator_log` block of `export_final_review_evidence()`
  (`scripts/run_logging.py:2484-2503`) to H.3's shape; keep the `assert_retained_path_field()` call
  on `["path"]`;
* add `integrity["omitted_content"] = []` and populate it;
* bump `FINAL_REVIEW_EXPORT_SCHEMA_VERSION` to `"2.0"` (`scripts/run_logging.py:863`);
* extend the `export_final_review_evidence()` docstring with the H.1 rule.

### Step 2 — tests for D-H (**HARD**, same commit as Step 1)

`scripts/test_run_logging.py`: T-7 below, plus updating the existing assertion at line 3181.

### Step 3 — `scripts/review_isolation.py` (D-G)

`compute_readable_set()`, `scan_readable_set()`, `render_seatbelt_profile()`, `wrap_command()`,
`build_session()`, `run_probes()`, `write_attestation()`, `repatriate()`, `teardown()`.
No import from `run_logging` beyond `redact_text` / `_relative_artifact_path` /
`assert_retained_path_field` (allowed direction).

### Step 4 — `scripts/final_review_eval.py` subcommands (**HARD**, same commit as Step 3)

`isolate` with `--run-id / --fixture / --session-base / --policy-file / --allow-read /
--enforcement / --out`, and the mutually exclusive `--repatriate <SESSION>` and
`--teardown <SESSION>` forms. Wire into `build_parser()` and `_dispatch()`; reuse the existing exit
codes per G.7. Update the module docstring's subcommand list (it currently says "Five subcommands").

### Step 5 — `scripts/test_review_isolation.py` (**HARD**, same commit as Steps 3-4)

NEG-0 … NEG-6 per G.9, plus the unit tests in T-8/T-9.

### Step 6 — re-run the §7 baseline under isolation (**HARD**, after Steps 1-5)

B-1′ … B-7 below. The predecessor baseline artifacts are not deleted or edited (ordering rule 4).
The new capture writes a new run directory and its own `FINAL_REVIEW_ISOLATION.json`.

### Step 7 — docs

* `COMPATIBILITY.md`: the two replacements in D-I.
* `CHANGELOG.md`: the export schema `2.0` break and its reader rule; the new isolation tooling.
* `orca-worker-reviewer-orchestration/SKILL.md` §9: one sentence that the exported bundle is
  sanitized under the recorded policy and that omitted content is reported rather than embedded.
  **Check `validate_skills.py:284` `FINAL_REVIEW_CONTRACT_MAX_LINES` is not affected** — this text
  is outside the `#### Final review contract` block; if an edit lands inside that block the
  validator's line bound and key set must be updated in the same commit, per the existing rule.
* `scripts/fixtures/final_review_eval/README.md`: point at `isolate` as the way a downstream user
  reproduces a baseline.

### Files summary

| file | change |
|---|---|
| `scripts/run_logging.py` | D-H: `safe_embedded_text()`, `_embedded_artifact()`, export bundle log block, schema `2.0` |
| `scripts/review_isolation.py` | **new** — D-G |
| `scripts/final_review_eval.py` | `isolate` subcommand family; docstring subcommand count |
| `scripts/test_run_logging.py` | T-7; update the `["content"]` assertion at :3181 |
| `scripts/test_review_isolation.py` | **new** — NEG-0…NEG-6, T-8, T-9 |
| `scripts/test_final_review_eval.py` | T-10 (CLI wiring / exit codes) |
| `COMPATIBILITY.md` | D-I |
| `CHANGELOG.md` | schema break + new tooling |
| `orca-worker-reviewer-orchestration/SKILL.md` | one §9 sentence |
| `scripts/fixtures/final_review_eval/README.md` | reproduction pointer |
| `artifacts/runs/<new run>/…` | the re-captured baseline (Step 6) |

Not changed, deliberately: `VERSION`, `LICENSE-DECISION.md`, the fixture trees, the answer key,
`release_manifest.py` (no new file ships into an installed Skill — `review_isolation.py` is
repository-side tooling, like `final_review_eval.py`).

---

## Testing Strategy

New groups T-7 … T-10 extend the existing T-1 … T-6. Nothing in T-1 … T-6 changes except the single
assertion named in Step 2.

### T-7 Bundle sanitization — `scripts/test_run_logging.py` (D-H)

Each case writes a synthetic `ORCHESTRATOR_LOG.md` row through `log_orchestrator_event()` with a
poisoned `detail` or `result`, exports, and asserts on the bundle **as a whole serialized string**,
not only on the log object — a leak that moved to another key is still a leak.

| id | poisoned cell | asserts |
|---|---|---|
| T-7.1 | `detail="artifact at /Volumes/ext/build/out.md"` (foreign absolute path) | the raw substring is absent from the serialized bundle; `redactions` reports `foreign_absolute_path ≥ 1`; `content_redacted` contains the placeholder |
| T-7.2 | `detail="/Users/<user>/aiAssistedProjects/x"` (username-bearing path) | the user-name segment is absent; `/Users/<REDACTED:absolute_local_path>/aiAssistedProjects/x` present |
| T-7.3 | `result="GITHUB_TOKEN=ghp_deadbeef…"` | `ghp_deadbeef…` absent; `env_secret_pattern ≥ 1` |
| T-7.4 | `detail="https://user:hunter2@example.test/x"` | `hunter2` absent; `url_credential ≥ 1` |
| T-7.5 | `detail="dcap_AAAABBBBCCCC…"` | the literal absent; `orca_dispatch_capability ≥ 1` |
| T-7.6 | a clean log | `redactions == []`, `content_redacted == raw`, `digest_post_redaction == digest_pre_redaction`, `content_omitted_reason == ""` |
| T-7.7 | identity/auditability | `digest_pre_redaction == sha256(local file)` and `digest_post_redaction == sha256(redact_text(local file)[0])`, recomputed independently in the test |
| T-7.8 | table structure | for every poisoned case, the embedded text has the same line count as the raw log and the same per-line `|` count |
| T-7.9 | residue path (monkeypatched `redact_text` returning a non-fixed-point value) | `content_redacted is None`, `content_omitted_reason == "redaction_residue"`, `digest_pre_redaction` still present, `integrity["omitted_content"]` names the log, **and the export still returns a written path** |
| T-7.10 | residue in a retained `report.md` (written with a stub that bypasses redaction) | content omitted with reason; `digest_recorded`/`digest_recomputed` still present; export does not raise |
| T-7.11 | the raw local log is untouched | its bytes and mtime-independent digest are identical before and after export |
| T-7.12 | schema | `schema_version == "2.0"`, `component_versions.export_schema == "2.0"`, `"content" not in bundle["orchestrator_log"]` |

### T-8 Isolation mechanism — `scripts/test_review_isolation.py` (D-G, unit)

| id | asserts |
|---|---|
| T-8.1 | `build_session()` produces exactly the G.2 layout; `control/` is a sibling of `review_root/`, not a descendant |
| T-8.2 | a session whose base resolves inside the repository or the fixture → exit 2, nothing left on disk |
| T-8.3 | a symlink in the policy copy list → exit 4 |
| T-8.4 | `scan_readable_set()` pass A/B/C/D each catch a planted copy: a renamed `key.json`, a file containing a key shingle, a byte-identical copy under another name, and a `.tar.gz` whose member list names `key/answer_key.json` |
| T-8.5 | a Class SYS root that is user-writable is reclassified to USR and scanned |
| T-8.6 | `render_seatbelt_profile()` emits the five clauses in G.4's order; a generated profile parses (`sandbox-exec -f … /usr/bin/true` exits 0) |
| T-8.7 | `ISOLATION.json` validates: no clock value; every path field is P1/P2/P3/P4 under `assert_retained_path_field`; `scanned: false` only for `class: "SYS"`; `properties` has three independent verdicts |
| T-8.8 | `--enforcement none` records `scope_enforcement: "unenforced"`, `S2: "FAIL"`, probes `NOT_APPLICABLE_UNENFORCED`, and exits 0 with a stderr warning |
| T-8.9 | `--enforcement seatbelt` on a host with no `sandbox-exec` → exit 4 |
| T-8.10 | `--repatriate` refuses a byte-differing copy and refuses an existing differing destination |
| T-8.11 | `--teardown` refuses a directory that is not a `frv_iso_` session with `control/ISOLATION.json` |

### T-9 The negative test — `scripts/test_review_isolation.py` (D-G, G.9)

NEG-0 … NEG-6 exactly as specified, against a temporary fixture that plants a real key file. Skipped
as a whole with an explicit reason on a non-darwin host (`sys.platform != "darwin"`), and **T-8.9
is the test that then carries the fail-closed guarantee**, so the guarantee is never merely
unasserted. NEG-0 runs first inside each of NEG-2 … NEG-4's test method, not as a separate test.

### T-10 CLI wiring — `scripts/test_final_review_eval.py`

Exit codes per G.7 for each `isolate` form; `--repatriate` and `--teardown` are mutually exclusive
with each other and with a session build; the module docstring's subcommand count matches
`build_parser()`.

### Amended §7 baseline procedure (B-1′ … B-7)

Replaces B-1 … B-5. Steps that are unchanged say so.

```text
B-1′ python3 scripts/final_review_eval.py isolate --run-id <run> --enforcement seatbelt
     Builds the session, computes and scans the readable set, generates scope.sb, runs the
     pre-flight probe and NEG-0..NEG-6, writes control/ISOLATION.json. Any failure: STOP.
B-2′ Dispatch ONE Final Review attempt with the G.5 launch line, cwd <SESSION>/review_root,
     with NO change to detection/search policy and no terminal reuse.
B-3  Capture the audit records for that dispatch -- UNCHANGED, including the dispatch-layer
     failure evidence rules. A captured failure is §3 evidence and is NOT a satisfied baseline.
B-4R On a dispatch-layer failure: retry under a NEW Task/Dispatch identity AND a NEW isolation
     session, leaving the failed dispatch's records and its session attestation untouched. Loop
     until one dispatch settles with a usable report or DEFAULT_MAX_ITERATIONS = 5 is exhausted;
     if exhausted, record the §7 baseline as FAIL and STOP.
B-5′ python3 scripts/final_review_eval.py isolate --repatriate <SESSION> --run-id <run>
     Copies FINAL_REVIEW.md, ISOLATION.json and the subject tree back into the run artifact root,
     digest-verified. Then run the UNCHANGED capture.
B-6′ Score, as a SEPARATE step after the reviewer submitted -- UNCHANGED except that --workspace
     is artifacts/runs/<run>/final_review_workspace/:
       parse-report --report <retained report> --out f.json
       score --findings f.json --key scripts/fixtures/final_review_eval/key/answer_key.json \
             --workspace artifacts/runs/<run>/final_review_workspace --out metrics.json
B-7  python3 scripts/run_logging.py final-review-audit-export --run-id <run>
     Then record B1..B6 independently.
```

| criterion | passes when |
|---|---|
| **B1** procedure ran | unchanged — every step executed as documented, including at least one dispatch that settled with a usable report |
| **B2** scoring worked | unchanged |
| **B3** artifacts produced | unchanged, **plus**: the P-PATH grep now also returns zero hits for the isolation session path spelling, and the exported bundle is grepped as a whole (D-H makes this meaningful for the log for the first time) |
| **B4** no answer-key leak | unchanged (D.6 scan of the retained reviewer input returns zero hits) |
| **B5** reproducible | unchanged — byte-identical metrics on re-score, no excepted field |
| **B6** scope enforced (**new**) | `FINAL_REVIEW_ISOLATION.json` exists for the accepted dispatch, `scope_enforcement == "seatbelt"`, `properties.S1/S2/S3` all `PASS`, NEG-0 `PASS` and NEG-1…NEG-6 all `PASS`, and `profile_digest` matches the profile the launch line actually used. A capture missing any of these is **not a baseline** — it is recorded as an exploratory run and labelled as one |

Unchanged and restated so it cannot drift: the Reviewer's **verdict is an observation, not a
criterion**. A baseline where the Reviewer returns FAIL, or misses all five seeded defects, still
passes B1-B6. No detection-quality conclusion is drawn and no H-1/H-2/H-4/H-5 comparison appears in
any artifact.

---

## Risks / Open Issues

### Risks, each with the concrete mechanism this design gives it

| id | risk | mechanism |
|---|---|---|
| **RK-1** | The allowlist is widened during pre-flight until the agent starts, and the widening quietly re-admits a key copy. | Every widening is an explicit `--allow-read` on a re-invocation, and every Class USR root is scanned by G.3 passes A-D *after* the widening. A root that cannot be scanned clean cannot be allowed. Recorded per-root in `ISOLATION.json.readable_set[]`. |
| **RK-2** | Class SYS roots are not content-scanned. | Stated in the attestation's `limitations[]` and in D-I. Admission requires `os.access(root, W_OK) == False`, so planting a key there needs privilege the threat model excludes. |
| **RK-3** | `sandbox-exec` is deprecated by Apple and could be removed. | The backend is behind `render_profile()`/`wrap_command()`; `--enforcement` is an enum with a fail-closed default. If the backend disappears, captures fail B6 loudly rather than degrading silently. |
| **RK-4** | The generated profile is too tight and the agent half-works — producing a *worse* review that gets mistaken for a detection signal. | The pre-flight probe runs the real agent command and `orca orchestration check` before any Task is dispatched, and B6's verdict is separate from the review's. Also: the baseline draws no detection conclusion at all, by design. |
| **RK-5** | Someone reads `scope_enforcement: unenforced` as "isolated enough". | It is a distinct enum value, `S2` is `FAIL`, probes read `NOT_APPLICABLE_UNENFORCED` rather than `SKIP`, and B6 fails. Three independent places say the same thing. |
| **RK-6** | A future contributor re-adds `content` to the bundle for convenience. | The key no longer exists; the H.1 rule is in the exporter docstring; T-7.12 asserts `"content" not in bundle["orchestrator_log"]`. |
| **RK-7** | The residue check turns out not to be a fixed point for some real input, making every export omit the log. | Checked against the seven concrete shapes listed in H.2 step 2 (all fixed points). If a real non-fixed-point input appears, the correct response is a redaction-policy fix (a MINOR bump), not relaxing the check — stated here so the next contributor does not relax it. |

### Open issues — raised, not designed around

* **O-1 — `orca` CLI behaviour from inside the sandbox is asserted by a probe, not proven here.**
  `orca orchestration send/check/ask` must work with cwd set to a non-worktree directory and the
  repository unreadable. `(allow default)` leaves network and process rights intact and the CLI
  binary is outside the repository, so there is no known blocker — but the CLI may resolve a
  worktree from cwd for some subcommands. G.5 makes this a **mandatory, fail-closed pre-flight
  assertion** rather than an assumption. If it fails, IMPLEMENTATION must report it as a blocker;
  the fallback (dispatching into a registered throwaway Orca worktree that is itself denied by the
  profile) is a design change, not an implementation detail, and must come back through DESIGN.
* **O-2 — the agent's own state directory may be shared with non-isolated sessions.** If the
  reviewer agent caches transcripts or files under a `--allow-read` root shared with the
  Coordinator's own sessions, key material could in principle arrive there from an unrelated
  session. G.3's scan catches it *at session-build time*; it cannot catch a write that happens
  *during* the dispatch. Mitigation available but **not designed here** because it changes the
  agent's environment: give the isolated agent a session-scoped state directory via environment
  variables. Recorded as a known residual, and it is a *narrow* one — it requires the key to reach
  that directory from another session during the dispatch window.
* **O-3 — `.git` denial removes legitimate reviewer capability.** An isolated Reviewer cannot run
  `git log`/`git blame` on the subject, because the subject is not a git checkout. That is
  unchanged from the existing `materialize()` design (rule 2: no `.git` is created or copied) and is
  what `DIFF.patch` exists for — but under isolation it is now enforced rather than merely
  arranged, so it is worth stating plainly: the isolated baseline measures a reviewer working from
  a diff plus a tree, not from history.

### Explicitly not designed (so IMPLEMENTATION cannot drift)

* No change to detection or search policy, prompts, or reviewer instructions. The isolated
  Reviewer receives the same rendered task spec it receives today, modulo the paths it points at.
* H-1, H-2, H-4, H-5: untouched, unmeasured, unmentioned in any produced artifact.
* No change to the fixture trees, the answer key, the adjudication schema, the scorer, the matching
  algorithm, the metric block, or the precision-refusal semantics.
* No change to the audit record schema, provenance state machine, publication atomicity, retry
  identity, or the redaction policy's five categories.
* No Linux/Windows enforcement backend, and no claim of one.
* No deletion or edit of the predecessor baseline's artifacts.
