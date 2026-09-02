#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.clarification_protocol import (
    ArtifactHumanApprovalPort,
    ClarificationError,
    MAX_BUNDLE_ITEMS,
    ResponseSubmission,
    decision_item_id,
    terminal_block_sources,
    _identifier,
)


def item(*, state="NEEDS_INPUT", suffix="1", open_item="deployment_target", independent=()):
    key=f"run_test/implementation/1/B2#{suffix}"
    return {
        "open_item":open_item,"source_ledger_key":key,"source_ledger_keys":[key],"source_state":state,
        "source_reason_code":"user_choice_required","phase":"implementation","iteration":1,
        "question":"Which deployment target should be used?","context":"The implementation is complete.",
        "what_is_blocked":"Deployment cannot proceed without an explicit target.",
        "options":[
            {"option_id":"staging","label":"Staging","action":"deploy to staging","tradeoff":"No production traffic."},
            {"option_id":"production","label":"Production","action":"deploy to production","tradeoff":"Immediate user impact."},
        ],"recommended_option_id":"staging","recommendation_rationale":"It limits initial risk.","deadline_at":None,
        "depends_on":[],"independent_with":list(independent),
        "custom_decision":{"allowed":False,"subject":"","value_type":"none","max_length":0,"pattern":None,"allowed_values":[],"sensitive":False},
        "narrowing_rationale":"",
    }


def create_input(items):
    return {"items":items,"bundle_rationale":"Independent deployment decisions." if len(items)>1 else "",
            "independence_declared_by":{"actor_id":"coordinator","actor_type":"service"},
            "accepted_response_modes":["option_id","response_file","cancel"],
            "sensitivity_guidance":"Use response-file for sensitive values."}


class ClarificationProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name); self.port=ArtifactHumanApprovalPort(self.base)

    def tearDown(self): self.tmp.cleanup()

    def create(self, value=None): return self.port.create(run_id="run_test",data=create_input(value or [item()]))

    def submit(self, request_id, *, token="reply1", option="staging", response_file=None, cancel=False, sensitivity="normal"):
        request=self.port.show(run_id="run_test",request_id=request_id)["request"]
        selector=None if cancel else request["items"][0]["decision_item_id"]
        return self.port.ingest(run_id="run_test",request_id=request_id,decision_item_id=selector,submission=ResponseSubmission(
            token,"alice","human","approval-system","2026-09-01T08:00:00Z",option if response_file is None and not cancel else None,response_file,cancel,sensitivity))

    @staticmethod
    def snapshot(root):
        return {path.relative_to(root):path.read_bytes() for path in root.rglob("*") if path.is_file()}

    @staticmethod
    def rewrite_event(path, **changes):
        record=json.loads(path.read_text()); record.update(changes)
        body=dict(record); body.pop("event_id")
        record["event_id"]=_identifier("event","os30-event-v1",body)
        path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")

    def test_needs_input_and_conflict_create_complete_non_default_request(self):
        for state in ("NEEDS_INPUT","CONFLICT"):
            with self.subTest(state=state):
                result=self.create([item(state=state,open_item=f"choice_{state.lower()}")])
                record=json.loads((self.base/"artifacts/runs/run_test/clarifications/requests"/result.request_ids[0]/"record.json").read_text())
                self.assertFalse(record["default_applicable"]); self.assertEqual("no selection; run remains blocked",record["on_timeout"])
                self.assertTrue(record["items"][0]["options"]); self.assertEqual("staging",record["items"][0]["recommended_option_id"])

    def test_option_response_preserves_raw_once_and_creates_decision_with_provenance(self):
        req=self.create().request_ids[0]; result=self.submit(req)
        self.assertEqual("DECIDED",result.status)
        root=self.base/"artifacts/runs/run_test/clarifications"
        raw=root/"responses"/result.response_id/"raw_response.txt"
        self.assertEqual(b"staging",raw.read_bytes()); self.assertEqual(0o600,raw.stat().st_mode & 0o777)
        decision=json.loads((root/"decisions"/result.decision_id/"record.json").read_text())
        self.assertEqual("OPTION",decision["kind"]); self.assertEqual("alice",decision["actor"]["actor_id"])
        self.assertEqual("explicit_user_reply",decision["provenance"]["source"])

    def test_sensitive_custom_value_exists_only_in_restricted_raw_file(self):
        value=item(); value["custom_decision"]={"allowed":True,"subject":"token alias","value_type":"text","max_length":64,"pattern":"[A-Za-z0-9-]+","allowed_values":[],"sensitive":True}
        req=self.create([value]).request_ids[0]
        secret="CANARY-OS30-93f06a"; source=self.base/"answer"; source.write_text(secret)
        result=self.submit(req,response_file=source,sensitivity="sensitive")
        root=self.base/"artifacts/runs/run_test/clarifications"
        occurrences=[]
        for path in root.rglob("*"):
            if path.is_file() and secret.encode() in path.read_bytes(): occurrences.append(path.name)
        self.assertEqual(["raw_response.txt"],occurrences)
        decision=json.loads((root/"decisions"/result.decision_id/"record.json").read_text())
        self.assertTrue(decision["custom"]["redacted"]); self.assertIsNone(decision["custom"]["value"])

    def test_ambiguous_response_reclarifies_twice_then_exhausts(self):
        req=self.create().request_ids[0]
        for index, expected in enumerate(("RECLARIFICATION_CREATED","RECLARIFICATION_CREATED","AMBIGUITY_LIMIT_REACHED"),1):
            answer=self.base/f"answer{index}"; answer.write_text("maybe later")
            result=self.submit(req,token=f"reply{index}",response_file=answer)
            self.assertEqual(expected,result.status)
            requests=[]
            for path in (self.base/"artifacts/runs/run_test/clarifications/requests").glob("*/record.json"):
                record=json.loads(path.read_text()); requests.append(record)
            req=max(requests,key=lambda x:x["revision"])["request_id"]
        self.assertEqual(3,len(requests))

    def test_changed_answer_supersedes_and_cancel_is_append_only(self):
        req=self.create().request_ids[0]; first=self.submit(req); second=self.submit(req,token="reply2",option="production")
        self.assertNotEqual(first.decision_id,second.decision_id)
        cancelled=self.submit(req,token="reply3",option=None,cancel=True); self.assertEqual("CANCELLED",cancelled.status)
        events=[]
        for path in sorted((self.base/"artifacts/runs/run_test/clarifications/lineage").glob("*/event.json")): events.append(json.loads(path.read_text())["event_type"])
        self.assertEqual(["decision_superseded","decision_cancelled"],events)
        self.assertTrue((self.base/"artifacts/runs/run_test/clarifications/decisions"/first.decision_id).exists())

    def test_cancel_then_new_decision_uses_lineage_order_not_decision_id_order(self):
        req=self.create().request_ids[0]
        first=self.submit(req); self.submit(req,token="cancel",option=None,cancel=True)
        second=self.submit(req,token="after_cancel",option="production")
        shown=self.port.show(run_id="run_test",request_id=req)
        item_id=self.create().item_ids[0]
        self.assertNotEqual(first.decision_id,second.decision_id)
        self.assertEqual(second.decision_id,shown["effective_decisions"][item_id])

    def test_unlinked_second_decision_is_orphan_and_read_is_non_mutating(self):
        req=self.create().request_ids[0]; self.submit(req); self.submit(req,token="reply2",option="production")
        root=self.base/"artifacts/runs/run_test/clarifications"
        next((root/"lineage").glob("*/event.json")).parent.rename(root/"removed_event")
        before=self.snapshot(root)
        with self.assertRaises(ClarificationError) as caught: self.port.show(run_id="run_test",request_id=req)
        self.assertEqual("ORPHAN_DECISION",caught.exception.code)
        self.assertEqual(before,self.snapshot(root))

    def test_wrong_linkage_and_conflicting_fork_fail_with_named_codes(self):
        for kind in ("broken","fork"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); req=port.create(run_id="run_test",data=create_input([item()])).request_ids[0]
                item_id=port.show(run_id="run_test",request_id=req)["request"]["items"][0]["decision_item_id"]
                first=port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                    "one","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
                second=port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                    "two","alice","human","approval-system","2026-09-01T08:01:00Z","production"))
                root=Path(td)/"artifacts/runs/run_test/clarifications"; event=next((root/"lineage").glob("*/event.json"))
                if kind=="broken":
                    self.rewrite_event(event,prior_decision_id="decision_000000000000000000000000")
                    expected="LINEAGE_INVALID"
                else:
                    third=port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                        "three","alice","human","approval-system","2026-09-01T08:02:00Z","staging"))
                    events=sorted((root/"lineage").glob("*/event.json")); fork=events[-1]
                    record=json.loads(fork.read_text()); details={"prior_decision_id":first.decision_id,"next_decision_id":third.decision_id}
                    self.rewrite_event(fork,prior_decision_id=first.decision_id,details=details)
                    expected="LINEAGE_FORK"
                before=self.snapshot(root)
                with self.assertRaises(ClarificationError) as caught: port.show(run_id="run_test",request_id=req)
                self.assertEqual(expected,caught.exception.code); self.assertEqual(before,self.snapshot(root))

    def test_zero_answer_request_cancel_is_irreversible_for_bundle_sizes(self):
        for count in (1,2,3):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); values=[item(suffix=str(i),open_item=f"choice_{i}") for i in range(1,count+1)]
                ids=[decision_item_id("run_test","implementation",value["open_item"],value["source_ledger_key"]) for value in values]
                for value,item_id in zip(values,ids): value["independent_with"]=sorted(set(ids)-{item_id})
                req=port.create(run_id="run_test",data=create_input(values)).request_ids[0]
                port.ingest(run_id="run_test",request_id=req,decision_item_id=None,submission=ResponseSubmission(
                    "cancel_all","alice","human","approval-system","2026-09-01T08:00:00Z",cancel=True))
                shown=port.show(run_id="run_test",request_id=req)
                self.assertEqual({item_id:"cancelled" for item_id in ids},shown["item_statuses"])
                events=[json.loads(path.read_text()) for path in (Path(td)/"artifacts/runs/run_test/clarifications/lineage").glob("*/event.json")]
                self.assertEqual(count,len(events)); self.assertTrue(all(event["prior_decision_id"] is None for event in events))
                root=Path(td)/"artifacts/runs/run_test/clarifications"; before=self.snapshot(root)
                with self.assertRaises(ClarificationError) as caught:
                    port.ingest(run_id="run_test",request_id=req,decision_item_id=ids[0],submission=ResponseSubmission(
                        "late","alice","human","approval-system","2026-09-01T08:01:00Z","staging"))
                self.assertEqual("LINEAGE_INVALID",caught.exception.code); self.assertEqual(before,self.snapshot(root))

    def test_identical_request_republication_is_idempotent_despite_created_at(self):
        first=self.create(); second=self.create()
        self.assertEqual(first.request_ids,second.request_ids)
        self.assertEqual("EXISTING",second.status)

    def test_duplicate_replay_and_conflicting_submission_fail_closed(self):
        req=self.create().request_ids[0]; first=self.submit(req); replay=self.submit(req)
        self.assertEqual(first.response_id,replay.response_id)
        with self.assertRaises(ClarificationError) as caught: self.submit(req,option="production")
        self.assertEqual("CLARIFICATION_ID_CONFLICT",caught.exception.code)

    def test_cancel_selector_and_response_file_security_have_declared_codes(self):
        req=self.create().request_ids[0]
        item_id=self.port.show(run_id="run_test",request_id=req)["request"]["items"][0]["decision_item_id"]
        with self.assertRaises(ClarificationError) as cancel_error:
            self.port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                "cancel_bad","alice","human","approval-system","2026-09-01T08:00:00Z",cancel=True))
        self.assertEqual("CANCEL_REQUEST_INVALID",cancel_error.exception.code)
        empty=self.base/"empty-response"; empty.write_bytes(b"")
        with self.assertRaises(ClarificationError) as security_error:
            self.submit(req,token="empty",response_file=empty)
        self.assertEqual("CLARIFICATION_SECURITY_FAILURE",security_error.exception.code)

    def test_stale_item_guard_has_declared_code(self):
        req=self.create().request_ids[0]
        request=self.port.show(run_id="run_test",request_id=req)["request"]
        item_id=request["items"][0]["decision_item_id"]
        # A stale revision that dropped an item is forbidden by today's writer, but
        # the read-side guard remains a declared fail-closed boundary. Exercise it
        # directly while retaining the real request and ingestion implementation.
        with patch.object(self.port,"_current_request",return_value=None), \
             patch.object(self.port,"_current_item_ids",return_value=set()), \
             self.assertRaises(ClarificationError) as caught:
            self.port._ingest_one("run_test",request,item_id,ResponseSubmission(
                "stale_item","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
        self.assertEqual("STALE_ITEM",caught.exception.code)

    def test_unknown_item_selector_returns_named_code_without_writes(self):
        req=self.create().request_ids[0]; root=self.base/"artifacts/runs/run_test/clarifications"; before=self.snapshot(root)
        with self.assertRaises(ClarificationError) as caught:
            self.port.ingest(run_id="run_test",request_id=req,decision_item_id="item_000000000000000000000000",
                submission=ResponseSubmission("wrongitem","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
        self.assertEqual("ITEM_NOT_IN_REQUEST",caught.exception.code); self.assertEqual(before,self.snapshot(root))

    def test_bundle_bound_and_independence(self):
        raw=[item(suffix=str(i),open_item=f"choice_{i}") for i in range(1,MAX_BUNDLE_ITEMS+1)]
        # Compute IDs once, then declare the complete symmetric antichain.
        singles=[self.port.create(run_id=f"scratch{i}",data=create_input([{**x,"source_ledger_key":x["source_ledger_key"].replace("run_test",f"scratch{i}"),"source_ledger_keys":[x["source_ledger_key"].replace("run_test",f"scratch{i}")]}])).item_ids[0] for i,x in enumerate(raw)]
        # Cross-run IDs are intentionally different; validate the bound separately.
        with self.assertRaises(ClarificationError): self.create(raw)
        with self.assertRaises(ClarificationError): self.create([item(suffix=str(i),open_item=f"x{i}") for i in range(1,5)])

    def test_bundle_items_answer_independently_then_request_level_cancel(self):
        values=[item(suffix=str(i),open_item=f"choice_{i}") for i in (1,2,3)]
        ids=[decision_item_id("run_test","implementation",value["open_item"],value["source_ledger_key"]) for value in values]
        for value,item_id in zip(values,ids): value["independent_with"]=sorted(set(ids)-{item_id})
        request=self.create(values).request_ids[0]
        for index,item_id in enumerate(ids):
            result=self.port.ingest(run_id="run_test",request_id=request,decision_item_id=item_id,
                submission=ResponseSubmission(f"reply{index}","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
            self.assertEqual(item_id,result.decision_item_id)
            shown=self.port.show(run_id="run_test",request_id=request)
            self.assertEqual(index+1,sum(value is not None for value in shown["effective_decisions"].values()))
        cancelled=self.port.ingest(run_id="run_test",request_id=request,decision_item_id=None,
            submission=ResponseSubmission("cancel_all","alice","human","approval-system","2026-09-01T08:01:00Z",cancel=True))
        self.assertEqual("CANCELLED",cancelled.status)
        self.assertTrue(all(value is None for value in self.port.show(run_id="run_test",request_id=request)["effective_decisions"].values()))

    def test_non_first_bundle_item_ambiguous_reclarifies_same_complete_bundle(self):
        for count in (2,3):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td))
                values=[item(suffix=str(i),open_item=f"choice_{i}") for i in range(1,count+1)]
                ids=[decision_item_id("run_test","implementation",value["open_item"],value["source_ledger_key"]) for value in values]
                for value,item_id in zip(values,ids): value["independent_with"]=sorted(set(ids)-{item_id})
                request_id=port.create(run_id="run_test",data=create_input(values)).request_ids[0]
                before=port.show(run_id="run_test",request_id=request_id)["request"]
                answer=Path(td)/"ambiguous.txt"; answer.write_text("maybe later")
                target=before["items"][-1]["decision_item_id"]
                result=port.ingest(run_id="run_test",request_id=request_id,decision_item_id=target,
                    submission=ResponseSubmission("ambiguous","alice","human","approval-system","2026-09-01T08:00:00Z",response_file=answer))
                self.assertEqual("RECLARIFICATION_CREATED",result.status)
                records=[json.loads(path.read_text()) for path in (Path(td)/"artifacts/runs/run_test/clarifications/requests").glob("*/record.json")]
                revised=max(records,key=lambda value:value["revision"])
                self.assertEqual(set(ids),{value["decision_item_id"] for value in revised["items"]})
                old={value["decision_item_id"]:value for value in before["items"]}
                new={value["decision_item_id"]:value for value in revised["items"]}
                self.assertTrue(new[target]["narrowing_rationale"])
                for item_id in set(ids)-{target}: self.assertEqual(old[item_id],new[item_id])

    def test_raw_record_digest_and_decision_rewrite_fails_closed_without_mutation(self):
        request=self.create().request_ids[0]; result=self.submit(request)
        root=self.base/"artifacts/runs/run_test/clarifications"
        response_path=root/"responses"/result.response_id/"record.json"
        response=json.loads(response_path.read_text()); raw_path=response_path.parent/"raw_response.txt"
        raw_path.write_bytes(b"production"); response["raw"]["sha256"]=hashlib.sha256(b"production").hexdigest()
        response["raw"]["byte_count"]=len(b"production")
        normalized={"kind":"OPTION","option_id":"production","action":"deploy to production"}
        from scripts.clarification_protocol import _identifier
        forged_id=_identifier("decision","os30-decision-v1",[result.response_id,normalized])
        response["decision_id"]=forged_id; response_path.write_text(json.dumps(response))
        decision_path=root/"decisions"/result.decision_id/"record.json"
        decision=json.loads(decision_path.read_text()); decision.update(
            decision_id=forged_id,option={"option_id":"production","action":"deploy to production"},scope="deploy to production")
        forged_dir=root/"decisions"/forged_id; forged_dir.mkdir(); (forged_dir/"record.json").write_text(json.dumps(decision))
        decision_path.unlink(); decision_path.parent.rmdir()
        before={path.relative_to(root):path.read_bytes() for path in root.rglob("*") if path.is_file()}
        with self.assertRaises(ClarificationError): self.port.show(run_id="run_test",request_id=request)
        self.assertEqual(before,{path.relative_to(root):path.read_bytes() for path in root.rglob("*") if path.is_file()})

    def test_response_identity_prevents_cross_item_authority_transfer(self):
        values=[item(suffix=str(i),open_item=f"choice_{i}") for i in (1,2)]
        ids=[decision_item_id("run_test","implementation",value["open_item"],value["source_ledger_key"]) for value in values]
        for value,item_id in zip(values,ids): value["independent_with"]=sorted(set(ids)-{item_id})
        request=self.create(values).request_ids[0]
        result=self.port.ingest(run_id="run_test",request_id=request,decision_item_id=ids[0],submission=ResponseSubmission(
            "transfer","alice","human","approval-system","2026-09-01T08:00:00Z","production"))
        root=self.base/"artifacts/runs/run_test/clarifications"
        response_path=root/"responses"/result.response_id/"record.json"
        response=json.loads(response_path.read_text()); response["decision_item_id"]=ids[1]
        response_path.write_text(json.dumps(response,sort_keys=True,indent=2)+"\n")
        decision_path=root/"decisions"/result.decision_id/"record.json"
        decision=json.loads(decision_path.read_text())
        decision.update(decision_item_id=ids[1],source_ledger_key=values[1]["source_ledger_key"],resolves=values[1]["source_ledger_key"])
        decision_path.write_text(json.dumps(decision,sort_keys=True,indent=2)+"\n")
        before=self.snapshot(root)
        with self.assertRaises(ClarificationError) as caught:
            self.port.show(run_id="run_test",request_id=request)
        self.assertEqual("SCHEMA_MALFORMED",caught.exception.code)
        self.assertIn("response_id content mismatch",str(caught.exception))
        self.assertEqual(before,self.snapshot(root))

    def test_tampered_decision_authority_payload_fails_closed(self):
        request=self.create().request_ids[0]; result=self.submit(request)
        path=self.base/"artifacts/runs/run_test/clarifications/decisions"/result.decision_id/"record.json"
        record=json.loads(path.read_text()); record["option"]["action"]="deploy to production AND delete backups"; path.write_text(json.dumps(record))
        with self.assertRaises(ClarificationError): self.port.show(run_id="run_test",request_id=request)

    def test_decision_validator_load_bearing_checks_each_reject(self):
        mutations=(
            ("wrong_version",lambda record: record.__setitem__("schema_version",2)),
            ("extra_field",lambda record: record.__setitem__("extra",True)),
            ("missing_field",lambda record: record.pop("scope")),
            ("wrong_source",lambda record: record.__setitem__("source_ledger_key","run_test/implementation/1/B2#999")),
        )
        for name,mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); req=port.create(run_id="run_test",data=create_input([item()])).request_ids[0]
                item_id=port.show(run_id="run_test",request_id=req)["request"]["items"][0]["decision_item_id"]
                result=port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                    "one","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
                path=Path(td)/"artifacts/runs/run_test/clarifications/decisions"/result.decision_id/"record.json"
                record=json.loads(path.read_text()); mutate(record); path.write_text(json.dumps(record))
                with self.assertRaises(ClarificationError): port.show(run_id="run_test",request_id=req)

        # Make directory/record/response bindings mutually consistent while the
        # decision identity itself does not re-derive from response+normalization.
        req=self.create([item(suffix="9",open_item="content_identity")]).request_ids[0]
        result=self.submit(req,token="contentid")
        root=self.base/"artifacts/runs/run_test/clarifications"; forged="decision_000000000000000000000000"
        decision_dir=root/"decisions"/result.decision_id; record=json.loads((decision_dir/"record.json").read_text())
        record["decision_id"]=forged; (decision_dir/"record.json").write_text(json.dumps(record)); decision_dir.rename(root/"decisions"/forged)
        response_path=root/"responses"/result.response_id/"record.json"; response=json.loads(response_path.read_text())
        response["decision_id"]=forged; response_path.write_text(json.dumps(response))
        with self.assertRaises(ClarificationError): self.port.show(run_id="run_test",request_id=req)

    def test_response_binding_identity_uniqueness_and_raw_recheck_are_load_bearing(self):
        for name in ("identity","duplicate","raw"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); req=port.create(run_id="run_test",data=create_input([item()])).request_ids[0]
                item_id=port.show(run_id="run_test",request_id=req)["request"]["items"][0]["decision_item_id"]
                result=port.ingest(run_id="run_test",request_id=req,decision_item_id=item_id,submission=ResponseSubmission(
                    "one","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
                root=Path(td)/"artifacts/runs/run_test/clarifications"; binding=next((root/"response_bindings").glob("*/record.json"))
                if name=="identity": binding.parent.rename(binding.parent.with_name("binding_000000000000000000000000"))
                elif name=="duplicate":
                    record=json.loads(binding.read_text()); record["raw_sha256"]="0"*64
                    record["binding_id"]=_identifier("binding","os30-response-raw-binding-v1",[result.response_id,"0"*64])
                    target=root/"response_bindings"/record["binding_id"]; target.mkdir(); (target/"record.json").write_text(json.dumps(record))
                else: (root/"responses"/result.response_id/"raw_response.txt").write_bytes(b"production")
                if name=="raw":
                    request=port._request("run_test",req)
                    response=port._validate_response_record(json.loads((root/"responses"/result.response_id/"record.json").read_text()),request)
                    with self.assertRaises(ClarificationError): port._validate_response_evidence(root,response)
                else:
                    with self.assertRaises(ClarificationError): port.show(run_id="run_test",request_id=req)

    def test_binding_content_address_changes_when_bound_digest_changes(self):
        req=self.create().request_ids[0]; result=self.submit(req); root=self.base/"artifacts/runs/run_test/clarifications"
        raw=b"production"; digest=hashlib.sha256(raw).hexdigest()
        response_path=root/"responses"/result.response_id/"record.json"; response=json.loads(response_path.read_text())
        response["raw"].update(sha256=digest,byte_count=len(raw)); response_path.write_text(json.dumps(response))
        (response_path.parent/"raw_response.txt").write_bytes(raw)
        binding_path=next((root/"response_bindings").glob("*/record.json")); binding=json.loads(binding_path.read_text())
        binding["raw_sha256"]=digest; binding_path.write_text(json.dumps(binding))
        request=self.port._request("run_test",req); validated=self.port._validate_response_record(response,request)
        with self.assertRaises(ClarificationError): self.port._validate_response_evidence(root,validated)

    def test_historical_v1_reads_without_binding_and_without_rewrite(self):
        req=self.create().request_ids[0]; result=self.submit(req)
        root=self.base/"artifacts/runs/run_test/clarifications"
        request_path=root/"requests"/req/"record.json"; request=json.loads(request_path.read_text())
        request["schema_version"]=1
        contract={key:request[key] for key in ("items","bundle_rationale","independence_declared_by",
                                                "accepted_response_modes","sensitivity_guidance")}
        v1_request=_identifier("request","os30-request-v1",{"items":[request["items"][0]["decision_item_id"]],
                                                              "revision":0,"contract":contract})
        request["request_id"]=v1_request
        response_path=root/"responses"/result.response_id/"record.json"; response=json.loads(response_path.read_text())
        v1_response=_identifier("response","os30-response-v1",[v1_request,response["submission_id"]])
        normalized={"kind":"OPTION","option_id":"staging","action":"deploy to staging"}
        v1_decision=_identifier("decision","os30-decision-v1",[v1_response,normalized])
        response.update(schema_version=1,request_id=v1_request,response_id=v1_response,decision_id=v1_decision)
        response.pop("decision_item_id")
        decision_path=root/"decisions"/result.decision_id/"record.json"; decision=json.loads(decision_path.read_text())
        decision.update(request_id=v1_request,response_id=v1_response,decision_id=v1_decision)
        request_path.write_text(json.dumps(request)); response_path.write_text(json.dumps(response)); decision_path.write_text(json.dumps(decision))
        request_path.parent.rename(root/"requests"/v1_request)
        response_path.parent.rename(root/"responses"/v1_response)
        decision_path.parent.rename(root/"decisions"/v1_decision)
        for path in list((root/"response_bindings").glob("*")):
            (path/"record.json").unlink(); path.rmdir()
        v2_request=self.create([item(suffix="2",open_item="disjoint_v2")]).request_ids[0]
        v2_result=self.submit(v2_request,token="v2reply")
        before=self.snapshot(root)
        shown=self.port.show(run_id="run_test",request_id=v1_request)
        self.assertEqual(v1_decision,next(iter(shown["effective_decisions"].values())))
        self.assertEqual(v2_result.decision_id,next(iter(self.port.show(run_id="run_test",request_id=v2_request)["effective_decisions"].values())))
        self.assertEqual(before,self.snapshot(root))

    def test_disjoint_v1_and_v2_lineages_coexist_but_cross_generation_fails(self):
        # The historical fixture above proves v1 admission; a separate run keeps
        # the v2 writer/read path authoritative, while an in-lineage version cross
        # remains fail-closed.
        req=self.create().request_ids[0]; result=self.submit(req)
        self.assertEqual(result.decision_id,next(iter(self.port.show(run_id="run_test",request_id=req)["effective_decisions"].values())))
        path=self.base/"artifacts/runs/run_test/clarifications/responses"/result.response_id/"record.json"
        record=json.loads(path.read_text()); record["schema_version"]=1; record.pop("decision_item_id"); path.write_text(json.dumps(record))
        with self.assertRaises(ClarificationError) as caught: self.port.show(run_id="run_test",request_id=req)
        self.assertEqual("SCHEMA_VERSION_MIXED",caught.exception.code)

    def test_tampered_decision_and_lineage_fail_show_without_mutation(self):
        for target in ("decision","lineage"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); request=port.create(run_id="run_test",data=create_input([item()])).request_ids[0]
                item_id=port.show(run_id="run_test",request_id=request)["request"]["items"][0]["decision_item_id"]
                first=port.ingest(run_id="run_test",request_id=request,decision_item_id=item_id,
                    submission=ResponseSubmission("one","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))
                port.ingest(run_id="run_test",request_id=request,decision_item_id=item_id,
                    submission=ResponseSubmission("two","alice","human","approval-system","2026-09-01T08:01:00Z","production"))
                root=Path(td)/"artifacts/runs/run_test/clarifications"
                path=(root/"decisions"/first.decision_id/"record.json" if target=="decision" else next((root/"lineage").glob("*/event.json")))
                record=json.loads(path.read_text()); record["decision_item_id"]="item_000000000000000000000000"; path.write_text(json.dumps(record))
                before={p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(ClarificationError): port.show(run_id="run_test",request_id=request)
                self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_lineage_closed_schema_version_check_is_load_bearing(self):
        req=self.create().request_ids[0]; self.submit(req); self.submit(req,token="two",option="production")
        root=self.base/"artifacts/runs/run_test/clarifications"; path=next((root/"lineage").glob("*/event.json"))
        record=json.loads(path.read_text()); record["schema_version"]=2
        body=dict(record); body.pop("event_id"); record["event_id"]=_identifier("event","os30-event-v1",body)
        path.write_text(json.dumps(record)); before=self.snapshot(root)
        with self.assertRaises(ClarificationError): self.port.show(run_id="run_test",request_id=req)
        self.assertEqual(before,self.snapshot(root))

    def test_stale_revision_response_is_evidence_without_decision(self):
        req=self.create().request_ids[0]; answer=self.base/"ambiguous"; answer.write_text("maybe")
        self.submit(req,response_file=answer)
        stale=self.submit(req,token="late",option="staging")
        self.assertEqual("STALE_REQUEST",stale.status); self.assertIsNone(stale.decision_id)

    def test_cli_is_noninteractive_and_installed_copy_runs(self):
        module=Path(__file__).with_name("clarification_protocol.py")
        text=module.read_text(); self.assertNotIn("input(",text); self.assertNotIn("orca orchestration ask",text)
        completed=subprocess.run([sys.executable,str(module),"--help"],input="",text=True,capture_output=True,timeout=5)
        self.assertEqual(0,completed.returncode)

    def test_cli_create_requires_existing_ledger_identity(self):
        source=self.base/"request.json"; source.write_text(json.dumps(create_input([item()])))
        module=Path(__file__).with_name("clarification_protocol.py")
        completed=subprocess.run([sys.executable,str(module),"create","--artifact-base",str(self.base),
            "--run-id","run_test","--ledger-key","run_test/implementation/1/B2#1","--input",str(source)],
            text=True,capture_output=True)
        self.assertEqual(2,completed.returncode); self.assertIn("SOURCE_NOT_OPEN",completed.stderr)
        self.assertFalse((self.base/"artifacts/runs/run_test/clarifications").exists())

    def test_unsupported_and_mixed_response_versions_fail_without_rewrite(self):
        request=self.create().request_ids[0]; result=self.submit(request)
        root=self.base/"artifacts/runs/run_test/clarifications"; path=root/"responses"/result.response_id/"record.json"
        for version,expected in ((99,"SCHEMA_UNSUPPORTED"),(1,"SCHEMA_VERSION_MIXED")):
            original=json.loads(path.read_text()); record=dict(original); record["schema_version"]=version
            if version!=2: record.pop("decision_item_id")  # v1-shaped: reach version/generation guards before closed schema.
            path.write_text(json.dumps(record))
            before={p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()}
            with self.assertRaises(ClarificationError) as caught: self.port.show(run_id="run_test",request_id=request)
            self.assertEqual(expected,caught.exception.code)
            self.assertEqual(before,{p.relative_to(root):p.read_bytes() for p in root.rglob("*") if p.is_file()})
            path.write_text(json.dumps(original))

    def test_published_request_content_address_rejects_response_mode_narrowing_without_writes(self):
        request_id=self.create().request_ids[0]
        root=self.base/"artifacts/runs/run_test/clarifications"
        path=root/"requests"/request_id/"record.json"; record=json.loads(path.read_text())
        record["accepted_response_modes"]=["option_id"]
        path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
        before=self.snapshot(root)
        with self.assertRaises(ClarificationError) as caught:
            self.port.show(run_id="run_test",request_id=request_id)
        self.assertEqual("CLARIFICATION_INVALID",caught.exception.code)
        self.assertIn("request_id: content mismatch",str(caught.exception))
        self.assertEqual(before,self.snapshot(root))

    def test_lineage_event_content_address_rejects_tampering_without_writes(self):
        request_id=self.create().request_ids[0]; self.submit(request_id); self.submit(request_id,token="two",option="production")
        root=self.base/"artifacts/runs/run_test/clarifications"
        path=next((root/"lineage").glob("*/event.json")); record=json.loads(path.read_text())
        record["occurred_at"]="2026-09-01T09:00:00Z"  # Deliberately do not recompute event_id.
        path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
        before=self.snapshot(root)
        with self.assertRaises(ClarificationError) as caught:
            self.port.show(run_id="run_test",request_id=request_id)
        self.assertEqual("SCHEMA_MALFORMED",caught.exception.code)
        self.assertIn("event_id content mismatch",str(caught.exception))
        self.assertEqual(before,self.snapshot(root))

    def test_validated_reviewer_is_unconditionally_folded_across_labels(self):
        worker={"run":"run_test","phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
                "sequence":1,"state":"NEEDS_INPUT","reason_code":"user_choice_required","open_decision_item":True,
                "open_item":"producer_label","verifies":None}
        reviewer={**worker,"role":"reviewer","boundary":"B3","sequence":2,"open_item":"different_label",
                  "verifies":{"worker_record_key":"run_test/implementation/1/B2#1"}}
        def key(record): return f"{record['run']}/{record['phase']}/{record['iteration']}/{record['boundary']}#{record['sequence']}"
        sources=terminal_block_sources(run_id="run_test",records=[worker,reviewer],
            coordinator_input={key(worker):item()},ledger_key=key,
            valid_reviewer_binding=lambda review, producer: review["verifies"]["worker_record_key"]==key(producer))
        self.assertEqual(1,len(sources)); self.assertEqual("producer_label",sources[0].open_item)
        self.assertEqual((key(worker),key(reviewer)),sources[0].source_ledger_keys)

    def test_folded_worker_reviewer_disagreement_fails_closed(self):
        worker={"run":"run_test","phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
                "sequence":1,"state":"NEEDS_INPUT","reason_code":"user_choice_required","open_decision_item":True,
                "open_item":"choice","verifies":None}
        reviewer={**worker,"role":"reviewer","boundary":"B3","sequence":2,"state":"CONFLICT",
                  "reason_code":"conflicting_instructions","verifies":{"worker_record_key":"run_test/implementation/1/B2#1"}}
        key=lambda record: f"{record['run']}/{record['phase']}/{record['iteration']}/{record['boundary']}#{record['sequence']}"
        with self.assertRaisesRegex(ClarificationError,"judgements disagree"):
            terminal_block_sources(run_id="run_test",records=[worker,reviewer],coordinator_input={key(worker):item()},
                ledger_key=key,valid_reviewer_binding=lambda review, producer: True)

    def test_published_fixture_files_are_exercised(self):
        fixture_root=Path(__file__).parent/"fixtures/clarification_protocol"
        valid=json.loads((fixture_root/"valid/needs_input_request.json").read_text())
        created=self.port.create(run_id="run_fixture",data=valid)
        self.assertEqual("CREATED",created.status)
        oversized=json.loads((fixture_root/"invalid/oversized_bundle.json").read_text())
        self.assertEqual("create",oversized["operation"])
        source=json.loads((fixture_root/oversized["base_fixture"]).read_text())
        template=source["items"][0]
        source["items"]=[]
        for index in range(1,oversized["repeat_items"]+1):
            value=json.loads(json.dumps(template))
            value["open_item"]=f"deployment_target_{index}"
            value["source_ledger_key"]=f"run_fixture/implementation/1/B2#{index}"
            value["source_ledger_keys"]=[value["source_ledger_key"]]
            source["items"].append(value)
        ids=[decision_item_id("run_fixture","implementation",value["open_item"],value["source_ledger_key"]) for value in source["items"]]
        for value,item_id in zip(source["items"],ids): value["independent_with"]=sorted(set(ids)-{item_id})
        source["bundle_rationale"]="Four independently answerable choices exceed the protocol bound."
        with self.assertRaises(ClarificationError) as caught:
            self.port.create(run_id="run_fixture",data=source)
        self.assertEqual(oversized["expected_error"],caught.exception.code)
        self.assertIn("bundle: requires 1..3 items",str(caught.exception))

    def test_persisted_request_negative_fixture_matrix_fails_closed_without_side_effects(self):
        fixture_root=Path(__file__).parent/"fixtures/clarification_protocol"
        matrix=json.loads((fixture_root/"invalid/recommended_default.json").read_text())
        self.assertEqual("mutate_published_request",matrix["operation"])
        for case in matrix["cases"]:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as td:
                port=ArtifactHumanApprovalPort(Path(td)); request_id=port.create(run_id="run_test",data=create_input([item()])).request_ids[0]
                root=Path(td)/"artifacts/runs/run_test/clarifications"
                record_path=root/"requests"/request_id/"record.json"
                record=json.loads(record_path.read_text())
                target=record
                for component in case["path"][:-1]: target=target[component]
                if case["operation"] == "remove": target.pop(case["path"][-1])
                else: target[case["path"][-1]]=case["value"]
                record_path.write_text(json.dumps(record,sort_keys=True,indent=2)+"\n")
                before={path.relative_to(root):path.read_bytes() for path in root.rglob("*") if path.is_file()}
                expected_error=case.get("expected_error",matrix["expected_error"])
                with self.assertRaises(ClarificationError) as show_error: port.show(run_id="run_test",request_id=request_id)
                self.assertEqual(expected_error,show_error.exception.code)
                with self.assertRaises(ClarificationError) as ingest_error: self.submit_with_port(port,request_id)
                self.assertEqual(expected_error,ingest_error.exception.code)
                after={path.relative_to(root):path.read_bytes() for path in root.rglob("*") if path.is_file()}
                self.assertEqual(before,after)

    @staticmethod
    def submit_with_port(port, request_id):
        item_id=port.show(run_id="run_test",request_id=request_id)["request"]["items"][0]["decision_item_id"]
        return port.ingest(run_id="run_test",request_id=request_id,decision_item_id=item_id,submission=ResponseSubmission(
            "reply1","alice","human","approval-system","2026-09-01T08:00:00Z","staging"))

    def test_complete_known_dag_rejects_unknown_dependency_and_cycle(self):
        unknown=item(suffix="2",open_item="child"); unknown["depends_on"]=["item_000000000000000000000000"]
        with self.assertRaises(ClarificationError): self.create([unknown])
        a=item(suffix="2",open_item="a"); b=item(suffix="3",open_item="b")
        aid=decision_item_id("run_test","implementation","a",a["source_ledger_key"])
        bid=decision_item_id("run_test","implementation","b",b["source_ledger_key"])
        a["depends_on"]=[bid]; b["depends_on"]=[aid]; a["independent_with"]=[bid]; b["independent_with"]=[aid]
        with self.assertRaises(ClarificationError): self.create([a,b])

    def test_scope_expansion_appends_child_identity_and_edge_without_replacing_head(self):
        request=self.create().request_ids[0]; decided=self.submit(request)
        parent=json.loads((self.base/"artifacts/runs/run_test/clarifications/requests"/request/"record.json").read_text())["items"][0]
        child=item(suffix="2",open_item="regional_target"); child["depends_on"]=[parent["decision_item_id"]]
        child_id=decision_item_id("run_test","implementation","regional_target",child["source_ledger_key"])
        result=self.port.expand_scope(run_id="run_test",decision_item_id=parent["decision_item_id"],request_id=request,
            child_items=[child],edges=[(parent["decision_item_id"],child_id)],
            actor={"actor_id":"coordinator","actor_type":"service"},
            provenance={"source":"explicit_user_reply","capture_mechanism":"cli","where_recorded":"approval-system"},
            scope_statements=["Regional target now requires a separate explicit choice."])
        self.assertEqual((child_id,),result.item_ids)
        events=[json.loads(path.read_text()) for path in sorted((self.base/"artifacts/runs/run_test/clarifications/lineage").glob("*/event.json"))]
        self.assertEqual("decision_scope_expanded",events[-1]["event_type"])
        self.assertEqual(decided.decision_id,self.port.show(run_id="run_test",request_id=request)["effective_decisions"][parent["decision_item_id"]])


class HarnessClarificationSeamTests(unittest.TestCase):
    class FakePort(ArtifactHumanApprovalPort):
        """Records publish calls while doing REAL work.

        A pure stub cannot exercise promotion: promote() reads persisted requests
        and lineage to decide what is newly ready, so a stub that only appends to a
        list would make the seam tests agree with any implementation.
        """
        def __init__(self, base): super().__init__(base); self.calls=[]
        def publish(self, **kwargs):
            self.calls.append(kwargs); return super().publish(**kwargs)

    @staticmethod
    def records():
        worker={"run":"run_seam","phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
                "sequence":1,"state":"NEEDS_INPUT","reason_code":"user_choice_required","open_decision_item":True,
                "open_item":"deployment_target","verifies":None}
        reviewer={**worker,"role":"reviewer","boundary":"B3","sequence":2,
                  "verifies":{"run":"run_seam","phase":"implementation","iteration":1,
                              "worker_record_key":"run_seam/implementation/1/B2#1"}}
        return worker,reviewer

    def test_both_harness_seams_call_fake_port_once(self):
        from scripts.e2e_harness import E2EHarness
        from scripts.orca_runtime_harness import OrcaRuntimeHarness
        worker,reviewer=self.records(); declaration=item(); declaration["source_ledger_key"]="run_seam/implementation/1/B2#1"
        declaration["source_ledger_keys"]=[declaration["source_ledger_key"]]
        for cls,base_attr in ((E2EHarness,"workspace"),(OrcaRuntimeHarness,"artifact_dir")):
            with self.subTest(harness=cls.__name__), tempfile.TemporaryDirectory() as td:
                harness=object.__new__(cls); port=self.FakePort(Path(td))
                harness.run_id="run_seam"; setattr(harness,base_attr,Path(td)); harness.human_approval_port=port
                harness.clarification_inputs={"run_seam/implementation/1/B2#1":declaration}; harness.clarification_errors=[]
                harness.phase="implementation"
                if cls is OrcaRuntimeHarness:
                    harness._safe_log=lambda func,*args,**kwargs: func(*args,**kwargs)
                with patch("scripts.run_logging.read_decision_ledger",return_value=[worker,reviewer]):
                    harness._publish_clarifications_for_terminal_block()
                self.assertEqual(1,len(port.calls))

    def test_terminal_block_with_two_three_four_items_covers_every_item(self):
        from scripts.e2e_harness import E2EHarness
        from scripts.orca_runtime_harness import OrcaRuntimeHarness
        for count in (2,3,4):
            records=[]; declarations={}
            for index in range(1,count+1):
                key=f"run_seam/implementation/1/B2#{index}"
                records.append({"run":"run_seam","phase":"implementation","iteration":1,"role":"worker",
                    "boundary":"B2","sequence":index,"state":"NEEDS_INPUT","reason_code":"user_choice_required",
                    "open_decision_item":True,"open_item":f"choice_{index}","verifies":None})
                declaration=item(suffix=str(index),open_item=f"choice_{index}")
                declaration["source_ledger_key"]=key; declaration["source_ledger_keys"]=[key]
                declarations[key]=declaration
            for cls,base_attr in ((E2EHarness,"workspace"),(OrcaRuntimeHarness,"artifact_dir")):
                with self.subTest(count=count,harness=cls.__name__), tempfile.TemporaryDirectory() as td:
                    harness=object.__new__(cls); port=self.FakePort(Path(td)); harness.run_id="run_seam"
                    setattr(harness,base_attr,Path(td)); harness.human_approval_port=port
                    harness.clarification_inputs=declarations; harness.clarification_errors=[]; harness.phase="implementation"
                    if cls is OrcaRuntimeHarness: harness._safe_log=lambda func,*args,**kwargs: func(*args,**kwargs)
                    with patch("scripts.run_logging.read_decision_ledger",return_value=records):
                        harness._publish_clarifications_for_terminal_block()
                    self.assertEqual((count+2)//3,len(port.calls))
                    covered=[source.source_ledger_key for call in port.calls for source in call["sources"]]
                    self.assertEqual(sorted(declarations),sorted(covered))

    def test_missing_declaration_publishes_nothing_through_both_harness_seams(self):
        from scripts.e2e_harness import E2EHarness
        from scripts.orca_runtime_harness import OrcaRuntimeHarness
        worker,reviewer=self.records()
        for cls,base_attr in ((E2EHarness,"workspace"),(OrcaRuntimeHarness,"artifact_dir")):
            with self.subTest(harness=cls.__name__), tempfile.TemporaryDirectory() as td:
                harness=object.__new__(cls); port=self.FakePort(Path(td))
                harness.run_id="run_seam"; setattr(harness,base_attr,Path(td)); harness.human_approval_port=port
                harness.clarification_inputs={}; harness.clarification_errors=[]; harness.phase="implementation"
                if cls is OrcaRuntimeHarness:
                    harness._safe_log=lambda func,*args,**kwargs: func(*args,**kwargs)
                with patch("scripts.run_logging.read_decision_ledger",return_value=[worker,reviewer]):
                    harness._publish_clarifications_for_terminal_block()
                self.assertEqual([],port.calls)
                self.assertEqual([],harness.clarification_errors)

    def test_runtime_missing_declaration_preserves_real_blocked_status_write(self):
        from scripts.orca_runtime_harness import OrcaRuntimeHarness
        worker,reviewer=self.records()
        with tempfile.TemporaryDirectory() as td:
            harness=object.__new__(OrcaRuntimeHarness); port=self.FakePort(Path(td))
            harness.run_id="run_seam"; harness.artifact_dir=Path(td); harness.human_approval_port=port
            harness.clarification_inputs={}; harness.clarification_errors=[]; harness._logging_errors=[]
            harness._run_started_at="2026-09-01T00:00:00Z"; harness.risk="high"; harness.risk_source="explicit"
            with (patch("scripts.run_logging.read_decision_ledger",return_value=[worker,reviewer]),
                  patch("scripts.run_logging.log_run_status") as status_write):
                harness.log_run_status("BLOCKED",reason="decision required")
            status_write.assert_called_once()
            self.assertEqual("BLOCKED",status_write.call_args.args[1])
            self.assertEqual([],port.calls)
            self.assertEqual([],harness.clarification_errors)

    def test_publication_failure_is_durable_and_reader_exposes_it(self):
        from scripts.e2e_harness import E2EHarness
        from scripts import run_logging
        worker,reviewer=self.records()
        with tempfile.TemporaryDirectory() as td:
            harness=object.__new__(E2EHarness); harness.run_id="run_seam"; harness.workspace=Path(td)
            harness.human_approval_port=self.FakePort(Path(td)); harness.clarification_inputs={}; harness.clarification_errors=[]; harness.phase="implementation"
            # Disagreement is rejected before a port call and recorded durably.
            reviewer={**reviewer,"state":"CONFLICT","reason_code":"conflicting_instructions"}
            with patch("scripts.run_logging.read_decision_ledger",return_value=[worker,reviewer]):
                harness._publish_clarifications_for_terminal_block()
            errors=run_logging.read_clarification_publication_errors("run_seam",base=Path(td))
            detail=json.loads(errors[0]["detail"])
            self.assertEqual("ClarificationError",detail["exception"]); self.assertIn("judgements disagree",detail["message"])
            self.assertIn("ledger_keys",detail); self.assertEqual([],harness.human_approval_port.calls)

if __name__ == "__main__": unittest.main()


class DependentPromotionTests(unittest.TestCase):
    """M-001: a dependent question must actually become askable once its
    predecessor carries an effective decision, through the shipped harness seam,
    without resuming the run and without republishing anything already asked."""

    RUN = "run_promote"

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.port=ArtifactHumanApprovalPort(self.base)

    def tearDown(self): self.tmp.cleanup()

    def key(self, index): return f"{self.RUN}/implementation/1/B2#{index}"

    def item_id(self, index, label): return decision_item_id(self.RUN,"implementation",label,self.key(index))

    def chain_sources(self):
        """A -> B -> C plus an independent root D."""
        from scripts.clarification_protocol import ClarificationSource
        layout=[(1,"choice_a",[]),(2,"choice_b",[self.item_id(1,"choice_a")]),
                (3,"choice_c",[self.item_id(2,"choice_b")]),(4,"choice_d",[])]
        sources=[]
        for index,label,depends in layout:
            value=item(suffix=str(index),open_item=label)
            value["source_ledger_key"]=self.key(index); value["source_ledger_keys"]=[self.key(index)]
            value["depends_on"]=depends
            sources.append(ClarificationSource(
                open_item=label,source_ledger_key=self.key(index),source_ledger_keys=(self.key(index),),
                state="NEEDS_INPUT",reason_code="user_choice_required",phase="implementation",
                iteration=1,request_input=value))
        return tuple(sources)

    def published_item_ids(self):
        root=self.base/f"artifacts/runs/{self.RUN}/clarifications/requests"
        ids=set()
        for path in root.glob("request_*/record.json"):
            for value in json.loads(path.read_text())["items"]: ids.add(value["decision_item_id"])
        return ids

    def request_count(self):
        return len(list((self.base/f"artifacts/runs/{self.RUN}/clarifications/requests").glob("request_*/record.json")))

    def answer(self, item_id, token):
        root=self.base/f"artifacts/runs/{self.RUN}/clarifications/requests"
        for path in root.glob("request_*/record.json"):
            record=json.loads(path.read_text())
            if any(value["decision_item_id"]==item_id for value in record["items"]):
                return self.port.ingest(run_id=self.RUN,request_id=record["request_id"],decision_item_id=item_id,
                    submission=ResponseSubmission(token,"alice","human","desk","2026-09-01T08:00:00Z","staging",None,False,"normal"))
        raise AssertionError(f"{item_id} was never published")

    def test_dependent_items_are_published_only_after_predecessors_resolve(self):
        sources=self.chain_sources()
        a,b,c,d=(self.item_id(1,"choice_a"),self.item_id(2,"choice_b"),
                 self.item_id(3,"choice_c"),self.item_id(4,"choice_d"))

        self.port.promote(run_id=self.RUN,sources=sources)
        self.assertEqual({a,d},self.published_item_ids(),"only dependency-free roots may be asked first")

        # Nothing is ready yet, so a second promotion must be a no-op, not a duplicate.
        before=self.request_count()
        self.assertEqual("EXISTING",self.port.promote(run_id=self.RUN,sources=sources).status)
        self.assertEqual(before,self.request_count())

        self.answer(a,"reply-a")
        self.port.promote(run_id=self.RUN,sources=sources)
        self.assertEqual({a,d,b},self.published_item_ids(),"B becomes askable once A is effective")
        self.assertNotIn(c,self.published_item_ids(),"C must wait for B")

        self.answer(b,"reply-b")
        self.port.promote(run_id=self.RUN,sources=sources)
        self.assertEqual({a,b,c,d},self.published_item_ids(),"C becomes askable once B is effective")

        # Every item was asked exactly once across the whole chain.
        asked=[value["decision_item_id"]
               for path in (self.base/f"artifacts/runs/{self.RUN}/clarifications/requests").glob("request_*/record.json")
               for value in json.loads(path.read_text())["items"]]
        self.assertEqual(sorted(asked),sorted({a,b,c,d}),"an item must never be published twice")

    def test_cancelled_predecessor_never_promotes_its_dependent(self):
        sources=self.chain_sources()
        a,b=self.item_id(1,"choice_a"),self.item_id(2,"choice_b")
        self.port.promote(run_id=self.RUN,sources=sources)
        root=self.base/f"artifacts/runs/{self.RUN}/clarifications/requests"
        request=next(json.loads(path.read_text()) for path in root.glob("request_*/record.json")
                     if any(v["decision_item_id"]==a for v in json.loads(path.read_text())["items"]))
        self.port.ingest(run_id=self.RUN,request_id=request["request_id"],decision_item_id=None,
            submission=ResponseSubmission("cancel-a","alice","human","desk","2026-09-01T08:00:00Z",None,None,True,"normal"))
        self.port.promote(run_id=self.RUN,sources=sources)
        self.assertNotIn(b,self.published_item_ids(),
                         "abandonment is irreversible, so a dependent of a cancelled item stays unasked")

    def test_dependent_chain_advances_through_the_installed_cli_after_respond(self):
        """The post-response path must exist in the SHIPPED CLI, not only in Python.

        The harness seam runs while recording terminal BLOCKED; by the time a human
        answers, that run is over. So the chain is driven here exactly as an operator
        would: one terminal-boundary publication, then `respond` through the installed
        module. No second terminal-boundary invocation is simulated.
        """
        from scripts.e2e_harness import E2EHarness
        from scripts import clarification_protocol
        sources=self.chain_sources()
        declarations={source.source_ledger_key:source.request_input for source in sources}
        records=[{"run":self.RUN,"phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
                  "sequence":index,"state":"NEEDS_INPUT","reason_code":"user_choice_required",
                  "open_decision_item":True,"open_item":label,"verifies":None}
                 for index,label in ((1,"choice_a"),(2,"choice_b"),(3,"choice_c"),(4,"choice_d"))]
        a,b,c,d=(self.item_id(1,"choice_a"),self.item_id(2,"choice_b"),
                 self.item_id(3,"choice_c"),self.item_id(4,"choice_d"))
        module=Path(clarification_protocol.__file__)

        harness=object.__new__(E2EHarness); harness.run_id=self.RUN; harness.workspace=self.base
        harness.human_approval_port=self.port; harness.clarification_inputs=declarations
        harness.clarification_errors=[]; harness.phase="implementation"
        with patch("scripts.run_logging.read_decision_ledger",return_value=records):
            harness._publish_clarifications_for_terminal_block()
        self.assertEqual({a,d},self.published_item_ids(),"only the roots are asked at terminal BLOCKED")
        self.assertEqual([],harness.clarification_errors)

        def respond_via_cli(item_id, token):
            root=self.base/f"artifacts/runs/{self.RUN}/clarifications/requests"
            request=next(json.loads(path.read_text()) for path in root.glob("request_*/record.json")
                         if any(v["decision_item_id"]==item_id for v in json.loads(path.read_text())["items"]))
            completed=subprocess.run([sys.executable,str(module),"respond","--artifact-base",str(self.base),
                "--run-id",self.RUN,"--request-id",request["request_id"],"--decision-item-id",item_id,
                "--submission-id",token,"--actor-id","alice","--actor-type","human",
                "--where-recorded","desk","--responded-at","2026-09-01T08:00:00Z","--option-id","staging"],
                text=True,capture_output=True)
            self.assertEqual(0,completed.returncode,completed.stderr)
            return json.loads(completed.stdout)

        first=respond_via_cli(a,"cli-a")
        self.assertEqual("DECIDED",first["status"])
        self.assertIn(b,self.published_item_ids(),"answering A through the CLI must make B askable")
        self.assertNotIn(c,self.published_item_ids(),"C must still wait for B")
        self.assertIn(b,first["promoted"]["item_ids"],"the CLI must report what it promoted")

        second=respond_via_cli(b,"cli-b")
        self.assertEqual("DECIDED",second["status"])
        self.assertEqual({a,b,c,d},self.published_item_ids(),"answering B must make C askable")

        asked=[v["decision_item_id"]
               for path in (self.base/f"artifacts/runs/{self.RUN}/clarifications/requests").glob("request_*/record.json")
               for v in json.loads(path.read_text())["items"]]
        self.assertEqual(sorted(asked),sorted({a,b,c,d}),"no item may be asked twice across the chain")

    def test_installed_cli_promote_is_explicit_and_idempotent(self):
        from scripts import clarification_protocol
        module=Path(clarification_protocol.__file__)
        sources=self.chain_sources()
        self.port.promote(run_id=self.RUN,sources=sources)  # persists the declarations
        a=self.item_id(1,"choice_a")
        self.answer(a,"reply-a")
        completed=subprocess.run([sys.executable,str(module),"promote","--artifact-base",str(self.base),
            "--run-id",self.RUN],text=True,capture_output=True)
        self.assertEqual(0,completed.returncode,completed.stderr)
        self.assertIn(self.item_id(2,"choice_b"),json.loads(completed.stdout)["item_ids"])
        again=subprocess.run([sys.executable,str(module),"promote","--artifact-base",str(self.base),
            "--run-id",self.RUN],text=True,capture_output=True)
        self.assertEqual("EXISTING",json.loads(again.stdout)["status"],"a second promote must publish nothing new")

    def test_blocked_sources_are_persisted_immutably_and_validated_on_read(self):
        from scripts.clarification_protocol import ClarificationConflict, SchemaMalformed, SchemaUnsupported
        sources=self.chain_sources()
        self.port.promote(run_id=self.RUN,sources=sources)
        path=self.base/f"artifacts/runs/{self.RUN}/clarifications/blocked_sources/record.json"
        self.assertTrue(path.exists(),"promotion is unreachable later unless the declarations are on disk")
        self.assertEqual(0o600,path.stat().st_mode & 0o777)
        self.assertEqual(4,len(self.port.load_blocked_sources(self.RUN)))
        # Re-declaring the same set is a no-op; a different set is a conflict.
        self.assertFalse(self.port.persist_blocked_sources(self.RUN,sources))
        with self.assertRaises(ClarificationConflict):
            self.port.persist_blocked_sources(self.RUN,sources[:2])
        for mutate,expected in ((lambda r: r.__setitem__("schema_version",99),SchemaUnsupported),
                                (lambda r: r.__setitem__("schema","orca.other"),SchemaMalformed),
                                (lambda r: r.__setitem__("run_id","run_other"),SchemaMalformed),
                                (lambda r: r.__setitem__("extra",1),SchemaMalformed),
                                (lambda r: r["sources"][0].__setitem__("extra",1),SchemaMalformed)):
            record=json.loads(path.read_text()); mutate(record)
            path.write_text(json.dumps(record))
            with self.assertRaises(expected):
                self.port.load_blocked_sources(self.RUN)


class CliSourceLedgerKeysAuthenticationTests(unittest.TestCase):
    """M-002: every member of source_ledger_keys is authority-bearing, so the CLI
    must authenticate the whole derived set, not just the primary key."""

    RUN = "run_keys"
    PRIMARY = "run_keys/implementation/1/B2#1"
    REVIEWER = "run_keys/implementation/1/B3#2"

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def ledger(self, *, reviewer=True, verifies=None, extra=()):
        worker={"run":self.RUN,"phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
                "sequence":1,"state":"NEEDS_INPUT","reason_code":"user_choice_required",
                "open_decision_item":True,"open_item":"deployment_target","verifies":None}
        records=[worker]
        if reviewer:
            records.append({**worker,"role":"reviewer","boundary":"B3","sequence":2,
                            "verifies":{"run":self.RUN,"phase":"implementation","iteration":1,
                             "worker_record_key":self.PRIMARY} if verifies is None else verifies})
        records.extend(extra)
        return records

    def run_create(self, keys, records):
        from scripts import clarification_protocol
        value=item(); value["source_ledger_key"]=self.PRIMARY; value["source_ledger_keys"]=list(keys)
        value["phase"]="implementation"; value["iteration"]=1
        source=self.base/"request.json"; source.write_text(json.dumps(create_input([value])))
        argv=["create","--artifact-base",str(self.base),"--run-id",self.RUN,
              "--ledger-key",self.PRIMARY,"--input",str(source)]
        with patch.object(clarification_protocol,"read_decision_ledger",return_value=records):
            return clarification_protocol.main(argv)

    def assertRejected(self, keys, records, message):
        self.assertEqual(2,self.run_create(keys,records),message)
        self.assertFalse((self.base/f"artifacts/runs/{self.RUN}/clarifications").exists(),
                         "a rejected create must publish nothing")

    def test_valid_b2_plus_bound_b3_fold_is_accepted(self):
        self.assertEqual(0,self.run_create([self.PRIMARY,self.REVIEWER],self.ledger()))
        record=next((self.base/f"artifacts/runs/{self.RUN}/clarifications/requests").glob("request_*/record.json"))
        self.assertEqual([self.PRIMARY,self.REVIEWER],json.loads(record.read_text())["items"][0]["source_ledger_keys"])

    def test_fabricated_b3_key_absent_from_the_ledger_is_rejected(self):
        self.assertRejected([self.PRIMARY,"run_keys/implementation/1/B3#99"],self.ledger(reviewer=False),
                            "a key that exists in no ledger record must not ride along on a genuine primary")

    def test_unrelated_b3_key_bound_to_another_worker_is_rejected(self):
        other={"run":self.RUN,"phase":"implementation","iteration":1,"role":"reviewer","boundary":"B3",
               "sequence":3,"state":"NEEDS_INPUT","reason_code":"user_choice_required",
               "open_decision_item":True,"open_item":"deployment_target",
               "verifies":{"run":self.RUN,"phase":"implementation","iteration":1,
                           "worker_record_key":"run_keys/implementation/1/B2#7"}}
        self.assertRejected([self.PRIMARY,"run_keys/implementation/1/B3#3"],
                            self.ledger(reviewer=False,extra=[other]),
                            "a real B3 bound to a different producer is not this request's provenance")

    def test_invalid_verifies_worker_record_key_is_rejected(self):
        self.assertRejected([self.PRIMARY,self.REVIEWER],
                            self.ledger(verifies={"run":self.RUN,"phase":"implementation","iteration":1,
                                                  "worker_record_key":"run_keys/implementation/1/B2#42"}),
                            "a B3 whose binding does not resolve to the producer must not be folded")

    def test_cross_item_key_injection_is_rejected(self):
        other={"run":self.RUN,"phase":"implementation","iteration":1,"role":"worker","boundary":"B2",
               "sequence":5,"state":"NEEDS_INPUT","reason_code":"user_choice_required",
               "open_decision_item":True,"open_item":"other_choice","verifies":None}
        self.assertRejected([self.PRIMARY,"run_keys/implementation/1/B2#5"],
                            self.ledger(reviewer=False,extra=[other]),
                            "another item's producer key must not be injected into this item's set")

    def test_reviewer_primary_without_folding_is_rejected(self):
        from scripts import clarification_protocol
        value=item(); value["source_ledger_key"]=self.REVIEWER; value["source_ledger_keys"]=[self.REVIEWER]
        source=self.base/"request.json"; source.write_text(json.dumps(create_input([value])))
        argv=["create","--artifact-base",str(self.base),"--run-id",self.RUN,
              "--ledger-key",self.REVIEWER,"--input",str(source)]
        with patch.object(clarification_protocol,"read_decision_ledger",return_value=self.ledger()):
            self.assertEqual(2,clarification_protocol.main(argv),
                             "a bound B3 folds onto its B2, so it may not stand as the primary source")

    def test_omitting_a_real_bound_reviewer_understates_provenance_and_is_rejected(self):
        self.assertRejected([self.PRIMARY],self.ledger(),
                            "the set is derived, so dropping a genuinely bound B3 is also a mismatch")

    def test_forged_inner_verifies_fields_are_each_rejected(self):
        """M-002 residual: `verifies` is a CLOSED four-field object and every field
        binds authority. Checking only worker_record_key let a schema-valid B3 carry
        forged inner run/phase/iteration and still be folded into the request."""
        cases={
            "forged_run":{"run":"run_other","phase":"implementation","iteration":1,"worker_record_key":self.PRIMARY},
            "forged_phase":{"run":self.RUN,"phase":"design","iteration":1,"worker_record_key":self.PRIMARY},
            "forged_iteration":{"run":self.RUN,"phase":"implementation","iteration":9,"worker_record_key":self.PRIMARY},
            "missing_field":{"run":self.RUN,"phase":"implementation","worker_record_key":self.PRIMARY},
            "extra_field":{"run":self.RUN,"phase":"implementation","iteration":1,
                           "worker_record_key":self.PRIMARY,"extra":"x"},
        }
        for name,verifies in cases.items():
            with self.subTest(case=name):
                self.setUp()  # a rejected create must leave a clean tree per case
                self.assertRejected([self.PRIMARY,self.REVIEWER],self.ledger(verifies=verifies),
                                    f"{name}: a forged inner binding must not be folded")

    def test_fully_valid_inner_verifies_is_still_accepted(self):
        verifies={"run":self.RUN,"phase":"implementation","iteration":1,"worker_record_key":self.PRIMARY}
        self.assertEqual(0,self.run_create([self.PRIMARY,self.REVIEWER],self.ledger(verifies=verifies)))
