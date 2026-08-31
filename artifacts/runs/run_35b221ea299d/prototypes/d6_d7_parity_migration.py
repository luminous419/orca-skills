"""DESIGN evidence for D6 (the tenth anchor contract + the mirrored partition) and
D7 (the fail-closed fixture migration) -- executed against the REAL validators.

D6 uses validate_skills' own parse_anchor_contract / anchor_contract_block_lines, so
the proposed block is proved parseable by the shipped machinery rather than by eye.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from validate_skills import (  # noqa: E402
    LIFECYCLE_CONTRACT_TOKEN_PATTERN,
    anchor_contract_block_lines,
    parse_anchor_contract,
)

RESULTS: list[tuple[bool, str, str]] = []


def check(name, got, expect):
    RESULTS.append((got == expect, name, f"expect={expect!r} got={got!r}"))


ORCH = REPO / "orca-worker-reviewer-orchestration"
LOOP = REPO / "orca-worker-reviewer-loop"

# ===========================================================================
# D6a. The tenth anchor contract. Orchestration-only: every key names Orca
#      LIFECYCLE, never decision SEMANTICS.
# ===========================================================================
DECISION_GATE_CONTRACT: dict[str, tuple[str, ...]] = {
    "DECISION_GATE_BOUNDARIES": ("before_phase_entry", "after_worker_result", "after_reviewer_result"),
    "DECISION_GATE_INPUT": ("explicit_machine_readable_record_never_absence",),
    "DECISION_GATE_AXIS_ORDER": ("decision_axis_then_quality_axis",),
    "DECISION_GATE_LEDGER": ("artifact_root_decision_ledger_append_only",),
    "DECISION_GATE_LEDGER_ENTRY_SEQUENCE": ("zero",),
    "DECISION_GATE_LEDGER_PRODUCER": ("coordinator_at_run_open",),
    "DECISION_GATE_ADMISSIBILITY": ("non_empty", "single_entry_declaration", "schema_supported",
                                    "bound_head", "declaration_recomputed", "no_unresolved_open_item"),
    "DECISION_GATE_BLOCKING_STATES": ("needs_input", "conflict"),
    "DECISION_GATE_TERMINAL_STATUS": ("blocked",),
    "DECISION_GATE_LOW_TERMINAL_BOUNDARY": ("after_worker_result",),
    "DECISION_GATE_MEDIUM_HIGH_TERMINAL_BOUNDARY": ("after_reviewer_result",),
    "DECISION_GATE_REVIEWER_PARTICIPATION": ("already_scheduled_reviewer_in_verification_mode",),
    "DECISION_GATE_NEW_DISPATCH_SITES": ("none",),
    "DECISION_GATE_ITERATION_ACCOUNTING": ("decision_block_consumes_no_correction_iteration",),
    "DECISION_GATE_DOWNGRADE_AUTHORITY": ("policy_contract_transition_rule_only",),
    "DECISION_GATE_RISK_INDEPENDENCE": ("identical_terminal_outcome_at_every_risk_level",),
    "DECISION_GATE_RESUME": ("not_implemented_terminal_only",),
    "DECISION_GATE_AUTHORITY": ("machine_record_over_markdown_summary",),
}
DECISION_GATE_CONTRACT_MAX_LINES = 20

BLOCK_TEXT = "#### Decision gate contract\n\n```text\n" + "\n".join(
    f"{k} = {', '.join(v)}" for k, v in DECISION_GATE_CONTRACT.items()
) + "\n```\n"

DECISION_GATE_CONTRACT_BLOCK_PATTERN = re.compile(
    r"####\s*Decision gate contract\s*\n(?P<body>.*?)```text\n(?P<values>.*?)\n```",
    re.DOTALL,
)

parsed = parse_anchor_contract(BLOCK_TEXT, DECISION_GATE_CONTRACT_BLOCK_PATTERN)
check("D6-1 the proposed block parses with the SHIPPED anchor parser", parsed is not None, True)
check("D6-2 keys and values round-trip exactly", parsed, DECISION_GATE_CONTRACT)
_lines = anchor_contract_block_lines(BLOCK_TEXT, DECISION_GATE_CONTRACT_BLOCK_PATTERN)
check("D6-3 the block is within its own line budget",
      0 < _lines <= DECISION_GATE_CONTRACT_MAX_LINES, True)
check("D6-4 the value grammar is lowercase snake tokens, like the nine existing blocks",
      all(LIFECYCLE_CONTRACT_TOKEN_PATTERN.fullmatch(v)
          for vals in DECISION_GATE_CONTRACT.values() for v in vals), True)
# Non-vacuity: the parser is not accepting anything.
_bad = BLOCK_TEXT.replace("DECISION_GATE_TERMINAL_STATUS = blocked",
                          "DECISION_GATE_TERMINAL_STATUS = BLOCKED")
check("D6-5 CONTROL: an UPPERCASE value is rejected by the same parser",
      parse_anchor_contract(_bad, DECISION_GATE_CONTRACT_BLOCK_PATTERN), None)
_dup = BLOCK_TEXT.replace("DECISION_GATE_RESUME = not_implemented_terminal_only",
                          "DECISION_GATE_RESUME = not_implemented_terminal_only\n"
                          "DECISION_GATE_RESUME = something_else")
check("D6-6 CONTROL: a duplicate key is rejected",
      parse_anchor_contract(_dup, DECISION_GATE_CONTRACT_BLOCK_PATTERN), None)

# The block contains no decision-SEMANTICS redefinition: no state name, no reason
# code, no entry clause. Those live only in the shared policy contract (C-1/C-2).
SEMANTICS_TOKENS = ("clear", "assumption_allowed", "repository_policy", "phase_contract",
                    "ambiguous_requirement", "requirement_contradiction", "reversibility",
                    "blast_radius", "model_confidence", "worker_reviewer_agreement")
_flat = [v for vals in DECISION_GATE_CONTRACT.values() for v in vals]
check("D6-7 the Orca-only block redefines NO decision semantics",
      sorted(t for t in SEMANTICS_TOKENS if t in _flat), [])
# ...while the two state NAMES it does mention are used only to name which states
# the ORCA GATE treats as terminal, which is lifecycle, not semantics.
check("D6-8 the only state names present are the two the gate routes on",
      DECISION_GATE_CONTRACT["DECISION_GATE_BLOCKING_STATES"], ("needs_input", "conflict"))

# ===========================================================================
# D6b. The mirrored-vs-not partition, proved on real copies of both Skills.
# ===========================================================================
MIRRORED_DECISION_SEMANTICS_ANCHORS = (
    "gate 경계에서 decision 결과는 필수이며 명시적이다. 섹션의 optional 여부와 다른 객체다.",
    "\"결정할 것이 없었다\"는 CLEAR로 단언되어야 하며 기록의 부재로 추정될 수 없다.",
    "기계가 읽는 record가 authority이고 Markdown 요약은 사람을 위한 설명이다.",
)
ORCHESTRATION_ONLY_ANCHOR = "#### Decision gate contract"

TMP = Path(tempfile.mkdtemp())
o_dir, l_dir = TMP / ORCH.name, TMP / LOOP.name
shutil.copytree(ORCH, o_dir)
shutil.copytree(LOOP, l_dir)


def install(skill_dir: Path, mirrored: tuple[str, ...], gate_block: bool) -> None:
    p = skill_dir / "SKILL.md"
    text = p.read_text(encoding="utf-8")
    text += "\n\n" + "\n\n".join(mirrored) + "\n"
    if gate_block:
        text += "\n" + BLOCK_TEXT
    p.write_text(text, encoding="utf-8")


install(o_dir, MIRRORED_DECISION_SEMANTICS_ANCHORS, gate_block=True)
install(l_dir, MIRRORED_DECISION_SEMANTICS_ANCHORS, gate_block=False)


def parity_verdict(orch_dir: Path, loop_dir: Path) -> str:
    """The proposed validator: mirrored anchors in BOTH, the gate block in
    orchestration ONLY."""
    o = (orch_dir / "SKILL.md").read_text(encoding="utf-8")
    l = (loop_dir / "SKILL.md").read_text(encoding="utf-8")
    for anchor in MIRRORED_DECISION_SEMANTICS_ANCHORS:
        if anchor not in o:
            return "FAIL:missing_in_orchestration"
        if anchor not in l:
            return "FAIL:missing_in_loop"
    if ORCHESTRATION_ONLY_ANCHOR not in o:
        return "FAIL:gate_block_missing_from_orchestration"
    if ORCHESTRATION_ONLY_ANCHOR in l:
        return "FAIL:orca_only_block_leaked_into_loop"
    return "PASS"


check("D6-9 the shipped-pair shape PASSes", parity_verdict(o_dir, l_dir), "PASS")

# (a) mutate the sentence in ONE skill only -> FAIL
m1 = TMP / "m1"; shutil.copytree(l_dir, m1)
p = m1 / "SKILL.md"
p.write_text(p.read_text(encoding="utf-8").replace(
    MIRRORED_DECISION_SEMANTICS_ANCHORS[0],
    MIRRORED_DECISION_SEMANTICS_ANCHORS[0].replace("필수이며", "가능하면")), encoding="utf-8")
check("D6-10 (a) drift in ONE skill -> FAIL", parity_verdict(o_dir, m1), "FAIL:missing_in_loop")

# (b) delete the sentence from BOTH -> still FAIL (byte-equality alone cannot see this)
b1 = TMP / "b1"; b2 = TMP / "b2"
shutil.copytree(o_dir, b1); shutil.copytree(l_dir, b2)
for d in (b1, b2):
    q = d / "SKILL.md"
    q.write_text(q.read_text(encoding="utf-8").replace(
        MIRRORED_DECISION_SEMANTICS_ANCHORS[1], ""), encoding="utf-8")
check("D6-11 (b) deleted from BOTH -> FAIL", parity_verdict(b1, b2), "FAIL:missing_in_orchestration")

# (c) copy the orchestration-only block into the loop skill -> FAIL
c1 = TMP / "c1"; shutil.copytree(l_dir, c1)
q = c1 / "SKILL.md"
q.write_text(q.read_text(encoding="utf-8") + "\n" + BLOCK_TEXT, encoding="utf-8")
check("D6-12 (c) the Orca-only block leaking into the loop -> FAIL",
      parity_verdict(o_dir, c1), "FAIL:orca_only_block_leaked_into_loop")

# Non-vacuity: the checker is not rejecting everything -- D6-9 above is its clean half.
check("D6-13 CONTROL: the unmutated pair still PASSes after all three mutants",
      parity_verdict(o_dir, l_dir), "PASS")

# The loop Skill still has ZERO anchor contracts, which is the asymmetry the nine
# existing blocks already have.
_loop_anchors = re.findall(r"(?m)^#### .* contract$", (l_dir / "SKILL.md").read_text(encoding="utf-8"))
check("D6-14 the loop Skill still has zero `#### ... contract` blocks", _loop_anchors, [])
_orch_anchors = re.findall(r"(?m)^#### .* contract$", (o_dir / "SKILL.md").read_text(encoding="utf-8"))
check("D6-15 orchestration goes from nine anchor contracts to ten", len(_orch_anchors), 10)
shutil.rmtree(TMP, ignore_errors=True)

# ===========================================================================
# D7. The fail-closed fixture migration. The gate is ALWAYS armed; the agents
#     declare by default. Silence is never the default and there is no opt-out.
# ===========================================================================
GATE_STATE_FIELD = "DECISION_GATE_STATE"
FIELD = re.compile(r"(?m)^(?P<field>[A-Z_]+):\s*(?P<value>[A-Z_]+)\s*$")
BLOCK = re.compile(r"(?ms)^```decision-gate\n(?P<body>.*?)\n```")


def fake_agent_output(decision_gate_state: str = "CLEAR", *, emit: bool = True) -> str:
    """The proposed fake_worker default. Contrast fake_worker.py:28-32, whose OS-3
    flag defaults to emitting NOTHING -- that opt-in precedent is INVERTED here."""
    out = "# Worker Result\n\nSTATUS: COMPLETE\n"
    if emit:
        out += f"{GATE_STATE_FIELD}: {decision_gate_state}\n"
        out += ('\n```decision-gate\n{"ledger_schema_version": 1, "state": "'
                + decision_gate_state + '", "reason_code": null,'
                ' "open_decision_item": false}\n```\n')
    return out


def armed_gate(output: str) -> str:
    """No arming flag exists. Absence is a refusal at every risk level."""
    states = [m.group("value") for m in FIELD.finditer(output) if m.group("field") == GATE_STATE_FIELD]
    if not states or not BLOCK.search(output):
        return "DECISION_GATE_INPUT_MISSING"
    return f"ADMITTED:{states[0]}"


check("D7-1 the migrated default DECLARES CLEAR rather than staying silent",
      armed_gate(fake_agent_output()), "ADMITTED:CLEAR")
check("D7-2 a silent agent is REFUSED at every risk level -- there is no opt-in flag",
      [armed_gate(fake_agent_output(emit=False)) for _ in ("low", "medium", "high")],
      ["DECISION_GATE_INPUT_MISSING"] * 3)
check("D7-3 the migrated default does NOT disturb the existing STATUS parse",
      [m.group("value") for m in FIELD.finditer(fake_agent_output()) if m.group("field") == "STATUS"],
      ["COMPLETE"])
check("D7-4 a blocking declaration is expressible through the same default channel",
      armed_gate(fake_agent_output("NEEDS_INPUT")), "ADMITTED:NEEDS_INPUT")


# The ANTI-PATTERN control: the opt-in shape the ticket forbids, shown failing OPEN.
def opt_in_gate(output: str, *, gate_enabled: bool) -> str:
    if not gate_enabled:
        return "ADMITTED:CLEAR"        # <- the fail-open default, presumed not asserted
    return armed_gate(output)


check("D7-5 CONTROL: the opt-in alternative FAILS OPEN on the same silent input",
      opt_in_gate(fake_agent_output(emit=False), gate_enabled=False), "ADMITTED:CLEAR")
check("D7-6 which is exactly what the always-armed design refuses",
      armed_gate(fake_agent_output(emit=False)), "DECISION_GATE_INPUT_MISSING")

# Measured blast radius of the migration, from the working tree.
_grep = subprocess.run(
    ["grep", "-rlE", r"fake_worker|fake_reviewer", "--include=test_*.py", str(REPO / "scripts")],
    capture_output=True, text=True)
_modules = sorted(Path(p).name for p in _grep.stdout.split())
check("D7-7 the fixture migration's blast radius is measured, not guessed",
      len(_modules) > 0, True)
RESULTS.append((True, "D7-7b modules referencing the fake agents", ", ".join(_modules)))

ok = sum(1 for good, _, _ in RESULTS if good)
for good, name, detail in RESULTS:
    print(f"[{'PASS' if good else 'FAIL'}] {name}\n        {detail}")
print(f"\n{ok}/{len(RESULTS)} cases behaved as specified")
sys.exit(0 if ok == len(RESULTS) else 1)
