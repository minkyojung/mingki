"""규정 원문 읽기 · 조항 분리 · 신·구 비교 · 검색. MCP와 무관한 순수 함수.

포맷 규칙은 data/regulations/README.md 를 따른다. 조항 ID 규칙은 Agent의 app/data.py 와 같다.
접근 범위: REGULATIONS_DIR/{regulation_id}/v{N}.md 만 읽는다 (eval/, docs/ 는 볼 수 없다).
"""

import os
import re
from functools import lru_cache
from pathlib import Path

def _default_dir() -> Path:
    # 로컬: mcp/regulation-mcp/regulation_mcp/ → 레포 루트 data/regulations. Docker에서는 환경 변수로 준다.
    return Path(__file__).resolve().parents[3] / "data" / "regulations"


REGULATIONS_DIR = Path(os.environ.get("REGULATIONS_DIR") or _default_dir()).resolve()

ARTICLE = re.compile(r"^### (부칙 )?제(\d+)조(?:의(\d+))?\((.+)\)$")
_REGULATION_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION = re.compile(r"^v\d+$")


class RegulationError(ValueError):
    """Agent에게 그대로 보여줄 수 있는 입력 오류."""


def article_id(supplementary: bool, num: str, sub: str | None) -> str:
    aid = f"{num}-{sub}" if sub else num
    return f"supp-{aid}" if supplementary else aid


def uri(regulation_id: str, version: str, aid: str | None = None) -> str:
    return f"reg://{regulation_id}/{version}" + (f"/{aid}" if aid else "")


def regulation_path(regulation_id: str, version: str, root: Path = REGULATIONS_DIR) -> Path:
    """입력값으로 파일 경로를 만들되, 허용 범위(root/{id}/v{N}.md) 밖이면 거절한다."""
    if not _REGULATION_ID.match(regulation_id):
        raise RegulationError(f"잘못된 regulation_id: {regulation_id!r} (소문자 · 숫자 · 하이픈만)")
    if not _VERSION.match(version):
        raise RegulationError(f"잘못된 version: {version!r} (예: v3, v4)")
    path = (root / regulation_id / f"{version}.md").resolve()
    if not path.is_relative_to(root.resolve()):
        raise RegulationError("허용된 규정 폴더 밖입니다")
    if not path.is_file():
        raise RegulationError(f"규정이 없습니다: {regulation_id} {version} (가능: {list_versions(root)})")
    return path


def list_versions(root: Path = REGULATIONS_DIR) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in sorted(p for p in root.iterdir() if p.is_dir() and _REGULATION_ID.match(p.name)):
        versions = sorted(
            (f.stem for f in d.glob("v*.md") if _VERSION.match(f.stem)), key=lambda v: int(v[1:])
        )
        if versions:
            out[d.name] = versions
    return out


@lru_cache
def load(regulation_id: str, version: str, root: Path = REGULATIONS_DIR) -> dict:
    """{"meta": {frontmatter}, "articles": {article_id: {"title", "text"}}}"""
    raw = regulation_path(regulation_id, version, root).read_text(encoding="utf-8")
    _, front, body = raw.split("---", 2)
    meta = dict(line.split(": ", 1) for line in front.strip().splitlines() if ": " in line)

    articles: dict[str, dict] = {}
    current = None
    for line in body.splitlines():
        if m := ARTICLE.match(line):
            current = article_id(bool(m[1]), m[2], m[3])
            articles[current] = {"title": m[4], "lines": []}
        elif line.startswith("## ") or line.startswith("# "):
            current = None
        elif current:
            articles[current]["lines"].append(line)
    return {
        "meta": meta,
        "articles": {k: {"title": v["title"], "text": "\n".join(v["lines"]).strip()} for k, v in articles.items()},
    }


def get_article(regulation_id: str, version: str, aid: str, root: Path = REGULATIONS_DIR) -> dict:
    articles = load(regulation_id, version, root)["articles"]
    if aid not in articles:
        raise RegulationError(f"조항이 없습니다: {aid} (예: 15, 15-2, supp-2)")
    return {"uri": uri(regulation_id, version, aid), "article_id": aid, **articles[aid]}


def list_articles(regulation_id: str, version: str, root: Path = REGULATIONS_DIR) -> list[dict]:
    return [
        {"uri": uri(regulation_id, version, aid), "article_id": aid, "title": a["title"]}
        for aid, a in load(regulation_id, version, root)["articles"].items()
    ]


def diff_versions(regulation_id: str, old_version: str, new_version: str, root: Path = REGULATIONS_DIR) -> dict:
    """원문 기준 결정적 diff. 바뀐 조항만 돌려준다 (자구 수정 포함 — 실질 여부 판단은 Agent 몫)."""
    old = load(regulation_id, old_version, root)
    new = load(regulation_id, new_version, root)
    changes = []
    for aid in dict.fromkeys([*old["articles"], *new["articles"]]):
        o, n = old["articles"].get(aid), new["articles"].get(aid)
        if o and n and o == n:
            continue
        changes.append({
            "article_id": aid,
            "title": (n or o)["title"],
            "change": "added" if o is None else "removed" if n is None else "modified",
            "old_text": o["text"] if o else None,
            "new_text": n["text"] if n else None,
            "old_uri": uri(regulation_id, old_version, aid) if o else None,
            "new_uri": uri(regulation_id, new_version, aid) if n else None,
        })
    return {
        "regulation_id": regulation_id,
        "old_version": old_version,
        "new_version": new_version,
        "effective_from": new["meta"].get("effective_from"),
        "changes": changes,
    }


def search(query: str, regulation_id: str, version: str, root: Path = REGULATIONS_DIR) -> list[dict]:
    """키워드 검색. 공백으로 나눈 단어가 모두 제목이나 본문에 있는 조항.

    TODO(데이터 팀원 Vector DB): 의미 검색이 준비되면 여기만 교체한다. 반환 형식은 그대로 둔다.
    """
    terms = query.split()
    if not terms:
        raise RegulationError("query가 비어 있습니다")
    return [
        {"uri": uri(regulation_id, version, aid), "article_id": aid, **a}
        for aid, a in load(regulation_id, version, root)["articles"].items()
        if all(t in a["title"] or t in a["text"] for t in terms)
    ]
