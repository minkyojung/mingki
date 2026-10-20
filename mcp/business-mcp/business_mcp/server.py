"""business-mcp: 상품 · 상품 규칙 · 계약 · DB 스키마 (Spring /internal/** 경유) + 작업 티켓 생성.

API 계약은 docs/BACKEND_API.md 7 · 9절. 응답 키는 백엔드 그대로 camelCase.

실행:
    uv run python -m business_mcp.server          # HTTP  http://127.0.0.1:8003/mcp
    uv run python -m business_mcp.server --stdio
"""

import os
import re
import sys
from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from business_mcp.backend import Backend, BackendError

mcp = FastMCP(
    "business-mcp",
    instructions=(
        "리플은행 개인신용대출의 상품 · 상품 규칙 · 계약 데이터 (Spring 백엔드 응답 그대로). "
        "상품 규칙은 effectiveTo가 null이면 현재 유효하다. "
        "create_work_ticket은 검토 · 승인(approval_id) 이후 publish 단계에서만 쓴다."
    ),
)
backend = Backend()
_PRODUCT_ID = re.compile(r"^[A-Za-z0-9_-]{1,10}$")


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BackendError as e:
        raise ToolError(str(e)) from e


# ---------- 읽기 ----------

@mcp.tool(annotations={"readOnlyHint": True})
def get_products() -> list[dict]:
    """상품 목록.

    Returns:
        [{productId, name, description, repaymentTypes, minTermMonths, maxTermMonths, status}]
    """
    return _call(backend.get, "/internal/products")


@mcp.tool(annotations={"readOnlyHint": True})
def get_product_rules(rule_key: str | None = None, product_id: str | None = None) -> list[dict]:
    """상품 규칙 (rule_key · product_id로 좁힐 수 있음, 둘 다 없으면 전체).

    같은 숫자라도 rule_key가 다르면 의미가 다르다 (예: DSR_LIMIT 0.40 vs MORTGAGE_LTV_LIMIT 0.40).

    Args:
        rule_key: 규칙 키 (예: "DSR_LIMIT", "ELIGIBLE_MIN_AGE")
        product_id: 상품 ID (예: "P002")

    Returns:
        [{ruleId, productId, ruleKey, value, unit, effectiveFrom, effectiveTo}]
        unit: ratio | percent_point | krw | age | score | bool
    """
    if product_id:
        # URL 경로에 들어가므로 ../ 같은 값으로 다른 /internal API에 닿지 못하게 막는다
        if not _PRODUCT_ID.match(product_id):
            raise ToolError(f"잘못된 product_id: {product_id!r} (예: P002)")
        rules = _call(backend.get, f"/internal/products/{product_id}/rules")
        return [r for r in rules if rule_key is None or r.get("ruleKey") == rule_key]
    return _call(backend.get, "/internal/product-rules", {"key": rule_key})


@mcp.tool(annotations={"readOnlyHint": True})
def get_db_schema() -> list[dict]:
    """샘플 업무 테이블(products, product_rules, loan_contracts)의 컬럼 정보.

    Returns:
        [{table, columns: [{name, type, nullable}]}]
    """
    return _call(backend.get, "/internal/db/schema")


@mcp.tool(annotations={"readOnlyHint": True})
def get_contract_impact(age_from: int = 0, age_to: int = 200, applied_before: str | None = None) -> dict:
    """신청일 기준 만 나이가 [age_from, age_to]인 유효 계약 · 신청 (경과조치 영향 확인용).

    거절 건과 만기 지난 계약은 빠진다. applied_before(시행일)보다 먼저 신청한 건은
    grandfathered=true (종전 기준 유지)로 센다.

    Args:
        age_from: 최소 나이 (예: 60)
        age_to: 최대 나이 (예: 64)
        applied_before: 시행일 YYYY-MM-DD (예: "2026-12-01")

    Returns:
        {ageFrom, ageTo, total, byProduct, appliedBefore?, grandfathered?,
         contracts: [{contractId, productId, status, appliedAt, ageAtApplication, grandfathered?}]}
    """
    return _call(
        backend.get,
        "/internal/contracts/impact",
        {"ageFrom": age_from, "ageTo": age_to, "appliedBefore": applied_before},
    )


# ---------- 쓰기 (승인 필요) ----------

@mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
def create_work_ticket(
    task_id: str,
    approval_id: int,
    idempotency_key: str,
    title: str,
    description: str | None = None,
    type: Literal["CODE", "CONFIG", "DB", "DOC", "TEST"] = "CODE",
    target_ref: str | None = None,
    spec_keys: list[str] | None = None,
    assignee: str | None = None,
) -> dict:
    """작업 티켓 생성 요청 (승인 필요). 백엔드가 approval_id를 다시 검증하고 큐에 넣는다.

    거절 조건 (403/409): 같은 작업의 최신 REVIEW/APPROVE 승인이 아님, 작업 상태가 PUBLISHING이 아님,
    spec_keys에 승인 때 제외한 위치가 있음. 같은 idempotency_key로 다시 부르면 새로 만들지 않는다.

    Args:
        task_id: 분석 작업 ID (UUID)
        approval_id: 검토 · 승인 기록 ID
        idempotency_key: 예: "{task_id}:ticket:S1"
        title: 티켓 제목
        description: 본문
        type: CODE | CONFIG | DB | DOC | TEST
        target_ref: 대상 위치 (예: "src/loan/eligibility.py:21")
        spec_keys: 대상 변경 명세 ID 목록
        assignee: 담당자

    Returns:
        {jobId, status: "QUEUED", duplicate}. 완료는 GET /internal/jobs/{jobId}로 확인한다.
    """
    return _call(backend.post, "/internal/work-tickets", {
        "taskId": task_id,
        "approvalId": approval_id,
        "idempotencyKey": idempotency_key,
        "title": title,
        "description": description,
        "type": type,
        "targetRef": target_ref,
        "specKeys": spec_keys or [],
        "assignee": assignee,
    })


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="http",
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 8003)),
            path="/mcp",
        )
