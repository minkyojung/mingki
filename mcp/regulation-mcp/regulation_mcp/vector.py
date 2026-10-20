"""규정 조항 의미 검색 (Vector DB).

조항 1개 = 조각 1개. 임베딩은 OpenAI, 저장은 프로세스 안의 Chroma(메모리).
색인은 (regulation_id, version)마다 처음 검색할 때 만들고, 원문이 같으면 다시 만들지 않는다.
OPENAI_API_KEY가 없거나 임베딩 호출이 실패하면 키워드 검색(regulations.search)으로 대체한다.
색인 규칙은 docs/VECTOR_INDEX.md 를 따른다.
"""

import hashlib
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

import chromadb
from chromadb.config import Settings

from regulation_mcp import regulations as reg
from regulation_mcp.regulations import RegulationError

log = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
TOP_K = int(os.environ.get("SEARCH_TOP_K", 5))

Embedder = Callable[[list[str]], list[list[float]]]


def openai_embedder(texts: list[str]) -> list[list[float]]:
    from openai import OpenAI

    resp = OpenAI().embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]


def article_label(aid: str) -> str:
    """'15-2' → '제15조의2', 'supp-2' → '부칙 제2조'."""
    supplementary = aid.startswith("supp-")
    num, _, sub = aid.removeprefix("supp-").partition("-")
    label = f"제{num}조" + (f"의{sub}" if sub else "")
    return f"부칙 {label}" if supplementary else label


def chunk_text(regulation_title: str, aid: str, article: dict) -> str:
    """임베딩할 텍스트. 규정 제목과 조항 제목을 앞에 붙여 맥락을 준다."""
    return f"{regulation_title} > {article_label(aid)}({article['title']})\n{article['text']}"


class VectorIndex:
    def __init__(self, embedder: Embedder, root: Path = reg.REGULATIONS_DIR):
        self._embed = embedder
        self._root = root
        client = chromadb.EphemeralClient(settings=Settings(anonymized_telemetry=False))
        self._col = client.create_collection(
            f"regulations-{uuid.uuid4().hex}", metadata={"hnsw:space": "cosine"}, embedding_function=None
        )
        self._digests: dict[tuple[str, str], str] = {}

    def _ensure_indexed(self, regulation_id: str, version: str) -> dict:
        doc = reg.load(regulation_id, version, self._root)  # 허용 범위 검사 포함
        title = doc["meta"].get("title", regulation_id)
        texts = {aid: chunk_text(title, aid, a) for aid, a in doc["articles"].items()}
        digest = hashlib.sha256("\n\n".join(texts.values()).encode()).hexdigest()
        key = (regulation_id, version)
        if self._digests.get(key) == digest:
            return doc

        self._col.delete(where={"$and": [{"regulation_id": regulation_id}, {"version": version}]})
        aids = list(texts)
        self._col.add(
            ids=[f"{regulation_id}/{version}/{aid}" for aid in aids],
            embeddings=self._embed([texts[aid] for aid in aids]),
            documents=[texts[aid] for aid in aids],
            metadatas=[
                {
                    "regulation_id": regulation_id,
                    "version": version,
                    "article_id": aid,
                    "title": doc["articles"][aid]["title"],
                    "effective_from": doc["meta"].get("effective_from", ""),
                    "uri": reg.uri(regulation_id, version, aid),
                }
                for aid in aids
            ],
        )
        self._digests[key] = digest
        return doc

    def search(self, query: str, regulation_id: str, version: str, k: int = TOP_K) -> list[dict]:
        if not query.strip():
            raise RegulationError("query가 비어 있습니다")
        doc = self._ensure_indexed(regulation_id, version)
        res = self._col.query(
            query_embeddings=self._embed([query]),
            n_results=min(k, len(doc["articles"])),
            where={"$and": [{"regulation_id": regulation_id}, {"version": version}]},
        )
        return [
            {"uri": reg.uri(regulation_id, version, m["article_id"]), "article_id": m["article_id"], **doc["articles"][m["article_id"]]}
            for m in res["metadatas"][0]
        ]


_index: VectorIndex | None = None


def search(query: str, regulation_id: str, version: str) -> list[dict]:
    """의미 검색. 관련도 순으로 최대 TOP_K개. 사용할 수 없으면 키워드 검색으로 대체한다."""
    global _index
    if not os.environ.get("OPENAI_API_KEY"):
        return reg.search(query, regulation_id, version)
    try:
        if _index is None:
            _index = VectorIndex(openai_embedder)
        return _index.search(query, regulation_id, version)
    except RegulationError:
        raise
    except Exception:
        log.warning("의미 검색 실패, 키워드 검색으로 대체합니다", exc_info=True)
        return reg.search(query, regulation_id, version)
