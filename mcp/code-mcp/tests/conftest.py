import pytest


@pytest.fixture(autouse=True)
def allow_local_sandbox(monkeypatch):
    # 테스트는 로컬에서 샌드박스를 돌린다. 막히는지 보는 테스트는 이 값을 지운다.
    monkeypatch.setenv("RR_ALLOW_LOCAL_SANDBOX", "1")
