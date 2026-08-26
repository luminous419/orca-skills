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
| "Add a negative test proving the Reviewer-visible/executable filesystem cannot read or discover the key" | D-G.9 (**NEG-1 … NEG-8**, incl. **NEG-0** positive control. Iteration 1 wrote NEG-1 … NEG-6; **NEG-7** and **NEG-8** were added by iteration 2's F-001 fix.) |
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
   NEG-1 … NEG-8 is an assertion, not a guarantee; splitting them lets an unverified profile exist
   in history as if it were verified. *(Iteration 1 wrote NEG-1 … NEG-6 here; NEG-7 and NEG-8 join
   the same rule, per iteration 2.)*
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

**Added in iteration 2 (F-001).** Every row below was executed on this host; the planted copies
were removed afterwards and their absence re-checked.

| fact | how verified | result |
|---|---|---|
| the run user's real temp dir is a **writable descendant of `/private/var`** | `os.path.realpath(tempfile.gettempdir())`, then `os.access(..., W_OK)` | `/private/var/folders/<hash>/T`, `W_OK=True`, while `/private/var` itself is `W_OK=False` |
| the iteration-1 profile **does** leak an answer-key copy planted there | plant → `sandbox-exec -f <iteration-1 profile> python3 probe.py` | `open` returns the key's exact sha256, `listdir` lists it, `cat`/`grep`/`find` all succeed — **F-001 reproduced** |
| `/System/Volumes/Data` is a **mount point, not a symlink**, and re-exposes the whole data volume inside `/System` | `os.path.islink` → `False`; `os.stat` of `/System/Volumes/Data<tmpdir>` vs `<tmpdir>` → same `(st_dev, st_ino)`; `os.path.realpath` does **not** collapse it | the repository and the key are reachable as `/System/Volumes/Data<repo>/…/answer_key.json` |
| `st_dev` cannot discriminate the sealed system volume from the data volume | `os.stat("/usr").st_dev` vs `os.stat("/Library").st_dev` | identical (`16777230`) — APFS firmlinks share the fsid, so **`st_dev` is not a usable boundary test**; `statvfs().f_flag & ST_RDONLY` is (`/usr` True, `/usr/local` False) |
| which Class SYS roots actually contain user-writable descendants | exhaustive walk, `W_OK` on every directory and regular file | **`/private/var`, `/Library`, `/usr/local`, `/System/Volumes/Data`, `/opt/homebrew`, `/Users` do; `/bin`, `/sbin`, `/private/etc`, `/usr`(−carve-outs), `/System`(−carve-outs), `/dev`, `/private/var/select`, `/Library/Developer/CommandLineTools` do not** |
| `/usr/share/firmlinks` is an authoritative boundary list and agrees with the walk | `cat /usr/share/firmlinks`; compare to the `statvfs` flag-change set | exact agreement (19 entries; `/usr/local`, `/usr/libexec/cups`, `/usr/share/snmp`, the `/System/Library/*` set, and the root-level `/Users`, `/Library`, `/private`, `/opt`, `/Volumes`, `/Applications`) |
| the full recursive proof is affordable | timed walks | `/System` 13.0 s (169,297 dirs / 286,743 files), `/Library/Developer/CommandLineTools` 5.1 s, `/usr` 0.7 s, rest < 0.1 s |
| a global `(allow file-read-metadata)` is a **discovery channel**, even with a corrected readable set | corrected readable set + global metadata allow | `os.path.exists(plant)` → `True`, `os.stat(plant).st_size` → `9347` (the key's real size) |
| the closed traversal set closes it without breaking exec | traversal set + carve-outs | `exists` → `False`, `stat`/`listdir`/`open`/`cat`/`ls` → `Operation not permitted`, `find`/`grep` → empty stdout; `/bin/echo`, `python3`, `git --version`, reading `review_root` all still work |
| seatbelt **canonicalizes firmlink spellings** before matching | `open("/System/Volumes/Data" + <repo>/…/answer_key.json)` under the iteration-1 profile | `PermissionError` — the deny on the repo realpath already covered the alias |
| a symlink inside `review_root` pointing at a denied file does not bypass the profile | `os.symlink(plant, review_root/lnk); open(lnk)` | `PermissionError` — seatbelt evaluates the resolved target |
| removing `/Library` and `/private/var` breaks tooling unless `HOME`/`TMPDIR` are session-scoped | `git --version` under the corrected profile with the host `HOME` | `fatal: unable to access '/Users/<user>/.gitconfig': Operation not permitted`; with `HOME=<SESSION>/home` it exits 0, while `git -C <repo> show HEAD:VERSION` still fails |

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
| **S3 — cleanliness of the readable set** | **Every** path the Reviewer *can* read is exhaustively content-scanned and contains zero key material — Class USR and Class IMM alike (**corrected in iteration 5**; before it, Class IMM rested on immutability alone, which is a statement about writes and not about content). A Class IMM root is *additionally* proven immutable, and the two proofs do different jobs that neither can do for the other: the content scan says the key material is not there **now**, the immutability proof says nothing unprivileged can put it there **afterwards**. No admitted path is a proper ancestor of a mutable path covered by neither. | G.3 readable-set computation + G.3.1 proof + G.3.3 scan + `assert_no_unscanned_descendant()` | NEG-5, NEG-6, **NEG-7**, **NEG-8** |

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
    tmp/                        <- the sandboxed agent's TMPDIR (created empty)
    home/                       <- the sandboxed agent's HOME   (created empty)
    control/
        scope.sb
        ISOLATION.json
        probes/
```

`tmp/` and `home/` are an **iteration-2 addition (F-001)**. Once the host's `/private/var` and
`/Library` are no longer admitted, the agent has no writable scratch and no config root, and
`git`/`xcrun`-shimmed tools fail (measured: `fatal: unable to access '/Users/<user>/.gitconfig':
Operation not permitted`). Pointing `TMPDIR`/`HOME` at session-private directories restores that
without re-admitting anything host-wide, and has the side benefit of closing the *write* half of
open issue **O-2**: an isolated agent can no longer deposit anything into, or pick anything up
from, a state directory shared with non-isolated sessions.

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

> **Superseded in iteration 2 (F-001).** The rule below replaces the iteration-1 rule
> *"each Class SYS root is admitted only if `os.access(root, os.W_OK)` is False"*. That rule was
> wrong: root non-writability does not imply descendant non-writability, and on this host the
> unchanged iteration-1 profile let a sandboxed process read an answer-key-identical file planted
> in the current user's real `tempfile.gettempdir()`. The reproduction and the fix's verification
> are recorded in `## DESIGN iteration 2 — correction for F-001`.

Every admitted path belongs to exactly one of **two** classes, and **there is no third way in**.
A path that cannot be placed in one of the two is not admitted at all.

| class | what it means | admission requires | content-scanned at session-build time? | content-scanned at NEG-5? |
|---|---|---|---|---|
| **IMM** | no unprivileged process can cause new readable content to appear anywhere in the subtree | the **recursive immutability proof** below, run over the whole subtree at session-build time | no — not because a scan would find nothing, but because NEG-5 runs the same scan later over the profile's *own admitted set*, which is strictly better evidence, and running it twice per capture would double the dominant cost (G.3.3, D-5.1) | **yes — mandatory passes A, B, C, D** (G.9, **D-5.1**). Pass B is on by default for every admitted IMM root and there is no flag that turns it off |
| **USR** | mutable, but proven to contain no key material right now | the **exhaustive content scan** (passes A–D) over the whole subtree | yes | **yes — passes A, B, C, D, S** |

**The proof is never a substitute for the content scan, in either direction.** G.3.1's I-1…I-6
establish *current write incapability* and nothing else; they say nothing about content that was
already present when the proof ran, because the proof never opens a file. The authoritative
statement of this is the paragraph in G.3.3 beginning *"The immutability proof is not a substitute
for that scan"*; **DESIGN iteration 5** §C is its derivation. Any passage that reads as though the
proof makes a content scan unnecessary is stale and this rule wins.

**The invariant this restores (call it the *no-unscanned-descendant* invariant):**

> For every path `p` admitted by the profile, every path reachable under `p` is covered by either
> the IMM proof or the USR scan. Equivalently: **no admitted path may be a proper ancestor of a
> mutable path that is not itself proven or scanned.** `assert_no_unscanned_descendant()` checks
> this over the assembled readable set and is a hard failure (exit 4).

This is the specific clause the iteration-1 design violated. `(subpath "/private/var")` was an
admitted proper ancestor of `tempfile.gettempdir()`, which is mutable and was never scanned.

##### G.3.1 The recursive immutability proof — `prove_immutable(root, carve_outs)`

Run over the *entire* subtree, not its root. It returns PASS only if **all six** hold:

| # | check | why it is load-bearing |
|---|---|---|
| **I-1** | `os.walk(root, followlinks=False)` completes without an unreadable directory | a subtree that cannot be enumerated cannot be certified |
| **I-2** | **no directory** in the subtree satisfies `os.access(d, os.W_OK)` | a writable directory admits `create`/`rename`/`unlink` — i.e. planting a new file — anywhere beneath it |
| **I-3** | **no regular file** in the subtree satisfies `os.access(f, os.W_OK)` | a writable regular file admits overwriting existing content with key material |
| **I-4** | every filesystem boundary strictly inside the subtree is enumerated and each is either itself IMM-proven or listed in `carve_outs` | a boundary crossing re-enters a different volume, where I-2/I-3 were never evaluated |
| **I-5** | `os.statvfs(p).f_flag & ST_RDONLY` is identical for every directory `p` in the subtree (excluding carve-outs) | this is how I-4's boundaries are *found* — a firmlink or mount point shows up as a flag change |
| **I-6** | every non-regular, non-directory node that is writable is a character or block device (`/dev`), never a regular file | device nodes hold no persistable file content; this is the one narrow exception, and it is named rather than assumed |

Escaping symlinks are **recorded but are not hits**: seatbelt evaluates the *resolved* target
against the profile, so a symlink out of an IMM root reaches the target's own class or is denied.
Verified on this host (see the iteration-2 evidence table, row *symlink indirection*).

`carve_outs` is not a discretionary `--ignore`. It is the enumerated boundary set from I-4/I-5, and
**every carve-out is emitted as an explicit `(deny file-read* file-read-metadata (subpath …))`
clause in the profile** (G.4 clause 4). A boundary that is carved out of the proof but not denied
in the profile is exactly the F-001 defect in a new place, so the two lists are generated from one
variable and `assert_carve_outs_denied()` compares them before the profile is written.

Boundaries are *found* by I-5 and *cross-checked* against two cheap authorities, both read at
session-build time and both recorded in `ISOLATION.json`:

* the mount table (`/sbin/mount`, parsed for mount point + `read-only`), and
* `/usr/share/firmlinks` on darwin — the OS's own system-volume→data-volume firmlink table.

If I-5's walk finds a boundary that neither authority names, that is a hard failure, not a warning.

##### G.3.2 The Class IMM default list, as proven on this host

The iteration-1 list is **not** the list. `/private/var` and `/Library` are removed wholesale;
`/usr` and `/System` are admitted only with their carve-outs; `/private/var/select` is added
because the pre-flight probe proved it necessary and the proof passed on it.

| root | I-2/I-3 result | carve-outs required | verdict |
|---|---|---|---|
| `/` | — | admitted as `(literal "/")` only, never `(subpath "/")` | IMM (literal) |
| `/bin` | 1 dir, 37 files, 0 writable | none | **IMM** |
| `/sbin` | 1 dir, 74 files, 0 writable | none | **IMM** |
| `/private/etc` | 31 dirs, 230 files, 0 writable | none | **IMM** (note: on a *writable* volume — see below) |
| `/usr` | 1,173 dirs, 21,796 files, 0 writable | `/usr/local`, `/usr/libexec/cups`, `/usr/share/snmp` | **IMM with carve-outs** |
| `/System` | 169,297 dirs, 286,743 files, 0 writable | `/System/Volumes` **(critical)**, `/System/Library/Caches`, `/System/Library/Assets`, `/System/Library/AssetsV2`, `/System/Library/PreinstalledAssets`, `/System/Library/PreinstalledAssetsV2`, `/System/Library/Speech`, `/System/Library/CoreServices/CoreTypes.bundle/Contents/Library` | **IMM with carve-outs** |
| `/dev` | 3 dirs (`/dev`, `/dev/fd`, `/dev/monotonic`), none writable; 407 char + 37 block nodes; the only writable regular files are `/dev/fd/1`, `/dev/fd/2` — the probing process's own stdout/stderr | none | **IMM by I-6** |
| `/private/var/select` | 1 dir, 0 writable files | none | **IMM** (added: the `xcrun` shim `readlink`s `/var/select/developer_dir`) |
| `/Library/Developer/CommandLineTools` | 27,983 dirs, 109,959 files, 0 writable | none | **IMM** (needed: `git` and the `/usr/bin/python3` shim resolve here) |
| **`/private/var`** | contains `/private/var/tmp`, `/private/var/run/…`, `/private/var/protected/sfanalytics`, and **`tempfile.gettempdir()`**, all user-writable | — | **REMOVED — this is F-001** |
| **`/Library`** | `/Library/Caches`, `/Library/Fonts`, `/Library/Receipts`, `/Library/Logs/DiagnosticReports`, `/Library/Frameworks/Python.framework/Versions/3.11` all user-writable | — | **REMOVED wholesale**; only the proven `CommandLineTools` subtree is admitted |

Two results in that table are the reason the proof is written as *"exhaustive walk"* and not
*"read-only mount"*:

* **`/private/etc` sits on a read-write volume** (`statvfs().f_flag & ST_RDONLY` is false, because
  `/private` is firmlinked to the data volume) **yet passes I-2/I-3 exhaustively.** A mount-flag
  test alone would wrongly reject it.
* **`/System` is a sealed read-only mount, and that is still not sufficient**, because
  `/System/Volumes/Data` is a *mount point of the writable data volume nested inside it*. It is
  **not a symlink**, so `os.path.realpath()` does not collapse it: `os.path.realpath(
  "/System/Volumes/Data" + tmpdir)` returns the aliased spelling unchanged, and
  `os.stat()` confirms the same `(st_dev, st_ino)`. The whole of `/Users`, `/private/var` and the
  repository is reachable by that spelling. The mount flag is therefore **corroborating evidence
  recorded in the attestation, never the proof**.

Measured cost of the full proof on this host: `/System` 13.0 s, `/Library/Developer/CommandLineTools`
5.1 s, `/usr` 0.7 s, everything else < 0.1 s — under 20 s in total, once per session. There is no
performance argument for the root-only shortcut that produced F-001.

##### G.3.3 Class USR

Everything else that must be readable, admitted **only** as an exact path and always scanned:

* `<SESSION>/review_root` (always);
* `<SESSION>/tmp` and `<SESSION>/home` — the session-scoped `TMPDIR` and `HOME` (G.2, G.5). These
  exist *because* the host's per-user temp and home are no longer admitted: the agent still needs a
  writable scratch and config root, and giving it a session-private one is what makes removing
  `/private/var` and `/Library` survivable. They are created empty by `isolate`, so their scan is
  trivially clean, and they are destroyed with the session;
* any further root the pre-flight probe proves necessary, supplied by an explicit `--allow-read`.

**Not admitted, and named here so a future edit cannot re-add them by habit:** the host
`tempfile.gettempdir()` (or any ancestor of it), `$HOME`, `~/Library/Caches`, `/private/var`,
`/private/tmp`, `/Library`, `/opt/homebrew`, `/Users`, `/Applications`, `/System/Volumes/Data`.

**The pass table below is the definition of every pass, for both classes.** It is stated inside
G.3.3 because Class USR is where the passes first run — **every Class USR root is scanned
exhaustively** by `scan_readable_set()` at session-build time, with the full set A/B/C/D/S. It is
**not** a Class-USR-only table: NEG-5 (G.9) runs the same `scan_readable_set()` over every admitted
root, with **A/B/C/D** for Class IMM and **A/B/C/D/S** for Class USR. The per-pass rows say which
class each pass applies to wherever the two differ.

| pass | what it does | a hit means |
|---|---|---|
| **A — name** | walk the root; any file whose basename is `answer_key.json`, or whose path contains a component named `key` or `adjudications` **under a directory that also contains a `subject/`** | a fixture tree is reachable |
| **B — content** | open every regular file the walk reaches, normalise whitespace, casefold, and test it against the key's vocabulary — the existing D.6 token/shingle/expected-count test, but driven from **this walk** rather than from `scan_leak()`'s own `rglob`, so that it prunes the same carve-outs as A/C/D and follows no symlink. **Mandatory for every admitted root, IMM and USR alike (iteration 5).** The *vocabulary* is per class and is the only thing that differs: Class USR matches the full `key_leak_tokens()` set plus the two expected-count regexes; Class IMM matches `key_material_tokens()` — the key's **content** vocabulary (fixture id, archetypes, prose shingles, and the *identifier-form* markers `answer_key` / `seeded_defect` / `expected_finding_count`) without its **natural-language** vocabulary (`answer key`, `seeded defect`, `expected finding`, `seeded`, `정답`, `시드`) and without the defect ids (`sd-1`…`sd-5`) or the count heuristics — 712 tokens against 723. Both are exhaustive over files. See **D-5.1**, which measures why a literally-whole pass B over an OS tree is not a gate. | key material is reachable |
| **C — key digest** | any file whose sha256 equals the answer key file's sha256. **Size prefilter (iteration 4):** a file whose `st_size` differs from the key file's byte length can never have the key's sha256, so only size-matching files are opened and hashed. This is an *equivalence*, not an approximation — it detects exactly the set the unfiltered pass detects — and it is what makes pass C affordable over a Class IMM root. | a renamed copy is reachable |
| **S — escaping symlink** | a symlink whose realpath leaves the root — an allowed read path the walk did not cover. **Class USR only** (recorded here in iteration 4 to match what ships): for a Class IMM root the *profile* is the evidence, seatbelt evaluates the resolved target, so such a link reaches the target's own class or is denied on the target's own terms. | the scan did not cover an allowed read path |
| **D — archive members** | for every `*.tar`, `*.tar.gz`, `*.tgz`, `*.zip` under the root, list members with `tarfile`/`zipfile` (**names only — members are never extracted and never read**) and match member names by pass A's rule | a packaged copy is reachable (this is what catches `dist/orca-skills-*.tar.gz`) |

A hit in any pass is a **hard failure** (exit 4) with the offending path printed. There is
deliberately no `--ignore` flag: the operator's remedy is to remove the copy, or to stop allowing
that root and re-run the pre-flight probe. Pass D reads only the archive's index; if an archive is
unreadable or malformed it counts as a hit, because an archive whose contents cannot be enumerated
cannot be certified clean.

Class IMM roots are content-scanned like every other admitted root. What differs is only *when*
and with *which vocabulary*. The scan is not run a **second** time at session-build time, because
NEG-5 already runs it over **the profile's own admitted set**, after the profile has been computed —
strictly better evidence than a build-time scan of the candidate list, because NEG-5's whole job is
to not trust the classification it is checking — and running the same read of tens of gigabytes
twice per capture would double the one cost that now dominates a capture (measured in **D-5.1**).

**The immutability proof is not a substitute for that scan and is nowhere offered as one.** G.3.1's
I-1…I-6 establish exactly one thing: *no unprivileged process can create or overwrite readable
content anywhere in the subtree*, evaluated against the run user's privileges at session-build time.
It says nothing whatever about content that was **already there** when the proof ran, because it
never opens a file. Pass B is what covers a reformatted or partial rendering of the key; pass C is
what covers a byte-identical copy; the proof is what makes both durable, by establishing that
nothing unprivileged can add such content after the scan.

`ISOLATION.json` records, per root, the full proof counters
(directories walked, files walked, writable directories, writable regular files, boundaries found,
carve-outs applied) — not a single `W_OK` boolean. A root whose proof fails is **not** admitted as
IMM: it is either narrowed by a carve-out and re-proven, demoted to Class USR and content-scanned,
or dropped. The residual limitation is now precisely one sentence: *the proof is evaluated at
session-build time against the run user's own privileges, so it does not bind a privileged
(root) writer* — and that writer is outside the G.1 threat model. That sentence, and nothing
broader, is what `ISOLATION.json.limitations[]` carries.

**Iteration 5 (DESIGN review F-001), superseding iteration 4's pass set.** NEG-5 re-runs the scan
**over the resolved readable set the profile actually grants**, and there its job is precisely *not*
to trust this classification — that is what makes it the check the IMPLEMENTATION review's F-001
would have failed. G.9's NEG-5 row
therefore runs passes **A, B, C, D** over each Class IMM root and **A, B, C, D, S** over each Class
USR root. Pass B over Class IMM is **required, on by default, and not gated behind a flag**;
iteration 4's optional `--scan-imm-content` is removed, because a content-cleanliness gate that is
off by default is not a gate. `## DESIGN iteration 5` records the one thing that is per class — the
vocabulary — and the measurement that forces it.

Bounding: every pass is driven by **one shared** `os.walk(..., followlinks=False)` that prunes the
root's carve-outs and skips nothing else, and no pass ever follows a symlink — pass B included,
which is exactly why iteration 5 drives it from this walk instead of from `scan_leak()`'s own
`rglob` (**D-5.1**). An escaping symlink is a hit under **pass S, and pass S is Class USR only**, as
its row above states; for a Class IMM root the *profile* is the evidence, because seatbelt evaluates
the resolved target, so such a link reaches the target's own class or is denied on the target's own
terms. Refusing to follow symlinks costs no content coverage in either class: a link that stays
inside the root points at a file this walk reads directly, and a link that leaves it is a pass-S hit
(Class USR) or is resolved by the profile against the target's own class (Class IMM).

#### G.4 The scope profile — exact generated text, and the ordering rule

Backend `seatbelt` (darwin). `render_seatbelt_profile(imm, usr, carve_outs, writable, denied)`
emits exactly this, in exactly this order; **seatbelt is last-match-wins, so the order is the
semantics**:

> **Superseded in iteration 2 (F-001).** Two structural changes against the iteration-1 skeleton:
> (a) clause 2's *global* `(allow file-read-metadata)` is replaced by a **closed traversal set**,
> so the profile's metadata surface can never exceed its data surface; (b) clause 4 is new — every
> carve-out from G.3.1's proof is denied explicitly. Without (a), the corrected readable set still
> let a sandboxed process `os.stat()` the planted key copy and learn its exact size (measured:
> `exists() == True`, `st_size == 9347`); with (a), `exists()` returns `False` and `stat` raises
> `PermissionError`. Both measurements are in the iteration-2 evidence table.

```scheme
(version 1)
;; Generated by scripts/review_isolation.py -- do not edit. Session: <SESSION>
(allow default)

;; 1. Deny every file read -- data AND metadata. Allowlist, not denylist: a profile
;;    that denied only the repository is defeated by any copy of the key outside it.
(deny file-read*)

;; 2. Traversal set: metadata ONLY, on the exact path components needed to resolve
;;    the readable set, plus the root-level symlinks that spell them (/var, /etc,
;;    /tmp) and /dev. Computed as
;;      {ancestors(r) for r in readable} u {root symlinks resolving into readable}
;;    -- NOT a global allow. A global (allow file-read-metadata) makes every path on
;;    the machine stat-able and existence-checkable; that is a discovery channel, and
;;    NEG-7 fails on it.
(allow file-read-metadata
    (literal "/") (literal "/usr") (literal "/private") (literal "/private/var")
    (literal "/var") (literal "/etc") (literal "/tmp")
    (literal "/System") (literal "/System/Library")
    (literal "/Library") (literal "/Library/Developer")
    (subpath "/dev"))

;; 3. The readable set: one (subpath "<realpath>") per Class IMM root (G.3.2) and per
;;    Class USR root (G.3.3). file-read* is data + metadata, so nothing here can be
;;    readable-but-not-stat-able or the reverse. "(literal \"/\")" is required or dyld
;;    aborts at exec.
(allow file-read*
    (literal "/")
    (subpath "/usr") (subpath "/bin") (subpath "/sbin") (subpath "/System")
    (subpath "/private/etc") (subpath "/private/var/select") (subpath "/dev")
    (subpath "/Library/Developer/CommandLineTools")
    (subpath "<SESSION>/review_root")
    (subpath "<SESSION>/tmp")
    (subpath "<SESSION>/home")
    …one per additional --allow-read root, each USR-scanned…)

;; 4. Carve-outs: every boundary G.3.1's proof excluded, denied for data AND metadata.
;;    Generated from the same variable as the proof's carve_out list;
;;    assert_carve_outs_denied() refuses to write the profile if the two differ.
;;    "/System/Volumes" is the load-bearing one: /System/Volumes/Data is a mount point
;;    of the writable data volume nested inside the sealed system volume, and it is not
;;    a symlink, so realpath() does not collapse it.
(deny file-read* file-read-metadata
    (subpath "/usr/local") (subpath "/usr/libexec/cups") (subpath "/usr/share/snmp")
    (subpath "/System/Volumes")
    (subpath "/System/Library/Caches") (subpath "/System/Library/Assets")
    (subpath "/System/Library/AssetsV2") (subpath "/System/Library/PreinstalledAssets")
    (subpath "/System/Library/PreinstalledAssetsV2") (subpath "/System/Library/Speech")
    (subpath "/System/Library/CoreServices/CoreTypes.bundle/Contents/Library"))

;; 5. Writes: deny everything, then name the writable set. The session-scoped tmp/home
;;    are what let clause 3 drop the host's /private/var and /Library entirely.
(deny file-write*)
(allow file-write*
    (subpath "<SESSION>/review_root")
    (subpath "<SESSION>/tmp")
    (subpath "<SESSION>/home")
    (subpath "/dev"))

;; 6. The key-bearing roots, denied for BOTH data and metadata, after everything else
;;    so that existence itself is hidden. Redundant with (1) for reads; kept because a
;;    future edit that widens (3) must not silently widen these, and because it makes
;;    the profile self-documenting about what it is protecting.
(deny file-read-metadata
    (subpath "<repo realpath>")
    …one per key-bearing root discovered in G.3…)
(deny file-read* file-read-metadata file-write*
    (subpath "<repo realpath>")
    …one per key-bearing root discovered in G.3…)
```

**Aliases need no separate enumeration, and this was measured rather than assumed.** seatbelt
canonicalizes a firmlinked spelling to the same vnode before matching, so the clause-6 deny on the
repository realpath also denied `/System/Volumes/Data/<repo>/…/answer_key.json`, and clause 4's
`/System/Volumes` deny covered the aliased spelling of the planted temp copy. The design therefore
does **not** generate per-alias deny clauses — but G.9's **NEG-8** probes the aliases anyway,
because "the sandbox canonicalizes" is a property of this OS release, not of this design.

Verified live: with clause 6 present, `os.path.exists(<key path>)` inside the sandbox returns
`False`, `os.stat` and `os.listdir` return `Operation not permitted`, `open()` returns
`Operation not permitted`, and `git -C <repo> show HEAD:<path>` fails. Without clause 6's
`file-read-metadata` line, `os.stat` succeeded — so that line is load-bearing and must not be
dropped as "redundant". The same is true of clause 2 being a closed set rather than a global
allow, for the same reason applied to every *other* path on the machine.

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
  --command "cd <SESSION>/review_root && TMPDIR=<SESSION>/tmp HOME=<SESSION>/home exec /usr/bin/sandbox-exec -f <SESSION>/control/scope.sb <resolved agent command>"
```

Why this is correct rather than a trick:

* **`TMPDIR` and `HOME` are set to the session-scoped directories** (iteration 2, F-001). They are
  part of the launch line rather than of `agent_command`, for the same reuse-gate reason the
  sandbox wrapper is. Two host facts make this mandatory rather than cosmetic: with the corrected
  profile and the host `HOME`, `git` fails on `~/.gitconfig`; with the session `HOME`, `git
  --version` exits 0 and `git -C <repo> show HEAD:VERSION` still fails, which is exactly the
  intended pair of outcomes. One residual is recorded honestly: the `/usr/bin/xcrun` shim resolves
  its cache directory through `confstr(_CS_DARWIN_USER_TEMP_DIR)`, which ignores `TMPDIR`, so it
  emits a non-fatal `couldn't create cache file` line on stderr. It is a diagnostic, not a failure
  — measured: the shimmed `/usr/bin/python3` and the resolved
  `/Library/Developer/CommandLineTools/usr/bin/git` both run correctly through it. The pre-flight
  probe must classify that specific line as benign, and **must not** be "fixed" by granting write
  access to the host per-user temp directory (tried; it does not even help — the shim still fails —
  and it would re-admit a host-wide mutable path).
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
    {"class": "IMM", "path": "/usr", "scanned": false,
     "proof": {"dirs": 1173, "files": 21796, "writable_dirs": 0, "writable_files": 0,
               "boundaries_found": 3, "carve_outs": ["/usr/local", "/usr/libexec/cups",
                                                     "/usr/share/snmp"],
               "mount_readonly": true, "escaping_symlinks": 41}},
    {"class": "USR", "path": "<REDACTED:foreign_absolute_path>",
     "scanned": true,
     "scan": {"files": 412, "archives": 0, "hits": 0}}
  ],
  "carve_outs_denied": ["/usr/local", "/System/Volumes", "…"],
  "no_unscanned_descendant": "PASS",
  "traversal_set": ["/", "/usr", "/private", "…"],
  "writable_set": ["<REDACTED:foreign_absolute_path>"],
  "denied_roots": ["<REDACTED:foreign_absolute_path>"],
  "key_bearing_roots_discovered": 2,
  "properties": {"S1": "PASS", "S2": "PASS", "S3": "PASS"},
  "probes": [
    {"id": "NEG-0", "kind": "positive_control", "result": "PASS"},
    {"id": "NEG-1", "kind": "review_root_walk",  "result": "PASS", "hits": 0}
  ],
  "threat_model": "unconstrained well-behaved reviewer agent; not a sandbox-escaping adversary",
  "limitations": [
    "The recursive immutability proof is evaluated at session-build time against the run user's own privileges, so it does not bind a privileged (root) writer, who is outside the stated threat model"
  ]
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
* `readable_set[].scanned: false` is only ever legal for `class: "IMM"` **and** only when that
  entry carries a complete `proof` object whose `writable_dirs` and `writable_files` are both `0`.
  Enforced when the document is written. `class: "SYS"` is not a legal value any more — the
  iteration-1 name is retired deliberately, so a half-migrated writer fails loudly instead of
  producing a document that looks valid.
* **`scanned: false` means *not at session-build time*, and nothing more.** It is **not** a
  statement that the root went uncontent-scanned: every admitted IMM root is content-scanned by
  the mandatory passes A/B/C/D at NEG-5 (G.3, G.9, **D-5.1**), and
  `probes[NEG-5].roots[]` carries that root's `passes`, `vocabulary` and `content_scanned` count.
  A reader needs both fields; neither is the whole answer on its own. The `limitations[]` entry is
  correspondingly narrow: it is about the **immutability proof's durability** — a privileged
  writer, outside the G.1 threat model — and it makes no content claim, because content is
  established independently at NEG-5.
* `no_unscanned_descendant` records the G.3 invariant check by name. It is the field a reader
  should look at first, because it is the one F-001 would have set to `FAIL`.

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
| **NEG-5** | The G.3 readable-set scan over **every admitted root, IMM and USR alike**, re-run against the resolved readable set the profile grants, with each root's carve-outs pruned. (**Wording corrected in iteration 5**, because D-5.1 leans on what this probe actually does: NEG-5 is an *in-process* re-scan inside `run_probes()`, not a sandboxed subprocess like NEG-2/NEG-3/NEG-4. Its independence comes from running against the **computed** readable set rather than the candidate list, not from being sandboxed. No behaviour changes; the earlier "from inside the sandbox" phrasing simply did not describe the code.) (Iteration 1 re-scanned Class USR only, which is why it could not have caught F-001.) **The pass set is stated here rather than left to IMPLEMENTATION, and iteration 5 settles it:** Class USR gets **A, B, C, D, S**; Class IMM gets **A, B, C, D**. **Pass B is mandatory over Class IMM** — no flag, no default-off path — because the immutability proof bounds writes and says nothing about pre-existing content, and pass C alone catches only a byte-identical copy. The single per-class difference is pass B's **vocabulary** (`key_material_tokens()` for IMM, `key_leak_tokens()` for USR), measured and justified in **D-5.1**. Pass **S is Class-USR-only**, justified in G.3.3's pass table. The per-root pass list **and vocabulary** are recorded in the NEG-5 probe record. | zero hits in every pass the root's class runs |
| **NEG-6** | Profile integrity: `sha256(scope.sb)` equals `ISOLATION.json.profile_digest`, and the profile parses (`sandbox-exec -f scope.sb /usr/bin/true` exits 0). | both hold |
| **NEG-7** | **The writable-descendant probe (iteration 2, F-001).** Plants, in the current user's *resolved* mutable roots, (a) a byte-identical copy of the answer key and (b) a second file carrying the fixture's real seeded-defect identity strings. Then runs `open`, `os.path.exists`, `os.stat`, `os.listdir` on the plant and on its parent, `os.listdir` on the temp root itself, `/bin/cat`, `/bin/ls`, `/usr/bin/find -name answer_key.json`, `/usr/bin/grep -rl <key content marker>`, a read through a **symlink placed inside `review_root` pointing at the plant**, and a read through the plant's `/System/Volumes/Data` alias. | **every one is denied**: `open`/`stat`/`listdir`/`cat`/`ls` raise `PermissionError` or exit non-zero, `os.path.exists` returns `False`, and `find`/`grep` produce **no stdout** |
| **NEG-8** | **Alias probe.** For every denied root and every carve-out, the same read/stat/list battery through each alias spelling derived from the mount table and `/usr/share/firmlinks` — in particular `/System/Volumes/Data<path>`. | all denied, exactly as the canonical spelling is |

**The NEG-7 plant sites are resolved, never assumed.** The probe writes to
`os.path.realpath(tempfile.gettempdir())` and to `os.path.realpath(os.path.expanduser(
"~/Library/Caches"))` — the actual values on the running host, because the whole point of F-001 is
that an *assumed* path (`/tmp`, `$TMPDIR` as spelled in the environment) is not the path the
allowlist actually admitted. `/private/var/folders/<hash>/T` is a per-user, per-boot path; a test
that hard-codes any part of it tests nothing. Both plants are removed in a `finally:` block, and
`isolate` refuses to start if a previous run's plant directory still exists — leaving a real
answer-key copy on disk would be a worse outcome than the bug.

**NEG-7's positive control is NEG-0's, extended.** Before asserting any denial, the *same probe
script* runs unsandboxed and must report every one of those reads as succeeding, with the plant's
sha256 equal to the answer key's. A denial result is only meaningful paired with a demonstration
that the identical operation succeeds without the sandbox.

**The discovery oracle is stated explicitly, because the obvious one is wrong.** A discovery is
*successful, non-empty stdout* — never "the probed path appears somewhere in the output". Measured:
under the corrected profile `find <plant dir> -name answer_key.json` exits 1 and prints
`find: <plant dir>: Operation not permitted` **on stderr**, which contains the plant path verbatim.
A substring oracle reads that as a leak. The oracle is therefore `rc == 0 and stdout.strip()` for
the command probes, and for content probes it matches a marker that exists only inside the key's
bytes (`final_review_eval/v1`), never a path fragment.

NEG-2 … NEG-4 — **and only those three** — run the probe as a *separate process launched by exactly
the same `wrap_command()` used for the real dispatch* — not by re-implementing the launch line in
the test. A negative test that builds its own launch line proves something about the test, not
about the dispatch.

**NEG-5 is not one of them, and this is the authoritative statement of that** (corrected in
iteration 5; see §D′). `run_probes()` executes in the parent process, and NEG-5 calls
`scan_readable_set()` **in-process**, once per entry of the computed readable set, with each root's
carve-outs pruned. It launches nothing through `wrap_command()` and it is not sandboxed. Its
independence comes from scanning the **computed** admitted set — after classification, so a
wrongly-classified root is still scanned and a planted copy in it is still a hit — not from a
process boundary. NEG-6 likewise runs in the parent (it parses the profile with `sandbox-exec -f …
/usr/bin/true`); NEG-7 and NEG-8 do use sandboxed probes.

Under `--enforcement none`, NEG-2 … NEG-8 are **not** recorded as `SKIP`; they are recorded as
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
2. **Residue check — every residual match is already its own output.**

   > **Corrected in iteration 4 (IMPLEMENTATION review F-001/F-002).** The iteration-1 rule read
   > *"`again, extra = redact_text(candidate)`; require `again == candidate` **and**
   > `extra == ()`"*. The second clause is **not a stricter version of the intended property; it
   > is a different property**, and `redaction/1.1` cannot satisfy it on exactly the inputs this
   > gate exists for. See `## DESIGN iteration 4` for the derivation and the measured
   > counterexamples.

   For every `(name, pattern, replacement)` in `REDACTION_CATEGORIES`, in policy order, **every**
   match `m` of `pattern` in `candidate` must satisfy `m.expand(replacement) == m.group(0)`. In
   words: the second pass may still *recognise* text, but it must have nothing left to *remove*.
   This is the whole security statement, and it is decided per match rather than by comparing two
   whole strings, so no combination of matches elsewhere in the text can mask one that removed
   something.

   `again == candidate` is an immediate consequence (a substitution whose every replacement equals
   its own span cannot change the string), so the text fixed point does not need to be asserted
   separately — but asserting it costs nothing and IMPLEMENTATION may keep it as a redundant
   check.

   Because category 5 (`foreign_absolute_path`) matches *every* absolute POSIX path with no
   minimum segment count and replaces it whole, and category 4 owns the three home spellings,
   satisfying this rule means **no absolute path, no `dcap_…`, no `scheme://user:pass@`, and no
   `SECRET`/`TOKEN`/`PASSWORD`/`API_KEY`-named environment assignment survives in a form the
   policy could still remove**. That is a closed statement over the policy's own categories, not
   an inspection.

   Verified for the concrete cases: `/Users/<u>/x/y`, `/private/tmp/claude-501/foo`, `/luminous`,
   `file:///Users/<u>/a`, `AWS_SECRET_ACCESS_KEY=…`, `https://u:p@h/p`, `dcap_…` all satisfy the
   rule after one pass, and `<REPO>/scripts/x.py` is untouched. Two of those seven —
   `AWS_SECRET_ACCESS_KEY=…` and `https://u:p@h/p` — report `extra=(env_secret_pattern, 1)` and
   `extra=(url_credential, 1)` respectively while removing nothing, which is why the deleted
   `extra == ()` clause was unsatisfiable rather than merely strict.
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

Iteration 2 (F-001) adds to this module: `prove_immutable(root, carve_outs)` (G.3.1 I-1…I-6),
`enumerate_boundaries()` (mount table + `/usr/share/firmlinks` + the `statvfs` flag-change walk),
`assert_carve_outs_denied()`, `assert_no_unscanned_descendant()`, `compute_traversal_set()` (G.4
clause 2), and the session-scoped `tmp/`+`home/` creation and `TMPDIR`/`HOME` launch-line
environment. `render_seatbelt_profile()` changes signature to take the carve-out and traversal
sets. The `SYS` class name is replaced by `IMM` throughout, including in `ISOLATION.json`.

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
| T-7.3 | `result="GITHUB_TOKEN=ghp_deadbeef…"` | `ghp_deadbeef…` absent; `env_secret_pattern ≥ 1`; **the log is embedded, not omitted** — `content_redacted is not None` and `content_omitted_reason == ""`. *(iteration 4: this is one of the two rows the deleted `extra == ()` clause made unsatisfiable; the residual `env_secret_pattern` match on the second pass expands to itself and is therefore not residue.)* |
| T-7.4 | `detail="https://user:hunter2@example.test/x"` | `hunter2` absent; `url_credential ≥ 1`; **the log is embedded, not omitted** — `content_redacted is not None` and `content_omitted_reason == ""`. *(iteration 4: the second of the two rows above; the residual `url_credential` match expands to itself.)* |
| T-7.5 | `detail="dcap_AAAABBBBCCCC…"` | the literal absent; `orca_dispatch_capability ≥ 1` |
| T-7.6 | a clean log | `redactions == []`, `content_redacted == raw`, `digest_post_redaction == digest_pre_redaction`, `content_omitted_reason == ""` |
| T-7.7 | identity/auditability | `digest_pre_redaction == sha256(local file)` and `digest_post_redaction == sha256(redact_text(local file)[0])`, recomputed independently in the test |
| T-7.8 | table structure | for every poisoned case, the embedded text has the same line count as the raw log and the same per-line `|` count. *(iteration 4: "the embedded text" presupposes every poisoned case is embedded — T-7.3/T-7.4 included — which the corrected H.2 step 2 makes true and the iteration-1 rule did not.)* |
| T-7.9 | residue path. *(iteration 4: the monkeypatch target moves from `redact_text` to `REDACTION_CATEGORIES`, because the corrected step 2 iterates the category tuple directly and a stubbed `redact_text` would no longer drive it. Inject one synthetic category whose replacement does not expand to its own span — e.g. `("t", re.compile(r"ZZ_[A-Z]+"), "<REDACTED:t>")` — over a log containing `ZZ_LEAK`.)* | `content_redacted is None`, `content_omitted_reason == "redaction_residue"`, `digest_pre_redaction` still present, `integrity["omitted_content"]` names the log, **and the export still returns a written path** |
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
| T-8.5 | `prove_immutable()` **rejects** a root containing a writable descendant at any depth (not merely a writable root), and such a root is then either carved out and re-proven, demoted to USR and scanned, or dropped — never admitted unproven. Includes the F-001 shape directly: a root whose own `W_OK` is false but which contains a writable descendant |
| T-8.5b | `enumerate_boundaries()` finds every `statvfs` flag-change inside a candidate root and cross-checks it against the mount table and `/usr/share/firmlinks`; a boundary named by neither is a hard failure |
| T-8.5c | `assert_carve_outs_denied()` refuses to write a profile whose deny clauses do not exactly cover the proof's carve-out list; `assert_no_unscanned_descendant()` refuses an admitted proper ancestor of an unproven mutable path |
| T-8.6 | `render_seatbelt_profile()` emits the six clauses in G.4's order; the metadata surface is a closed traversal set rather than a global allow (a generated profile containing `(allow file-read-metadata)` with no operand list is a test failure); a generated profile parses (`sandbox-exec -f … /usr/bin/true` exits 0) |
| T-8.7 | `ISOLATION.json` validates: no clock value; every path field is P1/P2/P3/P4 under `assert_retained_path_field`; `scanned: false` only for `class: "IMM"` **and** only with a complete `proof` object whose `writable_dirs` and `writable_files` are `0`; `class: "SYS"` is rejected outright; `no_unscanned_descendant` is present; `properties` has three independent verdicts |
| T-8.8 | `--enforcement none` records `scope_enforcement: "unenforced"`, `S2: "FAIL"`, probes `NOT_APPLICABLE_UNENFORCED`, and exits 0 with a stderr warning |
| T-8.9 | `--enforcement seatbelt` on a host with no `sandbox-exec` → exit 4 |
| T-8.10 | `--repatriate` refuses a byte-differing copy and refuses an existing differing destination |
| T-8.11 | `--teardown` refuses a directory that is not a `frv_iso_` session with `control/ISOLATION.json` |

### T-9 The negative test — `scripts/test_review_isolation.py` (D-G, G.9)

NEG-0 … NEG-6 exactly as specified, against a temporary fixture that plants a real key file. Skipped
as a whole with an explicit reason on a non-darwin host (`sys.platform != "darwin"`), and **T-8.9
is the test that then carries the fail-closed guarantee**, so the guarantee is never merely
unasserted. NEG-0 runs first inside each of NEG-2 … NEG-4's test method, not as a separate test.

**T-9.7 … T-9.9 (iteration 2, F-001), all in one test method so the control cannot be skipped:**

* **T-9.7** — NEG-7 end to end against a *synthetic* key fixture: resolve
  `tempfile.gettempdir()` and `~/Library/Caches`, plant the byte-identical key copy plus the
  seeded-defect identity file, assert the unsandboxed control reads both, then assert the exact
  `wrap_command()` launch line cannot `open`/`stat`/`exists`/`listdir`/`cat`/`ls`/`find`/`grep` them,
  nor reach them through a `review_root` symlink or the `/System/Volumes/Data` alias. `finally:`
  removes both plants and the test asserts their absence.
* **T-9.8** — NEG-8 alias battery over every denied root and carve-out.
* **T-9.9** — **the regression guard for F-001 itself**: build a profile from the *iteration-1*
  rule (`os.access(root, W_OK)` at the root only, `/private/var` admitted wholesale, global
  metadata allow) and assert that `prove_immutable()` **rejects** it and that
  `assert_no_unscanned_descendant()` raises. This is the test that fails if someone reintroduces
  the shortcut, and it does not depend on a plant surviving on disk.

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
| **B6** scope enforced (**new**) | `FINAL_REVIEW_ISOLATION.json` exists for the accepted dispatch, `scope_enforcement == "seatbelt"`, `properties.S1/S2/S3` all `PASS`, NEG-0 `PASS` and NEG-1…NEG-8 all `PASS`, and `profile_digest` matches the profile the launch line actually used. A capture missing any of these is **not a baseline** — it is recorded as an exploratory run and labelled as one |

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
| **RK-2** | Class IMM roots rest on an immutability proof rather than on content. **Closed in iteration 5:** they are now content-scanned too, by a mandatory pass B at NEG-5 (D-5.1); what is left of this risk is only the privileged-writer boundary below. | **Rewritten in iteration 2 (F-001); the previous mechanism — "admission requires `os.access(root, W_OK) == False`" — was the defect.** Admission now requires G.3.1's recursive proof I-1…I-6 over the whole subtree, every boundary it finds is denied in the profile (G.4 clause 4), `assert_no_unscanned_descendant()` refuses an admitted ancestor of an unproven mutable path, and it is re-proved at every capture: NEG-7/NEG-8 from inside the sandbox, and NEG-5 **in-process** over the profile's own computed admitted set (G.9, iteration 5 §D′ — NEG-5 is not a sandboxed subprocess). What remains is only that the proof is evaluated against the run user's privileges at session-build time, so it does not bind a privileged writer — who is outside G.1's threat model. Stated in exactly those terms in G.3.3 and in `ISOLATION.json.limitations[]`; D-I's `COMPATIBILITY.md` wording is unchanged. |
| **RK-8** | The proof is a session-build-time snapshot: a root proven IMM could in principle be mutated *during* the dispatch by a privileged writer or an OS update. | Same class of residual as **O-2**, and bounded by the same reasoning: it requires privilege the threat model excludes, and the window is one dispatch. `ISOLATION.json` records the proof counters so a re-run on the same host is comparable; a differing count on re-capture is a signal worth investigating, not silently absorbed. Not designed around further, and named rather than implied. |
| **RK-9** | A future contributor "simplifies" G.4 clause 2 back to a global `(allow file-read-metadata)` because the traversal set is fiddly to compute. | NEG-7 fails immediately and loudly on exactly that change — measured: with a global metadata allow and an otherwise-correct readable set, `os.path.exists(plant)` is `True` and `os.stat(plant).st_size` is the key's real size. The comment in the generated profile says so at the point of edit. |
| **RK-3** | `sandbox-exec` is deprecated by Apple and could be removed. | The backend is behind `render_profile()`/`wrap_command()`; `--enforcement` is an enum with a fail-closed default. If the backend disappears, captures fail B6 loudly rather than degrading silently. |
| **RK-4** | The generated profile is too tight and the agent half-works — producing a *worse* review that gets mistaken for a detection signal. | The pre-flight probe runs the real agent command and `orca orchestration check` before any Task is dispatched, and B6's verdict is separate from the review's. Also: the baseline draws no detection conclusion at all, by design. |
| **RK-5** | Someone reads `scope_enforcement: unenforced` as "isolated enough". | It is a distinct enum value, `S2` is `FAIL`, probes read `NOT_APPLICABLE_UNENFORCED` rather than `SKIP`, and B6 fails. Three independent places say the same thing. |
| **RK-6** | A future contributor re-adds `content` to the bundle for convenience. | The key no longer exists; the H.1 rule is in the exporter docstring; T-7.12 asserts `"content" not in bundle["orchestrator_log"]`. |
| **RK-7** | The residue check turns out not to be satisfiable for some real input, making every export omit the log. | **Rewritten in iteration 4.** The iteration-1 cell described this as a hypothetical; it was already true, and the cell's own remedy contradicted *Explicitly not designed*. Both are fixed. (a) The class of input that actually triggered it — a category that re-matches its own placeholder — is **no longer residue at all** under the corrected H.2 step 2, because such a match expands to its own span. That is the whole of `env_secret_pattern` and `url_credential`, i.e. every observed instance. (b) For a genuine residue — a match that expands to *different* bytes — the designed response is the one already built and unchanged: the affected value is omitted with `content_omitted_reason: "redaction_residue"`, the digests are kept, `integrity.omitted_content[]` records it, and **the bundle is still written**. Nothing is blocked and no policy change is required to stay safe. (c) Closing such a gap in the policy itself is a **separate work package** under a `redaction/1.x` MINOR bump; it is out of this design's scope, which is what *Explicitly not designed* fences, and that fence is therefore intact. Relaxing the residue check is never the remedy. |

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

---

## DESIGN iteration 2 — correction for F-001

STATUS: COMPLETE

Scope of this iteration: **D-G's Class SYS handling only**. D-H (evidence-bundle sanitization) and
D-I (`COMPATIBILITY.md` wording) were confirmed concretely resolved by
`REVIEW_DESIGN_iteration1.md` and are **not touched** — both stand byte-for-byte at their approved
iteration-1 text, no `COMPATIBILITY.md` replacement block changes, and nothing below depends on
either changing. The narrower residual that the recursive proof earns is recorded only where D-G
owns the text — G.3.3's prose and `ISOLATION.json.limitations[]` (G.6) — and is not carried into
D-I's `COMPATIBILITY.md` wording. *(Corrected in iteration 3 for F-002; see the section at the end
of this document.)*

### Summary / Requirements

F-001 said the Class SYS allowlist admitted `/private/var` wholesale on the strength of
`os.access(root, os.W_OK) == False` at the *root*, while the run user's real
`tempfile.gettempdir()` is a writable descendant of it that G.3 never scanned and NEG-0…NEG-5 never
probed. That is correct, and the reproduction below shows it is not theoretical: under the
unmodified iteration-1 profile a sandboxed process read a byte-identical answer-key copy planted in
that descendant.

The correction has four parts, in the order the review's Required Action names them:

1. **Remove the wholesale grants.** `/private/var` and `/Library` are gone from the admitted list
   entirely (G.3.2). What replaces them is narrow: `/private/var/select` (one directory, proven)
   and `/Library/Developer/CommandLineTools` (proven), plus the *session* paths the runtime
   actually needs — `<SESSION>/review_root`, `<SESSION>/tmp`, `<SESSION>/home`.
2. **Prove immutability recursively instead of checking a root.** `prove_immutable()` (G.3.1)
   walks the entire subtree and requires I-1…I-6: no writable directory anywhere, no writable
   regular file anywhere, every filesystem/firmlink boundary inside it enumerated and either proven
   or carved out, and the one narrow device-node exception named explicitly.
3. **Apply the same analysis to every other root**, not just `/private/var` (§2 below — it turned
   up two further defects the review had not seen).
4. **Add the load-bearing negative test**, NEG-7, plus NEG-8 for aliases and T-9.9 as a regression
   guard against the iteration-1 rule itself (G.9, T-9).

Everything below was executed on this host before being written into the spec, the same way
iteration 1 verified `sandbox-exec`. All planted material was removed afterwards and its absence
re-verified.

### What the same analysis found on the other Class SYS roots

The review asked that `/private/var` not be patched in isolation. Doing the descendant analysis
across the whole iteration-1 list found **two additional defects of the same kind**, both of which
the narrow fix would have missed:

* **`/System` re-exposed the entire data volume, including the answer key itself.**
  `/System/Volumes/Data` is a *mount point*, not a symlink, so `os.path.realpath()` does not
  collapse it, and `(subpath "/System")` therefore reached
  `/System/Volumes/Data<repo>/scripts/fixtures/final_review_eval/key/answer_key.json`. This is
  strictly worse than F-001 as filed: F-001 required someone to plant a copy, this needed nothing
  planted at all. It is closed by the mandatory `(deny … (subpath "/System/Volumes"))` carve-out.
  *(Measured mitigation: seatbelt canonicalizes the firmlinked spelling before matching, so
  iteration-1's clause-5 deny on the repo realpath happened to already deny that particular read.
  That is a property of this OS release, not of the design — which is why the carve-out is
  mandatory and NEG-8 probes aliases explicitly.)*
* **`/usr` was not immutable either**: `/usr/local` (writable — it holds a
  `node_modules/@openai/codex/…` tree and a writable Foxit directory), `/usr/libexec/cups` and
  `/usr/share/snmp` are all firmlinks onto the writable data volume. Carved out.
* **`/Library` failed outright** — `/Library/Caches`, `/Library/Fonts`, `/Library/Receipts`,
  `/Library/Logs/DiagnosticReports` and `/Library/Frameworks/Python.framework/Versions/3.11` are
  all user-writable. Removed wholesale.
* `/bin`, `/sbin`, `/private/etc` and `/dev` **passed** the exhaustive proof and stay.

Two method corrections fell out of that work and are now stated in G.3.1 rather than assumed:

* **`st_dev` is not a boundary test.** `/usr` and `/Library` report the *same* `st_dev`
  (`16777230`) because APFS firmlinks share the fsid. `statvfs().f_flag & ST_RDONLY` does
  discriminate them, and is what I-5 uses.
* **The read-only mount flag is not the proof either.** `/private/etc` sits on a read-write volume
  yet is exhaustively immutable (a flag test would wrongly reject it), while `/System` is a sealed
  read-only mount that nonetheless contains a writable volume's mount point (a flag test would
  wrongly accept it). The exhaustive walk is the proof; the flag is corroborating evidence recorded
  in the attestation. Measured cost of the full proof: **under 20 s per session**, so there is no
  performance argument for the shortcut that caused F-001.

### Empirical verification — the same probe, three ways

One probe script, three launches: unsandboxed (positive control), under the **unmodified
iteration-1 profile**, and under the **corrected profile**. The plant is a byte-identical copy of
`scripts/fixtures/final_review_eval/key/answer_key.json`
(`sha256:953c97482d7e75ede033ff09c30ae2cde6aae5812d38dffe235ba19580be9222`) plus a second file
carrying the fixture's real seeded-defect identities (`SD-1,SD-2,SD-3`), written to the **resolved**
`tempfile.gettempdir()` — `/private/var/folders/<hash>/T/frv_neg7_probe` — and, separately, to the
resolved `~/Library/Caches`.

| probe | NEG-0 control (unsandboxed) | iteration-1 profile | corrected profile |
|---|---|---|---|
| `open(plant)` → sha256 | `953c9748…` (= key) | **`953c9748…` — LEAK** | `PermissionError` |
| read seeded-defect identity file | `defect identity: SD-1,SD-2,SD-3` | **same string — LEAK** | `PermissionError` |
| `os.path.exists(plant)` | `True` | **`True`** | **`False`** |
| `os.stat(plant).st_size` | `9347` | **`9347`** | `PermissionError` |
| `os.listdir(plant dir)` | `['notes.txt','answer_key.json']` | **same listing** | `PermissionError` |
| `os.listdir(real TMPDIR)` | `1548` entries | **`1548` entries** | `PermissionError` |
| `os.listdir("/private/var/folders")` | `2` | **`2`** | `PermissionError` |
| `/bin/cat plant` | prints the key JSON | **prints the key JSON** | `rc=1`, `Operation not permitted` |
| `/bin/ls <plant dir>` | lists both files | **lists both files** | `rc=1`, `Operation not permitted` |
| `find <plant dir> -name answer_key.json` | `rc=0`, path on stdout | **`rc=0`, path on stdout** | `rc=1`, **empty stdout** |
| `grep -rl final_review_eval/v1 <plant dir>` | `rc=0`, path on stdout | **`rc=0`, path on stdout** | `rc=2`, **empty stdout** |
| read via symlink placed inside `review_root` | `9347` bytes | **`9347` bytes** | `PermissionError` |
| read via `/System/Volumes/Data` alias of the plant | `9347` bytes | **`9347` bytes** | `PermissionError` |
| the same battery against `~/Library/Caches/frv_neg7_probe` | all readable | (host `$HOME` was not admitted in either profile) | `open`/`stat`/`listdir` → `PermissionError`, `exists` → `False` |
| **functionality:** `ls review_root` | works | works | works |
| **functionality:** `git --version` | `git version 2.50.1` | (fails on `~/.gitconfig`) | `git version 2.50.1` **with session `HOME`** |
| **functionality:** `git -C <repo> show HEAD:VERSION` | succeeds | fails | fails |
| **functionality:** `/bin/echo`, `python3` startup, reading `review_root/subject` | work | work | work |

An intermediate measurement is worth keeping, because it is the reason G.4 clause 2 changed:
with the corrected *readable set* but iteration-1's **global `(allow file-read-metadata)`** still in
place, content reads were denied but `os.path.exists(plant)` was `True` and
`os.stat(plant).st_size` was `9347`. The task's requirement is that the Reviewer cannot *stat, list
or otherwise discover* the plant, so a global metadata allow does not satisfy it. Replacing it with
the closed traversal set (G.4 clause 2) is what turns that row from `True/9347` into
`False/PermissionError`, and it costs nothing: `/bin/cat`, `python3`, `git` and `review_root` reads
all still work.

Cleanup was verified: both plant directories and the probe session are gone
(`No such file or directory` for all three).

### Components / Interfaces / Data Flow — what changed, precisely

| location | iteration-1 | iteration-2 |
|---|---|---|
| **G.3** classification | `SYS` (admitted on root `W_OK`, unscanned) / `USR` (scanned) | `IMM` (admitted on the recursive proof I-1…I-6, not additionally scanned at session-build time) / `USR` (scanned). `SYS` is retired as a value so a half-migrated writer fails loudly. **Iteration 2's rationale for the build-time omission — *"because planting is impossible"* — is superseded by iteration 5:** the proof establishes current write incapability only, and Class IMM is content-scanned by mandatory passes A/B/C/D at NEG-5 (**D-5.1**). What survives from iteration 2 is the *classification*, not that rationale. |
| **G.3** invariant | implicit | explicit *no-unscanned-descendant* invariant + `assert_no_unscanned_descendant()`, exit 4 |
| **G.3** admitted list | `/usr /bin /sbin /System /Library /private/etc /private/var /dev` | `/bin /sbin /private/etc /dev /private/var/select`, `/usr`−carve-outs, `/System`−carve-outs, `/Library/Developer/CommandLineTools`; **`/private/var` and `/Library` removed** |
| **G.3** boundaries | not considered | `enumerate_boundaries()` — mount table + `/usr/share/firmlinks` + the `statvfs` flag-change walk; an unexplained boundary is a hard failure |
| **G.4** clause 2 | global `(allow file-read-metadata)` | closed traversal set; metadata surface ⊆ data surface |
| **G.4** clause 4 | did not exist | carve-out denies, generated from the proof's own list, checked by `assert_carve_outs_denied()` |
| **G.2 / G.5** | session held `review_root` + `control` | adds `tmp/` and `home/`; launch line sets `TMPDIR`/`HOME` to them |
| **G.6** attestation | `{"class":"SYS","user_writable":false,"scanned":false}` | `{"class":"IMM","scanned":false,"proof":{…counters…}}` + `carve_outs_denied`, `traversal_set`, `no_unscanned_descendant` |
| **G.9** | NEG-0…NEG-6; NEG-5 re-scanned Class USR only | NEG-5 re-scans **every** admitted root; **NEG-7** (writable-descendant plant) and **NEG-8** (alias battery) added; the discovery oracle is specified |
| **RK-2** | "admission requires `W_OK == False`" | rewritten around the recursive proof; **RK-8** (proof is a build-time snapshot) and **RK-9** (do not restore the global metadata allow) added |

### Error Handling / Compatibility

No change to failure posture: every new check is fail-closed at exit 4 with the offending path
printed, and a half-built session is still removed. Three new hard-failure conditions join the G.7
table under the same exit code — a failed `prove_immutable()`, a boundary named by neither the
mount table nor `/usr/share/firmlinks`, and a mismatch between the proof's carve-out list and the
profile's deny list.

The `ISOLATION.json` schema stays at `1.0` because it has never been emitted: no attestation exists
on disk, so there is no reader to break. The `class` value changes from `SYS` to `IMM` and
`readable_set[].proof` is added; `scanned: false` now additionally requires a complete `proof`
object with zero writable directories and zero writable regular files.

**No compatibility-document change.** D-I's two `COMPATIBILITY.md` replacement blocks stand exactly
as approved in iteration 1, and D-H is likewise untouched. The narrower residual that the recursive
proof earns — *the proof is evaluated at session-build time against the run user's own privileges,
so it does not bind a privileged (root) writer, who is outside the G.1 threat model* — is recorded
only where D-G owns the text: in G.3.3's prose and verbatim in `ISOLATION.json.limitations[]`
(G.6). It is an attestation field and a design-internal statement, not a public compatibility
claim, so `COMPATIBILITY.md` needs no edit for it. *(Corrected in iteration 3 for F-002.)*

### Expected Changed Files / Implementation Steps

Unchanged from iteration 1 except within two files already on the list:

* `scripts/review_isolation.py` — add `prove_immutable()`, `enumerate_boundaries()`,
  `compute_traversal_set()`, `assert_carve_outs_denied()`, `assert_no_unscanned_descendant()`;
  `render_seatbelt_profile()` takes the carve-out and traversal sets; session `tmp/`+`home/` and the
  `TMPDIR`/`HOME` launch environment; `SYS` → `IMM` throughout.
* `scripts/test_review_isolation.py` — T-9.7 (NEG-7), T-9.8 (NEG-8), T-9.9 (the regression guard
  that asserts the iteration-1 rule is *rejected*).

No new file, no new subcommand, no new exit code. `VERSION`, `LICENSE-DECISION.md`, the fixture
trees, the answer key and `release_manifest.py` remain untouched, and no D-H or D-I file is
reopened.

### Risks / Open Issues

* **RK-2** is rewritten, **RK-8** and **RK-9** are added — all three are in the risk table above.
* **O-2 is partly closed as a side effect.** The session-scoped `HOME`/`TMPDIR` mean an isolated
  agent no longer shares a state directory with non-isolated sessions, which was the mechanism O-2
  described. The residual it named — key material arriving from another session *during* the
  dispatch — no longer has a shared directory to arrive in. O-2 stays on the list because an agent
  configured with an absolute state path via `--allow-read` could still reintroduce it, and that
  root is then subject to the G.3 scan.
* **New, and named rather than implied:** the readable set is now tight enough that a different
  reviewer agent may need roots this host's probe did not surface. That is what the mandatory
  pre-flight probe is for, and every widening is an explicit `--allow-read` that is then proven or
  scanned. A widening that can be neither proven nor scanned clean must fail the capture, not be
  waived.
* **Portability, restated:** the carve-out list above is *derived*, not hard-coded — it is whatever
  `enumerate_boundaries()` finds on the host at session-build time. The concrete list in G.3.2 is
  this host's result, recorded so the implementation has a known-good target to test against, and
  `ISOLATION.json` records the host's own list per capture. No Linux/Windows backend is claimed.

---

## DESIGN iteration 3 — correction for F-002

STATUS: COMPLETE

### Summary / Requirements

F-002 is a consistency defect in the iteration-2 delta, not in the designed behaviour. The delta
asserted that D-I was byte-for-byte untouched and, in the same section, supplied a corrected
`COMPATIBILITY.md` limitation sentence that it called "the only D-I-adjacent change". The main D-I
block (line 965 onward) still carried its approved iteration-1 wording, so IMPLEMENTATION would
have faced two competing specifications for the same `COMPATIBILITY.md` text.

The correction takes the review's first branch — the lower-risk one: **D-I stays exactly as
approved and no boundary change is requested.** The narrower residual the recursive proof earns is
recorded only where D-G already owns the text. Nothing about F-001's actual fix is reopened: the
recursive `prove_immutable()` I-1…I-6 proof, the carve-outs, the removal of `/private/var` and
`/Library`, the closed metadata traversal set, NEG-7, NEG-8 and T-9.9 are untouched. D-H is
untouched.

### Current Architecture

The narrower limitation already had a home before this correction. It is stated in D-G twice:

* **G.3.3** (line 464 onward), as the justification for not content-scanning Class IMM roots: *the
  proof is evaluated at session-build time against the run user's own privileges, so it does not
  bind a privileged (root) writer*.
* **G.6**, verbatim, as the single entry of `ISOLATION.json.limitations[]` (line 690).

**RK-2** and **RK-8** carry the same residual in the risk table. So the sentence iteration 2 wanted
to push into `COMPATIBILITY.md` was already recorded in two authoritative places inside D-G, which
is why removing the D-I replacement loses no information.

### Proposed Design

Four textual corrections to the already-written design, all of them removals of a claim about D-I.
No behaviour, interface, file list, exit code or test changes.

| # | location | before | after |
|---|---|---|---|
| 1 | G.3.3, end of the residual-limitation paragraph *(called "the 'not content-scanned' paragraph" when iteration 3 edited it; iteration 5 rewrote its opening, and it now begins "Class IMM roots are content-scanned like every other admitted root")* | "…is what `ISOLATION.json.limitations[]` and D-I carry." | "…is what `ISOLATION.json.limitations[]` carries." |
| 2 | iteration-2 scope declaration | "no sentence of either changed … `D-I` gains no new claim: the one limitation sentence it carries is narrowed … quoted in §4 below" | D-H and D-I stand byte-for-byte at their approved iteration-1 text; the narrower residual is recorded only in G.3.3 and `ISOLATION.json.limitations[]`, not carried into D-I |
| 3 | RK-2 mitigation cell | "Stated in `limitations[]` and in D-I in exactly those terms." | "Stated in exactly those terms in G.3.3 and in `ISOLATION.json.limitations[]`; D-I's `COMPATIBILITY.md` wording is unchanged." |
| 4 | iteration-2 Error Handling / Compatibility | the "**D-I's limitation sentence narrows**" paragraph plus the block-quoted replacement sentence | "**No compatibility-document change.**" — D-I's two replacement blocks stand as approved; the residual is an attestation field and a design-internal statement, not a public compatibility claim |

After these, the document contains exactly one specification of the `COMPATIBILITY.md` text: the
D-I block at line 965.

### Components / Interfaces / Data Flow

Unchanged. No component, interface, function signature, JSON field, exit code or profile clause is
added, removed or renamed by this iteration. `ISOLATION.json.limitations[]` keeps the same single
entry with the same wording it already had.

### Error Handling / Compatibility

Unchanged, and that is the point of the correction. `COMPATIBILITY.md:120-122` and
`COMPATIBILITY.md:124-127` are replaced with exactly the two blocks quoted in D-I, and with nothing
else. The MINOR version bump justification in D-I is unaffected, since the compatibility surface is
now provably identical to what iteration 1 approved.

### Expected Changed Files / Implementation Steps

The implementation file list is unchanged from iteration 2. Restated for the implementer so no
search is needed:

* `scripts/review_isolation.py`, `scripts/test_review_isolation.py` — per iteration 2 (D-G).
* `scripts/run_logging.py`, `CHANGELOG.md` — per iteration 1 (D-H).
* `COMPATIBILITY.md` — the two replacements in **D-I as written at line 965**, verbatim. If any
  other text in this document appears to specify `COMPATIBILITY.md`, it is stale and D-I wins.

### Testing Strategy

No test is added, removed or altered. NEG-0…NEG-8, T-8, T-9.1…T-9.9 stand as designed in
iterations 1 and 2. The only verification this iteration needs is documentary, and it was run:

* Extracted the `### D-I` block and the `### D-H` block from `565e5a8` (the approved iteration-1
  commit) and from the corrected working copy and compared them byte-for-byte — both identical
  (D-I: 2952 bytes each).
* Confirmed the deletions in `git diff 565e5a8..HEAD` for this file all fall inside D-G.
* Grepped every remaining `D-I` mention. What is left is: the section itself (line 965); four
  index/file-map rows that only name it (lines 32, 46, 1110, 1130); the threat-model sentence at
  line 250, which is unchanged iteration-1 text and accurate, because D-I does state that boundary;
  and the three corrected pointers above, none of which now claims D-I changes.

### Risks / Open Issues

* **RK-10 (new, documentation-only).** D-I's approved iteration-1 text says the isolated reviewer's
  every readable path "has been exhaustively scanned for key material", while both iteration 1
  (Class SYS) and iteration 2 (Class IMM) admit some roots on a non-scan proof instead. This
  imprecision is **pre-existing and approved** — it is identical in truth-value before and after
  the F-001 fix, since unscanned-SYS became unscanned-IMM — so it is *not* corrected here, and
  correcting it would require exactly the boundary change F-002 says must not be taken
  unilaterally. Recorded for the boundary owner: if a future iteration is allowed to reopen D-I,
  the one-word fix is "scanned" → "scanned or proven immutable". IMPLEMENTATION must apply D-I as
  written and must not "improve" it.
* No other open issue is added. O-1…O-3, RK-1…RK-9 stand as written.

---

## DESIGN iteration 4 — correction for the IMPLEMENTATION review's F-001/F-002

STATUS: COMPLETE

### Summary / Requirements

Two DESIGN decisions, both raised by the IMPLEMENTATION Worker as Findings rather than shipped
silently, and both escalated by the IMPLEMENTATION Reviewer as blocking G1 violations:

| review id | IMPLEMENTATION.md id | subject | decision |
|---|---|---|---|
| **F-002** | **F-101** | D-H.2 step 2's `extra == ()` residue clause | **The clause was never a correct formalization. Replaced** — not relaxed — by a *per-match* rule that is strictly stronger than the text equality the implementation shipped. |
| **F-001** | **F-102** | NEG-5's reduced pass set over Class IMM roots | **Partly upheld, partly rejected — and the rejected half was reversed in iteration 5.** Pass **C is required** over Class IMM roots, redefined with a size prefilter that makes it *walk-cost*; that part stands. Iteration 4 kept pass **B** out; the DESIGN reviewer's F-001 showed that leaves the readable set unproved clean of a reformatted or partial copy, and **D-5.1 makes pass B mandatory over Class IMM**. Read D-4.2 with its superseded banner. |

What this iteration changes, and nothing else:

* **D-H**: H.2 step 2 (rewritten), T-7.3 / T-7.4 / T-7.8 / T-7.9 (reconciled), RK-7 (rewritten).
* **D-G**: G.3.3's pass table (pass C gains the size prefilter; pass S is recorded), G.3.3's
  residual-limitation paragraph — the one iteration 4 still called "the 'not content-scanned'
  paragraph" — scoped to session-build time, G.9's NEG-5 row (per-class pass set).
* **New**: T-7.13, T-7.14, T-8.4b, T-8.4c, T-9.5; RK-11, RK-12; one additive CLI flag.
  *(Iteration 5 reverses the last two of those: RK-12 is rewritten and the CLI flag is withdrawn.)*

What this iteration does **not** touch, restated so a re-review does not have to re-derive it:

* **Iteration 2's F-001 fix** — `prove_immutable()` I-1…I-6, the carve-outs, the removal of
  `/private/var` and `/Library`, the closed metadata traversal set, NEG-7, NEG-8, T-9.9. Settled.
* **Iteration 3's F-002 fix** — the single-authority D-I. **D-I is not touched**, `COMPATIBILITY.md`
  is not touched, and `ISOLATION.json.limitations[]` keeps its single entry with its existing
  wording. See "Error Handling / Compatibility" for why that entry already covers the one residual
  this iteration creates.
* The bundle schema (`2.0`), the five redaction categories, the exit-code table, the profile
  clause order, the session layout, the baseline procedure.
* **F-003 / F-103** (the two `RetainedReportWhitespaceExemptionTests` failures caused by trailing
  whitespace in two committed DESIGN review reports) is not a design defect and is not addressed
  here. It is a conflict between the `.gitattributes` exemption scope and an artifact the
  Coordinator committed, and it belongs to whoever owns that commit.

---

### Current Architecture

Everything below was re-derived on this host against the code as it stands at `cac283b`, not taken
from the IMPLEMENTATION Worker's report.

#### A. D-H.2 — what `extra == ()` actually asserts

`redact_text()` applies five `(name, pattern, replacement)` triples in order. The question the
residue check is trying to answer is *"is there anything left that this policy would still
remove?"*. `extra == ()` answers a different question: *"is there anything left that this policy
still **recognises**?"* Those two coincide only for a policy in which no placeholder can re-match
its own producing pattern. `redaction/1.1` is deliberately not such a policy: categories 4 and 2
and 3 are documented as *readability-preserving* — they keep a readable anchor and replace only the
identifying part.

Measured, by running each category's own placeholder output back through its own pattern:

| # | category | replacement | can its output re-match its pattern? | why |
|---|---|---|---|---|
| 1 | `orca_dispatch_capability` | constant `<REDACTED:orca_dispatch_capability>` | **no** | the pattern is `\bdcap_[A-Za-z0-9_\-]{8,}`; the placeholder begins `<` |
| 2 | `url_credential` | `\1://<REDACTED:url_credential>@` | **yes** | the anchor `scheme://…@` is preserved on purpose, and `<REDACTED` / `url_credential>` are a legal `user` / `pass` pair for the pattern |
| 3 | `env_secret_pattern` | `\1\2<REDACTED:env_secret_pattern>` | **yes** | the anchor `KEY=` is preserved on purpose, and the value class `[^\s\n]+` matches the placeholder |
| 4 | `absolute_local_path` | `\1<REDACTED:absolute_local_path>` | **no** | the `(?!<\|\{)` lookahead is exactly a guard against re-matching a placeheld segment |
| 5 | `foreign_absolute_path` | constant `<REDACTED:foreign_absolute_path>` | **no** | the pattern requires a leading `/` and carries `(?!<)`; the placeholder has neither |

So `extra` is non-empty *by construction* for any text that ever contained an
`env_secret_pattern`-shaped assignment or a credentialed URL — and those are precisely the two
things a sanitizer for an orchestrator log exists to remove. The two counterexamples reproduce
exactly as IMPLEMENTATION.md F-101 reports them:

```text
'GITHUB_TOKEN=ghp_deadbeef1234'      -> 'GITHUB_TOKEN=<REDACTED:env_secret_pattern>'
                                        again == candidate : True
                                        extra              : (('env_secret_pattern', 1),)
'https://user:hunter2@example.test/x' -> 'https://<REDACTED:url_credential>@example.test/x'
                                        again == candidate : True
                                        extra              : (('url_credential', 1),)
```

Run over the fifteen canonical shapes — H.2 step 2's own seven, T-7.1…T-7.5's five, a table row,
`<REPO>/scripts/x.py`, and one row mixing all five categories — **all fifteen** are text fixed
points and **five** have non-empty `extra`, including **two of H.2's own seven "verified fixed
point" cases** (`AWS_SECRET_ACCESS_KEY=…`, `https://u:p@h/p`) and **both of T-7.3 and T-7.4**.

That is the finding, and it is upheld in full: enforcing `extra == ()` literally would omit the
orchestrator log from every bundle that ever logged a secret-named assignment or a credentialed
URL, and would make T-7.3 / T-7.4 / T-7.8 unsatisfiable, since each requires the content to be
*embedded* with a non-zero count. **The design was wrong; the implementation was right to stop and
say so.**

#### B. Is bare text equality the complete security statement?

The IMPLEMENTATION Worker's argument is: *"the only region a category can rewrite to itself is a
region that is already its own placeholder."* That is true per match, and it is the right idea.
Checked against the five categories, self-rewrite forces the removed region to be the placeholder
exactly:

* category 2 self-rewrite ⟹ `user:pass` is literally `<REDACTED:url_credential>`. It cannot be
  anything else: `pass` excludes `@` and `user` excludes `:`, so the matched span and the
  replacement can only be equal as whole strings, never as a prefix of one another.
* category 3 self-rewrite ⟹ the value is literally `<REDACTED:env_secret_pattern>`.
* categories 1, 4, 5 cannot self-rewrite at all (table above), so any match they make must change
  the text.

Where the Worker's argument is **incomplete** is one step up, at the whole-string level.
`again == candidate` compares two strings; a match that rewrote to *different* bytes is invisible to
that comparison only if some other match's length change compensates for it exactly. Ruling that
out requires a per-category, first-differing-character argument — I did work it through for
`redaction/1.1` and found no construction, and a 400,000-trial randomized search over an alphabet
seeded with every placeholder literal, `dcap_`, `TOKEN`, `://`, `/Users/` and path punctuation found
**zero** texts that are text fixed points while containing a non-self-expanding match.

But that argument is *policy-specific*. It is exactly the shape of reasoning this design has twice
punished: `extra == ()` was defended as "a closed statement over the policy's own categories" and
was not one, and the P-PATH note two hundred lines below says in as many words that a postcondition
phrased as "redaction is a fixed point of this value" is **circular** — a value the policy does not
recognise satisfies it trivially. A sixth category added tomorrow could invalidate the
compensation argument silently, and nothing in the codebase would notice.

The per-match rule has no such dependency. It is the property itself, stated directly.

#### C. D-G.9 — what the recursive proof does and does not establish

`prove_immutable()`'s I-1…I-6 is a statement about **write capability**: *no unprivileged process
can cause new readable content to appear anywhere in the subtree*, evaluated against the run user's
privileges at session-build time. It is emphatically **not** a statement about **existing content**.
A file already sitting in an admitted IMM root — placed there at any time in the past, by anything
— passes the proof unexamined, because the proof never opens it.

So the review's hypothetical is real as stated: *a pre-existing byte-identical answer-key copy
already sitting under an unexpected filename inside an admitted IMM root* passes I-1…I-6, is
invisible to pass A (wrong basename) and to pass D (not inside an archive), and is caught **only**
by pass C. Under the shipped A/D-only pass set it is not caught at all. The claim in
`review_isolation.py`'s NEG-5 comment that what B and C "would be re-deriving is already
established more strongly by the recursive proof" is **false for pass C**: the proof establishes
nothing whatever about content that predates it.

**Verified, not asserted.** Against a temporary root holding (a) a byte-identical copy of
`scripts/fixtures/final_review_eval/key/answer_key.json` renamed to `libfoo.dat`, (b) a file of
exactly 9,347 bytes differing from the key in one byte, and (c) the key's bytes plus a trailing
newline, the shipped code gives:

```text
passes=('A', 'D')       hits=[]                         <- the review's gap, reproduced
passes=('A', 'C', 'D')  hits=[('C', 'libfoo.dat')]      <- pass C is what closes it
passes=('C',)           hits=[('C', 'libfoo.dat')]      <- (b) and (c) are not hits either way
```

There is a second, more important reason not to lean on the proof here, and it is the reason NEG-5
exists in the form iteration 2 gave it. **NEG-5's job is to not trust the classification.** It runs
over the profile's own **computed** admitted set, precisely so that a wrong classification shows up
as a hit rather than as a silent admission. *(Iteration 4 wrote "from inside the sandbox" here.
That was inaccurate and is corrected in iteration 5 §D′: NEG-5 is an in-process scan, not a
sandboxed subprocess. The argument below is unaffected — it never depended on a process boundary,
only on scanning the computed set rather than the candidate list.)* F-001 was a classification bug;
a rescan that assumes the classification is correct cannot catch the next one. Justifying a reduced
NEG-5 pass set *by citing the proof* is therefore circular in the same way `extra == ()` was: it
assumes the thing the test exists to check.

#### D. What pass C over a Class IMM root actually costs

Measured on this host, walking each admitted IMM root with `os.walk(followlinks=False)`, carve-outs
pruned, counting directory entries (symlinks included, hence counts above G.3.2's file counts):

| root | entries | bytes | walk only | walk + `st_size` compare | walk + sha256 of every file |
|---|---:|---:|---:|---:|---:|
| `/usr` | 43,422 | 2.29 GB | 0.49 s | **0.79 s** | 9.17 s |
| `/Library/Developer/CommandLineTools` | 109,959 | 5.87 GB | 2.22 s | **2.03 s** | 23.83 s |
| `/System` (8 carve-outs pruned) | 286,743 | 18.69 GB | — | **9.8 s** | not run — 18.69 GB |
| **total** | **440,124** | **26.85 GB** | — | **~13 s** | **minutes, and ~27 GB of reads** |

And the number that decides it:

> The answer key is `scripts/fixtures/final_review_eval/key/answer_key.json`, **9,347 bytes**,
> `sha256:953c9748…`. Across all 440,124 entries in all three admitted IMM roots, the number of
> regular files whose size is exactly 9,347 bytes is **one**.

A file whose sha256 equals the key's sha256 is byte-identical to the key and therefore has the
key's exact byte length. Filtering pass C's hash set by `st_size == len(key_bytes)` is an
**equivalence, not an approximation**: it detects exactly the set the unfiltered pass detects, and
it reduces the work from 26.85 GB of reads to a walk plus **one** sha256 of a 9 KB file.

Pass **B** cannot be prefiltered that way. `scan_leak()` is a token/shingle/expected-count scan, so
any file long enough to hold a shingle is a candidate; there is no sound size, name or type gate,
and it costs the full 26.85 GB. That, and only that, is the pass this iteration declines to run
over Class IMM roots.

---

### Proposed Design

#### Decision D-4.1 — the residue rule is per-match self-output

> **For every `(name, pattern, replacement)` in `REDACTION_CATEGORIES`, in policy order, every match
> `m` of `pattern` in `candidate` must satisfy `m.expand(replacement) == m.group(0)`.**
>
> The second pass may still *recognise* text; it must have nothing left to *remove*.

Properties, each of which is why this and not one of the two alternatives:

1. **It is the security statement, not a proxy for it.** "Nothing left to remove" is what the H.1
   rule means. Text equality is a *consequence* of it (a substitution whose every replacement
   equals its own span cannot change the string), so the corrected rule is **strictly stronger than
   what the implementation shipped**, and strictly weaker than nothing else.
2. **It is satisfiable.** All fifteen canonical shapes satisfy it, T-7.3 / T-7.4 / T-7.8 become
   satisfiable exactly as written, and a clean log still produces `redactions == []`.
3. **It is policy-independent.** No appeal to "category 5 matches every absolute path" and no
   appeal to a compensation-impossibility argument. A sixth category added tomorrow is covered on
   the day it is added, and a category whose replacement does *not* reproduce its own span fails
   loudly at the first log that trips it — which is the behaviour H.2 step 3's structure check was
   already written to provide for a different future-policy hazard.
4. **It costs one extra pass of `finditer` over a log-sized string.** The current code already runs
   `redact_text(candidate)` a second time and throws the counts away; this replaces that call.

`safe_embedded_text()`'s signature, its `redact=True`/`redact=False` split, step 1, step 3 and the
closed `EMBED_OMISSION_REASONS` vocabulary are **unchanged**. `"redaction_residue"` remains the one
reason this check can produce.

#### Decision D-4.2 — NEG-5's pass set is per class, and pass C is required over Class IMM

> **SUPERSEDED IN ITERATION 5 by D-5.1. Do not implement this decision.** The DESIGN review's F-001
> is upheld: leaving pass B out of Class IMM left the readable set unproved clean of a reformatted
> or partial rendering of the key, and the optional `--scan-imm-content` flag did not make the
> *default* baseline satisfy the answer-key non-exposure requirement. **Pass B is now mandatory over
> every admitted root, the flag is removed, and `SCAN_PASSES_IMM_CONTENT` is never created.** What
> survives from this decision, unchanged and still required, is exactly two things: pass C's **size
> prefilter** (an equivalence, not an approximation) and pass **S being Class USR only**. Everything
> below is retained only as the record of what iteration 4 reasoned and why iteration 5 reversed it.

| class | passes | rationale (iteration 4 — superseded) |
|---|---|---|
| **USR** | **A, B, C, D, S** | unchanged. The root is small (a freshly created session tree), mutable, and the scan is the only evidence there is. |
| **IMM** | ~~**A, C, D**~~ → **A, B, C, D** (D-5.1) | A and D are walk passes. **C is required**, under the size prefilter — the proof says nothing about pre-existing content, and NEG-5 must not assume the classification it is checking. Cost: a walk plus, on this host, one 9 KB hash. |

Iteration 4 argued that pass B over Class IMM was an acceptable residual on three grounds — that
I-3 proves the run user cannot have *written* such a copy, that the one in-scope way key material
reaches an admitted root is a classification bug already covered four other ways, and that pass B
costs 26.85 GB of reads per capture. **Ground 1 is the one that fails, and it fails on the same
point iteration 4 itself established two sections above:** I-3 is a statement about *write
capability*, so it cannot speak to a file that was already present when the proof ran. A
pre-existing reformatted copy is an **initial-state condition**, not an act by a privileged writer,
and therefore is not covered by the out-of-scope privileged-writer boundary that ground 1 leans on.
Grounds 2 and 3 remain true as stated and are simply not sufficient: ground 2 enumerates coverage
for a *different* failure (a classification bug), and ground 3 is a cost, which this design has
consistently ruled must never be the reason a safety gate is skipped.

The escape hatch iteration 4 proposed, `isolate --scan-imm-content` (default **off**), is
**withdrawn**. A default-off content-cleanliness gate leaves the default capture — the one §7
actually takes — unproved, which is precisely the reviewer's finding. D-5.1 replaces it with a
mandatory pass B, and removes the flag rather than leaving it as a no-op, so that the design and the
CLI cannot disagree.

**Rejected alternative — keep A/D only and document it.** Rejected outright, in iteration 4 and
again in iteration 5. It leaves the review's named gap open, and its stated justification ("the
proof establishes it more strongly") is false for pass C and for pass B alike.

---

### Components / Interfaces / Data Flow

No new module, no new dependency, no schema version change, no exit-code change, no profile-clause
change, no `ISOLATION.json` field removal or rename.

**`scripts/run_logging.py`**

```python
def _residual_matches_are_self_output(text: str) -> bool:
    """D-H.2 step 2. True when the policy has nothing left to REMOVE from `text`.

    Not `redact_text(text)[0] == text`, and not `redact_text(text)[1] == ()`:
    the first is a whole-string proxy for a per-match property, and the second is
    a DIFFERENT property that redaction/1.1 cannot satisfy on the very inputs this
    gate exists for -- env_secret_pattern and url_credential preserve a readable
    anchor on purpose and therefore re-match their own placeholder output while
    rewriting it to identical bytes.
    """
    return all(
        match.expand(replacement) == match.group(0)
        for _name, pattern, replacement in REDACTION_CATEGORIES
        for match in pattern.finditer(text)
    )
```

`safe_embedded_text()` step 2 becomes `if not _residual_matches_are_self_output(candidate): return
None, redactions, "redaction_residue"`. The `again, _second_pass_counts = redact_text(candidate)`
call is **removed**, not kept alongside — one authority for one property. Its docstring's
"It is deliberately NOT additionally asserted…" paragraph is replaced with the corrected statement,
including why `extra == ()` is a different property rather than a stricter one.

**`scripts/review_isolation.py`**

| symbol | change |
|---|---|
| `SCAN_PASSES_ALL` | unchanged: `("A", "B", "C", "D", "S")` |
| `SCAN_PASSES_NAME_ONLY` | **renamed** `SCAN_PASSES_IMM`, value **`("A", "B", "C", "D")`** (iteration 5; iteration 4's `("A", "C", "D")` is superseded). The old name is removed, not aliased — it no longer describes the value. |
| `SCAN_PASSES_IMM_CONTENT` | **not created.** Iteration 4 proposed it for the withdrawn `--scan-imm-content` flag; with pass B mandatory there is nothing for a second IMM pass set to mean. |
| `scan_readable_set()` | pass C gains the size prefilter. Add `_answer_key_size(key)` next to the existing `_answer_key_digest(key)`, reading the size from the **same** `key["__source_path__"]` and returning `None` on a missing path or `OSError`, exactly as `_answer_key_digest()` does — one source of truth for "which file is the key". In the walk, `continue` before `sha256_path(entry)` when `key_size is not None and entry.lstat().st_size != key_size`. When `key_size is None` pass C is already inert (`key_digest` is `None` too) and nothing changes. Applied on **both** classes — it is an equivalence, so it is strictly a speedup for Class USR too. An `OSError` from `lstat()` falls through to hashing rather than skipping, so the prefilter can never turn an unreadable file into a silent pass. |
| `run_negative_probes()` | the NEG-5 loop selects `SCAN_PASSES_ALL` (USR) or `SCAN_PASSES_IMM` (IMM) — no third case and no flag; each `rescan_detail[]` entry gains `"vocabulary": "key_leak" \| "key_material"` alongside its existing `passes` list. The NEG-5 comment block at `review_isolation.py:1315-1330` is rewritten wholesale: lines 1318-1319 are the duplicated sentence iteration 4 already flagged, and lines 1321-1330 — "A Class IMM root gets the two WALK passes … and not the two READ passes … What passes B and C would be re-deriving is already established more strongly by the recursive proof" — state exactly the claim D-5.1 refutes, so they are **deleted** rather than edited. |
| `build_parser()` | **no change.** `--scan-imm-content` is not added. |

**Data flow, unchanged everywhere else.** `ISOLATION.json`'s `readable_set[]` entries keep
`scanned: false` for Class IMM: that field describes the **session-build-time** scan (G.3.3), which
this iteration does not change, and T-8.7's assertion on it stands as written. The NEG-5 probe
record is where the in-process rescan's pass set is reported (iteration 5 §D′), and it already
carries a per-root `passes` list.

---

### Error Handling / Compatibility

* **Exit codes: unchanged.** A pass-C hit inside a Class IMM root is a hit like any other — exit 4,
  offending path printed. There is still no `--ignore`.
* **No new CLI surface.** `--scan-imm-content` is not added (D-4.2's proposal is withdrawn by
  D-5.1), so `isolate`'s option set is unchanged and no invocation anywhere needs editing. The
  behaviour change is that a capture now scans more, never that a caller must ask it to.
* **Bundle schema stays `2.0`.** The residue rule changes *which* inputs are embedded versus
  omitted, but relative to what is deployed at `cac283b` (text equality) the corrected rule embeds a
  subset, and no input is known — after a 400,000-trial search — on which the two differ. Relative
  to the *approved* iteration-1 rule it embeds a strict superset, which is the whole point. No
  reader-visible key, field or version changes, so no compatibility surface moves.
* **`COMPATIBILITY.md` is not touched.** D-I stands exactly as iteration 3 settled it, and RK-10's
  observation about D-I's "exhaustively scanned" wording is unchanged in truth-value **by iteration
  4 itself**: under iteration 4's A/C/D pass set, Class IMM roots were partly unscanned before and
  partly unscanned after, and reopening D-I remains out of bounds. *(Iteration 5 changes the
  truth-value, in D-I's favour rather than against it — see iteration 5's Error Handling /
  Compatibility. Reopening D-I remains out of bounds there too.)*
* **`ISOLATION.json.limitations[]` keeps its single existing entry** — and iteration 5 makes that
  *easier* to defend, not harder. D-4.2's residual (pass B not run over Class IMM) is **gone**: the
  content gap it left is now closed by a mandatory pass B. What the single entry still says is the
  only thing still true — *the proof is evaluated at session-build time against the run user's own
  privileges, so it does not bind a privileged (root) writer* — and that is a statement about the
  **immutability** proof's durability, not about content, which is now independently established.
  Adding a second entry would restate the same limitation in different words, which is the drift R1
  punished. The per-root pass set **and vocabulary** live in the NEG-5 probe record, where a reader
  can see them per root.
* **Failure posture on the new `stat` call:** an `OSError` from `entry.lstat()` in the prefilter is
  treated as "cannot certify" and falls through to hashing the file rather than skipping it, so the
  prefilter can never turn an unreadable file into a silent pass.

---

### Expected Changed Files / Implementation Steps

| # | file | change | hard? |
|---|---|---|---|
| 1 | `scripts/run_logging.py` | `_residual_matches_are_self_output()`; `safe_embedded_text()` step 2; docstring correction | **HARD** |
| 2 | `scripts/test_run_logging.py` | T-7.3 / T-7.4 / T-7.8 assertions extended; T-7.9 monkeypatch retargeted; T-7.13, T-7.14 added | **HARD**, same commit as 1 |
| 3 | `scripts/review_isolation.py` | `SCAN_PASSES_IMM = ("A","B","C","D")`, pass B relocated into `scan_readable_set()`'s pruned walk with the per-class vocabulary, pass-C size prefilter, NEG-5 pass/vocabulary selection, stale comment block deleted | **HARD** |
| 4 | `scripts/final_review_eval.py` | `key_material_tokens()` + `scan_leak_text()` extracted next to `key_leak_tokens()` / `scan_leak()`; `scan_leak()` re-expressed over `scan_leak_text()` so there is one authority for "what counts as key material". **No CLI change.** | same commit as 3 |
| 5 | `scripts/test_review_isolation.py` | T-8.4b (assertion corrected), T-8.4c, T-8.4d, T-8.4e, T-8.4f, T-9.5, plus two in-place test corrections — **see `## DESIGN iteration 5`'s Testing Strategy, which is the authority** | **HARD**, same commit as 3-4 |
| 6 | `orca-worker-reviewer-orchestration/tools/run_logging.py` | mirror of 1 — `cmp` with `scripts/run_logging.py` must stay byte-identical, as the existing gate requires | same commit as 1 |
| 7 | *§7 baseline re-capture* | re-run per the amended B-1′…B-7 once 1-6 are green | after 1-6 |

No documentation file changes. `CHANGELOG.md` is untouched: the schema version does not move, no
CLI surface changes (iteration 5 withdraws iteration 4's proposed flag), and no reader-visible field
is added or renamed. `SKILL.md` §9's description of the export is unchanged.

---

### Testing Strategy

**Corrected in place** (see the T-7 and G.9 tables above): T-7.3, T-7.4, T-7.8, T-7.9, and NEG-5's
row.

**New — D-H (`scripts/test_run_logging.py`):**

| id | asserts |
|---|---|
| **T-7.13** | *The F-101 regression guard.* A log containing **both** `GITHUB_TOKEN=ghp_deadbeef1234` and `https://user:hunter2@example.test/x` exports with `content_redacted is not None`, `content_omitted_reason == ""`, `env_secret_pattern ≥ 1` and `url_credential ≥ 1`, and neither secret appears in the serialized bundle. Independently asserts that `redact_text(content_redacted)[1]` is **non-empty** — i.e. the test states out loud that a non-empty second-pass count is expected and safe, so a future contributor who re-adds `extra == ()` fails here with the reason in front of them. |
| **T-7.14** | *The residue path, per-match.* Monkeypatch `REDACTION_CATEGORIES` with one synthetic category whose replacement does not reproduce its own span (`("t", re.compile(r"ZZ_[A-Z]+"), "<REDACTED:t>")`), log a line containing `ZZ_LEAK`, and assert `content_redacted is None`, `content_omitted_reason == "redaction_residue"`, `digest_pre_redaction` present, `integrity["omitted_content"]` names the log, and the export still returns a written path. Also asserts the converse: a synthetic category whose placeholder *does* re-match and expands to its own span (the shape of categories 2 and 3) is **embedded**. |

**New — D-G (`scripts/test_review_isolation.py`):**

| id | asserts |
|---|---|
| **T-8.4b** | *Pass C survives the IMM pass set.* `scan_readable_set(key, root, passes=SCAN_PASSES_IMM)` over a fixture root containing a byte-identical copy of the answer key under an unrelated basename (`libfoo.dat`) returns **exactly one `pass == "C"` hit**. **Corrected in iteration 5:** the original wording said "exactly one hit" full stop, which mandatory pass B falsifies — a byte-identical copy of the key contains all of the key's prose, so pass B fires on it too. The test asserts the C hit by filtering on `pass`, and asserts the B hits are present as *corroboration*, which is the correct relationship between the two passes. The same call with the shipped `("A", "D")` set returns zero hits, and the test says so in its docstring. |
| **T-8.4c** | *The size prefilter is an equivalence, not an approximation.* Over a fixture root holding (a) a byte-identical key copy, (b) a file of exactly the key's byte length whose content differs in one byte, and (c) a file with the key's content plus a trailing newline: `passes=("C",)` returns exactly one hit and it is (a). Asserts (b) is hashed and not a hit, and (c) is size-filtered out and not a hit — a file that is not byte-identical is not a hit under either implementation, which is what makes the two equivalent. |
| **T-9.5** | *The NEG-5 contract, at the probe record.* Against the synthetic fixture, the NEG-5 record's `roots[]` has `passes == ["A", "B", "C", "D"]` and `vocabulary == "key_material"` for every `class == "IMM"` entry, and `passes == ["A", "B", "C", "D", "S"]` with `vocabulary == "key_leak"` for every `class == "USR"` entry, and every entry carries an integer `content_scanned`. Asserts there is **no** `imm_content_scan` field and **no** `--scan-imm-content` option on the `isolate` parser — the regression guard against reintroducing a default-off content gate. Darwin-only, skipped with an explicit reason elsewhere, like the rest of T-9. **The authoritative statement of this test is `## DESIGN iteration 5`'s Testing Strategy.** |

**Unchanged and still required:** T-7.1, T-7.2, T-7.5…T-7.7, T-7.10…T-7.12; T-8.1…T-8.11 (T-8.4's
existing pass A/B/C/D assertions still run with the full USR pass set); T-9.1…T-9.4, T-9.6…T-9.9;
T-10; NEG-0…NEG-8. `python3 -m unittest discover -s scripts -p 'test_*.py'`,
`validate_skills.py`, `verify_package.py`, and the `cmp` of the two `run_logging.py` copies are the
gate, exactly as before.

---

### Risks / Open Issues

* **RK-7** — rewritten in place; see the risk table above. The contradiction the IMPLEMENTATION
  Worker identified between RK-7's "the remedy is a redaction-policy fix" and *Explicitly not
  designed*'s "no change to the redaction policy's five categories" is resolved by separating the
  two claims that were conflated: the **safe** response to a genuine residue is omission, which is
  already built and requires no policy change; **closing** the policy gap is a separate work
  package under a MINOR bump and is out of this design's scope, which is exactly what the
  *Explicitly not designed* fence says. Neither statement now claims the other's ground.
* **RK-11 (new).** The corrected residue rule is strictly stronger than what is deployed at
  `cac283b`. If some real log ever contains a match that expands to different bytes, that log is
  omitted where the deployed code would have embedded it. That is the fail-safe direction, it is
  the direction H.1 mandates, and no such input is known after a 400,000-trial search — but a first
  occurrence would show up as a bundle with `content_omitted_reason: "redaction_residue"` rather
  than as a leak, and should be investigated as a policy bug under RK-7(c).
* **RK-12 — rewritten in iteration 5.** Iteration 4's RK-12 recorded "pass B is not run over Class
  IMM roots" as an accepted residual. That residual is **closed**: pass B is mandatory over every
  admitted root. What replaces it is a narrower and differently-shaped risk, stated in full as
  **RK-14** in `## DESIGN iteration 5`: pass B's *vocabulary* over a Class IMM root is the key's
  content vocabulary rather than its full leak vocabulary, because the full one is measurably not a
  gate over an OS tree (40 vendor-file hits under `/usr` alone). RK-12 as iteration 4 wrote it no
  longer describes anything this design does.
* **RK-13 (new, small).** `SCAN_PASSES_NAME_ONLY` is removed rather than aliased. Any out-of-tree
  caller breaks loudly at import. That is intended: the name asserts "name-only", the new pass set
  is not name-only, and a silently-wrong name is how F-001 was defended for an iteration.
* **G.3.2's cost figure is superseded, and this is the record of it.** G.3.2 states the full proof
  at "under 20 s in total"; IMPLEMENTATION.md F-102 measured ~3.5 minutes on this host (`/System`
  210 s), and my own walk timings are consistent with that order. The *mechanism* G.3.2 specifies
  is unchanged and correct; only the constant was wrong, and no design decision in this document
  rested on it. **Iteration 5 supersedes the constant again, upward:** with pass B mandatory over
  Class IMM the proof is no longer the dominant cost of a capture, and D-5.1 carries the measured
  end-to-end figure. No decision rests on that constant either — D-5.1 is decided on soundness, and
  its cost is reported rather than traded against.
* **O-1, O-2, O-3 stand unchanged.** O-1 (the `orca` CLI's behaviour from inside the sandbox)
  remains undischarged and is still a blocking pre-flight assertion for the Step 7 capture; O-2 (a
  shared agent state directory) is the one place where in-scope key material could reach a
  `--allow-read` root, and that root is Class USR and gets the full pass set **and the full
  `key_leak_tokens()` vocabulary**, so neither D-4.2 nor D-5.1 touches it.
* **RK-1…RK-6, RK-8…RK-10 stand unchanged.**

---

## DESIGN iteration 5 — correction for the NEG-5 Class IMM gap

STATUS: COMPLETE

### Summary / Requirements

The DESIGN review's **F-001** is upheld in full, and this iteration closes it.

| review finding | disposition |
|---|---|
| The default Class IMM NEG-5 pass set (A/C/D, with pass B available only through an optional, default-off `--scan-imm-content`) does not prove an admitted readable IMM root clean of pre-existing answer-key material. | **Upheld. Pass B is now mandatory over every admitted root, IMM and USR alike, by default and with no flag.** `--scan-imm-content` is withdrawn — not turned into a no-op — so the design and the CLI cannot disagree. |
| G.3.3 lines ~465-468 ("there is nothing a scan could find that the proof allows to exist") contradict lines ~481-482 ("the proof … says nothing about what is *already there*"). | **Upheld. Reconciled to one statement**, stated once and repeated nowhere in a weaker form: the recursive proof establishes *current write incapability* and nothing else. |
| G.3.3's "Bounding" paragraph says every escaping symlink is a hit, next to a pass-S row that is Class-USR-only. | **Upheld. Rewritten** to match the pass-S contract exactly, with the reason the two classes settle the case differently stated at the point of the claim. |

The reviewer's Required Action offered two ways to close the gap. This iteration takes the first —
*require pass B for every admitted IMM root in a qualifying baseline* — and delivers it. **One thing
about it could not be delivered literally, and the reason is a measurement, not a preference:** pass
B's *predicate*, run verbatim over an OS tree, is not a gate. It is not the 26 GB of reads that
stops it; it is that `key_leak_tokens()` fires **40 times under `/usr` alone**, across 36 vendor files —
the OS dictionary, `/usr/bin/man`, a dozen man pages, a zsh completion script, a Korean tokenizer
table — none of which contains any answer-key material. A hit is a hard failure with no `--ignore`, and the design's
prescribed remedies ("remove the copy", "stop allowing that root") are not available for
`/usr/share/dict/web2`. Shipping pass B verbatim over Class IMM would therefore make **every §7
baseline capture fail**, which is a G2 defect, not a stricter gate.

So pass B is mandatory over Class IMM — the pass, its exhaustiveness over files, its
hard-failure posture and its default-on status are all exactly what the reviewer asked for — and
the single thing that is per class is its **vocabulary**: Class IMM matches the key's *content*
vocabulary rather than the fixture's *label* vocabulary. §A and §B below measure both halves of
that, and D-5.1 states the soundness argument and the residual.

What this iteration changes, and nothing else:

* **D-G**: S3's definition (both classes are content-scanned now), G.3.3's pass-B row, G.3.3's
  "not content-scanned" paragraph, G.3.3's iteration-4 paragraph, G.3.3's "Bounding" paragraph,
  G.9's NEG-5 row, RK-2's label. G.9's NEG-5 row also gets one **accuracy** correction, unrelated
  to the pass set and explained in §D′: "from inside the sandbox" did not describe the code.
* **D-4.2**: superseded in place by **D-5.1**, with its reasoning retained as the record and its
  proposed flag withdrawn. Pass C's size prefilter and pass S's Class-USR-only scope, the two
  parts of D-4.2 that survive, are unchanged.
* **New**: `key_material_tokens()` and `scan_leak_text()` in `final_review_eval.py`; T-8.4d,
  T-8.4e, T-8.4f; RK-14, RK-15. RK-12 is rewritten; T-9.5 is rewritten.

What this iteration does **not** touch, restated so a re-review does not have to re-derive it:

* **D-H.2 and RK-7.** Both were confirmed sound and implementation-ready by the same review. Not a
  character of D-4.1, `_residual_matches_are_self_output()`, T-7.13, T-7.14, RK-7 or RK-11 is
  changed here.
* **Iteration 2's F-001 fix** — `prove_immutable()` I-1…I-6, the carve-outs, the removal of
  `/private/var` and `/Library`, the closed metadata traversal set, NEG-7, NEG-8, T-9.9.
* **Iteration 3's F-002 fix** — the single-authority D-I. `COMPATIBILITY.md` is not touched and
  `ISOLATION.json.limitations[]` keeps its single entry with its existing wording.
* The bundle schema (`2.0`), the five redaction categories, the exit-code table, the profile clause
  order and text, the session layout, the readable-set *membership* rules, the baseline procedure's
  steps.

---

### Current Architecture

Everything below was re-derived on this host against the code as it stands at `19753f6`. Nothing is
taken from a previous iteration's report.

#### A. What pass B does over a Class IMM root, measured

Pass B is `scan_leak(key, [root])`: for every file, normalise whitespace, casefold, then report a
hit for **any** of `key_leak_tokens(key)` present as a substring, plus a hit if either
`_EXPECTED_COUNT` regex matches. Against the shipped fixture that vocabulary is **723 tokens**.

Ran it, verbatim, over `/usr` with the root's three carve-outs pruned:

```text
/usr: files=17,953  bytes=1.67 GB  decoded=11,027  undecodable=6,926  hits=40
```

All 40 are false — 40 hit records across 36 distinct files. Counted from the run's own output, not
estimated:

| hits | token | where |
|---:|---|---|
| 20 | `sd-1` … `sd-5` (all five) | `sd-2` × 11 (`/usr/bin/man`, `whatis`, `manpath`, `apropos`, `locate.mklocatedb`, `bc.1`, `dc.1`, `tmux.vim`, `_composer`, …), `sd-3` × 4, `sd-4` × 2, `sd-5` × 2 (`/usr/bin/par.pl`), `sd-1` × 1 (`/usr/share/man/man1/time.1`) |
| 8 | `seeded` | `/usr/share/dict/web2`, `/usr/share/dict/web2a`, `/usr/share/cracklib/pw_dict.pwd`, `dc.1`, `zshparam.1`, … — an ordinary English word, and one of those files is literally the system dictionary |
| 1 | `정답` | `/usr/share/tokenizer/ko/dicrc` — a vendor Korean tokenizer dictionary |
| 11 | *(no token)* | `_EXPECTED_COUNT` / `_EXPECTED_COUNT_REVERSE` firing on ordinary prose: `nohup.1`, `perlguts.1`, `perl5201delta.1`, `perl5301delta.1`, `comm.n`, `vi_diff.txt`, `version9.txt`, … |

Zero hits came from `answer_key`, `answer key`, `seeded_defect`, `seeded defect`, `expected
finding`, `expected_finding_count`, `시드`, the `fixture_id`, any `archetype`, or any prose shingle.

Every one of those tokens is drawn from `FIXED_LEAK_MARKERS` or from a `seeded_defects[].id`, or is
the natural-language count heuristic. **Not one hit came from a `fixture_id`, an `archetype`, or a
prose shingle.**

Re-running with the label half removed then surfaced one more collision of exactly the same kind, in
a root `/usr`'s size does not reach:

```text
/Library/Developer/CommandLineTools/SDKs/MacOSX15.2.sdk/System/Library/Frameworks/
  AVFoundation.framework/Versions/A/Headers/AVContentKeySession.h
    "…a persistable content key cannot be used to answer key requests during later playback…"
```

`answer key` is a `FIXED_LEAK_MARKERS` entry and it is also an ordinary English verb-plus-noun. That
one hit is why D-5.1's rule below is **structural** — *keep the identifier forms, drop the
natural-language forms* — rather than a list of three hand-picked words. `answer_key`,
`seeded_defect` and `expected_finding_count` cannot appear in English prose by accident; `answer
key`, `seeded defect`, `expected finding`, `seeded`, `정답` and `시드` measurably can.

That asymmetry is not luck, it is what the two halves of the vocabulary *are*.
`key_leak_tokens()` answers *"does this file bear any trace of the fixture?"* — the right question
for a materialised review workspace, where any trace is bleed, and where the low-entropy half costs
nothing because the tree is small and derived. Over 398,241 vendor files it is the wrong question,
and its low-entropy half answers it wrongly 40 times in the smallest of the three big roots — the
one that holds 1.67 GB of the 26.25 GB admitted.

#### B. Why pass B cannot be delegated to `scan_leak()` for a Class IMM root

This is a second, independent defect in "just run pass B over Class IMM", and it is a correctness
defect rather than a precision one.

`scan_leak(key, targets)` takes no carve-out parameter — deliberately, and `scan_readable_set()`'s
own comment says why it is safe: *"that is right for a Class USR root — which is the only class that
runs pass B, and which never has a carve-out. A carve-out only ever arises inside a Class IMM
root."* The NEG-5 loop computes `root_carve_outs` per root and threads it into `scan_readable_set()`
precisely because passes A/C/D must not descend into them; pass B, being delegated, ignores it.

The carve-out that matters is `MANDATORY_CARVE_OUTS = ("/System/Volumes",)`.
`/System/Volumes/Data` is a mount point of the writable data volume nested inside the sealed system
volume and is **not** a symlink, so a carve-out-blind walk of `/System` descends through it into the
entire data volume — the repository, and `scripts/fixtures/final_review_eval/key/answer_key.json`
itself. `review_isolation.py`'s own NEG-5 comment already names this outcome: *"wrong in the loud
direction: /System/Volumes/Data re-exposes the entire data volume, so the rescan reports every
answer-key copy anywhere on the machine as a hit while the sandboxed process cannot reach a single
one of them."* The existing test
`test_a_carved_out_subtree_is_not_scanned_because_it_is_not_readable` documents the same constraint
in an inline comment.

So pass B over a Class IMM root has to be **driven by the same carve-out-pruned walk** as A/C/D. It
cannot be a delegated `rglob`.

#### C. What the recursive immutability proof establishes — stated once

`prove_immutable()`'s I-1…I-6 is a statement about **write capability**: *no unprivileged process
can cause new readable content to appear anywhere in the subtree*, evaluated against the run user's
privileges at session-build time. It is **not** a statement about existing content, because the
proof never opens a file.

Iteration 4 derived exactly this and wrote it down at §C of its own *Current Architecture*, and then
G.3.3 still carried the older sentence *"there is nothing a scan could find that the proof allows to
exist"* fourteen lines above it. That sentence is false and is now deleted. A pre-existing
reformatted copy inside an admitted IMM root is an **initial-state condition**: it needs no
malicious operator, no privileged writer during the review, and no classification bug. It is
therefore *not* covered by the privileged-writer boundary G.1 excludes, which is the ground D-4.2
leaned on hardest — and that is precisely why F-001 is upheld.

#### D′. What NEG-5 actually is, since D-5.1 leans on it

One accuracy correction, made because this iteration's argument depends on it rather than as a
re-opening of settled work. G.9's NEG-5 row said the rescan is *"re-run from inside the sandbox"*.
It is not: `run_probes()` executes in the parent process (`review_isolation.py:1196`), and only
NEG-2 / NEG-3 / NEG-4 spawn sandboxed subprocesses via `_command_probe()`. NEG-5 calls
`scan_readable_set()` in-process, once per entry of `readable["entries"]`, with each root's
carve-outs pruned.

What NEG-5's independence actually rests on — and this is unchanged and still sufficient — is that
it scans the **computed** readable set, after classification, rather than the candidate list. A root
wrongly classified as IMM is still scanned by NEG-5, with the IMM pass set, and a planted copy in it
is still a hit. That is the property F-001 needed and it does not require a sandbox. G.9's row is
corrected to say so; no behaviour changes.

#### D. `rglob` versus `os.walk(followlinks=False)`, re-derived rather than assumed

Relocating pass B into the walk changes which symlinks it reads, so the difference was measured on
this interpreter (Python 3.11.8) against a temp tree holding a real directory, a symlink to an
outside directory, and a symlink to an outside file:

```text
rglob("*")                  -> ['linkdir', 'linkfile.txt', 'real', 'real/a.txt']
rglob("*") + is_file()      -> ['linkfile.txt', 'real/a.txt']
os.walk(followlinks=False)  -> ('.', ['real','linkdir'], ['linkfile.txt']), ('real', [], ['a.txt'])
```

Two facts, both relevant:

1. **Neither** recurses into a symlinked directory (`linkdir` is listed, nothing under it is). So
   relocating pass B loses no *directory* coverage.
2. `rglob` + `is_file()` **does** yield a symlink-to-a-file, and `scan_leak()` therefore reads its
   target. `scan_readable_set()`'s walk `continue`s on `os.path.islink(entry)`, so the relocated
   pass B does not.

Fact 2 is a real behavioural delta and is stated rather than glossed: **an escaping
symlink-to-a-file inside a Class USR root used to be caught by pass S *and* pass B; after this
change it is caught by pass S only.** No coverage is lost — pass S is a hard failure on the same
terms — and a link that stays inside the root points at a file the walk reads directly. For a Class
IMM root pass S does not run and the *profile* is the evidence, as G.3.3's pass-S row states.

#### E. What a mandatory pass B costs, measured end to end

Walking every admitted IMM root with carve-outs pruned, opening every regular file, decoding as
UTF-8, normalising and matching:

| root | regular files | bytes | decoded as UTF-8 | undecodable (skipped) | hits | elapsed |
|---|---:|---:|---:|---:|---:|---:|
| `/usr` (3 carve-outs pruned) | 17,953 | 1.67 GB | 11,027 | 6,926 | 0 | 117.6 s |
| `/Library/Developer/CommandLineTools` | 98,658 | 5.87 GB | 94,472 | 4,186 | 3 | **1,953.9 s** |
| `/System` (8 carve-outs pruned) | 281,314 | 18.69 GB | 77,256 | 204,058 | 0 | 683.0 s |
| `/private/etc` | 227 | 0.7 MB | 214 | 13 | 0 | 3.1 s |
| `/bin` | 37 | 6 MB | 0 | 37 | 0 | < 0.1 s |
| `/sbin` | 52 | 6 MB | 0 | 52 | 0 | < 0.1 s |
| `/private/var/select` | 0 | 0 | 0 | 0 | 0 | < 0.1 s |
| **total** | **398,241** | **26.25 GB** | **182,969** | **215,272** | **3** | **2,757.7 s ≈ 46 min** |

Four things in that table are load-bearing, and none of them is the total:

1. **The three hits are all `answer key`, all in `AVContentKeySession.h`, in three SDK copies.** That
   run used the 715-token intermediate vocabulary, before §A's third collision forced the
   identifier-form rule. `key_material_tokens()` as D-5.1 finally defines it is a **strict subset**
   of what was measured — it removes exactly `answer key`, `seeded defect` and `expected finding` —
   so its hit count over every admitted IMM root on this host is **zero**, by subset, without a
   re-run. Stated that way rather than as a measured zero, because it is the former.
2. **Cost tracks *decodable* bytes, not total bytes.** `/System` is 3.2× CommandLineTools' bytes and
   costs a third as much, because 204,058 of its 281,314 files fail UTF-8 decode in their first
   bytes and are skipped. CommandLineTools is 96% decodable (SDK headers, `.tbd` files, sources) and
   is 71% of the whole run's time on its own.
3. **Memory is bounded and small.** Peak RSS over the whole run was 51 MB, and the largest single
   decoded file was 24.5 MB
   (`/System/Library/PrivateFrameworks/DocumentUnderstanding.framework/.../l4-smolberto.nlembedding`).
   Reading whole files is not a memory hazard at this scale, so no streaming/chunking mechanism is
   introduced — one less thing that could silently change the predicate.
4. **`/bin` and `/sbin` decode zero files.** A root can be admitted, walked, and contribute nothing
   to pass B — which is the normal case for a pure-binary tree, and worth knowing before someone
   reads a zero as a bug.

For scale, the same host's immutability proof over the same roots is IMPLEMENTATION.md F-102's ~3.5
minutes. **A mandatory pass B is now the dominant cost of a capture by roughly 13×**, and a §7
capture goes from minutes to the better part of an hour. That is reported here, not traded against: this design has held throughout that a
correction may cost readability or time and may never cost safety, and a §7 baseline capture is
taken once per remediation run, not per dispatch.

NEG-5's scan runs **in process**, inside `run_negative_probes()`; the `timeout=` arguments in
`review_isolation.py` all belong to `subprocess.run()` calls for the sandboxed command probes, so
there is no timeout for this work to breach.

---

### Proposed Design

#### Decision D-5.1 — pass B is mandatory over every admitted root; the only per-class difference is its vocabulary

| class | passes | pass-B vocabulary | count heuristics |
|---|---|---|---|
| **USR** | **A, B, C, D, S** | `key_leak_tokens()` — 723 tokens, unchanged | **yes**, unchanged |
| **IMM** | **A, B, C, D** | `key_material_tokens()` — 712 tokens | **no** |

Three properties define the decision, and each is the answer to one of the review's points:

1. **Pass B is not optional, not flagged, and not default-off.** `--scan-imm-content` is withdrawn
   and `SCAN_PASSES_IMM_CONTENT` is never created. A content-cleanliness gate the default capture
   does not run is not a gate, and the §7 baseline is taken with the default.
2. **Pass B is exhaustive over files in both classes.** Every regular file the pruned walk reaches
   is opened and tested. There is no size cap, no extension filter, no type gate and no sampling —
   each of which would be a real weakening, unlike the vocabulary difference below.
3. **The vocabulary is per class, and that is the whole of the difference.** Same walk, same
   normalisation, same hard-failure posture, same absence of an `--ignore`.

##### `key_material_tokens()` — the definition

```python
def _is_identifier_form(marker: str) -> bool:
    """True for `answer_key`, false for `answer key`, `seeded`, `정답`.

    The whole rule, and it is deliberately mechanical rather than a curated list: a
    marker spelled as an identifier cannot occur in running prose by accident, and a
    marker spelled as a word or a phrase measurably can. `/usr/share/dict/web2` contains
    `seeded`; `/usr/share/tokenizer/ko/dicrc` contains `정답`; AVFoundation's
    `AVContentKeySession.h` contains "used to answer key requests". See DESIGN D-5.1 sec A.
    """
    return "_" in marker


def _key_tokens(key: dict, *, include_labels: bool) -> set[str]:
    """One construction, two questions. See DESIGN D-5.1.

    include_labels=True  -> key_leak_tokens():     "does this file bear ANY trace of the fixture?"
    include_labels=False -> key_material_tokens(): "is this file a RENDERING of the key?"
    """
    tokens = {
        marker.casefold()
        for marker in FIXED_LEAK_MARKERS
        if include_labels or _is_identifier_form(marker)
    }
    fixture_id = key.get("fixture_id")
    if isinstance(fixture_id, str) and fixture_id:
        tokens.add(fixture_id.casefold())
    for entry in key.get("seeded_defects", []):
        if include_labels and entry.get("id"):
            tokens.add(str(entry["id"]).casefold())
        if entry.get("archetype"):
            tokens.add(str(entry["archetype"]).casefold())
        for field in ("summary", "negative_space_argument"):
            text = entry.get(field) or ""
            tokens.update(shingle.casefold() for shingle in _shingles(" ".join(text.split())))
    return {token for token in tokens if token}


def key_leak_tokens(key: dict) -> set[str]:
    return _key_tokens(key, include_labels=True)


def key_material_tokens(key: dict) -> set[str]:
    return _key_tokens(key, include_labels=False)
```

Deriving both from one construction is the point: `key_material_tokens(key) ⊆ key_leak_tokens(key)`
holds **structurally**, not by inspection, so a sixth marker or a new key field added tomorrow
cannot make the IMM vocabulary drift into something the USR vocabulary does not contain. T-8.4f
asserts the containment and the exact difference.

Against the shipped fixture the difference is exactly eleven tokens — the six
natural-language markers `{answer key, seeded defect, expected finding, seeded, 정답, 시드}` and the
five defect ids `{sd-1 … sd-5}`, 723 → 712 — plus the two count heuristics, which pass B skips for
Class IMM. The three identifier markers `answer_key`, `seeded_defect` and `expected_finding_count`
are **kept**, and `expected_finding_count` is a real substring of a real top-level key field
(`expected_finding_count_is_not_a_contract`).

##### What the IMM vocabulary catches

> **Claim.** Let *F* be any file whose content is derived from the answer key by reformatting,
> excerpting or quoting, and which reproduces **either** one `seeded_defects[].archetype` **or**
> six consecutive words of one `summary` / `negative_space_argument` (`_shingles()` uses `size=6`). Then pass B over a Class IMM
> root flags *F*.
>
> **Why.** Archetypes and six-word shingles are members of `key_material_tokens()` verbatim, and
> matching happens after `" ".join(text.split()).casefold()` — the same normalisation on both
> sides. Re-indentation, re-wrapping, JSON pretty-printing and case changes are therefore all
> irrelevant to the match, which is exactly the class of transformation the review named. A
> *partial* copy is caught as soon as it retains one such field.

That is the review's counterexample, closed: *"a reformatted/partial copy, excerpt, or quoted
fragment under an unrelated filename"* carries the key's prose, and the key's prose is what this
vocabulary is made of. It is caught for the same reason pass C catches a byte-identical copy under
an unrelated filename — the test is on content, and the filename is not consulted.

**Verified, not asserted.** Built the three plants T-8.4d specifies — `vendor.dat` holding
`json.dumps(key, indent=4, ensure_ascii=False)`, `notes.log` holding one
`negative_space_argument` re-wrapped at 40 columns, `chat.txt` holding one `summary` quoted with
`> ` markers inside unrelated prose — under unrelated basenames in a scratch root, then ran the real
`scan_readable_set()` and the proposed vocabulary over it:

```text
scan_readable_set(key, root, passes=("A","C","D"))   ->  hits = []          <- the review's gap
key_material_tokens(key)  vs  key_leak_tokens(key)   ->  712 vs 723, proper subset
  difference = ['answer key','expected finding','sd-1','sd-2','sd-3','sd-4','sd-5',
                'seeded','seeded defect','시드','정답']                      <- exactly the rule
pass B [key_material]  vendor.dat  ->  711 token hits
pass B [key_material]  notes.log   ->  127 token hits
pass B [key_material]  chat.txt    ->   11 token hits                        <- all three caught
```

Iteration 4's pass set finds **nothing** in any of the three; the mandatory pass B finds all three,
by margins of two orders of magnitude rather than by a single lucky token. The eleven-token
difference computed from the running code is exactly the six natural-language markers plus the five
defect ids — the identifier-form rule, confirmed against the real fixture rather than assumed.

##### What it does not catch, stated as a bounded residual (RK-14)

A file whose **entire** overlap with the key is the eleven excluded tokens — no archetype, no prose
shingle, no `fixture_id`, and none of the three retained identifier markers (`answer_key`,
`seeded_defect`, `expected_finding_count`). Such a file carries no answer-key content: `sd-1` is a
two-character local label, and `seeded` / `정답` / `시드` / `answer key` / `seeded defect` /
`expected finding` are ordinary words and phrases that this host demonstrably ships inside vendor
files. The one real datum reachable through them alone is the *number* of seeded defects, and a
document stating that number in a form a reviewer could use would have to say what was being
counted. This is RK-14, and it applies only to Class IMM — vendor-owned, run-user-unwritable trees. Every root where the fixture's own artifacts can plausibly appear
(`review_root`, `tmp`, `home`, every `--allow-read` root, including O-2's shared agent state
directory) is Class USR and keeps the full 723-token vocabulary **and** the count heuristics.

##### Rejected alternatives

**Run `key_leak_tokens()` verbatim over Class IMM.** This is the reviewer's Required Action read
literally, and it is what this iteration set out to do. Rejected on measurement, not on preference:
40 false hits under `/usr` alone, on files a hard-failure gate offers no remedy for, so every §7
capture fails and the design ships a G2 defect instead of a stricter gate. §A has the paths.

**Carve out the vendor files that hit.** The design already provides carve-outs as the remedy for an
unwanted path, and 40 paths could be carved from `/usr`. Rejected: that is an `--ignore` list in a
carve-out costume — per host, re-derived after every OS update, and growing silently. The design
removed `--ignore` for exactly this reason, and a gate whose exception list is measured rather than
principled proves nothing about the next host.

**Cap pass B by file size, extension or type.** Rejected outright. Unlike the vocabulary
difference, each of these leaves a *placement* that defeats the gate: a key copy appended to a large
file, renamed to `.dylib`, or stored with a byte that fails UTF-8 strict decode at offset zero.
Pass B's exhaustiveness over files is not negotiable, and it is what makes the cost in §E
irreducible.

**Keep the `sd-N` ids as a conjunctive signal** — flag a file that contains *every*
`seeded_defects[].id`. This would close RK-14 at essentially zero marginal cost, and it is the first
thing to reach for if RK-14 ever needs closing. Rejected **for this iteration** because it is a new
hard-failure predicate whose false-positive rate over all 398,241 vendor files has not been
measured, and the one lesson §A teaches is that shipping an unmeasured hard-failure gate over an OS
tree is how you get a capture that can never pass. The partial evidence is encouraging and is
recorded so the next iteration does not start from zero: across `/usr`, the largest number of
*distinct* defect ids in any single vendor file is **three**
(`/usr/share/zsh/5.9/functions/_composer` holds `sd-2`, `sd-3`, `sd-4`), so a conjunction over all
five would not have fired there. That is one root, not three, which is exactly why it is not shipped
here.

#### Decision D-5.2 — the two internal contradictions, reconciled

Both are corrected **in place** in G.3.3, so a reader of the normative text never meets the
contradiction and then a footnote resolving it.

1. **What the proof establishes.** The clause *"so there is nothing a scan could find that the proof
   allows to exist"* is **deleted**. In its place G.3.3 now says, once: Class IMM roots *are*
   content-scanned; the scan simply runs at NEG-5 rather than twice per capture; and the proof
   establishes current write incapability and nothing about pre-existing content, because it never
   opens a file. The paragraph beginning *"The immutability proof is not a substitute for that scan
   and is nowhere offered as one"* is the single authority, and §C above is its derivation. S3's
   row in G.1 is updated to match — both classes are content-scanned, and the immutability proof is
   what makes the IMM scan *durable* rather than what replaces it.
2. **Symlink bounding.** The blanket sentence *"every symlink encountered whose realpath escapes the
   root is itself a hit"* is **replaced** by a statement scoped exactly like the pass-S row it sits
   under: an escaping symlink is a hit under **pass S, which runs for Class USR only**; for Class
   IMM the profile is the evidence, because seatbelt evaluates the resolved target. The same
   paragraph now also states why refusing to follow symlinks costs no content coverage in either
   class, which is the fact §D measured and which the relocation of pass B makes load-bearing.

---

### Components / Interfaces / Data Flow

No new module, no new dependency, no schema version change, no exit-code change, no profile-clause
change, no CLI option added or removed, no `ISOLATION.json` field removed or renamed.

**`scripts/final_review_eval.py`**

| symbol | change |
|---|---|
| `_is_identifier_form(marker)` | **new**, `return "_" in marker`. Its docstring names the three measured collisions (`/usr/share/dict/web2` for `seeded`, `/usr/share/tokenizer/ko/dicrc` for `정답`, `AVContentKeySession.h` for `answer key`) so the next reader does not re-derive why the natural-language markers are separated. A mechanical rule, not a curated list: a marker added to `FIXED_LEAK_MARKERS` tomorrow is classified on the day it is added. |
| `_key_tokens(key, *, include_labels)` | **new**, the single construction above. `include_labels=False` keeps only the identifier-form entries of `FIXED_LEAK_MARKERS` and drops every `seeded_defects[].id`. |
| `key_leak_tokens()` | **behaviour unchanged**; re-expressed as `_key_tokens(key, include_labels=True)`. The existing docstring is kept verbatim — it is still the right description of this function. |
| `key_material_tokens()` | **new**, `_key_tokens(key, include_labels=False)`. |
| `scan_leak_text(path, text, tokens, *, count_heuristics=True)` | **new**, the per-file body lifted out of `scan_leak()` unchanged: normalise, casefold, report every token present, then at most one expected-count hit. Returns the same `{path, token}` / `{path, expected_count_statement}` records. |
| `scan_leak()` | **behaviour unchanged**; its per-file body becomes a call to `scan_leak_text(path, raw, tokens)` with `count_heuristics=True`. It keeps its `rglob`, its `__pycache__` skip, its `(OSError, UnicodeDecodeError)` skip, and its deliberate absence of an exclusion parameter. Every existing caller — `materialize`'s workspace check included — is untouched. |

**`scripts/review_isolation.py`**

| symbol | change |
|---|---|
| `SCAN_PASSES_ALL` | unchanged: `("A", "B", "C", "D", "S")` |
| `SCAN_PASSES_NAME_ONLY` | **renamed** `SCAN_PASSES_IMM`, value **`("A", "B", "C", "D")`**. Removed, not aliased: the old name asserted "name-only" and the value is not name-only. |
| `SCAN_PASSES_IMM_CONTENT` | **not created** (D-4.2's proposal, withdrawn). |
| `scan_readable_set()` | gains `vocabulary: str = "key_leak"` (the other value is `"key_material"`). Pass B moves **into the existing walk**: before the walk, `b_tokens = final_review_eval.key_leak_tokens(key)` or `key_material_tokens(key)` per `vocabulary`, computed once; inside the file loop, after the symlink `continue` and before pass C's prefilter, skip when `"__pycache__" in entry.parts` (matching `scan_leak`), then `entry.read_text(encoding="utf-8")` under `except (OSError, UnicodeDecodeError): pass` (matching `scan_leak`), then extend `hits` with `{"pass": "B", **hit}` for each `scan_leak_text(entry, text, b_tokens, count_heuristics=(vocabulary == "key_leak"))` record. The trailing `if "B" in passes: for hit in final_review_eval.scan_leak(key, [root])` block is **deleted**, and so is the comment above it that justified delegating. Pass C's size prefilter is unchanged. `counters` gains `"content_scanned"` — the number of files pass B actually opened and decoded. `hits` is sorted by `(pass, path)` before return, so the record is stable now that B's hits interleave with A/C/D's instead of being appended after them. |
| `run_negative_probes()` | the NEG-5 loop selects `SCAN_PASSES_ALL` + `vocabulary="key_leak"` for Class USR and `SCAN_PASSES_IMM` + `vocabulary="key_material"` for Class IMM. Each `rescan_detail[]` entry gains `"vocabulary"` and `"content_scanned"` beside its existing `path` / `class` / `passes` / `carve_outs` / `hits`. The comment block at `review_isolation.py:1315-1330` is rewritten: lines 1318-1319 are the duplicated sentence, and lines 1321-1330 state the claim D-5.1 refutes. |
| `build_parser()` | **no change.** |

**Data flow.** `ISOLATION.json`'s `readable_set[]` entries keep `scanned: false` for Class IMM.
That field describes the **session-build-time** scan, which this iteration does not change, and
T-8.7's assertion on it stands as written. It is not in tension with S3's corrected wording, and the
reason is visible in the same document: S3's content leg is discharged for Class IMM at **NEG-5**,
and the NEG-5 probe record is where that in-process rescan's pass set, vocabulary and
`content_scanned` count are reported (§D′). A reader holding one `ISOLATION.json` sees both — `scanned:
false` meaning *not at build time*, and `probes[NEG-5].roots[].content_scanned` giving the number of
files the mandatory pass actually opened (182,969 of the 398,241 regular files walked on this host; the remaining 215,272 fail UTF-8 decode and are skipped exactly as `scan_leak()` already skips them). Neither field can be read as the
whole answer on its own, which is why both are kept.

**And NEG-5 is fail-closed, which is what makes relying on it sound.** `isolate` collects the probe
list and, at `review_isolation.py:1795-1798`, raises `IsolationError` for any probe whose result is
not `PASS` / `UNENFORCED` / `SKIP`. A pass-B hit inside a Class IMM root therefore aborts the
session build; it does not land as a `FAIL` line in an attestation that is written anyway.

---

### Error Handling / Compatibility

* **Exit codes: unchanged, and verified rather than assumed.** A pass-B hit inside a Class IMM root
  raises `IsolationError` at `review_isolation.py:1798`, which `final_review_eval.py:1313-1315`
  maps to `EXIT_LEAK_OR_FIXTURE == 4`. Same code, same printed path, still no `--ignore`.
* **No CLI surface changes.** `--scan-imm-content` is not added. `isolate`'s option set is
  byte-for-byte what ships today, so no invocation, script or document needs editing.
* **`ISOLATION.json` gains two additive fields** inside the NEG-5 probe record's `roots[]`
  (`vocabulary`, `content_scanned`) and removes none. No consumer reads that record positionally,
  and the bundle schema stays `2.0`: the isolation attestation is not the bundle.
* **Two additive fields do not need a `limitations[]` entry** — they narrow the limitation rather
  than widening it. The single existing entry is unchanged, and D-4.2's residual that would have
  needed a second one is closed rather than disclosed.
* **The failure mode that matters is a false hit, and it fails closed.** If a vendor file on some
  other host does contain a key archetype or a six-word run of the key's prose, that capture
  fails at exit 4 with the path printed, and the operator narrows the root or drops it. That is the
  same remedy every other pass offers, and it is the direction this design has consistently chosen.
* **The reverse failure — an unreadable or undecodable file — is treated exactly as `scan_leak()`
  already treats it:** `OSError` and `UnicodeDecodeError` skip the file. This is unchanged
  behaviour, and it is bounded by the other passes: a file pass B cannot decode is still walked by
  pass A, still size-compared and hashed by pass C, and still index-listed by pass D if it is an
  archive.
* **Wall-clock.** A capture's NEG-5 goes from a walk to a measured **2,757.7 s (~46 minutes)** on this host, 71% of it in `/Library/Developer/CommandLineTools` alone. §7's baseline procedure has no
  step timeout that this breaches, and `review_isolation.py`'s `timeout=` arguments all belong to
  `subprocess.run()` calls for the sandboxed command probes. B-6's operator-facing description
  should say the capture takes minutes, which is a wording change to a runbook, not a design change.
* **`COMPATIBILITY.md` is not touched.** D-I stands exactly as iteration 3 settled it. RK-10's
  observation about D-I's "exhaustively scanned" wording is **improved** by this iteration rather
  than disturbed: the readable set is now closer to that wording than it was, never further.

---

### Expected Changed Files / Implementation Steps

Steps 1, 2 and 6 (D-H) are unchanged from iteration 4 and are restated only so the commit plan is
readable end to end.

| # | file | change | hard? |
|---|---|---|---|
| 1 | `scripts/run_logging.py` | D-H.2 — `_residual_matches_are_self_output()`; `safe_embedded_text()` step 2; docstring correction. **Unchanged by iteration 5.** | **HARD** |
| 2 | `scripts/test_run_logging.py` | T-7.3 / T-7.4 / T-7.8 / T-7.9 / T-7.13 / T-7.14. **Unchanged by iteration 5.** | **HARD**, same commit as 1 |
| 3 | `scripts/review_isolation.py` | `SCAN_PASSES_IMM = ("A","B","C","D")`; pass B relocated into the pruned walk with the per-class vocabulary; `vocabulary` parameter; `content_scanned` counter; hits sorted by `(pass, path)`; NEG-5 pass/vocabulary selection and record fields; the `1315-1330` comment block rewritten | **HARD** |
| 4 | `scripts/final_review_eval.py` | `_is_identifier_form()`, `_key_tokens()`, `key_material_tokens()`, `scan_leak_text()`; `key_leak_tokens()` and `scan_leak()` re-expressed over them with behaviour unchanged. **No CLI change.** | **HARD**, same commit as 3 |
| 5 | `scripts/test_review_isolation.py` | T-8.4b (**assertion corrected**, see below), T-8.4c (unchanged), **T-8.4d, T-8.4e, T-8.4f (new)**, T-9.5 (rewritten), and the two in-place corrections below | **HARD**, same commit as 3-4 |
| 6 | `orca-worker-reviewer-orchestration/tools/run_logging.py` | mirror of 1 — `cmp` with `scripts/run_logging.py` must stay byte-identical | same commit as 1 |
| 7 | *§7 baseline re-capture* | re-run per the amended B-1′…B-7 once 1-6 are green, allowing for the new capture duration | after 1-6 |

`CHANGELOG.md`, `SKILL.md` and `COMPATIBILITY.md` are untouched: no schema version moves, no CLI
option is added or removed, and no reader-visible bundle field changes.

---

### Testing Strategy

**Corrected in place, in `scripts/test_review_isolation.py`:**

| existing test | correction |
|---|---|
| `test_an_escaping_symlink_is_not_a_hit_for_a_proven_immutable_root` | `SCAN_PASSES_NAME_ONLY` → `SCAN_PASSES_IMM`, and its two closing assertions become `assertIn("S", SCAN_PASSES_ALL)` / `assertNotIn("S", SCAN_PASSES_IMM)`. It must keep passing **with pass B in the IMM set**, which is exactly §D's fact 2 asserted as behaviour: the walk does not follow the link, so the escaping symlink yields neither an S hit nor a B hit for Class IMM. |
| `test_a_carved_out_subtree_is_not_scanned_because_it_is_not_readable` | switch to `SCAN_PASSES_IMM` and **delete the inline comment** *"(Pass B is `scan_leak()`, which has no exclusion parameter by design and is therefore never run over a root that has one.)"* — it is the assumption §B refutes. Strengthened: plant the key's **prose**, not only `answer_key.json`, under the carved subtree, so the test now proves the carve-out prunes **pass B** and not merely pass A. This is the regression guard for §B. |
| `test_t84_pass_b_catches_a_key_shingle` | unchanged assertion, but it now exercises the walk-driven pass B. |
| **T-8.4b** (iteration 4's own new test, corrected before it is written) | Its stated assertion was *"returns exactly one hit, `pass == \"C\"`"*. **Mandatory pass B falsifies the "exactly one" half**: a byte-identical copy of the answer key contains every archetype and every prose shingle, so pass B fires on `libfoo.dat` as well. The assertion becomes *exactly one hit whose `pass == "C"`*, plus a positive assertion that pass-B hits on the same path are present — the two passes corroborating each other is the correct relationship, and asserting "one hit total" would have been asserting that pass B does not work. The `("A","D")`-returns-zero half of the test is unchanged. Caught here rather than at IMPLEMENTATION time, which is the point of writing the assertion out in the design. |

**New, in `scripts/test_review_isolation.py`:**

| id | asserts |
|---|---|
| **T-8.4d** | *The review's F-001 counterexample, closed.* Over a fixture root, plant three files under unrelated basenames, none named like the fixture and none byte-identical to the key: (a) `vendor.dat` holding `json.dumps(key, indent=4, ensure_ascii=False)` — a **reformatted** copy (byte-identical to nothing, so pass C cannot see it); (b) `notes.log` holding a single `seeded_defects[0]["negative_space_argument"]` re-wrapped at 40 columns — a **partial excerpt**; (c) `chat.txt` holding one `summary` embedded in unrelated prose with `> ` quote markers — a **quoted fragment**. With `passes=SCAN_PASSES_IMM, vocabulary="key_material"`, **each of the three is a pass-B hit**. The test then asserts the same three files produce **zero** hits under iteration 4's `("A", "C", "D")`, and says so in its docstring: this is the gap the DESIGN review named, reproduced and closed. |
| **T-8.4e** | *The IMM vocabulary is specific, not merely smaller.* Over a fixture root holding a file that contains only the eleven excluded tokens, plus the literal sentence from `AVContentKeySession.h` ("a persistable content key cannot be used to answer key requests"), plus one sentence shaped like `_EXPECTED_COUNT` ("you should find three defects"), `vocabulary="key_material"` returns **zero** hits while `vocabulary="key_leak"` returns hits — the measured `/usr` situation reduced to a unit test, so a future contributor who "simplifies" the two vocabularies back into one fails here with the reason in front of them. Asserts in the same test that a file containing one `archetype` **is** a hit under both. |
| **T-8.4f** | *The two vocabularies cannot drift apart.* `key_material_tokens(key) < key_leak_tokens(key)` (proper subset), and the difference is exactly `{m for m in FIXED_LEAK_MARKERS if "_" not in m} ∪ {entry["id"].casefold() for entry in key["seeded_defects"]}` — computed from `FIXED_LEAK_MARKERS` and from the key, not hard-coded to eleven strings, so the assertion survives a fixture with more defects or a sixth marker. Asserts `count_heuristics` is off for `"key_material"` and on for `"key_leak"` by calling `scan_leak_text()` directly with each. |
| **T-9.5** *(rewritten)* | *The NEG-5 contract, at the probe record.* Against the synthetic fixture, every `class == "IMM"` entry has `passes == ["A","B","C","D"]` and `vocabulary == "key_material"`; every `class == "USR"` entry has `passes == ["A","B","C","D","S"]` and `vocabulary == "key_leak"`; every entry carries an integer `content_scanned`. Asserts there is **no** `imm_content_scan` field and **no** `--scan-imm-content` option on the `isolate` parser — the regression guard against reintroducing a default-off content gate. Darwin-only, skipped with an explicit reason elsewhere, like the rest of T-9. |

**New, in `scripts/test_final_review_eval.py`** (the module that already owns `scan_leak`'s tests):

| id | asserts |
|---|---|
| **T-8.4g** | *The refactor is behaviour-preserving.* For the shipped fixture and for a synthetic tree containing one file per hit shape (token hit, expected-count hit, clean, undecodable, `__pycache__`), `scan_leak(key, [tree])` returns records equal to the pre-refactor implementation's — same paths, same tokens, same at-most-one expected-count record per file, same ordering. This is what licenses "behaviour unchanged" in the Components table rather than leaving it as an assertion. |

**Unchanged and still required:** T-7.1…T-7.14; T-8.1…T-8.11 including T-8.4c;
T-9.1…T-9.4, T-9.6…T-9.9; T-10; NEG-0…NEG-8. `python3 -m unittest discover -s scripts -p
'test_*.py'`, `validate_skills.py`, `verify_package.py`, and the `cmp` of the two `run_logging.py`
copies are the gate, exactly as before.

---

### Risks / Open Issues

* **RK-14 (new).** Pass B's Class IMM vocabulary excludes eleven tokens — the six
  natural-language `FIXED_LEAK_MARKERS` and the five `seeded_defects[].id`s — and the two
  natural-language count heuristics, so a file inside a vendor-owned, run-user-unwritable subtree
  whose *entire* overlap with the key is the eleven excluded tokens (`answer key`, `seeded defect`,
  `expected finding`, `seeded`, `정답`, `시드`, `sd-1`…`sd-5`) — no archetype, no prose shingle, no
  `fixture_id`, none of the three retained identifier markers — is not flagged. Such a
  file carries no answer-key content beyond, at most, the number of seeded defects. **Why the
  exclusion is not optional:** the alternative is measured to produce 40 false hard failures under
  `/usr` alone (§A), which is a capture that can never pass. **What would close it:** the
  conjunctive-id rule of D-5.1's rejected alternatives, once someone measures its false-positive
  rate over all admitted IMM roots on more than this one host. **Where it does not apply:** every
  Class USR root, which keeps the full 723-token vocabulary and the count heuristics — including
  O-2's shared agent state directory, the one place in-scope key material could plausibly reach an
  admitted root.
* **RK-15 (new).** A capture now costs a measured ~46 minutes instead of a walk, and the cost scales with the
  bytes under the admitted IMM roots, which is a property of the host's OS install rather than of
  this repository. A host with a much larger `/System` pays proportionally more. Nothing about the
  design bounds it, and nothing should: the alternatives that would bound it (size caps, extension
  filters, sampling) are each a placement the gate would then miss. The mitigation is procedural —
  §7's baseline is captured once per remediation run — and `content_scanned` in the NEG-5 record
  makes the actual per-capture work visible instead of estimated. **The constant, unlike the
  predicate, is IMPLEMENTATION's to improve.** §E's figure comes from the straightforward form —
  normalise each file, then match — and is dominated by *decodable* bytes rather than by total
  bytes, which is why `/usr` (mostly binaries that fail UTF-8 decode in the first few bytes) is
  cheap per gigabyte and the SDK trees are not. A single-pass matcher over the normalised text is
  free to be substituted as long as the hit set is unchanged; T-8.4d/e/f and T-8.4g are what pin
  the hit set, so such a change is checkable rather than a matter of trust. The concrete candidate,
  named so the next person does not have to find it: intersect the *file's* 6-word shingle set with
  the key's, which turns 712 substring scans per file into one pass of set lookups. It is **not**
  adopted here because it is not obviously hit-set-equivalent at the margins — `token in haystack`
  is a substring test and a shingle-set intersection is a word-boundary test, and they differ on a
  key shingle embedded inside longer words. Proving or bounding that difference is the work; doing
  it silently is not.
* **RK-12 — rewritten**, see the risk table in `## DESIGN iteration 4`: its residual is closed and
  replaced by RK-14.
* **RK-13 stands** and is now doubly true: `SCAN_PASSES_NAME_ONLY` is removed rather than aliased,
  and its value changed twice (`("A","D")` → `("A","C","D")` → `("A","B","C","D")`). An out-of-tree
  caller must break loudly at import rather than silently scan less than the current contract.
* **RK-11, RK-7, RK-1…RK-10 stand unchanged.** RK-2's *label* is corrected (Class IMM roots are
  content-scanned now); its mechanism column, which is about the immutability proof, is untouched.
* **O-1, O-2, O-3 stand unchanged.** O-1 (the `orca` CLI's behaviour from inside the sandbox)
  remains undischarged and is still a blocking pre-flight assertion for the Step 7 capture.
* **Not reopened, deliberately:** D-H.2, D-4.1, RK-7, D-I, `COMPATIBILITY.md`,
  `ISOLATION.json.limitations[]`, the readable-set membership rules, and every iteration-2 and
  iteration-3 decision. The review confirmed the first two sound; the rest are outside F-001.

---

## DESIGN iteration 1 (Run `run_75c5c6046f35`) — full consistency sweep for `REVIEW_DESIGN_iteration5.md` F-001/F-002

STATUS: COMPLETE

Predecessor Run `run_4d1c47c838db` stays ESCALATED and untouched. This document is a copy of that
Run's `DESIGN.md` after its iteration 5, corrected **in place**. No decision is reopened: D-H.2,
D-4.1, RK-7, mandatory pass B (D-5.1), pass C's size prefilter, pass S's Class-USR-only scope, D-I,
`COMPATIBILITY.md`, OS-23, H-1/H-2/H-4/H-5, and every lifecycle/Risk/Quality/Agent-Profile semantic
stand exactly as approved. The whole of this iteration is making the **document** say one thing
about decisions already made, and reporting — rather than smoothing over — the two places where the
committed code has not yet caught up to what DESIGN requires.

### Summary / Requirements

| review finding | disposition |
|---|---|
| **F-001** — G.3's classification table and the emitted `ISOLATION.json.limitations[]` sentence still say Class IMM is "not content-scanned", contradicting G.1 S3, G.3.3 and D-5.1's mandatory pass B. | **Closed.** The classification table now splits *session-build-time* scanning from *NEG-5* scanning and states the IMM pass set at the point of the claim; the `limitations[]` sentence is replaced with the exact privileged-writer-only sentence G.3.3 promises. Four further passages carrying the same stale claim, which the review did not name, are corrected too (see the sweep table). |
| **F-002** — the probe-launch rule and two data-flow sentences still describe NEG-5 as a sandboxed subprocess launched through `wrap_command()`, contradicting G.9/D-5.1's in-process contract. | **Closed.** The launch rule is now `NEG-2 … NEG-4 — and only those three`, followed by an explicit authoritative statement of what NEG-5 is; three further "from inside the sandbox" passages, one of them outside the four locations the review named, are corrected. |

The task's seven concepts were swept across the whole document, not only at the four named
locations. **Seven additional stale passages were found and fixed**; they are marked ★ below.

### Current Architecture

Re-derived on this host against the code at `1309d89`. `git diff --stat cac283b..HEAD -- scripts/`
and `19753f6..HEAD -- scripts/` are both **empty** and `scripts/` is clean in the working tree, so
every passage in this document that describes "what ships" is describing the code at HEAD and the
commit references in iterations 4 and 5 are still accurate. Two facts were verified directly rather
than taken from the task spec or from a predecessor report:

* `scripts/review_isolation.py:491` is `SCAN_PASSES_NAME_ONLY = ("A", "D")`, selected for
  `CLASS_IMM` at `scripts/review_isolation.py:1334-1335`. This is the pass set D-4.2 proposed and
  D-5.1 **superseded**. See **Finding F-201**.
* `--scan-imm-content`, `scan_imm_content`, `imm_content_scan` and `SCAN_PASSES_IMM_CONTENT` appear
  **nowhere** under `scripts/` or `tests/` — only inside DESIGN artifacts, and there only as
  "withdrawn / not created / not added". Confirmed by
  `grep -rn "scan.imm.content\|imm_content_scan\|SCAN_PASSES_IMM_CONTENT" scripts/ tests/`, which
  returns nothing. The design and the CLI therefore do **not** disagree about the flag, and D-5.1's
  "withdrawn, not turned into a no-op" is satisfied by construction.

### Proposed Design

No design decision is added, removed or changed. The corrections are documentary.

### Components / Interfaces / Data Flow

No component, interface, function signature, JSON field, exit code, profile clause, CLI option or
test is added, removed or renamed by this iteration. `ISOLATION.json`'s field set is unchanged; one
`limitations[]` **string value** changes, and that value was already required to change by G.3.3's
own "that sentence, and nothing broader" rule — this iteration makes the example obey the rule.

**The sweep, concept by concept.** ★ marks a location the review did not name.

| # | concept | location | correction |
|---|---|---|---|
| 1 | answer-key isolation trust boundary | G.6 `ISOLATION.json.limitations[]` | replaced with G.3.3's exact privileged-writer-only sentence; the "not content-scanned" clause is gone |
| 1 | ″ | ★ G.6 Rules | new bullet scoping `scanned: false` to session-build time and pointing at `probes[NEG-5].roots[].content_scanned` |
| 2 | pass-set semantics | ★ G.3.3 pass-table lead-in | was "**Every Class USR root** is scanned exhaustively", heading a table that defines the passes for **both** classes; now states the table is the definition for both, and names each class's pass set |
| 2 | ″ | ★ Summary requirement row and ordering rule 2 | `NEG-1 … NEG-6` → `NEG-1 … NEG-8` (NEG-7/NEG-8 were added by iteration 2 and these two iteration-1 ranges were never updated) |
| 3 | mandatory pass B / no opt-in flag | G.3 classification table | IMM's NEG-5 column states A/B/C/D, "on by default … no flag that turns it off" |
| 4 | filesystem immutability | G.3 classification table | IMM's build-time cell no longer offers the proof as the *reason* a scan is unnecessary; the reason is now NEG-5 + double cost |
| 4 | ″ | G.3, new paragraph after the table | states once, normatively, that the proof establishes current write incapability only and is never a substitute for a content scan, and that any passage reading otherwise is stale and loses |
| 4 | ″ | ★ iteration-2 delta table, G.3 classification row | "unscanned **because planting is impossible**" → "not additionally scanned at session-build time", with the rationale explicitly marked superseded by iteration 5 |
| 4 | ″ | ★ iteration-4 Error Handling, RK-10 bullet | "Class IMM roots were partly unscanned before and are partly unscanned after" scoped to *iteration 4 itself*, with a forward pointer to iteration 5 where the truth-value changes |
| 5 | evidence-bundle sanitization | D-H.1/H.2/H.3/H.4/H.5, D-4.1, RK-7, RK-11, T-7.3/T-7.13/T-7.14 | **swept, no change needed.** The per-match `m.expand(replacement) == m.group(0)` rule, the "text fixed point is an immediate consequence" note, RK-7(a)/(b)/(c)'s fail-safe framing, and every cross-reference to them are mutually consistent and consistent with iteration 5's "not a character changed". Substance not reopened. |
| 6 | NEG-5 / pass-set state | G.9 probe-launch rule | `NEG-2 … NEG-5` → `NEG-2 … NEG-4 — and only those three`, plus an authoritative paragraph on what NEG-5 is and where NEG-6/NEG-7/NEG-8 run |
| 6 | ″ | ★ RK-2 mitigation cell | "NEG-5/NEG-7/NEG-8 re-prove it **from inside the sandbox**" split: NEG-7/NEG-8 sandboxed, NEG-5 in-process |
| 6 | ″ | ★ iteration-4 §C | "It runs *from inside the sandbox*, over the profile's own admitted set" → "over the profile's own **computed** admitted set", with a note that §C's argument never depended on a process boundary |
| 6 | ″ | iteration-4 Data-flow paragraph | "the from-inside-the-sandbox pass set" → "the in-process rescan's pass set" |
| 6 | ″ | iteration-5 Data-flow paragraph | same correction |
| 7 | current implementation state | — | see **Findings** below. No requirement language was softened to match the code. |
| — | navigability | iteration-3 correction table row 1; iteration-4 change list | the paragraph both call *"the 'not content-scanned' paragraph"* no longer contains that phrase, so a reader cannot find it by search. Both labels now also give the paragraph's current opening words. The historical labels are kept, because each accurately names what the paragraph said when that iteration edited it. |

The process boundary, stated once and now agreeing everywhere: **NEG-0** unsandboxed control;
**NEG-1** in-process walk of `review_root`; **NEG-2 / NEG-3 / NEG-4** sandboxed subprocesses through
`wrap_command()` / `_command_probe()`; **NEG-5** in-process `scan_readable_set()` over the computed
readable set; **NEG-6** in-parent `sandbox-exec -f … /usr/bin/true`; **NEG-7 / NEG-8** sandboxed
probes. Under `--enforcement none`, NEG-2 … NEG-8 are all recorded `NOT_APPLICABLE_UNENFORCED` —
NEG-5 included, because the admitted set it scans is only meaningful when a profile is enforced.
This matches `scripts/review_isolation.py:1274-1284` exactly and is unchanged by this iteration.

### Error Handling / Compatibility

Nothing moves. No exit code, no schema version, no CLI option, no profile clause, no test contract,
no `COMPATIBILITY.md` text, no `VERSION`, no `LICENSE-DECISION.md`. `ISOLATION.json`'s
`limitations[]` **string** changes to the sentence G.3.3 already required; the field, its cardinality
and its position are unchanged, and no consumer parses that string.

### Expected Changed Files / Implementation Steps

Unchanged from iteration 5's table (Steps 1-7). This iteration adds no implementation step and
removes none. Step 3 remains the step that closes **F-201**, and Step 1 the step that closes
**F-202**.

### Testing Strategy

No test is added, removed or altered. T-7, T-8, T-9 and the amended B-1′…B-7 baseline procedure
stand exactly as iterations 4 and 5 left them. The verification this iteration needed was
documentary and was run:

* `diff` of this file against `artifacts/runs/run_4d1c47c838db/DESIGN.md` — **17 correction hunks**
  plus this appended iteration section. Every one of the 17 is in the sweep table above, and none
  falls inside D-H (predecessor lines 858-1018), D-I (1019-1066), `COMPATIBILITY.md`, OS-23, or any
  H-1/H-2/H-4/H-5 or lifecycle/Risk/Quality/Agent-Profile passage: the highest-numbered hunk before
  D-H ends at predecessor line 851, and the next is at 1319 (RK-2).
* Residual greps over the corrected file, all clean: `nothing a scan could find` / `planting is
  impossible` / `scanning would be` survive **only** inside explicitly-superseded or
  quoting-the-old-text passages; `from inside the sandbox` survives only where it is true (the
  `orca` CLI in O-1, NEG-7/NEG-8 in RK-2) or where it is quoted as the wording being corrected;
  `not content-scanned` survives only as the historical *name* of a paragraph, never as a claim.
* `grep -rn` for the withdrawn flag over `scripts/` and `tests/` — no hit, as recorded above.
* `git diff --stat` of `scripts/` at `cac283b..HEAD` and `19753f6..HEAD` — both empty, which is what
  licenses this document's "what ships" passages and Findings F-201/F-202 below.

### Risks / Open Issues

* **RK-1 … RK-15, O-1 … O-3 stand unchanged.** No risk is added, closed or re-weighted. RK-2's
  mitigation cell is corrected on the NEG-5 process boundary only; its substance is untouched.
* **Not reopened, deliberately:** D-H.2, D-4.1, RK-7, D-5.1 (mandatory pass B), pass C's size
  prefilter, pass S's Class-USR-only scope, D-I, `COMPATIBILITY.md`, `ISOLATION.json`'s field set,
  the readable-set membership rules, OS-23, H-1/H-2/H-4/H-5, and all lifecycle/Risk/Quality/Agent
  Profile semantics.

### Findings

Both findings are **gaps between what DESIGN requires and what the committed code does**. Neither is
a defect in DESIGN, and neither was closed by editing DESIGN to describe the code as compliant.
Implementation changes are outside this Task's scope, so no code was touched.

#### F-201 — `SCAN_PASSES_NAME_ONLY = ("A", "D")` is the pass set D-5.1 superseded; pass B is not mandatory in the running code

* **DESIGN requires** (D-5.1; G.9's NEG-5 row; G.3.3's pass-B row; iteration 5's Components table):
  `SCAN_PASSES_IMM = ("A", "B", "C", "D")`, pass B **mandatory** over every admitted root with a
  per-class vocabulary, pass B relocated into the carve-out-pruned walk, a `vocabulary` parameter on
  `scan_readable_set()`, a `content_scanned` counter, and hits sorted by `(pass, path)`.
* **The code does instead**, at `1309d89` (and identically at `cac283b` and `19753f6`):
  * `scripts/review_isolation.py:491` — `SCAN_PASSES_NAME_ONLY = ("A", "D")`, with a docstring
    comment asserting the passes are "a WALK rather than a READ".
  * `scripts/review_isolation.py:1334-1335` — `passes = SCAN_PASSES_NAME_ONLY if entry["class"] ==
    CLASS_IMM else SCAN_PASSES_ALL`.
  * `scripts/review_isolation.py:1321-1330` — the comment block stating *"What passes B and C would
    be re-deriving is already established more strongly by the recursive proof"*, which is precisely
    the claim iteration 4 §C and iteration 5 §C refute. Iteration 5 already schedules its deletion.
  * `scripts/review_isolation.py:571-576` — `scan_readable_set()` has no `vocabulary` parameter, no
    `content_scanned` counter, and reaches pass B by delegating to `final_review_eval.scan_leak()`
    outside the pruned walk — the carve-out-blind `rglob` iteration 5 §B shows is unsafe over a
    Class IMM root. Its inline comment still asserts Class USR is "the only class that runs pass B",
    which D-5.1 falsifies; iteration 5 already schedules the deletion of both the block and the
    comment.
  * `scripts/final_review_eval.py` has `key_leak_tokens()` (line 304) but no `key_material_tokens()`,
    no `scan_leak_text()`, no `_key_tokens()` and no `_is_identifier_form()`.
* **Consequence:** a §7 baseline captured from the current code runs Class IMM at A/D only — not
  even iteration 4's A/C/D — so the readable set is unproved clean of both a byte-identical copy
  (pass C) and a reformatted or partial copy (pass B). This is the exact gap
  `REVIEW_DESIGN_iteration5.md`'s predecessor raised, still open **in the code**.
* **Not a DESIGN defect.** DESIGN states this correctly and in the right tense throughout: iteration
  4 §C calls it "the shipped A/D-only pass set", T-8.4b says "the same call with the shipped
  `("A", "D")` set returns zero hits", and RK-13 records that the constant is removed rather than
  aliased. Nothing was softened.
* **Closed by:** iteration 5's Implementation Step 3 (`scripts/review_isolation.py`) and Step 4
  (`scripts/final_review_eval.py`), both **HARD**, both in the same commit as Step 5's tests.

#### F-202 — `safe_embedded_text()`'s residue check is still the text-equality rule D-4.1 replaced

* **DESIGN requires** (D-4.1; H.2 step 2; iteration 4's Components table): a **per-match** rule —
  for every `(name, pattern, replacement)` in `REDACTION_CATEGORIES`, every match `m` of `pattern`
  in `candidate` must satisfy `m.expand(replacement) == m.group(0)` — implemented as
  `_residual_matches_are_self_output()`. Text equality is an immediate consequence and may be kept
  only as a redundant check.
* **The code does instead:** `scripts/run_logging.py:1235-1238` computes `again, _second_pass_counts
  = redact_text(candidate)` and returns `"redaction_residue"` on `again != candidate` — bare text
  equality, with no per-match test. `_residual_matches_are_self_output` does not exist in the file.
* **Consequence:** the deployed gate is weaker than the designed one in the way D-4.1 §B names —
  whole-string comparison lets a match that removed something be masked by matches elsewhere in the
  text. It is not the `extra == ()` bug (that clause is already absent, which is why F-101 is
  closed at the code level); it is the residual half of the same correction.
* **Not a DESIGN defect, and DESIGN already says so.** Iteration 4's Error Handling states "relative
  to what is deployed at `cac283b` (text equality) the corrected rule embeds a subset", and RK-11
  records that the corrected rule is strictly stronger than what is deployed. Both are accurate
  against HEAD.
* **Closed by:** iteration 5's Implementation Step 1 (`scripts/run_logging.py`), **HARD**, with its
  Step 6 mirror to `orca-worker-reviewer-orchestration/tools/run_logging.py` kept byte-identical.

#### No third gap found

The remaining iteration-4/5 requirements are either already satisfied by the code (the withdrawn
flag is genuinely absent everywhere; pass S is already Class-USR-only via `SCAN_PASSES_ALL`; NEG-5
already runs in-process over the computed readable set with per-root carve-outs threaded in; NEG-5
is already fail-closed through `IsolationError`) or are scheduled work in Steps 3-5 that F-201 and
F-202 already account for. One cosmetic code artifact was observed and is **not** raised as a
separate finding because iteration 5 already schedules its removal by name: the sentence duplicated
at `scripts/review_isolation.py:1316-1317` and `1318-1319`, inside the same NEG-5 comment block
Step 3 rewrites wholesale.
