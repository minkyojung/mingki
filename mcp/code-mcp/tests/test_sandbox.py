import hashlib

import pytest

from code_mcp import sandbox
from code_mcp.repo import SAMPLE_REPO_DIR, iter_files
from code_mcp.sandbox import PatchError, check_patch

SENIOR = {"bundle_id": "B:src/loan/eligibility.py",
          "diff": "@@ src/loan/eligibility.py:21 @@\n-return age >= 65\n+return age >= 60"}
DSR_CONFIG = {"bundle_id": "B:config/policy.yaml",
              "diff": "@@ config/policy.yaml:3 @@\n-DSR_LIMIT: 0.40\n"
                      "+DSR_LIMIT: [{value: 0.40}, {value: 0.35, effective_from: 2026-12-01}]"}


def repo_fingerprint():
    h = hashlib.sha256()
    for p in iter_files():
        h.update(str(p).encode() + p.read_bytes())
    return h.hexdigest()


def test_valid_patches_pass_individually_and_together():
    res = check_patch([SENIOR, DSR_CONFIG], [])
    assert [r["ok"] for r in res["results"]] == [True, True]
    assert res["system"]["ok"] is True


def test_original_repo_is_never_modified():
    before = repo_fingerprint()
    check_patch([SENIOR, DSR_CONFIG], [])
    assert repo_fingerprint() == before


def test_changing_a_trap_breaks_regression_tests():
    trap = {"bundle_id": "B:income", "diff": "@@ src/loan/income.py:3 @@\n-RETIREMENT_AGE = 65\n+RETIREMENT_AGE = 60"}
    res = check_patch([trap], [])
    assert res["results"][0]["ok"] is False
    assert any("test_income" in e for e in res["results"][0]["errors"])


def test_syntax_error_is_reported_with_location():
    broken = {"bundle_id": "B:x", "diff": "@@ src/loan/eligibility.py:21 @@\n-return age >= 65\n+return age >=="}
    res = check_patch([broken], [])
    assert res["results"][0]["ok"] is False
    assert "eligibility.py:21: 문법 오류" in res["results"][0]["errors"][0]


def test_wrong_original_line_is_explained():
    stale = {"bundle_id": "B:x", "diff": "@@ src/loan/eligibility.py:21 @@\n-return age >= 70\n+return age >= 60"}
    err = check_patch([stale], [])["results"][0]["errors"][0]
    assert "원문이 다릅니다" in err and "return age >= 65" in err


def test_shifted_line_number_still_applies_when_unique():
    shifted = {"bundle_id": "B:x", "diff": "@@ src/loan/rate.py:30 @@\n-if age > 64:\n+if age >= 60:"}
    assert check_patch([shifted], [])["results"][0]["ok"] is True


def test_patch_outside_repo_is_rejected():
    escape = {"bundle_id": "B:x", "diff": "@@ ../../eval/answer_key.md:1 @@\n-x\n+y"}
    assert "밖" in check_patch([escape], [])["results"][0]["errors"][0]


def test_unified_diff_is_supported():
    diff = (
        "--- a/src/loan/rate.py\n+++ b/src/loan/rate.py\n"
        "@@ -33,3 +33,3 @@\n     # 신청일 기준 만 나이\n-    if age > 64:\n+    if age >= 60:\n         discount = 0.003\n"
    )
    assert check_patch([{"bundle_id": "B:u", "diff": diff}], [])["results"][0]["ok"] is True


def test_generated_tests_run_and_system_tests_only_in_integration():
    unit = {"id": "REQ-15-B-60", "kind": "boundary",
            "code": "from loan.eligibility import is_senior\n\ndef test_60():\n    assert is_senior(60)\n"}
    system = {"id": "REQ-15-SYS", "kind": "system",
              "code": "from loan.rate import senior_discount\n\ndef test_62_gets_discount():\n    assert senior_discount(62) > 0\n"}
    no_code = {"id": "REQ-8-B-0.35", "kind": "boundary"}

    res = check_patch([SENIOR], [unit, system, no_code])
    assert res["results"][0]["ok"] is True          # 개별: boundary 통과, system은 아직 안 돌림
    assert res["system"]["ok"] is False             # 통합: rate.py(age > 64)를 놓쳐서 실패
    assert any("test_62_gets_discount" in e for e in res["system"]["errors"])
    assert res["tests"] == {"generated": 2, "skipped_without_code": 1}


def test_multiple_hunks_in_one_file_apply_bottom_up():
    diff = ("@@ src/loan/eligibility.py:8 @@\n-SENIOR_AGE_MONTHS = 780\n+SENIOR_AGE_MONTHS = 720\n"
            "@@ src/loan/eligibility.py:21 @@\n-return age >= 65\n+return age >= 60")
    assert check_patch([{"bundle_id": "B:e", "diff": diff}], [])["results"][0]["ok"] is True


def test_sandbox_refuses_outside_container(monkeypatch, tmp_path):
    # Agent 테스트 코드가 절대 경로로 eval/answer_key.md 를 읽을 수 있으므로 로컬에서는 실행 자체를 막는다.
    monkeypatch.delenv("RR_ALLOW_LOCAL_SANDBOX")
    monkeypatch.setattr(sandbox, "CONTAINER_MARKERS", (tmp_path / "no-dockerenv",))
    with pytest.raises(PatchError, match="Docker"):
        check_patch([SENIOR], [])


def test_sandbox_runs_inside_container(monkeypatch, tmp_path):
    monkeypatch.delenv("RR_ALLOW_LOCAL_SANDBOX")
    (tmp_path / ".dockerenv").touch()
    monkeypatch.setattr(sandbox, "CONTAINER_MARKERS", (tmp_path / ".dockerenv",))
    assert check_patch([SENIOR], [])["system"]["ok"] is True
