import pytest


@pytest.fixture(autouse=True)
def no_openai(monkeypatch):
    """테스트는 OpenAI를 호출하지 않는다. 의미 검색은 test_vector.py에서 가짜 임베딩으로 확인한다."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
