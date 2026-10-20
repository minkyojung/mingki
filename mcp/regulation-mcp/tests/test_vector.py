import hashlib
import math

import pytest

from regulation_mcp import regulations as reg
from regulation_mcp import vector
from regulation_mcp.regulations import RegulationError
from regulation_mcp.vector import VectorIndex


class FakeEmbedder:
    """글자 bigram 해시 벡터. 결정적이고 네트워크를 쓰지 않는다. 호출한 텍스트 수를 센다."""

    def __init__(self, dim: int = 512):
        self.dim = dim
        self.embedded = 0

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.embedded += len(texts)
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for a, b in zip(t, t[1:]):
                if not (a + b).isspace():
                    v[int(hashlib.md5((a + b).encode()).hexdigest(), 16) % self.dim] += 1
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture
def index():
    return VectorIndex(FakeEmbedder())


def test_article_label():
    assert vector.article_label("15") == "제15조"
    assert vector.article_label("15-2") == "제15조의2"
    assert vector.article_label("supp-2") == "부칙 제2조"


def test_returns_keyword_search_format_in_relevance_order(index):
    hits = index.search("고령자 기준 나이", "credit-loan-ops", "v4")
    assert 0 < len(hits) <= vector.TOP_K
    assert set(hits[0]) == {"uri", "article_id", "title", "text"}
    assert "15" in [h["article_id"] for h in hits[:3]]
    assert hits[0]["uri"] == f"reg://credit-loan-ops/v4/{hits[0]['article_id']}"


def test_searches_only_requested_version(index):
    index.search("DSR", "credit-loan-ops", "v4")
    hits = index.search("고령자 설명 확인 기록 보관", "credit-loan-ops", "v3")
    assert all(h["uri"].startswith("reg://credit-loan-ops/v3/") for h in hits)
    assert "15-2" not in {h["article_id"] for h in hits}  # v3에는 없는 조항


def test_indexes_each_version_once():
    embedder = FakeEmbedder()
    index = VectorIndex(embedder)
    n_articles = len(reg.load("credit-loan-ops", "v4")["articles"])

    index.search("DSR", "credit-loan-ops", "v4")
    assert embedder.embedded == n_articles + 1
    index.search("고령자", "credit-loan-ops", "v4")
    assert embedder.embedded == n_articles + 2  # 두 번째는 질의만 임베딩


@pytest.mark.parametrize(("regulation_id", "version"), [("../../eval", "answer_key"), ("credit-loan-ops", "README")])
def test_out_of_scope_is_rejected(index, regulation_id, version):
    with pytest.raises(RegulationError):
        index.search("정답", regulation_id, version)


def test_empty_query_is_rejected(index):
    with pytest.raises(RegulationError):
        index.search("  ", "credit-loan-ops", "v4")


def test_without_api_key_falls_back_to_keyword_search():
    assert vector.search("고령자", "credit-loan-ops", "v4") == reg.search("고령자", "credit-loan-ops", "v4")


def test_embedding_failure_falls_back_to_keyword_search(monkeypatch):
    def broken(texts):
        raise ConnectionError("network down")

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(vector, "_index", VectorIndex(broken))
    assert vector.search("고령자", "credit-loan-ops", "v4") == reg.search("고령자", "credit-loan-ops", "v4")


def test_out_of_scope_is_not_hidden_by_fallback(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(vector, "_index", VectorIndex(FakeEmbedder()))
    with pytest.raises(RegulationError):
        vector.search("정답", "../../eval", "answer_key")
