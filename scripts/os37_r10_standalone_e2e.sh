#!/bin/bash
# =====================================================================================
# OS-37 R10 -- THE HEADLINE.  A REAL Worker -> Reviewer -> correction -> fresh Final
# Review workflow, executed by the deterministic workflow graph through
# `run_workflow.py --adapter standalone`, in a process whose PATH cannot resolve `orca`
# and whose environment carries no ORCA_* marker.
#
# Nothing here is simulated at the workflow level: the graph is the repository's real
# LangGraph-or-fallback graph, the adapter is StandaloneAdapter, and every agent turn is
# a real local process on a real headless PTY.
#
# WHICH AGENT CLI IS INVOKED is decided by $R10_BINARY / $R10_BIN_DIR (see TEST.md).
#   default : the OS-37 native stub fixture (a real native executable, real execve)
#   optional: a real installed agent CLI, when the caller supplies one
#
# Usage: os37_r10_standalone_e2e.sh <output-dir>
#
# TRACKED, and that is external review finding #1.  This harness used to live in
# `artifacts/runs/run_54d90086bd75/evidence/`, an untracked run directory, so
# `test_os37_r10_workflow_e2e` pointed at a file a clean checkout does not have -- and the
# only reason CI stayed green about it is that those tests are gated on ORCA_OS37_E2E=1 and
# never ran.  The E2E driver is a REPRODUCIBLE ARTIFACT, so it belongs in the repository;
# the run directory keeps whatever OUTPUT a run produced, which is what a run directory is
# for.
# =====================================================================================
set -u
OUT="${1:?usage: os37_r10_standalone_e2e.sh <output-dir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUT"

# ---- 1. Build a PATH from which `orca` is NOT resolvable ----------------------------
SCRUBBED=""
DROPPED=""
IFS=':' read -r -a _dirs <<< "$PATH"
for d in "${_dirs[@]}"; do
  [ -z "$d" ] && continue
  if [ -x "$d/orca" ]; then DROPPED="$DROPPED $d"; continue; fi
  SCRUBBED="${SCRUBBED:+$SCRUBBED:}$d"
done
export PATH="$SCRUBBED"

# ---- 2. Drop every ORCA_* / CLAUDE_* / CODEX_* parent-session marker ----------------
while read -r name; do
  case "$name" in ORCA_*|CLAUDE_*|CODEX_*|ANTHROPIC_*) unset "$name" ;; esac
done < <(env | sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p')

# ---- 3. PROVE the precondition before anything runs --------------------------------
{
  echo "== R10 PRECONDITIONS =="
  echo "date              : $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "uname             : $(uname -a)"
  echo "python3           : $(command -v python3) -> $(python3 -V 2>&1)"
  echo "PATH dirs dropped : ${DROPPED:-<none>}"
  echo "command -v orca   : '$(command -v orca 2>/dev/null)'   (MUST be empty)"
  echo "which -a orca     : '$(which -a orca 2>/dev/null | tr '\n' ' ')' (MUST be empty)"
  echo "ORCA_* in env     : '$(env | grep -c '^ORCA_' )' (MUST be 0)"
  echo "PATH              : $PATH"
} > "$OUT/preconditions.txt" 2>&1
if command -v orca >/dev/null 2>&1; then
  echo "ABORT: orca is still resolvable; the E2E precondition is NOT established" \
    | tee -a "$OUT/preconditions.txt"
  exit 90
fi

# ---- 4. Resolve the agent binary this run really invokes ---------------------------
R10_BIN_DIR="${R10_BIN_DIR:-$(python3 -c "
import sys; sys.path.insert(0, '$REPO')
from scripts import os37_r10_fixture as f
d = f.native_agent_dir()
print(d if d else '')")}"
R10_BINARY="${R10_BINARY:-os37-r10-agent}"
if [ -z "$R10_BIN_DIR" ] || [ ! -x "$R10_BIN_DIR/$R10_BINARY" ]; then
  echo "ABORT: agent binary '$R10_BINARY' not executable under '$R10_BIN_DIR'" \
    | tee -a "$OUT/preconditions.txt"
  exit 91
fi
{
  echo "agent binary      : $R10_BIN_DIR/$R10_BINARY"
  echo "file(1)           : $(file "$R10_BIN_DIR/$R10_BINARY" 2>&1)"
  echo "--version         : $("$R10_BIN_DIR/$R10_BINARY" --version 2>&1 | head -1)"
} >> "$OUT/preconditions.txt"

# ---- 5. The launch specification ---------------------------------------------------
RUN_ID="run_r10$(date +%s)"
ARTIFACT_BASE="$OUT/artifact_base"
mkdir -p "$ARTIFACT_BASE"

FAIL_PHASE="${R10_FAIL_PHASE:-DESIGN}"
# DESIGN D4.2a: the delivery-mode capability axis is REQUIRED and has no default, so a
# profile that omits it is `profile_invalid` and `--adapter standalone` refuses the run.
# This retained fixture agent DOES wait for its prompt, so `post_ready_delivery` is its
# honest declaration -- the same one the deterministic twelve-case matrix makes.
#
# The fixture's own BEHAVIOUR is unchanged (USER DIRECTIVE D-H); only its profile now
# declares what every profile must declare.  This run stays runtime-boundary evidence and
# is INADMISSIBLE as the real-agent R10 evidence FINAL_REVIEW F-002 requires -- that is
# `scripts/os37_r10_real_agent.py` and `evidence/r10_real_agent/`.
#
# JSON carries no comments and `profile_from_mapping` REFUSES every unknown key, so this
# explanation lives here rather than inside the document below.
cat > "$OUT/profile.json" <<JSON
{
  "driver": "${R10_DRIVER:-claude}",
  "binary": "$R10_BINARY",
  "supported_range": [[1, 0, 0], [3, 0, 0]],
  "bin_dirs": ["$R10_BIN_DIR"],
  "readiness_records": [
    {"channel": "structured", "record_type": "system", "session_field": "session_id"}
  ],
  "delivery_mode": "post_ready_delivery",
  "identity_binding": "minted_echo",
  "identity_flag": "--session-id",
  "delivery_proofs": [
    {"channel": "structured", "record_type": "assistant"}
  ],
  "completion_records": [
    {"channel": "structured", "record_type": "result", "error_field": "is_error"}
  ],
  "driver_env": {"OS37_R10_FAIL_PHASE": "$FAIL_PHASE",
                 "OS37_R10_INTENT_DUMP": "$OUT/intents"},
  "auth_secret_ref": {"ANTHROPIC_API_KEY": "R10_AGENT_CREDENTIAL"},
  "timeouts": {"preflight_timeout_ms": 20000, "readiness_timeout_ms": 30000,
               "delivery_verify_timeout_ms": 15000}
}
JSON

# `auth_secret_ref` above names ANTHROPIC_API_KEY rather than a fixture-invented name.
# External review #7 made the admitted credential NAMES a closed contract, validated at
# profile construction: the child environment policy is an ALLOWLIST, so a name nobody
# enumerated is absent by construction and a profile declaring one could only ever fail at
# spawn.  The VALUE is still this harness's own non-secret fixture string (below); only the
# NAME is constrained.
PHASES_JSON="${R10_PHASES:-[\"ANALYSIS\", \"DESIGN\"]}"
cat > "$OUT/state.json" <<JSON
{
  "run_id": "$RUN_ID",
  "thread_id": "r10",
  "phases": $PHASES_JSON,
  "risk": "high",
  "max_iterations": 4
}
JSON

# ---- 6. RUN.  No Orca process, binary, API or terminal is reachable from here. ------
export ORCA_OS40_RUNTIME_STATE_DIR="$OUT/runtime_state"
export ORCA_OS40_CHECKPOINT_DIR="$OUT/checkpoints"
mkdir -p "$ORCA_OS40_RUNTIME_STATE_DIR" "$ORCA_OS40_CHECKPOINT_DIR"

# The profile declares a credential by REFERENCE; preflight verifies it RESOLVES.
export R10_AGENT_CREDENTIAL="${R10_AGENT_CREDENTIAL:-r10-non-secret-fixture-credential}"

START=$(date +%s)
python3 "$REPO/orca-worker-reviewer-orchestration/tools/run_workflow.py" \
  --adapter standalone \
  --state "$OUT/state.json" \
  --standalone-profile "$OUT/profile.json" \
  --artifact-base "$ARTIFACT_BASE" \
  --project-root "$REPO" \
  --json > "$OUT/run_stdout.json" 2> "$OUT/run_stderr.txt"
RC=$?
END=$(date +%s)
echo "$RC" > "$OUT/run_exit_code.txt"
echo "elapsed_seconds=$((END-START))" >> "$OUT/run_exit_code.txt"

# ---- 7. Collect the journal and the durable state as EVIDENCE ----------------------
find "$ARTIFACT_BASE" -type f \( -name '*.jsonl' -o -name '*.json' -o -name '*.md' \) \
  -print > "$OUT/artifact_inventory.txt" 2>&1
for f in $(find "$ARTIFACT_BASE" -name 'execution_journal.jsonl' 2>/dev/null); do
  cp "$f" "$OUT/execution_journal.jsonl"
done
cp -R "$ARTIFACT_BASE" "$OUT/artifact_base_copy" 2>/dev/null

exit "$RC"
