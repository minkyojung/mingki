"""code-mcp: 샘플 레포 검색 · 파일 읽기 · 사용처 · 패치 검증(샌드박스) · Draft PR.

접근 범위: SAMPLE_REPO_DIR 아래만. 검증은 복사본에서만 하고 원본은 바꾸지 않는다.

실행:
    uv run python -m code_mcp.server          # HTTP  http://127.0.0.1:8002/mcp
    uv run python -m code_mcp.server --stdio
"""

import os
import sys

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from code_mcp import publish, sandbox
from code_mcp import search as code
from code_mcp.backend import Backend, BackendError
from code_mcp.repo import RepoError
from code_mcp.sandbox import PatchError

mcp = FastMCP(
    "code-mcp",
    instructions=(
        "개인신용대출 샘플 코드 레포 도구. 검색 결과 한 줄은 {file, line, symbol, snippet, kind}이고 "
        "kind는 code | config | ui | test 이다. 같은 숫자라도 의미가 다를 수 있으니 get_file로 앞뒤를 확인한다. "
        "create_draft_pr은 검토 · 승인(approval_id) 이후 publish 단계에서만 쓴다."
    ),
)
backend = Backend()


def _guard(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except (RepoError, PatchError, BackendError) as e:
        raise ToolError(str(e)) from e


# ---------- 읽기 ----------

@mcp.tool(annotations={"readOnlyHint": True})
def search_code(query: str, regex: bool = False, path_glob: str = "**/*", max_results: int = 200) -> list[dict]:
    """샘플 레포에서 query가 들어 있는 줄을 찾는다.

    표현이 다른 같은 의미(age >= 65, age > 64, 780개월)는 문자열 검색으로 한 번에 안 잡힌다.
    regex=True로 여러 표현을 묶거나(예: "age\\s*>=?\\s*6[45]"), 여러 번 검색한다.

    Args:
        query: 찾을 문자열 (regex=True면 정규식)
        regex: query를 정규식으로 해석
        path_glob: 검색 범위 (예: "src/**/*.py", "config/*.yaml")
        max_results: 최대 결과 수 (최대 200)

    Returns:
        [{file, line, symbol, snippet, kind}]  symbol: 그 줄이 속한 함수 · 상수 · 설정 키
    """
    return _guard(code.search_code, query, regex, path_glob, max_results)


@mcp.tool(annotations={"readOnlyHint": True})
def get_file(path: str) -> str:
    """샘플 레포 파일 전체 내용.

    Args:
        path: 레포 기준 상대 경로 (예: "src/loan/eligibility.py")
    """
    return _guard(code.get_file, path)


@mcp.tool(annotations={"readOnlyHint": True})
def find_usages(symbol: str) -> list[dict]:
    """식별자(함수 · 상수 · 설정 키)가 정의되거나 쓰인 곳. 이름이 정확히 같은 것만 (SENIOR_AGE ≠ SENIOR_AGE_MONTHS).

    Returns:
        [{file, line, symbol, snippet, kind}]
    """
    return _guard(code.find_usages, symbol)


# ---------- 검증 (샌드박스) ----------

@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def check_patch(patches: list[dict], test_cases: list[dict] | None = None) -> dict:
    """샘플 레포 복사본에 패치를 적용하고 문법 · 테스트를 돌린다. 원본 레포는 바뀌지 않는다.

    묶음마다 따로 검증(적용 · 문법 · 기존 테스트 · 생성 테스트)한 뒤, 통과한 묶음을 모두 합쳐 통합 검증한다.

    Args:
        patches: [{bundle_id, diff, files?, locations?}]
            diff 형식: "@@ 파일:줄 @@\\n-원래 줄\\n+새 줄" (여러 hunk 가능) 또는 unified diff
        test_cases: [{id, kind, code?, ...}]  code(pytest 소스)가 있는 것만 실행한다.
            kind가 system인 것은 통합 검증에서만 실행한다.

    Returns:
        {results: [{bundle_id, ok, errors, files}],
         system: {ok, errors, missed_locations},
         tests: {generated, skipped_without_code}}
    """
    return _guard(sandbox.check_patch, patches, test_cases or [])


# ---------- 쓰기 (승인 필요) ----------

@mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True})
def create_draft_pr(
    task_id: str,
    approval_id: int,
    idempotency_key: str,
    title: str,
    patches: list[dict],
    spec_keys: list[str] | None = None,
) -> dict:
    """승인된 패치로 브랜치 · 커밋을 만들고 Draft PR을 요청한다 (승인 필요, merge는 사람이).

    순서: 복사본에 브랜치 · 커밋 준비 → 백엔드가 approval_id 검증 후 큐에 등록 → 통과했을 때만 브랜치를 남긴다.
    거절(403/409)되면 아무것도 남지 않는다. 같은 idempotency_key로 다시 부르면 새로 만들지 않는다.

    Args:
        task_id: 분석 작업 ID (UUID)
        approval_id: 검토 · 승인 기록 ID
        idempotency_key: 예: "{task_id}:pr:all"
        title: PR 제목 (커밋 메시지)
        patches: [{bundle_id, diff}] check_patch를 통과한 패치
        spec_keys: 대상 변경 명세 ID 목록 (승인 때 제외한 것이 있으면 거절된다)

    Returns:
        {jobId, status, duplicate, branch, commit}. PR 링크는 GET /internal/jobs/{jobId} 결과에 있다.
    """
    if not patches:
        raise ToolError("patches가 비어 있습니다")
    built = _guard(publish.build_branch, task_id, title, patches)
    try:
        enqueued = _guard(backend.post, "/internal/jobs", {
            "taskId": task_id,
            "type": "CREATE_DRAFT_PR",
            "approvalId": approval_id,
            "idempotencyKey": idempotency_key,
            "payload": {
                "branch": built["branch"],
                "title": title,
                "commit": built["commit"],
                "specKeys": spec_keys or [],
                "bundleIds": [p.get("bundle_id") for p in patches],
            },
        })
    except ToolError:
        publish.discard(built)
        raise

    if enqueued.get("duplicate") and (prev := publish.existing(task_id)):
        publish.discard(built)
        return {**enqueued, **prev}
    publish.keep(task_id, built)
    return {**enqueued, "branch": built["branch"], "commit": built["commit"]}


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="http",
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 8002)),
            path="/mcp",
        )
