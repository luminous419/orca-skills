# OS-14 — product permission scope for the dispatched Claude process: a PROPOSAL, not executed

**Status: 승인 대기 (awaiting approval). Nothing in this document has been executed,
configured, registered or enabled.** It is the written form of the design's **I-9** and of
§6's "Permission scope for the dispatched process", produced inside the first foundation PR
because the request asked for the proposal in writing — and *only* in writing.

This document **does not** widen any execution permission, does not promote any experimental
allow-list to a product default, and does not authorise a model turn. It proposes; the
product decision is separate from D2/D3/D5/D8 and has not been taken.

---

## 0. Why this is a separate decision at all

A dispatched Worker or Reviewer under the hosted driver is a **non-interactive** `claude`
turn. A non-interactive turn **cannot answer a permission prompt**: there is no human at the
other end of it and no TUI to render one. So the permission surface a role needs has to be
expressible **in advance** — in argv and in settings — or the role simply cannot do its job,
and the failure mode is a turn that stalls or silently refuses rather than one that asks.

That is the whole reason the scope must be decided before the hosted driver is enabled, and
the reason it is a **product** decision rather than a driver-implementation detail: it
determines what an automated agent is allowed to do to a repository without a person in the
loop.

**Two things this proposal will not do, whatever is approved.**

1. It will **never** propose `bypassPermissions` or `--dangerously-skip-permissions` for a
   dispatched role. Both exist, both are documented by the installed CLI as "recommended only
   for sandboxes with no internet access", and neither is an acceptable default for an agent
   editing a real repository.
2. It will **not** turn the first foundation PR into a permission framework. The scope below
   is a decision document; implementing it is I-9, and I-9 is gated on the approval, not on
   this text.

## 1. Where the option names come from

Every flag named below was read from the **installed** CLI's own read-only help on
**2026-11-01**:

```
$ claude --version
2.1.260 (Claude Code)
$ claude -p --help
```

No syntax is invented, and no flag is named that the installed help does not list. Where a
behaviour is **not** established by that help, this document says so rather than guessing —
the help text states what an option *is*, not what a provider does with it under every
configuration, and that distinction is kept.

The options this proposal uses, verbatim from that help:

| Option | The installed help's own description (abridged) |
| --- | --- |
| `--permission-mode <mode>` | permission mode for the session; choices `acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan` |
| `--permission-prompts <target>` | who answers permission prompts with `--print`: `host` (the SDK host or `--permission-prompt-tool`) or `none` (nobody: anything that would prompt is denied automatically; the permission mode still decides everything else) |
| `--allowedTools, --allowed-tools <tools...>` | comma or space-separated list of tool names to allow (e.g. `"Bash(git *) Edit"`) |
| `--disallowedTools, --disallowed-tools <tools...>` | comma or space-separated list of tool names to deny |
| `--tools <tools...>` | the list of available tools from the built-in set; `""` disables all tools, `default` uses all, or specific names (e.g. `"Bash,Edit,Read"`) |
| `--add-dir <directories...>` | additional directories to allow tool access to |
| `--settings <file-or-json>` | path to a settings JSON file, or a JSON string, to load additional settings from |
| `--setting-sources <sources>` | comma-separated list of setting sources to load (`user`, `project`, `local`) |
| `--restricted` | removes the built-in tools that run commands or code (Bash, PowerShell, REPL and the other code-running tools) and WebFetch unless `--tools` names them; ignores user, project and local settings files (managed settings and `--settings` still apply); confines the file tools to the working directories (`--add-dir` included); **refuses `bypassPermissions`**; and lets only a person or the configured permission handler approve writes to settings, git and tool-configuration files |
| `--strict-mcp-config` | only use MCP servers from `--mcp-config`, ignoring all other MCP configurations |
| `--safe-mode` | start with all customizations disabled (CLAUDE.md, skills, plugins, hooks, MCP servers, custom commands and agents, …) |
| `--session-id <uuid>` | use a specific session ID for the conversation (must be a valid UUID) |

## 2. What each role actually needs

The two dispatched roles do different work and should not get one permission set. Both are
stated as the **minimum** that lets the role settle its own Dispatch, because a role that
cannot settle its Dispatch is worse than a refused one — it leaves the engine waiting.

### 2.1 Worker — reads, edits and runs the project's tests

| Need | Why the role cannot do its job without it | Proposed expression |
| --- | --- | --- |
| **read** the repository | it is asked to change code it has not seen | the built-in file read tools, confined to the working directories |
| **edit** files under the worktree | the deliverable is a diff | the built-in edit/write tools, confined to the working directories |
| **run the project's test and validation commands** | `UNIT_TEST_STATUS` is a required field of its own result contract, and a Worker that cannot run tests can only claim a status it did not observe | a **narrow, explicit** `--allowed-tools` list of `Bash(...)` patterns naming the project's own validators and test runners — never a bare `Bash` |
| **write its own result artifact** | the settlement body is read from it | the same edit tools, confined to the run's artifact directory |
| **settle its Dispatch** (`worker_done`) | the engine blocks on it | one `Bash(orca orchestration send …)` pattern, or — preferably — a dedicated non-shell path, see §5 |

### 2.2 Reviewer — reads and verifies; does not edit production code

| Need | Why | Proposed expression |
| --- | --- | --- |
| **read** the repository and the Worker's artifacts | the verdict is about them | the built-in file read tools, confined to the working directories |
| **re-run the validators and the test suite** | a reviewer that accepts a claimed PASS is not reviewing | the **same** narrow `Bash(...)` allow-list as the Worker |
| **write its own review artifact only** | the verdict has to land somewhere | edit tools, and the review-artifact path is the only place a review is expected to write |
| **settle its Dispatch** | as above | as above |

**Deliberately NOT proposed for either role:** `git commit` / `git push` / branch mutation
(the Coordinator commits, after both phase reviews PASS), any network fetch, any package
install or uninstall, any change to settings, hooks or tool configuration, and any write
outside the worktree and the run's artifact directory.

## 3. The workspace and settings this proposal would apply

| Decision | Proposal | Why |
| --- | --- | --- |
| permission mode | **`acceptEdits`** for the Worker; **`dontAsk`** or `acceptEdits` for the Reviewer — and **never** `bypassPermissions` | edits inside the worktree are the role's whole job; everything else should still be gated |
| who answers prompts | **`--permission-prompts none`** | the honest setting for a non-interactive turn: anything that *would* prompt is denied automatically rather than hanging. The alternative (`host` plus a permission handler) is a bigger decision and is listed as an alternative in §5 |
| settings sources | **`--setting-sources ''`** (load none) plus an explicit **`--settings <file>`** | the role's permissions must come from one reviewed file, not from whatever a developer machine happens to have in `~/.claude` or in the project |
| customizations | **`--safe-mode`** | a dispatched role's behaviour should not depend on locally installed skills, hooks, plugins or MCP servers |
| MCP | **`--strict-mcp-config`** with no `--mcp-config` | no MCP server for a dispatched role in the first scope |
| working directories | the run's worktree only; **no `--add-dir`** unless a specific need is approved | the confinement is the point |
| tool set | an explicit **`--tools`** list rather than `default` | a tool nobody asked for is a surface nobody reviewed |
| hardening | **`--restricted`** is the strongest single lever the installed help offers: it drops the code-running tools unless `--tools` names them, **ignores user/project/local settings**, confines file tools to the working directories, and **refuses `bypassPermissions`** outright | it makes three of the rows above properties of the process rather than of our argv discipline |

**One honest caveat on `--restricted`.** Its own help says it "lets only a person or the
configured permission handler approve writes to settings, git and tool-configuration files".
In a non-interactive turn with `--permission-prompts none` there is no person, so such a
write is **denied**. That is the desired outcome for this scope — but it means a role under
`--restricted` cannot write settings or git configuration **at all**, and any future need to
do so is a new decision rather than a flag change.

## 4. What this scope cannot enforce — stated, not papered over

These are limits of the mechanism, not gaps in the proposal, and they are the reason this
scope is a *decision* rather than a guarantee:

1. **An allow-listed command is a command.** `Bash(pytest *)` permits whatever `pytest`'s own
   configuration causes it to execute. A pattern allow-list bounds the **verb**, not the
   transitive behaviour behind it. Nothing in this proposal claims otherwise.
2. **Pattern matching is not a sandbox.** Shell quoting, environment variables and wrapper
   scripts can make one allowed verb do more than its pattern suggests. Real containment is
   an OS-level sandbox, which is a separate decision and is **not** proposed here.
3. **Confinement to the working directory is enforced by the CLI, not by us.** The claim rests
   on `--restricted`'s documented behaviour; this repository has not independently verified
   it, and the verification in §6 is what would.
4. **A turn that is denied may still settle with a wrong answer.** Permissions bound what a
   role can *do*, never whether its conclusion is correct. The decision-gate contract and the
   phase Reviewer remain the mechanisms for that.
5. **The experimental two-command allow-list from the feasibility run is an observation, not a
   product default**, and this proposal does not promote it. It is recorded in
   `docs/OS-14-feasibility-evidence-summary.md` as what one probe ran with, nothing more.

## 5. Non-interactive prompt handling, and the one alternative worth naming

**The proposal:** `--permission-prompts none`. Anything that would prompt is denied
automatically; the permission mode still decides everything else. A denial is visible in the
turn's own stream, so the role can report it and the Coordinator can act on it — which is
strictly better than a turn that waits for an answer nobody can give.

**The alternative, named because it is the only other shape the installed help offers:**
`--permission-prompts host` together with `--permission-prompt-tool`, i.e. a programmatic
handler that answers prompts on policy. That is a larger decision — it introduces a second
authority that can grant permissions at runtime, and therefore a second thing to review and
audit — and it should not be adopted by default. It is the right shape if, and only if, the
narrow allow-list in §2 proves too narrow in practice.

**The settlement path is worth separating from the shell.** Letting a role settle its Dispatch
through `Bash(orca orchestration send …)` means the role holds a shell pattern it needs for
exactly one purpose. A dedicated, non-shell settlement path for the dispatched role would
remove that pattern from both roles' allow-lists. It is listed here as a design option for
I-9, not as an approved change.

## 6. The small verification to run AFTER approval, and its model-turn budget

Deliberately small, and deliberately **post-approval**: none of it has been run, and none of
it may be run under this PR's zero-turn budget.

| # | What it establishes | Shape | Model turns |
| --- | --- | --- | --- |
| **V-1** | the composed permission argv is **accepted** by the installed CLI and the session starts | one `--print` turn with a trivial prompt, under the proposed argv | **1** |
| **V-2** | a **permitted** action is permitted: one read and one in-worktree edit succeed | one turn, scripted prompt | **1** |
| **V-3** | a **denied** action is denied **without prompting**: one command outside the allow-list, and one write outside the worktree, are refused and the refusal is visible in the stream | one turn, two attempts | **1** |
| **V-4** | the role can settle its own Dispatch under the scope | one turn ending in the settlement call | **1** |

**Proposed budget: 4 model turns for the Worker scope and 4 for the Reviewer scope — 8 in
total, one run, one day, recorded as evidence.** Not a rolling allowance, not a licence for
further experimentation, and explicitly **not** a cost or latency commitment: D5 accepted the
*design* cost of per-dispatch verification and declined to guarantee billing or latency, and
nothing here changes that.

**What V-1…V-4 would NOT establish**, so the result cannot be over-read: they are checks that
the *declared* scope behaves as the installed CLI documents it on **one** build, on **one**
platform, for **one** configuration. They are not a security proof, not a sandbox, and not
evidence about any other CLI version. A later CLI version is a new observation.

## 7. What is being asked for

1. **Approve or amend §2's per-role needs** — in particular whether a Reviewer may run the
   test suite (this proposal says it must) and whether either role may hold a shell pattern
   at all (§5's alternative).
2. **Approve or amend §3's workspace and settings decisions**, especially `--restricted`,
   `--permission-prompts none` and `--setting-sources ''`.
3. **Acknowledge §4's unenforceable limits** as accepted limits rather than open work.
4. **Approve the 8-turn verification budget in §6**, or state a smaller one.

Until all four are answered, **I-9 stays unimplemented and the hosted driver stays
unregistered**. That is not a blocker for the foundation code in this PR, which is why the
two were deliberately separated.
