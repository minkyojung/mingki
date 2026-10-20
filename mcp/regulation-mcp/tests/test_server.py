"""MCP 프로토콜을 거쳐 호출되는지 확인 (Agent가 보는 모습 그대로)."""

import pytest
from fastmcp import Client
from fastmcp.exceptions import ResourceError, ToolError

from regulation_mcp.server import mcp


async def test_tools_are_registered():
    async with Client(mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {"diff_versions", "search_regulation", "get_article", "list_articles"}


async def test_diff_versions_over_mcp():
    async with Client(mcp) as c:
        r = await c.call_tool("diff_versions", {"regulation_id": "credit-loan-ops", "old_version": "v3", "new_version": "v4"})
    assert r.data["effective_from"] == "2026-12-01"
    assert any(ch["article_id"] == "15-2" for ch in r.data["changes"])


async def test_search_regulation_over_mcp():
    async with Client(mcp) as c:
        r = await c.call_tool("search_regulation", {"query": "고령자"})
    assert any(h["uri"] == "reg://credit-loan-ops/v4/15" for h in r.data)


async def test_out_of_scope_is_tool_error():
    async with Client(mcp) as c:
        with pytest.raises(ToolError, match="regulation_id"):
            await c.call_tool("search_regulation", {"query": "정답", "regulation_id": "../../eval", "version": "answer_key"})


async def test_article_resource():
    async with Client(mcp) as c:
        text = (await c.read_resource("reg://credit-loan-ops/v4/15"))[0].text
        title = (await c.read_resource("reg://credit-loan-ops/v4"))[0].text
    assert "60" in text
    assert title == "개인신용대출 운영기준"


async def test_missing_article_resource_errors():
    async with Client(mcp) as c:
        with pytest.raises(Exception):
            await c.read_resource("reg://credit-loan-ops/v4/999")
