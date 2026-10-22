"""Spring 전송 계약 변환. HTTP 호출/DB 접근 없이 테스트할 수 있는 연결 지점.

Agent 와 백엔드는 제안 단위가 다르다. Agent 는 상품 × 요건 1건에 규칙 변경 여러 개(changes)를 담고,
백엔드는 상품 × 규칙 1행이다 (docs/BACKEND_API.md 10.2). 여기서 펼친다. 유지 판정(no_change)은 Agent 에서
제안이 아니지만 백엔드는 모든 상품에 행 하나를 요구해서 keep 행으로 보낸다.
"""

from pydantic import BaseModel, ConfigDict, Field

VERIFY = {"pending": "PENDING", "passed": "PASSED", "failed": "FAILED"}


class DocumentInfo(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    side: str
    version_label: str = Field(alias="versionLabel")
    filename: str = ""
    sha256: str = ""


class RunRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    task_id: str = Field(alias="taskId")
    thread_id: str = Field(alias="threadId")
    documents: list[DocumentInfo]
    callback_base_url: str | None = Field(default=None, alias="callbackBaseUrl")

    def to_input(self, regulation_id):
        # regulation_id는 업로드 원문을 등록한 MCP의 식별자를 전달받는다.
        versions = {doc.side: doc.version_label for doc in self.documents}
        if (
            set(versions) != {"OLD", "NEW"}
            or len(self.documents) != 2
            or not regulation_id
        ):
            raise ValueError("구·신 문서 정보와 MCP 규정 식별자가 필요합니다")
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "regulation_id": regulation_id,
            "old_version": versions["OLD"],
            "new_version": versions["NEW"],
        }


class ResumeRequest(BaseModel):
    """백엔드 확인(10.3) 재개 요청. 그래프 명령으로 바꾸는 일은 app.bridge.review_decision 이 한다."""
    model_config = ConfigDict(populate_by_name=True)
    task_id: str = Field(alias="taskId")
    thread_id: str = Field(alias="threadId")
    step: str
    decision: str
    approval_id: int | None = Field(default=None, alias="approvalId")
    comment: str | None = None
    payload: dict = Field(default_factory=dict)
    callback_base_url: str | None = Field(default=None, alias="callbackBaseUrl")


class ContinueRequest(BaseModel):
    """반영 도중 Agent 가 죽어 PUBLISHING 에 멈춘 작업을 이어가는 요청 (운영자 · 백엔드 재시도)."""
    model_config = ConfigDict(populate_by_name=True)
    task_id: str = Field(alias="taskId")
    thread_id: str = Field(alias="threadId")
    callback_base_url: str | None = Field(default=None, alias="callbackBaseUrl")


def requirements_payload(items):
    return {
        "items": [
            {
                "reqKey": r.id,
                "summary": r.summary,
                "target": r.target,
                "effectiveDate": r.effective_date.isoformat()
                if r.effective_date
                else None,
                "transitional": r.transitional,
                "articleRef": r.article_ref,
                "oldText": r.old_text,
                "newText": r.new_text,
                "detail": r.model_dump(mode="json"),
            }
            for r in items
        ]
    }


def proposal_keys(proposal):
    """Agent 제안 하나가 백엔드에서 차지하는 행의 키. 규칙 변경이 있으면 규칙마다, 없으면 요건 하나."""
    if proposal.impact == "change_required" and proposal.changes:
        return [f"{proposal.product_id}:{c.rule_key}" for c in proposal.changes]
    return [f"{proposal.product_id}:{proposal.requirement_id}"]


BASIS_LIMIT = 200  # rule_proposals.basis VARCHAR(200)


def basis_text(refs):
    """근거 조항 uri를 쉼표로 잇는다. 백엔드 칸(200자)을 넘지 않게 앞에서부터 들어가는 만큼만."""
    out = []
    for ref in dict.fromkeys(r for r in refs if r):
        if len(", ".join([*out, ref])) > BASIS_LIMIT:
            break
        out.append(ref)
    return ", ".join(out) or None


def proposal_rows(proposal):
    """Agent 제안 1건 → 백엔드 제안 행들 (docs/contracts/proposals.put.json 모양). 키는 작성 버전이 바뀌어도 유지한다."""
    base = {
        "reqKey": proposal.requirement_id,
        "productId": proposal.product_id,
        "current": proposal.current,
        "proposed": proposal.recommendation,
        "effectiveDate": proposal.effective_date.isoformat() if proposal.effective_date else None,
        "basis": basis_text([proposal.article_ref, *proposal.basis_refs]),
        "reason": proposal.reason,
        "transition": proposal.transitional,
        "followUp": [*proposal.actions, *proposal.optional_actions],
        "version": proposal.version,
        "revisionOutcome": proposal.revision_outcome,
        "revisionResponse": proposal.revision_response or None,
        "changedFields": list(proposal.changed_fields),
        "round": proposal.attempt,
        "verifyStatus": VERIFY[proposal.verification],
        "verifyLog": proposal.generation_error,
    }
    if proposal.impact == "change_required" and proposal.changes:
        return [{**base, "proposalKey": f"{proposal.product_id}:{c.rule_key}", "ruleId": c.rule_id,
                 "ruleKey": c.rule_key, "impact": "change_required", "unit": c.unit,
                 "currentValue": c.current_value, "proposedValue": c.proposed_value}
                for c in proposal.changes]
    return [{**base, "proposalKey": f"{proposal.product_id}:{proposal.requirement_id}", "ruleId": None,
             "ruleKey": None, "impact": "needs_check", "unit": None, "currentValue": None, "proposedValue": None}]


def keep_row(impact, round_):
    """유지 판정은 Agent 제안이 아니라서 영향 판단에서 행을 만든다. 근거 검사 대상이 아니라 PASSED.

    유지는 바꿀 규칙이 없어서 target_rule_ids 가 비어 있다. 근거 규칙이 하나면 그것을 ruleId 로 둔다.
    """
    evidence = list(dict.fromkeys(impact.evidence_rule_ids))
    return {"proposalKey": f"{impact.product_id}:{impact.requirement_id}", "reqKey": impact.requirement_id,
            "productId": impact.product_id, "ruleId": evidence[0] if len(evidence) == 1 else None,
            "ruleKey": None, "impact": "keep", "current": impact.current, "proposed": None, "unit": None,
            "currentValue": None, "proposedValue": None, "effectiveDate": None, "basis": basis_text(impact.basis_refs),
            "reason": impact.reason, "transition": None, "followUp": [], "round": round_,
            "version": 1, "revisionOutcome": "none", "revisionResponse": None, "changedFields": [],
            "verifyStatus": "PASSED", "verifyLog": None}


def proposal_items(proposals, keep_impacts, round_):
    """PUT /proposals 본문. 백엔드는 상품 × 규칙 1행이라 같은 키가 두 번 나오면 조용히 덮어쓰지 않고 거부한다."""
    items = [row for p in proposals for row in proposal_rows(p)]
    items += [keep_row(i, round_) for i in keep_impacts]
    keys = [row["proposalKey"] for row in items]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        raise ValueError(f"같은 상품 · 규칙의 제안이 중복됩니다: {duplicated}. "
                         "요건 두 개가 같은 규칙을 바꾸면 백엔드 행 하나로 보낼 수 없습니다")
    return {"items": items}


def product_proposals_payload(items):
    """기존 CLI 검토 형식. 백엔드 10.2 저장에는 proposal_items 를 사용한다."""
    return {"items": [{
        "proposalKey": p.id, "reqKey": p.requirement_id,
        "productId": p.product_id, "productName": p.product_name,
        "articleRef": p.article_ref, "impact": p.impact, "targetRuleIds": p.target_rule_ids, "evidenceRuleIds": p.evidence_rule_ids, "documentRefs": p.document_refs,
        "changes": [c.model_dump() for c in p.changes], "scope": p.scope,
        "conditions": [c.model_dump() for c in p.conditions], "verification": p.verification,
        "optionalActions": p.optional_actions, "current": p.current, "reason": p.reason, "proposed": p.recommendation,
        "actions": p.actions, "precautions": p.precautions,
        "effectiveDate": p.effective_date.isoformat() if p.effective_date else None,
        "transitional": p.transitional, "status": p.status, "version": p.version,
        "humanEdited": p.human_edited, "reviewComment": p.review_comment,
        "generationError": p.generation_error,
        "revisionResponse": p.revision_response, "changedFields": p.changed_fields,
    } for p in items]}
