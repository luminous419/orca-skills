#!/usr/bin/env python3
"""Validate the Skill's OS-40 contract against the stdlib graph specification."""
from __future__ import annotations
import json
import re
from pathlib import Path

try:
    from scripts.deterministic_workflow.contracts import PHASES, ROUTE_TOKENS, SCHEMA_VERSION, TERMINAL_STATUSES, WORKFLOW_ID
    from scripts.deterministic_workflow.graph_spec import GRAPH_OWNED_DECISIONS
except ModuleNotFoundError:  # direct ``python3 scripts/...`` execution
    from deterministic_workflow.contracts import PHASES, ROUTE_TOKENS, SCHEMA_VERSION, TERMINAL_STATUSES, WORKFLOW_ID
    from deterministic_workflow.graph_spec import GRAPH_OWNED_DECISIONS

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/"orca-worker-reviewer-orchestration"/"SKILL.md"
PATTERN=re.compile(r"```workflow-graph-contract\n(?P<body>.*?)\n```",re.S)
CONTROL_PLANE_PATTERN=re.compile(r"```workflow-control-plane\n(?P<body>.*?)\n```",re.S)

# OS-40 forbids a second workflow controller.  The Skill delegates every graph-owned
# decision to the engine; the prose that still describes those decisions must carry this
# demotion marker, and the safety rules the graph does NOT own must never carry it.
NON_AUTHORITATIVE_MARKER=(
    "> **NON-AUTHORITATIVE (graph-owned).** 이 절의 routing 규칙은 deterministic workflow "
    "engine이 소유한다. 아래 설명은 engine 동작의 파생 문서이며, engine과 어긋나면 engine이 정답이다."
)
DELEGATION_CLAUSE=(
    "phase 전이, phase gate, correction loop, iteration budget, Final Review routing 결정은 "
    "deterministic workflow engine이 단독으로 소유한다. Coordinator는 이 결정들을 독립적으로 "
    "재판정하지 않고 engine이 반환한 route token과 terminal status를 따른다."
)


class ControlPlaneError(ValueError):
    """The Skill document and the engine disagree about who owns a workflow decision."""


SECTION_BOUNDARY=re.compile(r"^#{1,6} ",re.M)


def _section_body(text: str, heading: str) -> str:
    """The section's *own* prose, ending at the next heading of any level.

    Nested subsections are excluded on purpose: a demoted subsection must not satisfy the
    demotion check for its still-normative parent, or deleting the parent's own marker
    would go undetected.
    """
    marker=f"\n{heading}\n"
    if text.count(marker)!=1:
        raise ControlPlaneError(f"section heading missing or duplicated: {heading}")
    body=text.split(marker,1)[1]
    boundary=SECTION_BOUNDARY.search(body)
    return body[:boundary.start()] if boundary else body


def validate_control_plane(text: str) -> None:
    """Fail closed when prompt-owned routing is reintroduced or a safety rule is demoted."""
    blocks=CONTROL_PLANE_PATTERN.findall(text)
    if len(blocks)!=1:
        raise ControlPlaneError("expected exactly one workflow-control-plane block")
    try: block=json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        raise ControlPlaneError(f"workflow-control-plane is not valid JSON: {exc}") from exc
    if block.get("authority")!="deterministic_engine":
        raise ControlPlaneError("control plane authority must be the deterministic engine")
    if text.count(DELEGATION_CLAUSE)!=1:
        raise ControlPlaneError("missing or duplicated engine delegation clause")
    entries=block.get("graph_owned_decisions") or []
    declared=tuple(entry.get("decision") for entry in entries)
    if declared!=tuple(GRAPH_OWNED_DECISIONS):
        raise ControlPlaneError(
            f"graph-owned decision set drifted from the engine: declared={list(declared)}, "
            f"engine={list(GRAPH_OWNED_DECISIONS)}")
    owned={token for entry in entries for token in entry.get("route_tokens") or []}
    if owned!=set(ROUTE_TOKENS):
        raise ControlPlaneError(
            f"every route token must be owned by a declared decision; "
            f"unowned={sorted(set(ROUTE_TOKENS)-owned)}, unknown={sorted(owned-set(ROUTE_TOKENS))}")
    for entry in entries:
        for heading in entry.get("sections") or []:
            if NON_AUTHORITATIVE_MARKER not in _section_body(text,heading):
                raise ControlPlaneError(f"graph-owned section is not demoted: {heading}")
    for heading in block.get("skill_owned_safety") or []:
        try: body=_section_body(text,heading)
        except ControlPlaneError as exc:
            raise ControlPlaneError(f"preserved safety section is missing: {heading}") from exc
        if NON_AUTHORITATIVE_MARKER in body:
            raise ControlPlaneError(f"safety section must stay authoritative: {heading}")


def validate(path: Path = SKILL) -> None:
    text=path.read_text(encoding="utf-8")
    validate_control_plane(text)
    matches=PATTERN.findall(text)
    if len(matches)!=1: raise ValueError("expected exactly one workflow-graph-contract")
    actual=json.loads(matches[0])
    expected={"workflow_id":WORKFLOW_ID,"schema_version":SCHEMA_VERSION,"phases":list(PHASES),
              "route_tokens":list(ROUTE_TOKENS),"terminal_statuses":list(TERMINAL_STATUSES),
              "iteration_domains":["PHASE_ITERATIONS","FINAL_REVIEW_ITERATIONS"],
              "decision_first":True,"final_review_mandatory":True,"downstream_revalidation":"high_only",
              "launcher":"tools/run_workflow.py"}
    if actual!=expected: raise ValueError(f"workflow graph contract mismatch: {actual!r}")


def main() -> int:
    try: validate()
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print(f"Workflow graph documentation validation FAILED: {exc}"); return 1
    print("Workflow graph documentation validation PASSED"); return 0


if __name__=="__main__": raise SystemExit(main())
