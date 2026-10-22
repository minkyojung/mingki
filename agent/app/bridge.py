"""그래프 실행을 Spring 콜백으로 옮긴다. 계약은 docs/BACKEND_API.md 10절.

노드에 들어갈 때 STEP, 노드가 끝나면 산출물 저장, interrupt 에서 APPROVAL_REQUIRED, 끝나면 리포트 뒤 DONE 또는 FAILED.
제안 펼치기(상품 × 요건 → 상품 × 규칙 행)는 app.backend 에 있다.

사람 확인은 1번이다 (SIMPLIFICATION v2). 백엔드의 APPROVE + 승인 · 제외 목록을 Agent 의 제안별 approve · reject 와
apply 노드가 쓸 승인(apply_approval)으로 바꾸고 finish 로 검토를 끝낸다.
REQUEST_CHANGES 는 선택 제안만 재작성하고 다시 검토를 기다린다. 직접 편집은 CLI 전용이다.
"""

import logging

from app.backend import proposal_items, proposal_keys, requirements_payload
from app.validation import approval_problem, validate_feedback

log = logging.getLogger(__name__)

# Agent 리포트 상태 → 백엔드 DONE outcome. 백엔드 승인은 apply 를 거쳐 DONE 으로 끝나야 하므로
# 반영 없는 APPROVED · PARTIALLY_APPROVED 는 종료 상태가 아니다 (FAILED 로 보고한다).
OUTCOMES = {"NO_CHANGE": "NO_CHANGE", "NO_ACTION": "NO_CHANGE", "REJECTED": "REJECTED", "DONE": "DONE"}
# Agent 판정 → 백엔드 판정 이름 (docs/BACKEND_API.md 10.2)
IMPACTS = {"change_required": "change_required", "no_change": "keep", "needs_review": "needs_check"}


def review_decision(proposals, decision, payload, comment=None, approval_id=None):
    """백엔드 확인(10.3) → Agent 검토 명령.

    반영으로 고른 행이 하나라도 있는 제안은 approve, 나머지는 reject. APPROVE 면 apply 노드가 쓸 승인
    (approvalId + 반영 키)을 함께 넘긴다. 승인 · 제외 키는 Agent 가 보낸 현재 변경 제안 행과 정확히 맞아야 한다.
    """
    if decision == "REQUEST_CHANGES":
        proposal = next((p for p in proposals if p.id == payload.get("proposalId")), None)
        if proposal is None or proposal.status != "pending":
            raise ValueError("현재 검토할 제안 ID가 아닙니다. 상품 × 요건의 proposalId를 보내세요")
        version = payload.get("baseVersion")
        if type(version) is not int or version < 1:
            raise ValueError("baseVersion은 1 이상의 정수여야 합니다")
        if version != proposal.version:
            raise ValueError(f"오래된 제안 버전입니다: {proposal.id}의 현재 버전은 {proposal.version}")
        # 모델 실행을 제출하기 전에 의견을 검사한다. 400 이면 Spring 이 확인 대기로 복귀할 수 있다.
        validate_feedback(comment or "")
        return {"decisions": [{"proposal_id": proposal.id, "version": version,
                               "action": "request_changes", "comment": comment}],
                "finish": False, "apply_approval": None}
    if decision not in ("APPROVE", "REJECT"):
        raise ValueError("decision 은 APPROVE | REJECT | REQUEST_CHANGES 입니다")
    pending = [p for p in proposals if p.status == "pending"]
    change_keys = [k for p in pending if p.impact == "change_required" and p.changes for k in proposal_keys(p)]
    approved = list(payload.get("approvedProposalKeys") or []) if decision == "APPROVE" else []
    excluded = list(payload.get("excludedProposalKeys") or [])
    if len(approved) != len(set(approved)) or len(excluded) != len(set(excluded)) or set(approved) & set(excluded):
        raise ValueError("승인 · 제외 키가 중복됩니다")
    apply_approval = None
    if decision == "APPROVE":
        if not isinstance(approval_id, int) or approval_id <= 0:
            raise ValueError("APPROVE 에는 approvalId 가 필요합니다")
        if not approved:
            raise ValueError("반영할 제안이 없습니다. 전부 제외하려면 REJECT 를 쓰세요")
        if set(approved) | set(excluded) != set(change_keys):
            raise ValueError("승인 · 제외 키가 현재 변경 제안과 다릅니다: "
                             f"요청 {sorted(set(approved) | set(excluded))}, 현재 {sorted(change_keys)}")
        blocked = [f"{', '.join(k for k in proposal_keys(p) if k in approved)}: {problem}"
                   for p in pending if any(k in approved for k in proposal_keys(p))
                   and (problem := approval_problem(p))]
        if blocked:
            raise ValueError("승인 불가 제안: " + " / ".join(blocked))
        apply_approval = {"approval_id": approval_id, "proposal_keys": approved}
    decisions = [{"proposal_id": p.id, "version": p.version, "comment": comment or "",
                  "action": "approve" if any(k in approved for k in proposal_keys(p)) else "reject"}
                 for p in pending]
    return {"decisions": decisions, "finish": True, "apply_approval": apply_approval}


class SpringBridge:
    def __init__(self, spring):
        self.spring = spring
        self.round = 0
        self.keep_impacts = []
        self.proposals = {}

    def restore(self, state):
        """요청마다 생긴 Bridge 에 체크포인트의 조회 결과를 복원한다. 재작성은 impact 노드를 다시 돌지 않는다."""
        self.keep_impacts = [i for i in state.get("product_impacts", []) if i.impact == "no_change"]
        self.proposals = {p.id: p for p in state.get("proposals", [])}
        self.round = max((p.attempt for p in self.proposals.values()
                          if p.status == "pending" and p.verification == "pending"), default=1)

    # ---------- 노드 ----------

    def node_started(self, node):
        if node == "review":
            return
        payload = {"node": node}
        if node == "verify":
            payload["round"] = self.round
        self.spring.event("STEP", payload)

    def node_updated(self, node, delta):
        if node == "requirement_agent":
            self.spring.put("requirements", requirements_payload(delta["requirements"]))
        elif node == "product_impact_agent":
            self.keep_impacts = [i for i in delta["product_impacts"] if i.impact == "no_change"]
        elif node in ("proposal_agent", "verify"):
            proposals = delta["proposals"]
            if node == "proposal_agent":
                generated = [p for p in proposals if p.id not in self.proposals or
                             (p.version, p.attempt) != (self.proposals[p.id].version, self.proposals[p.id].attempt)]
                self.round = max((p.attempt for p in generated), default=self.round)
            self.proposals = {p.id: p for p in proposals}
            # 유지 판단에는 사용자 재작성이나 자동 보완이 없다. v1/round1 을 유지해 재개 때 회차가 흔들리지 않는다.
            self.spring.put("proposals", proposal_items(proposals, self.keep_impacts, 1))
        elif node == "review":
            self.proposals = {p.id: p for p in delta["proposals"]}
        message = _message(delta)
        if message and node != "review":
            self.spring.event("STEP", {"node": node, "message": message})

    # ---------- 작업 기록 (도구 호출 · 판단). 상태를 바꾸지 않으므로 전송이 실패해도 작업은 계속한다 ----------

    def tool_started(self, node, tool, args):
        self._record("TOOL_START", {"node": node, "tool": tool, "args": args})

    def tool_finished(self, node, tool, *, ok, ms, summary=None, error=None):
        payload = {"node": node, "tool": tool, "ok": ok, "ms": ms}
        if summary:
            payload["summary"] = summary
        if error:
            payload["error"] = error
        self._record("TOOL_END", payload)

    def planned(self, node, requirement_id, lookups, related=()):
        """모델이 고른 조회와 이유. 실행 결과는 이어지는 TOOL_START · TOOL_END.
        규정 · 조항 이름은 화면 표시용으로 다른 규정 목록(related)의 제목 · 목차에서 찾는다. 없으면 null."""
        regulations = {r["regulation_id"]: r for r in related}
        def entry(x):
            regulation = regulations.get(x.regulation_id, {})
            toc = {a["article_id"]: a["title"] for a in regulation.get("articles", [])}
            return {"tool": x.tool, "regulationId": x.regulation_id, "articleId": x.article_id, "query": x.query,
                    "reason": x.reason, "regulationTitle": regulation.get("title") or None, "articleTitle": toc.get(x.article_id)}
        self._record("TOOL_PLAN", {"node": node, "requirementId": requirement_id, "lookups": [entry(x) for x in lookups]})

    def decided(self, node, requirement_id, product_id, model, impact, target_rule_ids, checks):
        """model: 모델의 판단(ProductAssessment) 또는 None. impact · target_rule_ids: 코드 검사 뒤 최종 판단."""
        self._record("DECISION", {
            "node": node, "requirementId": requirement_id, "productId": product_id,
            "model": {"impact": IMPACTS[model.impact], "targetRuleIds": list(model.target_rule_ids),
                      "reason": model.reason, "basisRefs": list(model.basis_refs)} if model else None,
            "final": {"impact": IMPACTS[impact], "targetRuleIds": list(target_rule_ids)},
            "checks": checks,
        })

    def _record(self, type_, payload):
        try:
            self.spring.event(type_, payload)
        except Exception:
            log.warning("작업 기록 전송 실패: %s", type_, exc_info=True)

    # ---------- 멈춤 · 종료 ----------

    def finish(self, out):
        if out.get("__interrupt__"):
            self.spring.event("APPROVAL_REQUIRED", {"step": "REVIEW"})
            return
        result = out["result"]
        self.spring.put("report", report_payload(result))
        if result.status == "FAILED":
            self.spring.event("FAILED", {"reason": result.summary})
        elif result.status in OUTCOMES:
            self.spring.event("DONE", {"outcome": OUTCOMES[result.status]})
        else:
            self.spring.event("FAILED", {"reason": f"종료 상태가 아닙니다: {result.status} ({result.summary})"})

    def fail(self, exc):
        log.exception("Agent 실행 실패")
        try:
            self.spring.event("FAILED", {"reason": f"Agent 실행 실패: {exc}"})
        except Exception:
            log.exception("FAILED 이벤트 전송 실패")


def _message(delta):
    events = delta.get("events") or []
    return events[-1].message if events else None


def report_payload(result):
    data = result.model_dump(mode="json")
    lines = ["# Rule Ripple 상품 변경 제안서", "", f"**{result.status}** · {result.summary}"]
    for p in result.proposals:
        mark = {"approved": "반영", "rejected": "제외"}.get(p.status, p.status)
        lines += ["", f"## {p.product_name} ({p.product_id}) — {mark}", "",
                  f"- 근거: {p.article_ref}", f"- 현재: {p.current}", f"- 제안: {p.recommendation}"]
        lines += [f"- {c.rule_id or '새 규칙'} {c.rule_key}: {c.current_value or '없음'} → {c.proposed_value} ({c.unit})" for c in p.changes]
        lines += [f"- 조치: {a}" for a in p.actions]
    if result.unchanged:
        lines += ["", "## 유지", ""]
        lines += [f"- {i.product_name} ({i.product_id}): {i.reason}" for i in result.unchanged]
    if result.apply_results:
        lines += ["", "## 반영", ""]
        for r in result.apply_results:
            new_rule = r.result.get("newRuleId")
            lines.append(f"- {r.proposal_key}: {r.status}" + (f" → {new_rule}" if new_rule else "")
                         + (f" ({r.error})" if r.error else ""))
    return {"markdown": "\n".join(lines), **data}
