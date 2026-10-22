"""Spring 어댑터: 콜백 순서 · 제안 펼치기 · 확인 번역 · 반영 · 종료 상태 · 인증. 실제 Spring · LLM 없이 확인한다."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.backend import ResumeRequest, proposal_keys, proposal_rows
from app.bridge import SpringBridge, review_decision
from app.data import DATA_DIR
from app.graph import build_graph
from app.runner import AgentRunner
from app.schemas import RuleChange
from app.server import create_app
from app.spring import SpringClient, SpringError
from tests.test_apply import QueueTools, completed
from tests.test_graph import DsrReasoner, ThreeProductTools

TOKEN = "test-token"
CALLBACK = "http://backend:8080/internal/tasks/T1"
HEADERS = {"X-Internal-Token": TOKEN}
CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def sha(version):
    return hashlib.sha256((DATA_DIR / "regulations" / "credit-loan-ops" / f"{version}.md").read_bytes()).hexdigest()


class FakeSpring:
    """SpringClient 대역. 받은 콜백을 순서대로 기록하고, 큐 작업 조회는 바로 DONE 을 돌려준다."""

    def __init__(self, docs=None, job=completed):
        self.calls = []
        self.jobs_read = []
        self._job = job
        self.docs = docs or [{"side": "OLD", "versionLabel": "v3", "sha256": sha("v3")},
                             {"side": "NEW", "versionLabel": "v4", "sha256": sha("v4")}]

    def documents(self):
        return self.docs

    def event(self, type_, payload=None):
        self.calls.append(("event", type_, payload or {}))

    def put(self, path, body):
        self.calls.append(("put", path, body))

    def post(self, path, body):
        self.calls.append(("post", path, body))

    def job(self, job_id):
        self.jobs_read.append(job_id)
        return self._job(job_id)

    def close(self):
        pass

    def kinds(self):
        out = []
        for kind, name, body in self.calls:
            if kind == "event" and name == "STEP":
                out.append(f"STEP:{body['node']}")
            elif kind == "event":
                out.append(f"{name}:{body.get('step') or body.get('outcome') or ''}")
            else:
                out.append(f"{kind.upper()} {name}")
        return out

    def puts(self, path):
        return [b for k, n, b in self.calls if k == "put" and n == path]

    def last_reason(self):
        kind, name, body = self.calls[-1]
        assert name == "FAILED", self.kinds()[-3:]
        return body["reason"]


def in_order(kinds, expected):
    it = iter(kinds)
    return all(any(k == e for k in it) for e in expected)


def client(runner, spring):
    app = create_app(runner, submit=lambda fn, *args: fn(*args), spring_factory=lambda url: spring, token=TOKEN)
    return TestClient(app)


def runner(tools=None, reasoner=None):
    return AgentRunner(build_graph(tools or ThreeProductTools(), reasoner or DsrReasoner(), poll_interval=.001))


def run_body():
    return {"taskId": "T1", "threadId": "T1", "title": "개인신용대출 운영기준 v3 → v4",
            "documents": [{"side": "OLD", "versionLabel": "v3", "filename": "v3.md", "sha256": sha("v3")},
                          {"side": "NEW", "versionLabel": "v4", "filename": "v4.md", "sha256": sha("v4")}],
            "callbackBaseUrl": CALLBACK}


def resume_body(decision, approved=(), excluded=(), comment=None, approval_id=7):
    return {"taskId": "T1", "threadId": "T1", "step": "REVIEW", "decision": decision, "approvalId": approval_id,
            "comment": comment, "callbackBaseUrl": CALLBACK,
            "payload": {"approvedProposalKeys": list(approved), "excludedProposalKeys": list(excluded)}}


def started(spring=None, tools=None, reasoner=None):
    spring = spring or FakeSpring()
    tools = tools or ThreeProductTools()
    api = client(runner(tools, reasoner), spring)
    assert api.post("/run", json=run_body(), headers=HEADERS).status_code == 202
    return api, spring, tools


# ---------- 콜백 순서 · 제안 펼치기 ----------

def test_run_reports_each_node_and_waits_for_review():
    api, spring, _ = started()
    kinds = spring.kinds()
    assert in_order(kinds, ["STEP:requirement_agent", "PUT requirements", "STEP:product_impact_agent",
                            "STEP:proposal_agent", "PUT proposals", "STEP:verify", "PUT proposals",
                            "APPROVAL_REQUIRED:REVIEW"])
    verify = next(b for k, n, b in spring.calls if k == "event" and b.get("node") == "verify")
    assert verify["round"] == 1
    reqs = spring.puts("requirements")[0]["items"]
    assert [r["reqKey"] for r in reqs] == ["REQ-8"]


def test_proposals_are_one_row_per_product_rule_including_keep():
    api, spring, _ = started()
    rows = {r["proposalKey"]: r for r in spring.puts("proposals")[-1]["items"]}
    assert set(rows) == {"P001:DSR_LIMIT", "P002:DSR_LIMIT", "P003:REQ-8"}
    p001 = rows["P001:DSR_LIMIT"]
    assert (p001["impact"], p001["ruleId"], p001["unit"], p001["currentValue"], p001["proposedValue"]) == \
        ("change_required", "R001", "ratio", "0.40", "0.35")
    assert p001["effectiveDate"] == "2026-12-01" and p001["round"] == 1 and p001["verifyStatus"] == "PASSED"
    assert p001["followUp"] and p001["reason"]
    keep = rows["P003:REQ-8"]
    assert (keep["impact"], keep["ruleId"], keep["verifyStatus"]) == ("keep", "R015", "PASSED")
    # 제안 작성 직후(검사 전)는 PENDING
    assert spring.puts("proposals")[0]["items"][0]["verifyStatus"] == "PENDING"


# docs/BACKEND_API.md 10.2 · docs/contracts/proposals.put.json 의 필드. 바뀌면 백엔드 ProposalsTest 와 같이 고친다.
CONTRACT_FIELDS = {"proposalKey", "reqKey", "productId", "ruleId", "ruleKey", "impact", "current", "proposed", "unit",
                   "currentValue", "proposedValue", "effectiveDate", "basis", "reason", "transition", "followUp",
                   "round", "verifyStatus", "verifyLog", "version", "revisionOutcome", "revisionResponse", "changedFields"}


def test_rows_match_contract_field_set():
    api, spring, _ = started()
    for row in spring.puts("proposals")[-1]["items"]:
        assert set(row) == CONTRACT_FIELDS, row["proposalKey"]
    if (CONTRACTS / "proposals.put.json").exists():
        # 백엔드 계약 PR 병합 전에는 기존 공유 예시에 합의한 네 칸을 합쳐 비교한다.
        revision_fields = {"version", "revisionOutcome", "revisionResponse", "changedFields"}
        assert set(json.loads((CONTRACTS / "proposals.put.json").read_text())["items"][0]) | revision_fields == CONTRACT_FIELDS


class ExtraRuleOnce(DsrReasoner):
    """1회차에 대상이 아닌 규칙을 끼워 넣어 근거 검사에 실패하고, 2회차 재작성에서 뺀다."""

    def draft_product(self, context):
        draft = super().draft_product(context)
        if not context["previous_attempt"]:
            draft.changes.append(RuleChange(rule_id="R999", rule_key="EXTRA_KEY", current_value="1", proposed_value="2", unit="ratio"))
        return draft


def test_rewritten_round_drops_stale_rule_rows_and_approval_uses_current_keys():
    api, spring, tools = started(tools=QueueTools(), reasoner=ExtraRuleOnce())
    puts = spring.puts("proposals")
    first_check = {r["proposalKey"]: r for r in puts[1]["items"]}
    assert first_check["P001:EXTRA_KEY"]["round"] == 1 and first_check["P001:EXTRA_KEY"]["verifyStatus"] == "FAILED"
    current = {r["proposalKey"]: r for r in puts[-1]["items"]}
    assert "P001:EXTRA_KEY" not in current
    assert current["P001:DSR_LIMIT"]["round"] == 2 and current["P001:DSR_LIMIT"]["verifyStatus"] == "PASSED"
    # 백엔드는 묶음(reqKey × productId)마다 최고 회차 행만 현재 제안으로 본다 (Proposals.latest). 그 키 집합으로 승인이 온다.
    spring.calls.clear()
    res = api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT", "P002:DSR_LIMIT"]), headers=HEADERS)
    assert res.status_code == 202 and spring.kinds()[-1] == "DONE:DONE"
    assert [r["proposal_key"] for r in tools.requests] == ["P001:DSR_LIMIT", "P002:DSR_LIMIT"]


def test_two_requirements_changing_one_rule_fail_loudly_instead_of_overwriting():
    class TwoRequirements(DsrReasoner):
        def requirements(self, diff):
            req = super().requirements(diff)[0]
            return [req, req.model_copy(update={"id": "REQ-9"})]

    spring = FakeSpring()
    client(runner(reasoner=TwoRequirements()), spring).post("/run", json=run_body(), headers=HEADERS)
    assert "중복" in spring.last_reason() and "P001:DSR_LIMIT" in spring.last_reason()


# ---------- 확인 (10.3) → 반영 (10.4) ----------

def test_backend_approval_runs_apply_and_reports_done():
    api, spring, tools = started(tools=QueueTools())
    spring.calls.clear()
    res = api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT"], excluded=["P002:DSR_LIMIT"]),
                   headers=HEADERS)
    assert res.status_code == 202
    assert in_order(spring.kinds(), ["STEP:apply", "STEP:report", "PUT report", "DONE:DONE"])
    assert tools.requests == [{"task_id": "T1", "approval_id": 7, "proposal_key": "P001:DSR_LIMIT",
                               "idempotency_key": "T1:apply:P001:DSR_LIMIT"}]
    assert spring.jobs_read == ["P001:DSR_LIMIT"]   # 이 요청의 SpringClient.job 으로 조회한다
    report = spring.puts("report")[0]
    assert report["status"] == "DONE"
    assert {p["product_id"]: p["status"] for p in report["proposals"]} == {"P001": "approved", "P002": "rejected"}
    assert [(r["proposal_key"], r["status"]) for r in report["apply_results"]] == [("P001:DSR_LIMIT", "DONE")]
    assert "## 반영" in report["markdown"] and "R083" in report["markdown"]


def test_dead_job_reports_failed_with_reason():
    spring = FakeSpring(job=lambda job_id: {"id": job_id, "taskId": "T1", "status": "DEAD", "lastError": "이미 바뀐 규칙: R001"})
    api, spring, _ = started(spring=spring, tools=QueueTools())
    spring.calls.clear()
    api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT", "P002:DSR_LIMIT"]), headers=HEADERS)
    assert spring.kinds()[-2:] == ["PUT report", "FAILED:"]
    assert "이미 바뀐 규칙: R001" in spring.last_reason()
    assert spring.puts("report")[0]["status"] == "FAILED"


def test_approval_without_mcp_write_tool_never_reports_done():
    # 로컬 자료(LocalTools)로는 반영할 수 없다. 승인 즉시 DONE 으로 끝내지 않고 FAILED 로 보고한다.
    api, spring, _ = started()
    spring.calls.clear()
    api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT"], excluded=["P002:DSR_LIMIT"]), headers=HEADERS)
    assert spring.kinds()[-1] == "FAILED:" and "MCP" in spring.last_reason()
    assert not any(k == "DONE:DONE" for k in spring.kinds())


def test_reject_ends_rejected_without_apply():
    api, spring, tools = started(tools=QueueTools())
    spring.calls.clear()
    api.post("/resume", json=resume_body("REJECT", comment="보류"), headers=HEADERS)
    assert spring.kinds()[-1] == "DONE:REJECTED"
    assert not any(k == "STEP:apply" for k in spring.kinds()) and tools.requests == []


def test_review_translation_maps_backend_keys_to_agent_decisions_and_apply_approval():
    r = runner()
    r.start("T1", "credit-loan-ops")
    proposals = r.current_proposals("T1")
    assert [proposal_keys(p) for p in proposals] == [["P001:DSR_LIMIT"], ["P002:DSR_LIMIT"]]
    cmd = review_decision(proposals, "APPROVE", {"approvedProposalKeys": ["P002:DSR_LIMIT"],
                                                 "excludedProposalKeys": ["P001:DSR_LIMIT"]}, "ok", approval_id=7)
    assert cmd["finish"] is True
    assert cmd["apply_approval"] == {"approval_id": 7, "proposal_keys": ["P002:DSR_LIMIT"]}
    assert [(d["proposal_id"], d["action"], d["version"]) for d in cmd["decisions"]] == \
        [("REQ-8:P001", "reject", 1), ("REQ-8:P002", "approve", 1)]
    assert review_decision(proposals, "REJECT", {}, "보류")["apply_approval"] is None
    for bad in ({"decision": "REQUEST_CHANGES", "payload": {}},
                {"decision": "APPROVE", "payload": {"approvedProposalKeys": ["P001:DSR_LIMIT"]}},              # 제외 누락
                {"decision": "APPROVE", "payload": {"approvedProposalKeys": ["P009:X"], "excludedProposalKeys": ["P001:DSR_LIMIT", "P002:DSR_LIMIT"]}},
                {"decision": "APPROVE", "payload": {"approvedProposalKeys": [], "excludedProposalKeys": ["P001:DSR_LIMIT", "P002:DSR_LIMIT"]}},
                {"decision": "APPROVE", "payload": {"approvedProposalKeys": ["P001:DSR_LIMIT", "P002:DSR_LIMIT"]}, "approval_id": None}):
        with pytest.raises(ValueError):
            review_decision(proposals, bad["decision"], bad["payload"], approval_id=bad.get("approval_id", 7))


def test_rule_level_exclusion_within_one_proposal():
    r = runner()
    out = r.start("T1", "credit-loan-ops")
    p = out["proposals"][0].model_copy(deep=True)
    p.changes.append(RuleChange(rule_id="R2", rule_key="OTHER_DSR", current_value="0.4", proposed_value="0.35", unit="ratio"))
    assert [row["proposalKey"] for row in proposal_rows(p)] == ["P001:DSR_LIMIT", "P001:OTHER_DSR"]
    cmd = review_decision([p, out["proposals"][1]], "APPROVE",
                          {"approvedProposalKeys": ["P001:DSR_LIMIT"], "excludedProposalKeys": ["P001:OTHER_DSR", "P002:DSR_LIMIT"]},
                          approval_id=7)
    assert cmd["apply_approval"]["proposal_keys"] == ["P001:DSR_LIMIT"]
    assert [d["action"] for d in cmd["decisions"]] == ["approve", "reject"]


def test_resume_rejects_bad_keys_before_running_and_missing_or_finished_threads_with_409():
    api, spring, _ = started(tools=QueueTools())
    body = resume_body("APPROVE", approved=["P001:DSR_LIMIT"], excluded=["P002:DSR_LIMIT"])
    assert api.post("/resume", json={**body, "step": "REQUIREMENTS"}, headers=HEADERS).status_code == 400
    assert api.post("/resume", json={**body, "decision": "REQUEST_CHANGES"}, headers=HEADERS).status_code == 400
    assert api.post("/resume", json={**body, "approvalId": None}, headers=HEADERS).status_code == 400
    assert api.post("/resume", json=resume_body("APPROVE", approved=["P001:DSR_LIMIT"]), headers=HEADERS).status_code == 400
    assert api.post("/resume", json={**body, "threadId": "UNKNOWN"}, headers=HEADERS).status_code == 409
    spring.calls.clear()
    assert api.post("/resume", json=body, headers=HEADERS).status_code == 202
    assert spring.kinds()[-1] == "DONE:DONE"
    assert api.post("/resume", json=body, headers=HEADERS).status_code == 409   # 이미 끝난 thread


class Crash(BaseException):
    """프로세스가 죽은 것처럼. Exception 이 아니라 apply 노드가 잡지 않고 그래프 밖으로 나간다."""


def test_continue_finishes_apply_after_a_crash_with_the_same_idempotency_key():
    reads = []

    def job(job_id):
        reads.append(job_id)
        if len(reads) == 1:
            raise Crash
        return completed(job_id)
    tools, spring = QueueTools(), FakeSpring(job=job)
    r = runner(tools=tools)
    api = client(r, spring)
    assert api.post("/run", json=run_body(), headers=HEADERS).status_code == 202
    req = ResumeRequest.model_validate(resume_body("APPROVE", approved=["P001:DSR_LIMIT"], excluded=["P002:DSR_LIMIT"]))
    with pytest.raises(Crash):   # 큐 조회 도중 죽음. 백엔드는 PUBLISHING 에 멈춘다
        r.resume_backend(req, listener=SpringBridge(spring), job_reader=spring.job)
    assert not any(k.startswith("DONE") or k.startswith("FAILED") for k in spring.kinds())
    assert r.pending_nodes("T1") == ("apply",)
    cont = {"taskId": "T1", "threadId": "T1", "callbackBaseUrl": CALLBACK}
    spring.calls.clear()
    assert api.post("/continue", json=cont, headers=HEADERS).status_code == 202
    assert in_order(spring.kinds(), ["STEP:apply", "PUT report", "DONE:DONE"])
    assert [r["idempotency_key"] for r in tools.requests] == ["T1:apply:P001:DSR_LIMIT"] * 2
    assert api.post("/continue", json=cont, headers=HEADERS).status_code == 409           # 끝난 작업
    api2, _, _ = started(tools=QueueTools())
    assert api2.post("/continue", json=cont, headers=HEADERS).status_code == 409          # 승인 대기 중
    assert api2.post("/continue", json={**cont, "threadId": "NOPE"}, headers=HEADERS).status_code == 409


def test_threads_are_locked_independently():
    r = runner(tools=QueueTools())
    r.start("T1", "credit-loan-ops")
    r.start("T2", "credit-loan-ops")
    with r._lock("T1"):   # T1 이 LLM 호출 중이라고 치면
        assert r.waiting_review("T2")
        assert r.resume_backend(ResumeRequest(taskId="T2", threadId="T2", step="REVIEW", decision="REJECT"))["result"].status == "REJECTED"


# ---------- 종료 · 실패 · 인증 ----------

def test_no_requirements_ends_no_change_without_review():
    class NoChange(DsrReasoner):
        def requirements(self, diff):
            return []

    spring = FakeSpring()
    client(runner(reasoner=NoChange()), spring).post("/run", json=run_body(), headers=HEADERS)
    assert spring.kinds()[-1] == "DONE:NO_CHANGE"
    assert not any(k.startswith("APPROVAL_REQUIRED") for k in spring.kinds())


def test_document_mismatch_is_reported_as_failure():
    spring = FakeSpring(docs=[{"side": "NEW", "versionLabel": "v4", "sha256": "0" * 64}])
    client(runner(), spring).post("/run", json=run_body(), headers=HEADERS)
    assert "원문" in spring.last_reason()


def test_rejects_wrong_token_and_missing_callback():
    api = client(runner(), FakeSpring())
    assert api.post("/run", json=run_body(), headers={"X-Internal-Token": "x"}).status_code == 401
    assert api.post("/run", json={**run_body(), "callbackBaseUrl": None}, headers=HEADERS).status_code == 400


def test_spring_client_urls_and_token():
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url), request.headers["X-Internal-Token"]))
        return httpx.Response(200, json={"status": "DONE"})

    spring = SpringClient(CALLBACK, token=TOKEN, transport=httpx.MockTransport(handler))
    spring.event("STEP", {"node": "verify"})
    spring.put("proposals", {"items": []})
    spring.job("J1")
    assert seen == [("POST", f"{CALLBACK}/events", TOKEN), ("PUT", f"{CALLBACK}/proposals", TOKEN),
                    ("GET", "http://backend:8080/internal/jobs/J1", TOKEN)]


def test_spring_client_retries_transient_failures_but_not_client_errors():
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if request.url.path.endswith("/proposals"):
            return httpx.Response(409, text="작업 상태가 DONE 입니다")
        if request.url.path.endswith("/report"):
            raise httpx.ConnectError("connection refused")
        return httpx.Response(503) if attempts.count(request.url.path) < 3 else httpx.Response(200, json={})

    spring = SpringClient(CALLBACK, token=TOKEN, transport=httpx.MockTransport(handler), retries=2, backoff=0)
    spring.event("STEP", {"node": "verify"})                       # 503, 503, 200
    assert attempts.count(f"/internal/tasks/T1/events") == 3
    with pytest.raises(SpringError, match="409"):
        spring.put("proposals", {"items": []})                     # 4xx 는 바로 실패
    assert attempts.count(f"/internal/tasks/T1/proposals") == 1
    with pytest.raises(SpringError, match="연결 실패"):
        spring.put("report", {})                                   # 연결 실패는 재시도 뒤 실패
    assert attempts.count(f"/internal/tasks/T1/report") == 3
