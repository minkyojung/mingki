"""새 규칙: 새로 생긴 조항(제15조의2 고령자 설명 확인)의 의무를 상품에 더한다.

어느 상품에 필요한지는 모델이, 새 규칙의 모양(이름 = 요건 정책 키, 값 true, 단위 bool)은 코드가 정한다.
"""

from app.backend import proposal_rows
from app.data import active_rules
from app.graph import build_graph
from app.runner import AgentRunner
from app.schemas import ProductAssessment, Requirement, RuleChange
from app.tools.local import LocalTools
from app.validation import new_rule_errors, new_rule_key
from tests.test_apply import QueueTools
from tests.test_graph import DsrReasoner
from tests.test_server import HEADERS, in_order, resume_body, started

KEY = "SENIOR_EXPLANATION_REQUIRED"


class ExplanationReasoner(DsrReasoner):
    """제15조의2 하나를 요건으로 내고, P005는 유지 · P009는 확인 필요 · 나머지는 의무 적용으로 판단한다."""

    def requirements(self, diff):
        source = next(c for c in diff["changes"] if c["article_id"] == "15-2")
        return [Requirement(id="REQ-15-2", article_id="15-2", article="제15조의2", article_ref=source["new_uri"],
                            change="added", summary="고령자 설명 확인 의무 신설", target="Senior explanation-required")]

    def product_impacts(self, requirements, products, rules, *args):
        verdicts = {"P005": "no_change", "P009": "needs_review"}
        return [ProductAssessment(requirement_id="REQ-15-2", product_id=p["productId"],
                                  impact=verdicts.get(p["productId"], "change_required"), target_rule_ids=[],
                                  basis_refs=["reg://credit-loan-ops/v4/15-2"], applicability="all",
                                  scope="시행일 이후 체결하는 계약", reason="고령자 설명 확인 의무 신설") for p in products]


def requirement(change="added", target="Senior explanation-required"):
    return Requirement(id="REQ-15-2", article_id="15-2", article="제15조의2", article_ref="reg://credit-loan-ops/v4/15-2",
                       change=change, summary="설명 확인", target=target)


def test_new_rule_key_comes_from_the_requirement_policy_key():
    assert new_rule_key(requirement()) == KEY
    assert new_rule_key(requirement(target="")) == "REQ_15_2"


def test_new_rule_is_only_for_an_added_obligation_and_must_be_new():
    change = RuleChange(rule_key=KEY, proposed_value="true", unit="bool")
    assert new_rule_errors(requirement(), change, []) == []
    assert any("새로 생긴 조항" in e for e in new_rule_errors(requirement(change="modified"), change, []))
    wrong = RuleChange(rule_key=KEY, proposed_value="false", unit="bool")
    assert any("true (bool)" in e for e in new_rule_errors(requirement(), wrong, []))
    existing = [{"ruleId": "R900", "ruleKey": KEY}]
    assert any("R900" in e for e in new_rule_errors(requirement(), change, existing))


def test_added_obligation_becomes_a_new_rule_proposal():
    out = AgentRunner(build_graph(LocalTools(), ExplanationReasoner())).start("T1", "credit-loan-ops", article_ids=["15-2"])
    proposals = {p.product_id: p for p in out["proposals"]}
    p001 = proposals["P001"]
    assert p001.changes == [RuleChange(rule_id=None, rule_key=KEY, current_value=None, proposed_value="true", unit="bool")]
    assert p001.verification == "passed"
    assert "P005" not in proposals  # 유지
    assert proposals["P009"].changes == [] and proposals["P009"].impact == "needs_review"
    row = proposal_rows(p001)[0]
    assert (row["proposalKey"], row["ruleId"], row["impact"], row["currentValue"], row["proposedValue"], row["unit"]) == \
        (f"P001:{KEY}", None, "change_required", None, "true", "bool")


def test_sale_ended_product_is_kept_with_the_sale_ended_article_as_basis():
    """판매가 끝난 상품(P012)은 새 계약이 없다. 모델이 변경 필요라고 해도 유지이고, 근거는 판매 중단 상품 관리 조항."""
    out = AgentRunner(build_graph(LocalTools(), ExplanationReasoner())).start("T1", "credit-loan-ops", article_ids=["15-2"])
    p012 = next(i for i in out["product_impacts"] if i.product_id == "P012")
    assert p012.impact == "no_change" and p012.applicability == "excluded" and "판매가 끝난" in p012.reason
    assert "reg://credit-loan-ops/v4/21" in p012.basis_refs
    assert not [p for p in out["proposals"] if p.product_id == "P012"]


class HedgingReasoner(ExplanationReasoner):
    """청년 상품(P005)을 자료 부족이라며 확인 필요로 내는 모델. 코드가 나이 상한 규칙으로 유지를 확정해야 한다."""

    def product_impacts(self, requirements, products, rules, *args):
        out = super().product_impacts(requirements, products, rules, *args)
        for item in out:
            if item.product_id == "P005":
                item.impact, item.applicability, item.reason = "needs_review", "unknown", "고령자 해당 여부를 확정할 수 없음"
        return out


def test_product_whose_max_age_is_below_the_senior_threshold_is_kept():
    out = AgentRunner(build_graph(LocalTools(), HedgingReasoner())).start("T1", "credit-loan-ops", article_ids=["15-2"])
    p005 = next(i for i in out["product_impacts"] if i.product_id == "P005")
    assert p005.impact == "no_change" and p005.applicability == "excluded"
    assert "R046" in p005.evidence_rule_ids and "reg://credit-loan-ops/v4/15" in p005.basis_refs
    assert "만 60세" in p005.reason
    # 상한이 없는 상품은 그대로 모델 판단 (변경 필요 → 새 규칙)
    assert next(i for i in out["product_impacts"] if i.product_id == "P001").impact == "change_required"


def test_new_rule_goes_through_backend_review_and_apply():
    api, spring, tools = started(tools=QueueTools(), reasoner=ExplanationReasoner())
    rows = {r["proposalKey"]: r for r in spring.puts("proposals")[-1]["items"]}
    assert rows[f"P001:{KEY}"]["ruleId"] is None and rows[f"P001:{KEY}"]["verifyStatus"] == "PASSED"
    spring.calls.clear()
    res = api.post("/resume", json=resume_body("APPROVE", approved=[f"P001:{KEY}"], excluded=[f"P002:{KEY}", f"P003:{KEY}"]),
                   headers=HEADERS)
    assert res.status_code == 202
    assert in_order(spring.kinds(), ["STEP:apply", "STEP:report", "PUT report", "DONE:DONE"])
    assert [r["proposal_key"] for r in tools.requests] == [f"P001:{KEY}"]
    assert f"새 규칙 {KEY}: 없음 → true (bool)" in spring.puts("report")[0]["markdown"]


def test_existing_rules_picked_for_an_added_obligation_become_a_new_rule():
    """실제 실행: 모델이 P010에 추정소득 비율(R073)을 골랐다. 새로 생긴 조항을 따르는 기존 규칙은 있을 수 없으므로
    그 규칙은 빼고, 의무 적용(변경 필요)은 새 규칙으로 간다."""
    class PicksExisting(ExplanationReasoner):
        def product_impacts(self, requirements, products, rules, *args):
            rows = super().product_impacts(requirements, products, rules, *args)
            return [r.model_copy(update={"target_rule_ids": ["R073"], "evidence_rule_ids": ["R073"]}) if r.product_id == "P010" else r
                    for r in rows]
    out = AgentRunner(build_graph(LocalTools(), PicksExisting())).start("T1", "credit-loan-ops", article_ids=["15-2"])
    impact = next(i for i in out["product_impacts"] if i.product_id == "P010")
    assert impact.impact == "change_required" and impact.target_rule_ids == []
    p010 = next(p for p in out["proposals"] if p.product_id == "P010")
    assert [c.rule_key for c in p010.changes] == [KEY] and p010.verification == "passed"


# ---------- 새 규칙 이름 재사용 ----------

APPLIED = {"ruleId": "R900", "productId": "P001", "ruleKey": "SENIOR_DISCLOSURE", "value": "true", "unit": "bool",
           "effectiveFrom": "2026-12-01", "effectiveTo": None, "basisArticles": ["credit-loan-ops/15-2"]}


def test_new_rule_reuses_the_name_of_a_rule_following_the_same_article():
    assert new_rule_key(requirement(), [APPLIED]) == "SENIOR_DISCLOSURE"   # 모델 정책 키보다 기존 이름이 먼저
    other = {**APPLIED, "basisArticles": ["consumer-explanation/6"]}
    assert new_rule_key(requirement(), [other]) == KEY                      # 다른 조항을 따르는 규칙은 재사용하지 않는다


def test_reanalysis_after_apply_reuses_the_name_and_keeps_applied_products():
    """반영 뒤 같은 개정을 다시 분석: 모델이 다른 이름을 지어도 기존 이름을 쓰고, 이미 반영한 상품(P001)은 유지."""
    class Applied(LocalTools):
        def get_product_rules(self, rule_key=None, product_id=None, as_of=None):
            rows = super().get_product_rules(rule_key, product_id) + [APPLIED]   # 12월 1일부터 시작하는 반영 예정 행
            return active_rules(rows, as_of) if as_of else rows
    out = AgentRunner(build_graph(Applied(), ExplanationReasoner())).start("T1", "credit-loan-ops", article_ids=["15-2"])
    impacts = {i.product_id: i for i in out["product_impacts"]}
    assert impacts["P001"].impact == "no_change" and "이미 SENIOR_DISCLOSURE" in impacts["P001"].reason
    p002 = next(p for p in out["proposals"] if p.product_id == "P002")
    assert [c.rule_key for c in p002.changes] == ["SENIOR_DISCLOSURE"] and p002.verification == "passed"
