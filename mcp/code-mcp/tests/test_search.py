import pytest

from code_mcp import search as code
from code_mcp.repo import RepoError


def by_loc(hits):
    return {f"{h['file']}:{h['line']}": h for h in hits}


def test_search_matches_agent_mock_shape():
    hits = by_loc(code.search_code("age >= 65"))
    assert hits["src/loan/eligibility.py:21"] == {
        "file": "src/loan/eligibility.py", "line": 21, "symbol": "is_senior",
        "snippet": "return age >= 65", "kind": "code",
    }


def test_search_finds_all_mock_locations_and_kinds():
    hits = by_loc(code.search_code(r"65|64|780|0\.4|40%", regex=True))
    expected = {
        "config/policy.yaml:3": ("DSR_LIMIT", "config"),
        "config/policy.yaml:7": ("SENIOR_AGE", "config"),
        "src/loan/eligibility.py:8": ("SENIOR_AGE_MONTHS", "code"),
        "src/loan/eligibility.py:21": ("is_senior", "code"),
        "src/loan/rate.py:34": ("senior_discount", "code"),
        "src/loan/income.py:3": ("RETIREMENT_AGE", "code"),
        "src/loan/ltv.py:5": ("LTV_LIMIT", "code"),
        "src/batch/limit_job.py:12": ("recalc_limits", "code"),
        "frontend/src/views/LoanApply.vue:48": ("dsr-notice", "ui"),
    }
    for loc, (symbol, kind) in expected.items():
        assert (hits[loc]["symbol"], hits[loc]["kind"]) == (symbol, kind), loc


def test_literal_query_escapes_regex():
    files = {h["file"] for h in code.search_code("0.40")}
    assert files == {"config/policy.yaml", "src/loan/ltv.py"}


def test_path_glob_limits_scope():
    assert {h["file"] for h in code.search_code("65", path_glob="config/*.yaml")} == {"config/policy.yaml"}


def test_find_usages_is_identifier_exact():
    assert [h["line"] for h in code.find_usages("SENIOR_AGE")] == [7]
    assert {h["line"] for h in code.find_usages("SENIOR_AGE_MONTHS")} == {8, 25}


@pytest.mark.parametrize("path", ["../../eval/answer_key.md", "../../docs/PROJECT.md", "/etc/passwd", ""])
def test_get_file_is_limited_to_sample_repo(path):
    with pytest.raises(RepoError):
        code.get_file(path)


def test_glob_cannot_escape():
    with pytest.raises(RepoError):
        code.search_code("정답", path_glob="../../eval/*")


def test_symlink_out_of_repo_is_invisible(tmp_path):
    (tmp_path / "answer.md").write_text("RETIREMENT_AGE 함정\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "leak.md").symlink_to(tmp_path / "answer.md")
    assert code.search_code("함정", root=repo) == []
    with pytest.raises(RepoError):
        code.get_file("leak.md", root=repo)


def test_bad_regex_is_reported():
    with pytest.raises(RepoError, match="정규식"):
        code.search_code("(", regex=True)
