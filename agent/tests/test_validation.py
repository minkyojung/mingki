"""단위·필수 변경 보존·조건부 예외·선택적 재시도를 회귀 검사한다."""
import copy
import pytest

from app.cli import show_review, show_details, review_command
from app.graph import build_graph
from app.runner import AgentRunner
from app.schemas import ProductAssessment, RevisionCheck, RuleChange, ScopeCondition, Requirement
from app.validation import change_problem, validate_proposal, condition_errors, expected_value
from app.nodes.impact import make_product_impact_agent
from app.runner import DecisionError
from app.tools.local import LocalTools
from test_graph import DsrReasoner, start, decision


def test_invalid_ratio_retries_only_failed_item_without_extra_version():
    class WrongFirst(DsrReasoner):
        def draft_product(self, context):
            draft = super().draft_product(context)
            if context["product"]["productId"] == "P001" and not context["validation_errors"]:
                draft.changes[0].proposed_value = "35"
            return draft
    _, out, reasoner, _ = start(WrongFirst())
    one, two = out["proposals"]
    assert (one.version, one.attempt, one.verification) == (1, 2, "passed")
    assert (two.version, two.attempt, two.verification) == (1, 1, "passed")
    assert len(reasoner.contexts) == 3
    assert reasoner.contexts[-1]["validation_errors"]
    assert one.changes[0].proposed_value == "0.35"


def test_invalid_ratio_stops_after_two_attempts_and_blocks_approval():
    class AlwaysWrong(DsrReasoner):
        def draft_product(self, context):
            draft = super().draft_product(context)
            draft.changes[0].proposed_value = "35"
            return draft
    runner, out, _, _ = start(AlwaysWrong())
    assert all(p.attempt == 2 and p.verification == "failed" for p in out["proposals"])
    with pytest.raises(DecisionError):
        runner.resume("T1", {"decisions": [decision(out["proposals"][0])]})


def test_revision_cannot_drop_mandatory_changes_and_preserves_other_approval():
    class Drop(DsrReasoner):
        def draft_product(self, context):
            draft = super().draft_product(context)
            if context["comment"]:
                draft.changes = []
            return draft
    runner, out, _, _ = start(Drop())
    before = out["proposals"][0].model_copy(deep=True)
    out = runner.resume("T1", {"decisions": [decision(out["proposals"][1])]})
    approved = out["proposals"][1].model_dump()
    out = runner.resume("T1", {"decisions": [decision(before, "request_changes", comment="화면 안내 문구 추가")]})
    after = out["proposals"][0]
    assert (after.version, after.attempt, after.verification) == (2, 2, "failed")
    assert after.changes == before.changes and after.actions == before.actions
    assert after.changed_fields == []
    assert out["proposals"][1].model_dump() == approved


def test_different_words_without_requested_meaning_are_rejected():
    class Ignored(DsrReasoner):
        def check_revision(self, context):
            return RevisionCheck(feedback_addressed=False, mandatory_preserved=True, reason="실제 안내 문구가 없고 작성 계획만 있음")
    runner, out, _, _ = start(Ignored())
    before = out["proposals"][0]
    out = runner.resume("T1", {"decisions": [decision(before, "request_changes", comment="고객 안내 문구 한 문장 작성")]})
    p = out["proposals"][0]
    assert p.verification == "failed" and "실제 안내 문구" in p.generation_error
    assert p.recommendation == before.recommendation


@pytest.mark.parametrize("comment", ["다른 제안사항 줘봐", "새로운 제안", "새로운 아이디어"])
def test_vague_request_is_rejected_before_model_call_or_checkpoint_change(comment):
    runner, out, reasoner, _ = start()
    count = len(reasoner.contexts)
    with pytest.raises(DecisionError, match="구체적"):
        runner.resume("T1", {"decisions": [decision(out["proposals"][0], "request_changes", comment=comment)]})
    assert len(reasoner.contexts) == count
    assert runner.graph.get_state(runner.config("T1")).values["proposals"][0].version == 1


def test_percent_values_are_checked_in_original_unit():
    runner, out, _, _ = start()
    state = copy.deepcopy(runner.graph.get_state(runner.config("T1")).values)
    row = next(r for r in state["product_rules"] if r["ruleId"] == "R001")
    row.update(unit="percent", value="40")
    proposal = out["proposals"][0].model_copy(deep=True)
    proposal.changes[0] = RuleChange(rule_id="R001", rule_key="DSR_LIMIT", current_value="40", proposed_value="35", unit="percent")
    assert validate_proposal(state, proposal) == []
    proposal.changes[0].proposed_value = "0.35"
    assert any("35" in error for error in validate_proposal(state, proposal))


def test_multiple_justified_targets_are_supported_instead_of_arbitrary_one_rule_limit():
    runner, out, _, _ = start()
    state = copy.deepcopy(runner.graph.get_state(runner.config("T1")).values)
    row = copy.deepcopy(next(r for r in state["product_rules"] if r["ruleId"] == "R001"))
    row["ruleId"] = "R_EXTRA"
    state["product_rules"].append(row)
    proposal = out["proposals"][0].model_copy(deep=True)
    proposal.target_rule_ids.append("R_EXTRA")
    proposal.changes.append(proposal.changes[0].model_copy(update={"rule_id": "R_EXTRA"}))
    assert validate_proposal(state, proposal) == []


def test_wrong_effective_date_or_current_value_is_rejected():
    runner, out, _, _ = start()
    state = runner.graph.get_state(runner.config("T1")).values
    proposal = out["proposals"][0].model_copy(deep=True)
    proposal.effective_date = None
    proposal.changes[0].current_value = "0.99"
    errors = validate_proposal(state, proposal)
    assert any("시행일" in e for e in errors) and any("현재 값" in e for e in errors)


def impact_state(tools):
    return {"requirements": [Requirement(id="REQ-8", article_id="8", article="제8조", article_ref="reg://credit-loan-ops/v4/8",
        change="modified", summary="DSR 상한 변경", target="DSR_LIMIT", new_text="DSR은 35%를 초과할 수 없다.")],
        "snapshot_date": "2026-11-30", "regulation_context": tools.get_regulation_context("credit-loan-ops", "v4")["articles"]}


class SmallLoanExempt(DsrReasoner):
    """P006(소액대출)을 소액 예외로 유지 판단한다."""
    def product_impacts(self, *args):
        rows = super().product_impacts(*args)
        return [r.model_copy(update={"impact": "no_change", "applicability": "excluded", "target_rule_ids": [],
                    "evidence_rule_ids": ["R054"], "document_refs": ["product-doc://P006"]}) if r.product_id == "P006" else r for r in rows]


def test_small_loan_exemption_keeps_product_without_changeable_rule():
    """P006에는 이번 개정으로 바뀔 DSR 규칙이 없다. 차주별 소액 해당 여부는 심사 절차의 일이라 상품 규칙은 유지다."""
    tools = LocalTools()
    out = make_product_impact_agent(tools, SmallLoanExempt())(impact_state(tools))
    item = next(i for i in out["product_impacts"] if i.product_id == "P006")
    assert item.impact == "no_change"


def test_numeric_small_loan_exemption_is_not_a_blanket_exemption_when_a_rule_could_change():
    """같은 소액 예외라도 상품에 바뀔 DSR 규칙이 있으면 차주 자료 없이 상품 전체를 유지로 확정하지 않는다."""
    class WithDsrRule(LocalTools):
        def get_product_rules(self, *args, **kwargs):
            return super().get_product_rules(*args, **kwargs) + [
                {"ruleId": "R900", "productId": "P006", "ruleKey": "DSR_LIMIT", "value": "0.40", "unit": "ratio",
                 "effectiveFrom": "2024-01-01", "effectiveTo": None}]
    tools = WithDsrRule()
    out = make_product_impact_agent(tools, SmallLoanExempt())(impact_state(tools))
    item = next(i for i in out["product_impacts"] if i.product_id == "P006")
    assert item.impact == "needs_review" and item.applicability == "unknown"
    assert "차주별" in item.reason


def test_condition_citation_must_be_real_and_about_same_transaction():
    condition = ScopeCondition(text="잔액을 늘리지 않는 대환은 예외", source_ref="reg://example/12", quote="잔액을 늘리지 아니하는 연장은 허용한다")
    from app.schemas import ProductAssessment
    row = ProductAssessment(requirement_id="r", product_id="p", reason="조건", impact="change_required",
                            applicability="conditional", conditions=[condition])
    assert any("대환" in e for e in condition_errors(row, {condition.source_ref: condition.quote}))
    row.conditions[0].source_ref = "reg://unobserved"
    assert any("인용문" in e for e in condition_errors(row, {}))


def test_conditional_keep_is_exposed_for_review_not_hidden_as_unchanged():
    class Conditional(DsrReasoner):
        def product_impacts(self, *args):
            return [r.model_copy(update={"impact": "no_change", "applicability": "conditional"}) for r in super().product_impacts(*args)]
    _, out, _, _ = start(Conditional())
    assert len(out["proposals"]) == 3
    assert all(p.impact == "needs_review" for p in out["proposals"])


def test_cli_summary_is_short_and_does_not_repeat_full_proposals(capsys):
    _, out, _, _ = start()
    value = out["__interrupt__"][0].value
    show_review(value)
    text = capsys.readouterr().out
    assert len(text.splitlines()) <= 8
    assert "현재 → 제안" in text and "v 3" in text
    assert out["proposals"][0].recommendation not in text
    show_details(value["proposals"][0])
    assert "0.40 → 0.35" in capsys.readouterr().out


def test_direct_edit_cannot_bypass_brevity_constraints():
    runner, out, _, _ = start()
    with pytest.raises(DecisionError, match="직접 수정 검증"):
        runner.resume("T1", {"decisions": [decision(out["proposals"][0], "edit", edits={"actions": ["a", "b", "c", "d"]})]})


def test_wrong_uri_is_corrected_only_when_quote_uniquely_matches_observed_source():
    from app.validation import ground_conditions
    from app.schemas import ProductAssessment
    row = ProductAssessment(requirement_id="r", product_id="p", impact="needs_review", reason="조건",
        conditions=[ScopeCondition(text="갱신 조건", source_ref="reg://wrong", quote="갱신 시 한도를 늘리지 않는 경우에는 예외다")])
    sources = {"reg://correct": "갱신 시 한도를 늘리지 않는 경우에는 예외다"}
    ground_conditions(row, sources)
    assert row.conditions[0].source_ref == "reg://correct"
    row.conditions[0].source_ref = "reg://wrong"
    ground_conditions(row, {**sources, "reg://also": sources["reg://correct"]})
    assert row.conditions[0].source_ref == "reg://wrong"


def test_effective_date_comes_from_diff_not_model_guess():
    from datetime import date
    class WrongDate(DsrReasoner):
        def requirements(self, diff):
            return [r.model_copy(update={"effective_date": date(2030, 1, 1)}) for r in super().requirements(diff)]
    _, out, _, _ = start(WrongDate())
    assert all(p.effective_date == date(2026, 12, 1) for p in out["proposals"])


def test_unverified_condition_is_not_repeated_as_a_confirmed_value_change():
    class WrongCitation(DsrReasoner):
        def product_impacts(self, *args):
            rows = super().product_impacts(*args)
            rows[0] = rows[0].model_copy(update={"applicability": "conditional", "conditions": [
                ScopeCondition(text="증액 대환만 적용", source_ref="reg://missing", quote="실제 원문에 없는 가상의 예외 조건입니다")]})
            return rows
    _, out, _, _ = start(WrongCitation())
    item = out["proposals"][0]
    assert item.impact == "needs_review" and item.applicability == "unknown"
    assert not item.target_rule_ids and not item.changes and not item.conditions
    assert "확인되지 않아" in item.reason


def test_approved_rows_are_hidden_but_original_selection_numbers_work(capsys):
    runner, out, _, _ = start()
    out = runner.resume("T1", {"decisions": [decision(out["proposals"][0])]})
    value = out["__interrupt__"][0].value
    show_review(value)
    text = capsys.readouterr().out
    assert "\n1 |" not in text and "\n2 |" in text
    command = review_command("p 2", value["proposals"])
    assert command["decisions"][0]["proposal_id"] == out["proposals"][1].id


# ---------- 고른 규칙이 말이 되는지 (2026-10-21 실제 실행의 P004 · P010 · P012) ----------

DSR_REQ = Requirement(id="REQ-8", article_id="8", article="제8조", article_ref="reg://credit-loan-ops/v4/8",
                      change="modified", summary="DSR 한도", target="DSR",
                      old_text="차주의 총부채원리금상환비율(DSR)은 40%를 초과할 수 없다.",
                      new_text="차주의 총부채원리금상환비율(DSR)은 35%를 초과할 수 없다.")
ON_SALE = {"productId": "P000", "status": "ON_SALE"}


def rule(rule_id, key, value, unit="ratio"):
    return {"ruleId": rule_id, "ruleKey": key, "value": value, "unit": unit}


@pytest.mark.parametrize("row", [rule("R001", "DSR_LIMIT", "0.40"), rule("R056", "MAX_DSR_RATIO", "0.4"),
                                 rule("R044", "DSR_LIMIT", "40", "percent"), rule("RX", "DSR_LIMIT", "0.38")])
def test_rule_over_new_limit_can_change(row):
    assert change_problem(DSR_REQ, row, ON_SALE) is None


def test_rule_already_within_new_limit_is_kept():
    verdict, note = change_problem(DSR_REQ, rule("R037", "DSR_LIMIT", "0.30"), ON_SALE)
    assert verdict == "no_change" and "30%" in note
    assert change_problem(DSR_REQ, rule("R015", "DSR_LIMIT", "0.35"), ON_SALE)[0] == "no_change"


def test_rule_above_old_limit_is_not_the_same_limit():
    verdict, note = change_problem(DSR_REQ, rule("R073", "ESTIMATED_INCOME_RATIO", "0.85"), ON_SALE)
    assert verdict == "needs_review" and "R073" in note


def test_sale_ended_product_is_kept():
    verdict, note = change_problem(DSR_REQ, rule("R079", "DSR_LIMIT", "0.40"), {"status": "SALE_ENDED"})
    assert verdict == "no_change" and "판매가 끝난" in note


def test_senior_threshold_and_age_exclusion_come_from_rules_and_regulation_text():
    from app.validation import excluded_by_age, senior_threshold, sale_ended_basis
    context = [{"uri": "reg://credit-loan-ops/v4/15", "text": '이 기준에서 "고령자"란 신청일 현재 만 60세 이상인 자를 말한다.'},
               {"uri": "reg://credit-loan-ops/v4/21", "text": "판매가 중단된 상품은 신규로 취급하지 아니하며, 기존 계약의 관리만 한다."}]
    added = Requirement(id="REQ-15-2", article_id="15-2", article="제15조의2", article_ref="reg://credit-loan-ops/v4/15-2",
                        change="added", summary="고령자 설명 확인 의무 신설", target="Senior explanation-required",
                        new_text="은행은 고령자와 대출 계약을 체결하기 전에 설명하고 이해했는지 확인해야 한다.")
    youth = [rule("R045", "ELIGIBLE_MIN_AGE", "19", "age"), rule("R046", "ELIGIBLE_MAX_AGE", "34", "age")]
    senior = [rule("R010", "ELIGIBLE_MIN_AGE", "65", "age")]

    assert senior_threshold(context) == (60, "reg://credit-loan-ops/v4/15")
    age_rule, uri, note = excluded_by_age(added, youth, context)
    assert age_rule["ruleId"] == "R046" and uri == "reg://credit-loan-ops/v4/15" and "만 60세" in note
    assert excluded_by_age(added, senior, context) is None               # 상한이 없으면 고령자와 계약할 수 있다
    assert excluded_by_age(DSR_REQ, youth, context) is None              # 새 조항이 아니면 보지 않는다
    assert excluded_by_age(added, youth, context[1:]) is None            # 정의 조항이 없으면 확정하지 않는다
    assert sale_ended_basis(context) == ["reg://credit-loan-ops/v4/21"]


SENIOR_DEF_REQ = Requirement(id="REQ-15", article_id="15", article="제15조", article_ref="reg://credit-loan-ops/v4/15",
                             change="modified", summary="고령자 기준 65세 → 60세", target="Senior age threshold",
                             old_text='이 기준에서 "고령자"란 신청일 현재 만 65세 이상인 자를 말한다.',
                             new_text='이 기준에서 "고령자"란 신청일 현재 만 60세 이상인 자를 말한다.')


def test_changed_definition_only_touches_products_whose_rules_follow_it():
    from app.validation import definition_without_rule
    def based(row, *articles):
        return {**row, "basisArticles": list(articles)}
    follows = [based(rule("R010", "ELIGIBLE_MIN_AGE", "65", "age"), "credit-loan-ops/15")]
    other = [based(rule("R005", "SENIOR_RATE_DISCOUNT", "0.003", "ratio"), "credit-loan-ops/16")]
    unknown_age = other + [based(rule("R999", "SOME_AGE", "65", "age"))]
    unknown_ratio = other + [based(rule("R066", "BUSINESS_DSCR_MIN", "1.25", "ratio"))]

    assert definition_without_rule(SENIOR_DEF_REQ, follows) is None               # 정의를 따르는 규칙이 있다 → 모델 판단
    assert "credit-loan-ops/15" in definition_without_rule(SENIOR_DEF_REQ, other)  # 없으면 유지
    assert definition_without_rule(SENIOR_DEF_REQ, unknown_age) is None           # 근거 없는 나이 규칙은 따를 수도 있다
    assert definition_without_rule(SENIOR_DEF_REQ, unknown_ratio)                 # 비율 규칙은 나이 정의와 무관
    assert definition_without_rule(DSR_REQ, other) is None                         # 한도 변경은 정의가 아니다


def test_products_without_a_rule_on_the_changed_definition_are_kept_in_the_graph():
    class HedgesOnDefinition(DsrReasoner):
        def requirements(self, diff):
            return [SENIOR_DEF_REQ.model_copy(update={"article_ref": next(c for c in diff["changes"] if c["article_id"] == "15")["new_uri"]})]

        def product_impacts(self, requirements, products, rules, *args):
            out = []
            for p in products:
                if p["productId"] == "P002":
                    out.append(ProductAssessment(requirement_id="REQ-15", product_id="P002", impact="change_required",
                                                 target_rule_ids=["R010"], evidence_rule_ids=["R010"], applicability="all",
                                                 basis_refs=["reg://credit-loan-ops/v4/15"], scope="시행일 이후 신청", reason="가입 나이 65 → 60"))
                else:
                    out.append(ProductAssessment(requirement_id="REQ-15", product_id=p["productId"], impact="needs_review",
                                                 applicability="unknown", basis_refs=["reg://credit-loan-ops/v4/15"],
                                                 scope="확인", reason="나이 상한이 없어 고령자와 계약할 수 있으니 반영 필요"))
            return out
    out = AgentRunner(build_graph(LocalTools(), HedgesOnDefinition())).start("T1", "credit-loan-ops", article_ids=["15"])
    impacts = {i.product_id: i for i in out["product_impacts"]}
    assert impacts["P002"].impact == "change_required" and impacts["P002"].target_rule_ids == ["R010"]
    for pid in ("P001", "P005", "P007", "P011"):
        assert impacts[pid].impact == "no_change" and impacts[pid].applicability == "excluded", pid
        assert "reg://credit-loan-ops/v4/15" in impacts[pid].basis_refs and "정의를 따르는 규칙" in impacts[pid].reason
    assert {c.rule_id for p in out["proposals"] for c in p.changes} == {"R010"}


def test_non_limit_requirement_is_not_judged_by_numbers():
    age = DSR_REQ.model_copy(update={"old_text": "고령자란 신청일 현재 만 65세 이상인 자를 말한다.",
                                     "new_text": "고령자란 신청일 현재 만 60세 이상인 자를 말한다."})
    assert change_problem(age, rule("R010", "ELIGIBLE_MIN_AGE", "65", "age"), ON_SALE) is None


def test_wrong_targets_from_real_run_are_filtered_before_proposal():
    """실제 실행에서 모델이 고른 R037(이미 30%) · R073(추정소득 비율) · R079(판매 중단)를 영향 판단에서 거른다."""
    class RealMistakes(DsrReasoner):
        def product_impacts(self, requirements, products, rules, *args):
            picks = {"P001": "R001", "P004": "R037", "P010": "R073", "P012": "R079"}
            return [ProductAssessment(requirement_id="REQ-8", product_id=pid, target_rule_ids=[rid], evidence_rule_ids=[rid],
                                      scope="시행일 이후 신규 신청", applicability="all", impact="change_required",
                                      reason="DSR 한도 변경") for pid, rid in picks.items()]
    out = AgentRunner(build_graph(LocalTools(), RealMistakes())).start("T1", "credit-loan-ops", article_ids=["8"])
    impacts = {i.product_id: i for i in out["product_impacts"]}
    assert impacts["P001"].impact == "change_required" and impacts["P001"].target_rule_ids == ["R001"]
    assert impacts["P004"].impact == "no_change" and "R037" in impacts["P004"].evidence_rule_ids
    assert impacts["P010"].impact == "needs_review"
    # 판매가 끝난 상품은 정답지대로 유지. 고른 규칙은 근거로 남고 판매 중단 조항이 붙는다
    assert impacts["P012"].impact == "no_change" and "R079" in impacts["P012"].evidence_rule_ids
    assert "reg://credit-loan-ops/v4/21" in impacts["P012"].basis_refs
    assert {c.rule_id for p in out["proposals"] for c in p.changes} == {"R001"}


@pytest.mark.parametrize("row", [rule("R060", "DSR_LINE_TERM_MONTHS", "60", "months"), rule("R055", "COOLING_OFF_DAYS", "14", "days")])
def test_rule_whose_new_value_cannot_be_derived_goes_to_review(row):
    verdict, note = change_problem(DSR_REQ, row, ON_SALE)
    assert verdict == "needs_review" and row["ruleId"] in note


def test_one_underivable_target_does_not_block_the_others():
    """실제 실행 3회차: P007이 R056(비율)과 함께 R060(개월)을, P006이 R055(일)를 골라 근거 검사에 실패하고 확인 전체가 막혔다."""
    class ExtraTargets(DsrReasoner):
        def product_impacts(self, requirements, products, rules, *args):
            picks = {"P007": ["R060", "R056"], "P006": ["R055"]}
            return [ProductAssessment(requirement_id="REQ-8", product_id=pid, target_rule_ids=rids, evidence_rule_ids=rids,
                                      scope="시행일 이후 신규 신청", applicability="all", impact="change_required",
                                      reason="DSR 한도 변경") for pid, rids in picks.items()]
    out = AgentRunner(build_graph(LocalTools(), ExtraTargets())).start("T1", "credit-loan-ops", article_ids=["8"])
    impacts = {i.product_id: i for i in out["product_impacts"]}
    assert impacts["P007"].impact == "change_required" and impacts["P007"].target_rule_ids == ["R056"]
    assert impacts["P006"].impact == "needs_review" and impacts["P006"].target_rule_ids == []
    proposals = {p.product_id: p for p in out["proposals"]}
    assert proposals["P007"].verification == "passed" and [c.rule_id for c in proposals["P007"].changes] == ["R056"]
    assert proposals["P006"].verification == "passed"


# ---------- 근거 조항 (basis_refs) ----------

def test_keep_supported_only_by_regulation_article_is_kept():
    class KeepByArticle(DsrReasoner):
        def product_impacts(self, *args):
            return [r.model_copy(update={"impact": "no_change", "applicability": "all", "target_rule_ids": [],
                        "evidence_rule_ids": [], "document_refs": [], "basis_refs": ["reg://credit-loan-ops/v4/8"]})
                    if r.product_id == "P003" else r for r in super().product_impacts(*args)]
    tools = LocalTools()
    out = make_product_impact_agent(tools, KeepByArticle())(impact_state(tools))
    item = next(i for i in out["product_impacts"] if i.product_id == "P003")
    assert item.impact == "no_change" and item.basis_refs == ["reg://credit-loan-ops/v4/8"]


def test_basis_refs_reach_backend_rows_and_only_known_articles_are_allowed():
    from app.backend import keep_row
    from app.llm import output_schema
    from app.reasoning import ProductAssessmentsOutput
    from app.schemas import ProductImpact
    impact = ProductImpact(id="REQ-8:P009", requirement_id="REQ-8", product_id="P009", product_name="사장님 신용대출",
                           impact="no_change", reason="사업자대출은 적용 대상 아님", current="-",
                           basis_refs=["reg://dsr-management/v2/3"])
    assert keep_row(impact, 1)["basis"] == "reg://dsr-management/v2/3"
    payload = {"rules": [{"ruleId": "R001"}], "documents": [],
               "regulation_context": [{"uri": "reg://dsr-management/v2/3"}, {"uri": "reg://credit-loan-ops/v4/8"}]}
    schema = output_schema(ProductAssessmentsOutput, payload)
    assert schema["$defs"]["ProductAssessment"]["properties"]["basis_refs"]["items"]["enum"] == \
        ["reg://credit-loan-ops/v4/8", "reg://dsr-management/v2/3"]


def test_basis_text_fits_backend_column():
    from app.backend import BASIS_LIMIT, basis_text
    refs = [f"reg://dsr-management/v2/{n}" for n in range(1, 20)]
    text = basis_text(["reg://credit-loan-ops/v4/8", *refs])
    assert len(text) <= BASIS_LIMIT and text.startswith("reg://credit-loan-ops/v4/8, reg://dsr-management/v2/1")
    assert basis_text([]) is None



# ---------- 규칙의 근거 조항 (basisArticles) ----------

AGE_REQ = Requirement(id="REQ-15", article_id="15", article="제15조", article_ref="reg://credit-loan-ops/v4/15",
                      change="modified", summary="고령자 기준", target="SENIOR_AGE",
                      old_text="고령자란 신청일 현재 만 65세 이상인 자를 말한다.",
                      new_text="고령자란 신청일 현재 만 60세 이상인 자를 말한다.")


def test_rule_following_another_article_is_not_a_target():
    """상담 녹음 나이(고령 고객 보호 규정)는 숫자 65와 단위가 같아도 운영기준 제15조 개정과 무관하다."""
    recording = {**rule("R026", "SENIOR_RECORDING_AGE", "65", "age"), "basisArticles": ["senior-protection/5", "senior-protection/2"]}
    verdict, note = change_problem(AGE_REQ, recording, ON_SALE)
    assert verdict == "needs_review" and "senior-protection/5" in note
    eligible = {**rule("R010", "ELIGIBLE_MIN_AGE", "65", "age"), "basisArticles": ["credit-loan-ops/15"]}
    assert change_problem(AGE_REQ, eligible, ON_SALE) is None
    assert change_problem(AGE_REQ, rule("RX", "SENIOR_RECORDING_AGE", "65", "age"), ON_SALE) is None  # 근거 조항이 없으면 기존 판단


def test_senior_age_traps_are_filtered_by_rule_basis():
    """고령자 시나리오 실제 실행: 모델이 P001 · P002의 상담 녹음 나이(R026 · R029)와 보호자 연락 나이(R030)를 함께 골랐다."""
    class SeniorMistakes(DsrReasoner):
        def requirements(self, diff):
            source = next(c for c in diff["changes"] if c["article_id"] == "15")
            return [AGE_REQ.model_copy(update={"article_ref": source["new_uri"]})]

        def product_impacts(self, requirements, products, rules, *args):
            picks = {"P001": ["R026"], "P002": ["R010", "R029", "R030"]}
            return [ProductAssessment(requirement_id="REQ-15", product_id=pid, target_rule_ids=rids, evidence_rule_ids=rids,
                                      scope="시행일 이후 신규 신청", applicability="all", impact="change_required",
                                      reason="고령자 기준 변경") for pid, rids in picks.items()]
    out = AgentRunner(build_graph(LocalTools(), SeniorMistakes())).start("T1", "credit-loan-ops", article_ids=["15"])
    impacts = {i.product_id: i for i in out["product_impacts"]}
    assert impacts["P002"].target_rule_ids == ["R010"] and impacts["P002"].impact == "change_required"
    # 모델은 P001을 바꿔야 한다고 봤는데 고른 규칙(R026)은 다른 조항을 따른다. P001에는 제15조를 따르는 규칙이 없으므로
    # 정답지대로 유지. 고른 규칙은 근거로 남고 정의 조항이 붙는다
    assert impacts["P001"].impact == "no_change" and impacts["P001"].target_rule_ids == []
    assert "R026" in impacts["P001"].evidence_rule_ids and "reg://credit-loan-ops/v4/15" in impacts["P001"].basis_refs
