"""regulation-mcp: 규정 원문 비교 · 검색 · 조항 원문.

실행:
    uv run python -m regulation_mcp.server          # HTTP  http://127.0.0.1:8001/mcp
    uv run python -m regulation_mcp.server --stdio  # stdio (로컬 Client 테스트용)
"""

import os
import sys

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError, ToolError

from regulation_mcp import regulations as reg
from regulation_mcp.regulations import RegulationError

mcp = FastMCP(
    "regulation-mcp",
    instructions=(
        "개인신용대출 운영기준 원문 도구. 조항 ID는 '15', '15-2'(제15조의2), 'supp-2'(부칙 제2조) 형식이다. "
        "원문 링크는 reg://{regulation_id}/{version}/{article_id} 이다."
    ),
)


def _tool_error(e: RegulationError) -> ToolError:
    return ToolError(str(e))


@mcp.tool(annotations={"readOnlyHint": True})
def diff_versions(regulation_id: str, old_version: str, new_version: str) -> dict:
    """신·구 규정을 조항 단위로 비교해 바뀐 조항만 돌려준다 (원문 기준, 결정적).

    표현만 바뀐 조항도 포함된다. 실질 변경인지는 호출한 쪽이 판단한다.

    Args:
        regulation_id: 규정 ID (예: "credit-loan-ops")
        old_version: 구 버전 (예: "v3")
        new_version: 신 버전 (예: "v4")

    Returns:
        {regulation_id, old_version, new_version, effective_from,
         changes: [{article_id, title, change: "added"|"removed"|"modified",
                    old_text, new_text, old_uri, new_uri}]}
    """
    try:
        return reg.diff_versions(regulation_id, old_version, new_version)
    except RegulationError as e:
        raise _tool_error(e) from e


@mcp.tool(annotations={"readOnlyHint": True})
def search_regulation(query: str, regulation_id: str = "credit-loan-ops", version: str = "v4") -> list[dict]:
    """규정 조항 검색. 공백으로 나눈 단어가 모두 제목이나 본문에 있는 조항을 돌려준다.

    Args:
        query: 검색어 (예: "고령자", "DSR 한도")
        regulation_id: 규정 ID
        version: 버전 (예: "v4")

    Returns:
        [{uri, article_id, title, text}]
    """
    try:
        return reg.search(query, regulation_id, version)
    except RegulationError as e:
        raise _tool_error(e) from e


@mcp.tool(annotations={"readOnlyHint": True})
def get_article(regulation_id: str, version: str, article_id: str) -> dict:
    """조항 하나의 원문. Resource reg://{regulation_id}/{version}/{article_id} 와 같은 내용.

    Returns:
        {uri, article_id, title, text}
    """
    try:
        return reg.get_article(regulation_id, version, article_id)
    except RegulationError as e:
        raise _tool_error(e) from e


@mcp.tool(annotations={"readOnlyHint": True})
def list_articles(regulation_id: str = "credit-loan-ops", version: str = "v4") -> list[dict]:
    """버전 하나의 조항 목록 (본문 제외).

    Returns:
        [{uri, article_id, title}]
    """
    try:
        return reg.list_articles(regulation_id, version)
    except RegulationError as e:
        raise _tool_error(e) from e


@mcp.resource("reg://{regulation_id}/{version}/{article_id}", mime_type="text/plain")
def article_resource(regulation_id: str, version: str, article_id: str) -> str:
    """조항 원문 (신·구 원문 링크)."""
    try:
        return reg.get_article(regulation_id, version, article_id)["text"]
    except RegulationError as e:
        raise ResourceError(str(e)) from e


@mcp.resource("reg://{regulation_id}/{version}", mime_type="text/plain")
def regulation_resource(regulation_id: str, version: str) -> str:
    """규정 제목 (버전 링크)."""
    try:
        return reg.load(regulation_id, version)["meta"].get("title", regulation_id)
    except RegulationError as e:
        raise ResourceError(str(e)) from e


if __name__ == "__main__":
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="http",
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 8001)),
            path="/mcp",
        )
