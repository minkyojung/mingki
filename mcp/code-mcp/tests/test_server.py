import json
import subprocess

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from code_mcp import publish, server
from code_mcp.backend import Backend

PATCH = {"bundle_id": "B:e", "diff": "@@ src/loan/eligibility.py:21 @@\n-return age >= 65\n+return age >= 60"}
ARGS = {"task_id": "3f2a9c1e-0000-4000-8000-000000000001", "approval_id": 7,
        "idempotency_key": "k1", "title": "고령자 기준 60세", "patches": [PATCH], "spec_keys": ["S1"]}


class FakeJobs:
    def __init__(self):
        self.bodies, self.keys = [], set()

    def __call__(self, req):
        body = json.loads(req.content)
        self.bodies.append(body)
        if body["approvalId"] != 7:
            return httpx.Response(403, json={"error": "FORBIDDEN", "message": "검토·승인(REVIEW/APPROVE) 기록이 아닙니다"})
        dup = body["idempotencyKey"] in self.keys
        self.keys.add(body["idempotencyKey"])
        return httpx.Response(202, json={"jobId": "j-1", "status": "QUEUED", "duplicate": dup})


@pytest.fixture
def jobs(monkeypatch, tmp_path):
    fake = FakeJobs()
    monkeypatch.setattr(server, "backend", Backend("http://spring", "t", "code-mcp", transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(publish, "PUBLISH_DIR", tmp_path / "publish")
    return fake


async def call(name, args):
    async with Client(server.mcp) as c:
        return (await c.call_tool(name, args)).data


async def test_tools_are_registered():
    async with Client(server.mcp) as c:
        names = {t.name for t in await c.list_tools()}
    assert names == {"search_code", "get_file", "find_usages", "check_patch", "create_draft_pr"}


async def test_search_code_over_mcp():
    hits = await call("search_code", {"query": "RETIREMENT_AGE ="})
    assert hits == [{"file": "src/loan/income.py", "line": 3, "symbol": "RETIREMENT_AGE",
                     "snippet": "RETIREMENT_AGE = 65", "kind": "code"}]


async def test_get_file_out_of_scope_is_tool_error():
    with pytest.raises(ToolError, match="샘플 레포 밖"):
        await call("get_file", {"path": "../../eval/answer_key.md"})


async def test_check_patch_over_mcp():
    res = await call("check_patch", {"patches": [PATCH]})
    assert res["results"][0]["ok"] is True


async def test_approved_pr_keeps_branch_with_commit(jobs):
    out = await call("create_draft_pr", ARGS)
    assert out["jobId"] == "j-1" and out["branch"] == "rule-ripple/3f2a9c1e"
    body = jobs.bodies[0]
    assert body["type"] == "CREATE_DRAFT_PR" and body["payload"]["specKeys"] == ["S1"]

    repo = publish.workspace_for(ARGS["task_id"])
    log = subprocess.run(["git", "log", "--oneline", "-p", "-1"], cwd=repo, capture_output=True, text=True).stdout
    assert "고령자 기준 60세" in log and "+    return age >= 60" in log


async def test_rejected_pr_leaves_nothing(jobs):
    with pytest.raises(ToolError, match="403 FORBIDDEN"):
        await call("create_draft_pr", {**ARGS, "approval_id": 999})
    assert not publish.workspace_for(ARGS["task_id"]).exists()


async def test_duplicate_request_returns_same_branch(jobs):
    first = await call("create_draft_pr", ARGS)
    again = await call("create_draft_pr", ARGS)
    assert again["duplicate"] is True and again["commit"] == first["commit"]


async def test_unappliable_patch_never_reaches_backend(jobs):
    bad = {"bundle_id": "B:x", "diff": "@@ src/loan/eligibility.py:21 @@\n-nope\n+x"}
    with pytest.raises(ToolError, match="원문이 다릅니다"):
        await call("create_draft_pr", {**ARGS, "patches": [bad]})
    assert jobs.bodies == []
