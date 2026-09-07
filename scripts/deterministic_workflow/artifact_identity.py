"""OS-42: the ONE artifact identity for every gate attempt.

Two facts about the workflow's iteration counters make a single derivation necessary
rather than convenient:

* ``phase_iterations[phase]`` is ZERO-based and POST-incremented.  ``apply_result_node``
  advances it unconditionally in the ``PHASE_REVIEWER`` branch and, in the ``WORKER``
  branch, only when ``risk == "low" and status == "COMPLETE"``.  So at MEDIUM/HIGH a
  COMPLETE Worker does not advance it and the FIRST Phase Reviewer intent is prepared
  while the counter still reads 0.
* ``final_review_iterations`` is PRE-incremented, inside ``prepare_intent_node`` before
  ``make_intent`` is called, so it is already one-based at intent time.

Feeding the raw counter into the one-based ``_iteration<N>`` ladder of SKILL.md section 9
would give review 1 the path ``REVIEW_<PHASE>_iteration0.md`` and would map review 2 onto
review 1's unsuffixed ``REVIEW_<PHASE>.md`` -- overwriting the first review's evidence.
:func:`gate_iteration` is the single place that conversion happens, and ``make_intent``
stores its result on the intent so every consumer READS it instead of recomputing it.

``repair_attempt`` is deliberately NOT a parameter of either function here.  A repair is
the same gate attempt retried for representation only, so it must resolve to the
byte-identical path; making the value impossible to pass is what guarantees that.

The module lives inside the engine package because that package is the one that SHIPS
(``release_manifest`` installs ``tools/deterministic_workflow/**``) while
``task_context``, ``e2e_harness`` and ``review_isolation`` do not -- so this is the only
location every consumer can reach.  It imports the standard library and ``.contracts``
only.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ALL_PHASES, ROLES

# The one-based attempt domain, mirroring ``run_logging.ATTEMPT_MIN``.
ATTEMPT_MIN = 1

FINAL_REVIEW_PHASE = "final_review"
# Both spellings the repository uses for the same three roles.  The engine says
# WORKER/PHASE_REVIEWER/FINAL_REVIEWER; ``task_context`` says worker/reviewer.
_WORKER_ROLES = frozenset({"WORKER", "worker"})
_PHASE_REVIEWER_ROLES = frozenset({"PHASE_REVIEWER", "reviewer"})
_FINAL_REVIEWER_ROLES = frozenset({"FINAL_REVIEWER", "final_reviewer"})


class ArtifactIdentityError(ValueError):
    """A role, phase or gate iteration that cannot name an artifact."""


def attempt_domain_violation(attempt: object, label: str = "attempt") -> str | None:
    """The one-based attempt domain, as ONE predicate.  Message, or None if legal.

    It deliberately RAISES NOTHING, for the same reason
    ``run_logging.attempt_domain_violation`` does: four modules now enforce this rule and
    each owns a different exception contract (``RunLoggingError``, ``EvalInputError``, a
    plain ``ValueError``, and :class:`ArtifactIdentityError` here).  A shared *raiser*
    would force one of them to change its error contract; a shared *predicate* gives all
    of them the identical rule.
    """
    if not isinstance(attempt, int) or isinstance(attempt, bool):
        return f"{label} must be an int >= {ATTEMPT_MIN}, got {attempt!r}"
    if attempt < ATTEMPT_MIN:
        return f"{label} must be >= {ATTEMPT_MIN}, got {attempt!r}"
    return None


def gate_iteration(state: Mapping[str, Any], role: str, phase: str) -> int:
    """The ONE-BASED ordinal of THIS gate attempt.  Always >= 1.

    Evaluated against the state ``prepare_intent_node`` hands to ``make_intent``, i.e.
    after that node has applied its own mutations, which is why the FINAL_REVIEWER arm
    reads the counter directly and the other two add one.

    One formula covers every risk level with no risk branch: the counter means "gate
    attempts already settled for this phase", and the *gate* is the Worker at LOW and the
    Phase Reviewer at MEDIUM/HIGH -- which is exactly why ``apply_result_node`` advances
    it from two different branches.  The risk semantics already live in the counter, so
    the derivation must not restate them.
    """
    if role in _FINAL_REVIEWER_ROLES:
        value = state["final_review_iterations"]
    elif role in _WORKER_ROLES or role in _PHASE_REVIEWER_ROLES:
        try:
            value = state["phase_iterations"][phase] + 1
        except (KeyError, TypeError) as exc:
            raise ArtifactIdentityError(
                f"no phase_iterations entry for phase {phase!r}") from exc
    else:
        raise ArtifactIdentityError(f"unknown role: {role!r}")
    violation = attempt_domain_violation(value, label=f"gate_iteration for role {role!r}")
    if violation is not None:
        raise ArtifactIdentityError(violation)
    return value


def contract_phase(role: str, phase: str) -> str:
    """The phase THIS dispatch's decision-gate contract is rendered with.

    A Final Reviewer's contract is rendered for the ``final_review`` phase, not for the
    workflow phase the state happens to be sitting on, and every other role's is rendered
    for its own phase in the lower-case spelling ``task_context`` uses.
    ``OrcaAdapter.start`` computed this inline and ``validate_settlement_node`` needs the
    identical value to bind the returned record, so a second copy would let the record an
    agent is TOLD to write drift from the one the validator accepts.
    """
    if role in _FINAL_REVIEWER_ROLES:
        return FINAL_REVIEW_PHASE
    return str(phase).lower()


def _iteration_suffix(gate_iteration_value: int) -> str:
    """SKILL.md section 9: attempt 1 is unsuffixed; N>=2 carries ``_iteration<N>``.

    There is deliberately no ``_iteration1`` form -- the contract states it exists nowhere.
    """
    return "" if gate_iteration_value == 1 else f"_iteration{gate_iteration_value}"


def run_artifact_root(run_id: str) -> str:
    """``artifacts/runs/<run-id>/`` -- the one root every rule below writes under."""
    if not isinstance(run_id, str) or not run_id:
        raise ArtifactIdentityError(f"run_id must be non-empty text, got {run_id!r}")
    return f"artifacts/runs/{run_id}/"


def artifact_basename(*, phase: str, role: str, gate_iteration: int) -> str:
    """SKILL.md section 9 "Artifact path contract", first match wins -- the FILENAME.

    ```text
    1. phase == final_review -> FINAL_REVIEW.md              (ga == 1)
                                FINAL_REVIEW_iteration<N>.md (ga >= 2)
    2. role is a worker      -> <PHASE>.md            (in place, NO suffix, ever)
    3. role is a reviewer    -> REVIEW_<PHASE>.md              (ga == 1)
                                REVIEW_<PHASE>_iteration<N>.md (ga >= 2)
    ```

    Rule 2 takes no suffix at any gate iteration: a Worker artifact is updated in place by
    contract, which is also why a repair of a Worker attempt rewrites one file.

    Only the basename, deliberately.  Each caller owns the RUN ROOT and its own
    validation of it -- `task_context.run_artifact_root` refuses a run id containing a
    path separator or a `.`/`..` segment, and that guard must not be bypassed by a
    delegation.  Same reasoning as `attempt_domain_violation`: share the rule, never the
    raiser.
    """
    violation = attempt_domain_violation(gate_iteration, label="gate_iteration")
    if violation is not None:
        raise ArtifactIdentityError(violation)
    normalized_phase = str(phase).lower()
    if normalized_phase == FINAL_REVIEW_PHASE or role in _FINAL_REVIEWER_ROLES:
        return f"FINAL_REVIEW{_iteration_suffix(gate_iteration)}.md"
    if normalized_phase.upper() not in ALL_PHASES:
        raise ArtifactIdentityError(f"unknown phase: {phase!r}")
    upper_phase = normalized_phase.upper()
    if role in _WORKER_ROLES:
        return f"{upper_phase}.md"
    if role in _PHASE_REVIEWER_ROLES:
        return f"REVIEW_{upper_phase}{_iteration_suffix(gate_iteration)}.md"
    raise ArtifactIdentityError(f"unknown role: {role!r}")


def artifact_relative_path(*, run_id: str, phase: str, role: str,
                           gate_iteration: int) -> str:
    """The run-rooted path, for callers that have no root builder of their own."""
    return run_artifact_root(run_id) + artifact_basename(
        phase=phase, role=role, gate_iteration=gate_iteration)


def engine_roles() -> tuple[str, ...]:
    """The engine's own three role spellings, for tests that sweep every role."""
    return tuple(ROLES)
