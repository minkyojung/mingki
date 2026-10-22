"""조회 근거와 필수 변경을 검증한다. 의미 판단을 정답지로 대신하지 않는다."""

import re
from decimal import Decimal, InvalidOperation


def normalize(value):
    return re.sub(r"\s+", "", value).casefold()


def same_value(left, right):
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return left == right


def _single_percent(text):
    """원문에 비율 수치가 하나만 있으면 그 값(% 단위). 없거나 여러 개면 None."""
    found = re.findall(r"(\d+(?:\.\d+)?)\s*%(?!p)", text or "")
    found += re.findall(r"100\s*분의\s*(\d+(?:\.\d+)?)", text or "")
    numbers = {Decimal(v) for v in found}
    return next(iter(numbers)) if len(numbers) == 1 else None


def expected_value(requirement, unit):
    """단일 수치가 명시된 개정 원문만 변환한다. 모호한 계산식은 추측하지 않는다."""
    text = requirement.new_text or ""
    if unit in ("ratio", "percent"):
        value = _single_percent(text)
        if value is not None:
            return str(value / 100 if unit == "ratio" else value)
    if unit == "age":
        numbers = set(re.findall(r"만\s*(\d+)\s*세", text))
        if len(numbers) == 1:
            return numbers.pop()
    return None


_UPPER_LIMIT = re.compile(r"초과할\s*수\s*없|이하|이내")
_REG_URI = re.compile(r"reg://([a-z0-9-]+)/v\d+/([0-9a-z-]+)")


def article_key(uri):
    """조항 uri를 규칙의 근거 조항 표기로: reg://credit-loan-ops/v4/15 → credit-loan-ops/15. 형식이 다르면 None."""
    match = _REG_URI.fullmatch(uri or "")
    return f"{match.group(1)}/{match.group(2)}" if match else None


def change_problem(requirement, rule, product):
    """모델이 바꿀 대상으로 고른 규칙이 말이 되는지 본다. 문제가 없으면 None, 있으면 (판정, 이유).

    규정 원문 · 상품 · 규칙 조회 결과만 쓴다. 판단을 대신하지 않고 위험한 변경만 막는다:
    바꿀 필요가 없다는 것이 확실하면 no_change, 아니면 needs_review로 사람에게 넘긴다.
    """
    if product.get("status") == "SALE_ENDED":
        return "no_change", f"{rule['ruleId']}: 판매가 끝난 상품이라 새 계약이 없어 기준을 바꾸지 않음 (기간 연장은 계약 당시 기준)"
    basis, changed = rule.get("basisArticles") or [], article_key(requirement.article_ref)
    if basis and changed and changed not in basis:  # 같은 숫자 · 같은 단어여도 다른 조항을 따르는 규칙은 이 개정과 무관하다
        return "needs_review", f"{rule['ruleId']} {rule['ruleKey']}: {', '.join(basis)}를 따르는 규칙이라 이번 개정({changed})과 무관해 확인 필요"
    if expected_value(requirement, rule["unit"]) is None:  # 예: 35% 개정으로 60개월 · 15일 규칙의 새 값은 정할 수 없다
        return "needs_review", f"{rule['ruleId']} {rule['ruleKey']}: 개정 원문으로 이 규칙({rule['unit']})의 새 값을 정할 수 없어 확인 필요"
    old, new = _single_percent(requirement.old_text), _single_percent(requirement.new_text)
    if rule["unit"] not in ("ratio", "percent") or old is None or new is None or not _UPPER_LIMIT.search(requirement.new_text or ""):
        return None
    try:
        current = Decimal(rule["value"]) * (100 if rule["unit"] == "ratio" else 1)
    except InvalidOperation:
        return None
    if current <= new:
        return "no_change", f"{rule['ruleId']}: 지금 {current.normalize():f}%로 이미 개정 한도 {new:f}% 이내"
    if current > old:
        return "needs_review", (f"{rule['ruleId']} {rule['ruleKey']}: 지금 {current.normalize():f}%로 개정 전 한도 {old:f}%보다 커서 "
                                "같은 한도 규칙인지 확인 필요")
    return None


def changeable_rules(requirement, rules, product):
    """이번 개정으로 값을 바꿀 수 있는 규칙. 하나도 없으면 상품 규칙은 바꿀 것이 없다 (조건부 예외여도 유지)."""
    return [rule for rule in rules if change_problem(requirement, rule, product) is None]


def new_rule_key(requirement, catalog=()):
    """새 규칙 이름. 같은 조항을 따르는 예/아니오 규칙이 이미 있으면(반영 예정 포함) 그 이름을 다시 쓴다.
    없을 때만 요건의 정책 키(target)를 규칙 키 형식(대문자 · 밑줄, 50자 이내)으로. 모델이 매번 다른 이름을 지어도
    같은 의무가 다른 이름의 규칙으로 또 생기지 않는다."""
    article = article_key(requirement.article_ref)
    reused = sorted({r["ruleKey"] for r in catalog if r.get("unit") == "bool" and article in (r.get("basisArticles") or [])})
    if article and reused:
        return reused[0]
    key = re.sub(r"[^A-Z0-9]+", "_", (requirement.target or "").upper()).strip("_")[:50].strip("_")
    return key or "REQ_" + re.sub(r"[^A-Z0-9]+", "_", requirement.article_id.upper())


def new_rule_change(requirement, catalog=()):
    """새로 생긴 조항의 의무를 상품에 더하는 새 규칙. 모양은 코드가 정한다: 이름(new_rule_key), 값 true, 단위 bool."""
    return {"rule_id": None, "rule_key": new_rule_key(requirement, catalog), "current_value": None,
            "proposed_value": "true", "unit": "bool"}


def new_rule_errors(requirement, change, rules, catalog=()):
    """새 규칙 변경 검사. rules: 그 상품의 끝나지 않은 규칙(반영 예정 포함), catalog: 전체 상품의 끝나지 않은 규칙."""
    errors = []
    if requirement.change != "added":
        errors.append("새 규칙은 새로 생긴 조항의 의무에만 만듭니다")
    expected = new_rule_change(requirement, catalog)
    if (change.rule_key, change.proposed_value, change.unit) != (expected["rule_key"], expected["proposed_value"], expected["unit"]):
        errors.append(f"새 규칙은 {expected['rule_key']} = true (bool)이어야 합니다")
    same = [r["ruleId"] for r in rules if r["ruleKey"] == change.rule_key]
    if same:
        errors.append(f"{change.rule_key}: 이미 규칙 {', '.join(same)}가 있어(반영 예정 포함) 새 규칙이 아닙니다")
    return errors


_SENIOR_DEF = re.compile(r"고령자[\"”']?\s*(?:란|이란|라\s*함은)[^\n]*?만\s*(\d+)\s*세\s*이상")
_SALE_ENDED_ARTICLE = re.compile(r"판매가?\s*중단된\s*상품")


def senior_threshold(context):
    """규정 컨텍스트의 고령자 정의(만 N세 이상)와 그 조항 uri. 정의 조항이 없으면 None."""
    for article in context:
        match = _SENIOR_DEF.search(article.get("text") or "")
        if match:
            return int(match.group(1)), article["uri"]
    return None


def excluded_by_age(requirement, rules, context):
    """새로 생긴 고령자 의무인데 신청 나이 상한이 고령자 기준보다 낮은 상품. (상한 규칙, 정의 조항 uri, 이유) 또는 None.

    그 상품은 고령자와 계약하지 않으므로 의무를 적용할 거래가 없다. 상품 규칙과 규정 원문만 쓴다.
    """
    text = f"{requirement.new_text or ''} {requirement.summary or ''}"
    if requirement.change != "added" or "고령자" not in text:
        return None
    found = senior_threshold(context)
    if not found:
        return None
    threshold, uri = found
    for rule in rules:
        if rule.get("unit") != "age" or "MAX_AGE" not in (rule.get("ruleKey") or ""):
            continue
        try:
            limit = int(Decimal(rule["value"]))
        except InvalidOperation:
            continue
        if limit < threshold:
            return rule, uri, (f"{rule['ruleId']} {rule['ruleKey']}={limit}: 신청 나이 상한이 고령자 기준(만 {threshold}세)보다 "
                               "낮아 고령자와 계약하지 않음")
    return None


_DEFINITION = re.compile(r"(?:란|이란|라\s*함은)\s*[^\n]*?(?:말한다|한다)")


def definition_without_rule(requirement, rules):
    """정의가 바뀐 요건(예: "고령자"란 … 말한다)인데 그 조항을 따르는 규칙(basisArticles)이 상품에 없으면 그 이유. 아니면 None.

    정의는 그것을 값으로 쓰는 규칙이 있을 때만 상품 규칙에 영향을 준다. 근거 조항이 비어 있는 규칙이 그 정의의
    단위(예: 나이)로 돼 있으면 그 규칙이 정의를 따를 수도 있으므로 확정하지 않는다.
    """
    if requirement.change != "modified" or not _DEFINITION.search(requirement.new_text or ""):
        return None
    article = article_key(requirement.article_ref)
    if not article or any(article in (rule.get("basisArticles") or []) for rule in rules):
        return None
    unknown_units = {rule.get("unit") for rule in rules if not rule.get("basisArticles")}
    if any(expected_value(requirement, unit) is not None for unit in unknown_units):
        return None
    return f"{article}의 정의를 따르는 규칙이 이 상품에 없음 (근거 조항 기준)"


def sale_ended_basis(context):
    """판매 중단 상품의 관리를 정한 조항 uri (유지 판정의 근거). 없으면 빈 목록."""
    return [a["uri"] for a in context if _SALE_ENDED_ARTICLE.search(a.get("text") or "")][:1]


def applied_new_rule(requirement, rules, catalog=()):
    """상품에 이미 있는(반영 예정 포함) 그 조항의 새 규칙 행. 없으면 None. rules: 그 상품의 끝나지 않은 규칙."""
    key = new_rule_key(requirement, catalog)
    return next((r for r in rules if r["ruleKey"] == key), None)


def source_texts(state, product_id):
    refs = {a["uri"]: a["text"] for a in state["regulation_context"]}
    refs.update({d["uri"]: d["content"] for d in state.get("product_documents", []) if d["productId"] == product_id})
    return refs


def ground_conditions(assessment, sources):
    """인용문이 한 원문에만 실제로 있으면 잘못 붙인 URI를 그 조회 URI로 바로잡는다."""
    for condition in assessment.conditions:
        quote = normalize(condition.quote)
        if len(quote) < 6 or quote in normalize(sources.get(condition.source_ref, "")):
            continue
        matches = [uri for uri, text in sources.items() if quote in normalize(text)]
        if len(matches) == 1:
            condition.source_ref = matches[0]


def condition_errors(assessment, sources):
    errors = []
    if assessment.applicability == "conditional" and not assessment.conditions:
        errors.append("조건부 적용에는 예외 조건과 원문 인용이 필요합니다")
    for condition in assessment.conditions:
        text = sources.get(condition.source_ref, "")
        if "대환" in condition.text and "연장" in condition.quote and "대환" not in condition.quote:
            errors.append("대환 조건을 기간 연장 조항만으로 뒷받침할 수 없습니다. 대환 예외 원문을 인용하세요")
        if not condition.text.strip() or len(normalize(condition.quote)) < 6 or normalize(condition.quote) not in normalize(text):
            errors.append("적용 조건의 출처·인용문이 실제 조회 원문과 다릅니다")
    return errors


def validate_proposal(state, proposal, baseline=None):
    req = next(r for r in state["requirements"] if r.id == proposal.requirement_id)
    rules = {r["ruleId"]: r for r in state["product_rules"] if r["productId"] == proposal.product_id}
    product = next((p for p in state.get("products", []) if p["productId"] == proposal.product_id), {})
    errors = condition_errors(proposal, source_texts(state, proposal.product_id))
    if proposal.article_ref != req.article_ref or proposal.effective_date != req.effective_date:
        errors.append("법령 근거·시행일은 원래 요건과 같아야 합니다")
    if not proposal.scope.strip():
        errors.append("적용 범위를 작성해야 합니다")
    if not proposal.recommendation.strip() or len(proposal.recommendation) > 220:
        errors.append("변경 제안은 220자 이내로 구체적으로 작성하세요")
    for name, limit, width in (("actions", 3, 180), ("optional_actions", 2, 160), ("precautions", 2, 160)):
        values = getattr(proposal, name)
        if len(values) > limit or any(not v.strip() or len(v) > width for v in values):
            errors.append(f"{name}: {limit}개 이하, 항목당 {width}자 이내로 작성하세요")
    if not proposal.actions:
        errors.append("필수 수행 항목이 없습니다")
    existing = [change for change in proposal.changes if change.rule_id is not None]
    added = [change for change in proposal.changes if change.rule_id is None]  # 새 규칙
    ids = [change.rule_id for change in existing]
    if len(ids) != len(set(ids)):
        errors.append("같은 규칙의 변경이 중복되었습니다")
    if set(ids) != set(proposal.target_rule_ids):
        errors.append("구조화된 값 변경은 변경 대상 규칙과 정확히 일치해야 합니다")
    if len(added) > 1:
        errors.append("새 규칙은 요건 하나에 하나만 만듭니다")
    catalog = state.get("rule_catalog") or state["product_rules"]
    for change in added:
        errors.extend(new_rule_errors(req, change, [r for r in catalog if r["productId"] == proposal.product_id], catalog))
    for change in existing:
        row = rules.get(change.rule_id)
        if not row:
            errors.append(f"{change.rule_id}: 이 상품의 유효 규칙이 아닙니다")
            continue
        if change.rule_key != row["ruleKey"] or change.unit != row["unit"] or not same_value(change.current_value, row["value"]):
            errors.append(f"{change.rule_id}: 현재 값·키·단위를 조회 결과 그대로 사용하세요")
        expected = expected_value(req, row["unit"])
        if expected is None or not same_value(change.proposed_value, expected):
            errors.append(f"{change.rule_id}: 개정 원문의 값·단위와 다릅니다. 기대 값: {expected or '수치 근거 확인 필요'} ({row['unit']})")
        if same_value(change.current_value, change.proposed_value):
            errors.append(f"{change.rule_id}: 이미 같은 값인 규칙을 변경 대상으로 넣었습니다")
        problem = change_problem(req, row, product)  # 영향 판단에서 걸러지지만 직접 수정 등 다른 경로도 막는다
        if problem:
            errors.append(problem[1])
    if baseline and baseline.verification == "passed":
        before = [c.model_dump() for c in baseline.changes]
        after = [c.model_dump() for c in proposal.changes]
        if before != after or proposal.scope != baseline.scope:
            errors.append("재작성은 검증된 필수 값 변경·적용 범위를 유지해야 합니다")
    return list(dict.fromkeys(errors))


def approval_problem(proposal):
    """CLI 표시·검토·HTTP 승인에서 같은 승인 가능 기준을 사용한다."""
    data = proposal.model_dump() if hasattr(proposal, "model_dump") else proposal
    problems = []
    if data.get("verification") != "passed":
        problems.append("검증 미완료" if data.get("verification") == "pending" else "검증 실패")
    if data.get("generation_error"):
        problems.append(data["generation_error"])
    if not (data.get("recommendation") or "").strip():
        problems.append("제안 본문 없음")
    if not data.get("actions") or any(not a.strip() for a in data["actions"]):
        problems.append("필수 수행 항목 없음")
    return "; ".join(problems) or None


def validate_feedback(comment):
    text = normalize(comment).strip(".!?~")
    vague = {"새로운제안", "새로운아이디어", "새제안", "다른제안", "다른제안줘", "다른제안줘봐", "다른제안사항줘봐", "다시", "다시써줘", "수정해줘", "더좋게", "알아서", "바꿔줘"}
    if not text or text in vague:
        raise ValueError("무엇을 바꿀지 구체적으로 적어주세요. 예: 기존 신청은 제외하고 화면 안내 문구를 한 줄 추가해줘")


def exemption_needs_customer_data(requirement, rules):
    """DSR 수치형 예외 한도는 상품 전체 면제를 증명하지 않는다. 정답 ID·상품 목록은 쓰지 않는다."""
    if "DSR" not in (requirement.new_text or "").upper():
        return False
    return any("DSR" in r["ruleKey"].upper() and "EXEMPT" in r["ruleKey"].upper()
               and r["unit"] != "bool" for r in rules)
