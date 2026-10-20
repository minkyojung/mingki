"""백엔드 대신 가짜 HTTP 응답(MockTransport)을 꽂아서 MCP 도구를 검증한다."""

import json

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from business_mcp import server
from business_mcp.backend import Backend

RULES = [
    {"ruleId": "R010", "productId": "P002", "ruleKey": "ELIGIBLE_MIN_AGE", "value": "65", "unit": "age",
     "effectiveFrom": "2024-01-01", "effectiveTo": None},
    {"ruleId": "R011", "productId": "P002", "ruleKey": "DSR_LIMIT", "value": "0.40", "unit": "ratio",
     "effectiveFrom": "2024-01-01", "effectiveTo": None},
]


class FakeSpring:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if req.headers.get("X-Internal-Token") != "test-token":
            return httpx.Response(401, json={"error": "UNAUTHORIZED", "message": "internal token"})
        path = req.url.path
        if path == "/internal/product-rules":
            key = req.url.params.get("key")
            return httpx.Response(200, json=[r for r in RULES if key is None or r["ruleKey"] == key])
        if path == "/internal/products/P002/rules":
            return httpx.Response(200, json=RULES)
        if path == "/internal/products/P999/rules":
            return httpx.Response(404, json={"error": "NOT_FOUND", "message": "product not found"})
        if path == "/internal/contracts/impact":
            return httpx.Response(200, json={"ageFrom": int(req.url.params["ageFrom"]), "params": dict(req.url.params)})
        if path == "/internal/work-tickets":
            body = json.loads(req.content)
            if body["approvalId"] != 7:
                return httpx.Response(403, json={"error": "FORBIDDEN", "message": f"존재하지 않는 approvalId: {body['approvalId']}"})
            return httpx.Response(202, json={"jobId": "j-1", "status": "QUEUED", "duplicate": False})
        return httpx.Response(404, json={"error": "NOT_FOUND", "message": "not found"})


@pytest.fixture
def spring(monkeypatch):
    fake = FakeSpring()
    monkeypatch.setattr(server, "backend", Backend("http://spring", "test-token", transport=httpx.MockTransport(fake)))
    return fake


async def call(name, args=None):
    async with Client(server.mcp) as c:
        return (await c.call_tool(name, args or {})).data


async def test_tools_are_registered():
    async with Client(server.mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {"get_products", "get_product_rules", "get_db_schema", "get_contract_impact", "create_work_ticket"}


async def test_sends_internal_token_and_caller(spring):
    await call("get_product_rules")
    assert spring.requests[0].headers["X-Internal-Caller"] == "business-mcp"


async def test_product_rules_pass_backend_response_through(spring):
    rules = await call("get_product_rules", {"rule_key": "DSR_LIMIT"})
    assert rules == [RULES[1]]   # camelCase 그대로, 가공 없음
    assert spring.requests[0].url.params["key"] == "DSR_LIMIT"


async def test_product_rules_by_product_filters_key(spring):
    rules = await call("get_product_rules", {"product_id": "P002", "rule_key": "ELIGIBLE_MIN_AGE"})
    assert [r["ruleId"] for r in rules] == ["R010"]


async def test_backend_error_becomes_tool_error(spring):
    with pytest.raises(ToolError, match="404 NOT_FOUND: product not found"):
        await call("get_product_rules", {"product_id": "P999"})


async def test_product_id_cannot_escape_path(spring):
    with pytest.raises(ToolError, match="잘못된 product_id"):
        await call("get_product_rules", {"product_id": "../tasks"})
    assert spring.requests == []


async def test_contract_impact_maps_params_and_omits_none(spring):
    await call("get_contract_impact", {"age_from": 60, "age_to": 64})
    params = spring.requests[0].url.params
    assert params["ageFrom"] == "60" and params["ageTo"] == "64"
    assert "appliedBefore" not in params


async def test_work_ticket_body_matches_backend_contract(spring):
    out = await call("create_work_ticket", {
        "task_id": "t-1", "approval_id": 7, "idempotency_key": "t-1:ticket:S1",
        "title": "고령자 기준 변경", "spec_keys": ["S1"],
    })
    assert out == {"jobId": "j-1", "status": "QUEUED", "duplicate": False}
    body = json.loads(spring.requests[0].content)
    assert body == {
        "taskId": "t-1", "approvalId": 7, "idempotencyKey": "t-1:ticket:S1", "title": "고령자 기준 변경",
        "description": None, "type": "CODE", "targetRef": None, "specKeys": ["S1"], "assignee": None,
    }


async def test_unapproved_ticket_is_rejected_with_backend_reason(spring):
    with pytest.raises(ToolError, match="403 FORBIDDEN: 존재하지 않는 approvalId"):
        await call("create_work_ticket", {"task_id": "t-1", "approval_id": 999, "idempotency_key": "k", "title": "x"})


async def test_backend_down_is_tool_error(monkeypatch):
    def refuse(req):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(server, "backend", Backend("http://spring", "t", transport=httpx.MockTransport(refuse)))
    with pytest.raises(ToolError, match="연결할 수 없습니다"):
        await call("get_products")
