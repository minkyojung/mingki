"""10/22 확정안: 실제 LLM·Spring·DB 없이 수정 접수 → 콜백 → 재검토·재승인을 검증한다."""

import pytest

from app.runner import AgentRunner
from app.schemas import ProductRevision, RuleChange
from tests.test_apply import QueueTools
from tests.test_graph import DsrReasoner
from tests.test_server import (
    Crash, FakeSpring, HEADERS, CALLBACK, client, in_order, resume_body, run_body, runner,
)


class RevisionReasoner(DsrReasoner):
    def __init__(self, outcome="revised"):
        super().__init__()
        self.outcome = outcome

    def draft_product(self, context):
        draft = super().draft_product(context)
        if not context.get("previous_proposal"):
            return draft
        if self.outcome == "failed":
            raise ValueError("테스트: 재작성 연결 실패")
        return ProductRevision(**draft.model_dump(), revision_outcome=self.outcome,
                               revision_response="요청한 안내 문장을 조치에 추가했습니다" if self.outcome == "revised"
                               else "테스트: 근거 충돌 또는 추가 자료 확인이 필요합니다")


class InitialRepairReasoner(RevisionReasoner):
    def draft_product(self, context):
        draft = super().draft_product(context)
        if not context.get("previous_proposal") and not context.get("previous_attempt"):
            draft.changes.append(RuleChange(rule_id="R999", rule_key="EXTRA_KEY", current_value="1",
                                            proposed_value="2", unit="ratio"))
        return draft


def revision_body(version=1, proposal_id="REQ-8:P001", comment="고객 안내 문장을 조치에 추가해줘"):
    body = resume_body("REQUEST_CHANGES", comment=comment, approval_id=None)
    body["payload"] = {"proposalId": proposal_id, "baseVersion": version}
    return body


def setup_revision(reasoner=None):
    reasoner, tools, spring = reasoner or RevisionReasoner(), QueueTools(), FakeSpring()
    r = runner(tools=tools, reasoner=reasoner)
    api = client(r, spring)
    assert api.post("/run", json=run_body(), headers=HEADERS).status_code == 202
    return api, r, spring, tools, reasoner


def rows(spring):
    return {row["proposalKey"]: row for row in spring.puts("proposals")[-1]["items"]}


def test_revision_returns_current_rows_preserves_others_and_requires_fresh_approval():
    api, r, spring, tools, reasoner = setup_revision()
    old = rows(spring)
    assert all((row["version"], row["revisionOutcome"], row["revisionResponse"], row["changedFields"])
               == (1, "none", None, []) for row in old.values())
    spring.calls.clear()
    # 수정 요청에 승인 이력 ID가 있어도 실제 반영 승인으로 변환하지 않는다.
    body = {**revision_body(), "approvalId": 42}
    assert api.post("/resume", json=body, headers=HEADERS).status_code == 202
    current = rows(spring)
    changed = current["P001:DSR_LIMIT"]
    assert (changed["version"], changed["round"], changed["verifyStatus"], changed["revisionOutcome"]) == (2, 1, "PASSED", "revised")
    assert changed["revisionResponse"] and changed["changedFields"] == ["actions"]
    for name in ("ruleId", "ruleKey", "unit", "currentValue", "proposedValue", "effectiveDate", "impact", "basis"):
        assert changed[name] == old["P001:DSR_LIMIT"][name]
    assert current["P002:DSR_LIMIT"] == old["P002:DSR_LIMIT"]
    assert current["P003:REQ-8"] == old["P003:REQ-8"]
    assert len(reasoner.contexts) == 3 and tools.requests == [] and not spring.puts("report")
    assert r.waiting_review("T1")
    assert in_order(spring.kinds(), ["STEP:proposal_agent", "PUT proposals", "STEP:verify", "PUT proposals", "APPROVAL_REQUIRED:REVIEW"])
    assert spring.kinds()[-1] == "APPROVAL_REQUIRED:REVIEW"

    spring.calls.clear()
    assert api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT"],
                                              excluded=["P002:DSR_LIMIT"]), headers=HEADERS).status_code == 202
    assert spring.kinds()[-1] == "DONE:DONE"
    assert [p["version"] for p in spring.puts("report")[-1]["proposals"]] == [2, 1]
    assert [req["proposal_key"] for req in tools.requests] == ["P001:DSR_LIMIT"]


@pytest.mark.parametrize("payload,comment", [
    ({}, "안내 문장을 추가해줘"),
    ({"proposalId": "P001:DSR_LIMIT", "baseVersion": 1}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P003", "baseVersion": 1}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": 9}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": "1"}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": True}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": 0}, "안내 문장을 추가해줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": 1}, "다시 써줘"),
    ({"proposalId": "REQ-8:P001", "baseVersion": 1}, " "),
    ({"proposalId": "REQ-8:P001", "baseVersion": 1}, None),
])
def test_invalid_revision_is_rejected_synchronously_without_model_or_callback(payload, comment):
    api, r, spring, tools, reasoner = setup_revision()
    before = [p.model_dump() for p in r.current_proposals("T1")]
    spring.calls.clear()
    body = {**revision_body(comment=comment), "payload": payload}
    assert api.post("/resume", json=body, headers=HEADERS).status_code == 400
    assert len(reasoner.contexts) == 2 and spring.calls == [] and tools.requests == []
    assert r.waiting_review("T1") and [p.model_dump() for p in r.current_proposals("T1")] == before


def test_wrong_task_is_rejected_before_background_execution():
    api, r, spring, _, reasoner = setup_revision()
    spring.calls.clear()
    assert api.post("/resume", json={**revision_body(), "taskId": "OTHER"}, headers=HEADERS).status_code == 400
    assert spring.calls == [] and len(reasoner.contexts) == 2 and r.waiting_review("T1")


def test_repeated_revision_uses_new_versions_and_old_request_cannot_run_again():
    api, r, spring, tools, reasoner = setup_revision()
    for proposal_id, base_version in [("REQ-8:P001", 1), ("REQ-8:P002", 1), ("REQ-8:P001", 2)]:
        body = revision_body(base_version, proposal_id)
        assert api.post("/resume", json=body, headers=HEADERS).status_code == 202
        count = len(reasoner.contexts)
        callbacks = len(spring.calls)
        assert api.post("/resume", json=body, headers=HEADERS).status_code == 400
        assert len(reasoner.contexts) == count and len(spring.calls) == callbacks
    assert [(p.version, p.attempt) for p in r.current_proposals("T1")] == [(3, 1), (2, 1)]
    assert rows(spring)["P003:REQ-8"]["version"] == 1 and tools.requests == []


def test_version_two_attempt_one_follows_version_one_attempt_two():
    api, _, spring, _, _ = setup_revision(InitialRepairReasoner())
    old = rows(spring)
    assert (old["P001:DSR_LIMIT"]["version"], old["P001:DSR_LIMIT"]["round"]) == (1, 2)
    spring.calls.clear()
    assert api.post("/resume", json=revision_body(), headers=HEADERS).status_code == 202
    current = rows(spring)
    assert (current["P001:DSR_LIMIT"]["version"], current["P001:DSR_LIMIT"]["round"]) == (2, 1)
    assert current["P002:DSR_LIMIT"] == old["P002:DSR_LIMIT"]
    assert current["P003:REQ-8"] == old["P003:REQ-8"]
    verify_events = [b for k,n,b in spring.calls if k == "event" and n == "STEP" and b.get("node") == "verify" and "round" in b]
    assert [e["round"] for e in verify_events] == [1]


@pytest.mark.parametrize("outcome", ["cannot_apply", "needs_clarification", "failed"])
def test_non_success_outcomes_return_to_review_with_original_body_and_no_apply(outcome):
    api, r, spring, tools, _ = setup_revision(RevisionReasoner(outcome))
    original = r.current_proposals("T1")[0].model_dump()
    spring.calls.clear()
    assert api.post("/resume", json=revision_body(), headers=HEADERS).status_code == 202
    p = r.current_proposals("T1")[0]
    row = rows(spring)["P001:DSR_LIMIT"]
    assert p.version == 2 and row["revisionOutcome"] == outcome and row["revisionResponse"]
    for field in ("recommendation", "actions", "optional_actions", "precautions", "changes", "scope", "conditions", "impact"):
        assert p.model_dump()[field] == original[field]
    assert row["verifyStatus"] == ("FAILED" if outcome == "failed" else "PASSED")
    assert row["changedFields"] == [] and spring.kinds()[-1] == "APPROVAL_REQUIRED:REVIEW"
    assert r.waiting_review("T1") and tools.requests == [] and not spring.puts("report")
    if outcome == "failed":
        # 실패한 수정 제안을 제외하고 다른 정상 제안만 반영할 수 있다.
        assert api.post("/resume", json=resume_body("APPROVE", approved=["P002:DSR_LIMIT"],
                                                  excluded=["P001:DSR_LIMIT"]), headers=HEADERS).status_code == 202
        assert [req["proposal_key"] for req in tools.requests] == ["P002:DSR_LIMIT"]


def test_continue_revision_after_restart_restores_keep_rows_and_current_attempt():
    class CrashOnce(InitialRepairReasoner):
        crashed = False

        def check_revision(self, context):
            if not self.crashed:
                self.crashed = True
                raise Crash
            return super().check_revision(context)

    api, r, spring, tools, reasoner = setup_revision(CrashOnce())
    old = rows(spring)
    with pytest.raises(Crash):
        api.post("/resume", json=revision_body(), headers=HEADERS)
    assert r.pending_nodes("T1") == ("verify",)
    calls = len(reasoner.contexts)
    # 새 실행기·Bridge에서도 같은 체크포인트를 사용한다.
    api = client(AgentRunner(r.graph), spring)
    spring.calls.clear()
    assert api.post("/continue", json={"taskId":"T1", "threadId":"T1", "callbackBaseUrl":CALLBACK}, headers=HEADERS).status_code == 202
    current = rows(spring)
    assert (current["P001:DSR_LIMIT"]["version"], current["P001:DSR_LIMIT"]["round"], current["P001:DSR_LIMIT"]["verifyStatus"]) == (2, 1, "PASSED")
    assert current["P002:DSR_LIMIT"] == old["P002:DSR_LIMIT"] and current["P003:REQ-8"] == old["P003:REQ-8"]
    assert spring.kinds()[-1] == "APPROVAL_REQUIRED:REVIEW" and len(reasoner.contexts) == calls and tools.requests == []
