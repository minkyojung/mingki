"""check_patch: 샘플 레포 복사본(샌드박스)에 패치 적용 → 문법 → 테스트. 원본 레포는 건드리지 않는다.

패치(diff) 형식 두 가지를 받는다.
  1. 위치 지정 형식 (Agent design 노드):  "@@ src/loan/eligibility.py:21 @@\n-return age >= 65\n+return age >= 60"
     - 줄은 앞뒤 공백을 무시하고 비교하고, 새 줄에는 원래 줄의 들여쓰기를 붙인다.
     - 지정한 줄이 다르면 파일 안에서 같은 내용이 딱 한 곳일 때만 그 위치에 적용한다.
  2. unified diff (--- a/… +++ b/…): git apply 로 적용한다.

테스트: 샌드박스에서 pytest. test_cases 중 "code"(pytest 소스)가 있는 것만 실행 파일로 만들고,
나머지는 실행할 수 없어서 건너뛴 수로만 알린다.

격리: pytest는 Agent가 보낸 코드를 실제로 실행하므로 경로 검사로는 eval/ · docs/ 를 막을 수 없다.
그래서 컨테이너 안(레포 전체가 없는 곳)에서만 돌리고, 로컬은 RR_ALLOW_LOCAL_SANDBOX=1 일 때만 허용한다.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import yaml

from code_mcp.repo import SAMPLE_REPO_DIR, SKIP_DIRS, RepoError, safe_path

TEST_TIMEOUT_SEC = 60
CONTAINER_MARKERS = (Path("/.dockerenv"), Path("/run/.containerenv"))  # Docker · Podman
_HUNK = re.compile(r"^@@ (.+?):(\d+) @@\s*$")


class PatchError(Exception):
    pass


@contextmanager
def sandbox(root: Path = SAMPLE_REPO_DIR):
    tmp = Path(tempfile.mkdtemp(prefix="rr-sandbox-"))
    try:
        repo = tmp / "repo"
        shutil.copytree(root, repo, ignore=shutil.ignore_patterns(*SKIP_DIRS), symlinks=True)
        yield repo
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 패치 적용 ----------

def _is_unified(diff: str) -> bool:
    return bool(re.search(r"^--- ", diff, re.M) and re.search(r"^\+\+\+ ", diff, re.M))


def _parse_located(diff: str) -> list[tuple[str, int, list[str], list[str]]]:
    hunks, cur = [], None
    for raw in diff.splitlines():
        if m := _HUNK.match(raw):
            cur = (m[1].strip(), int(m[2]), [], [])
            hunks.append(cur)
        elif cur is None:
            if raw.strip():
                raise PatchError(f"diff가 '@@ 파일:줄 @@' 로 시작하지 않습니다: {raw[:80]!r}")
        elif raw.startswith("-"):
            cur[2].append(raw[1:])
        elif raw.startswith("+"):
            cur[3].append(raw[1:])
        elif raw.strip():
            raise PatchError(f"{cur[0]}:{cur[1]} 의 줄은 '-' 또는 '+' 로 시작해야 합니다: {raw[:80]!r}")
    if not hunks:
        raise PatchError("적용할 hunk가 없습니다")
    return hunks


def _apply_located(repo: Path, diff: str) -> list[str]:
    hunks = _parse_located(diff)
    by_file: dict[str, list] = {}
    for h in hunks:
        by_file.setdefault(h[0], []).append(h)

    for file, file_hunks in by_file.items():
        try:
            path = safe_path(file, repo)
        except RepoError as e:
            raise PatchError(str(e)) from e
        if not path.exists():
            if any(old for _, _, old, _ in file_hunks):
                raise PatchError(f"{file}: 파일이 샘플 레포에 없습니다 (코드 · 설정 파일만 패치할 수 있습니다)")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(l for *_, new in file_hunks for l in new) + "\n", encoding="utf-8")
            continue

        lines = path.read_text(encoding="utf-8").splitlines()
        # 아래쪽 hunk부터 적용해야 위쪽 줄 번호가 밀리지 않는다
        for _, line, old, new in sorted(file_hunks, key=lambda h: h[1], reverse=True):
            start = _locate(lines, file, line, old)
            indent = re.match(r"\s*", lines[start])[0] if old and start < len(lines) else ""
            new_lines = [l if l[:1].isspace() else indent + l for l in new]
            lines[start:start + len(old)] = new_lines
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return list(by_file)


def _locate(lines: list[str], file: str, line: int, old: list[str]) -> int:
    """old 블록이 시작하는 0-based 인덱스. old가 없으면 line 뒤에 삽입."""
    if not old:
        return min(line, len(lines))
    want = [o.strip() for o in old]

    def matches(i: int) -> bool:
        return [l.strip() for l in lines[i:i + len(old)]] == want

    if 1 <= line <= len(lines) and matches(line - 1):
        return line - 1
    found = [i for i in range(len(lines)) if matches(i)]
    if len(found) == 1:
        return found[0]
    actual = lines[line - 1].strip() if 1 <= line <= len(lines) else "(파일 끝 너머)"
    if not found:
        raise PatchError(f"{file}:{line} 원문이 다릅니다. 기대 {want[0]!r}, 실제 {actual!r}")
    raise PatchError(f"{file}:{line} 원문이 다르고, 같은 내용이 {len(found)}곳이라 위치를 정할 수 없습니다")


def _apply_unified(repo: Path, diff: str) -> list[str]:
    files = re.findall(r"^\+\+\+ (?:b/)?(\S+)", diff, re.M)
    for f in files:
        if f != "/dev/null":
            try:
                safe_path(f, repo)
            except RepoError as e:
                raise PatchError(str(e)) from e
    strip = "-p1" if re.search(r"^\+\+\+ b/", diff, re.M) else "-p0"
    try:
        res = subprocess.run(
            ["git", "apply", strip, "--whitespace=nowarn", "-"],
            cwd=repo, input=diff, text=True, capture_output=True, timeout=30,
        )
    except FileNotFoundError as e:
        raise PatchError("unified diff를 적용하려면 git이 필요합니다") from e
    if res.returncode != 0:
        raise PatchError(f"git apply 실패: {res.stderr.strip()[:500]}")
    return [f for f in files if f != "/dev/null"]


def apply_patch(repo: Path, diff: str) -> list[str]:
    """diff를 샌드박스 repo에 적용하고 바뀐 파일 목록을 돌려준다."""
    if not diff or not diff.strip():
        raise PatchError("diff가 비어 있습니다")
    return _apply_unified(repo, diff) if _is_unified(diff) else _apply_located(repo, diff)


# ---------- 문법 ----------

def syntax_errors(repo: Path, files: list[str]) -> list[str]:
    errors = []
    for f in files:
        path = repo / f
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        try:
            if path.suffix == ".py":
                compile(text, f, "exec")
            elif path.suffix in {".yaml", ".yml"}:
                yaml.safe_load(text)
            elif path.suffix == ".json":
                json.loads(text)
        except SyntaxError as e:
            errors.append(f"{f}:{e.lineno}: 문법 오류 {e.msg}")
        except (yaml.YAMLError, json.JSONDecodeError) as e:
            errors.append(f"{f}: 형식 오류 {str(e).splitlines()[0]}")
    return errors


# ---------- 테스트 ----------

def _slug(s: str) -> str:
    return re.sub(r"\W+", "_", s).strip("_").lower() or "case"


def write_generated_tests(repo: Path, test_cases: list[dict], kinds: set[str] | None = None) -> int:
    """code가 있는 test_case를 tests/rr_generated/test_*.py로 쓴다. 쓴 개수."""
    out = repo / "tests" / "rr_generated"
    n = 0
    for tc in test_cases:
        if not tc.get("code") or (kinds is not None and tc.get("kind") not in kinds):
            continue
        out.mkdir(parents=True, exist_ok=True)
        (out / f"test_{_slug(str(tc.get('id', n)))}.py").write_text(tc["code"], encoding="utf-8")
        n += 1
    return n


def run_tests(repo: Path) -> tuple[bool, list[str]]:
    """pytest 실행. 비밀값 없는 최소 환경 변수, 시간 제한. (통과 여부, 실패 요약)"""
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(repo),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", "-rf", "--tb=line"],
            cwd=repo, env=env, text=True, capture_output=True, timeout=TEST_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, [f"테스트가 {TEST_TIMEOUT_SEC}초 안에 끝나지 않았습니다"]
    if res.returncode in (0, 5):  # 5 = 수집된 테스트 없음
        return True, []
    lines = [l for l in res.stdout.splitlines() if l.startswith(("FAILED", "ERROR", "E ")) or ".py:" in l]
    return False, (lines or res.stdout.splitlines()[-15:] or [res.stderr.strip()[:500]])[:20]


# ---------- check_patch ----------

def require_isolation() -> None:
    """컨테이너 밖에서는 Agent 코드를 실행하지 않는다 (로컬 파일시스템 전체가 보이므로)."""
    if any(m.exists() for m in CONTAINER_MARKERS) or os.environ.get("RR_ALLOW_LOCAL_SANDBOX") == "1":
        return
    raise PatchError(
        "check_patch는 Docker 안에서만 실행합니다 (docker compose up code-mcp). "
        "로컬 개발용으로는 RR_ALLOW_LOCAL_SANDBOX=1"
    )


def check_patch(patches: list[dict], test_cases: list[dict], root: Path = SAMPLE_REPO_DIR) -> dict:
    """묶음별(개별) 검증 + 통과한 묶음을 모두 합친 통합 검증."""
    require_isolation()
    results, applicable = [], []
    for p in patches:
        bundle_id = p.get("bundle_id") or "?"
        with sandbox(root) as repo:
            try:
                changed = apply_patch(repo, p.get("diff", ""))
            except PatchError as e:
                results.append({"bundle_id": bundle_id, "ok": False, "errors": [f"패치 적용 실패: {e}"]})
                continue
            errors = syntax_errors(repo, changed)
            if not errors:
                write_generated_tests(repo, test_cases, kinds={"unit", "boundary", "effective_date"})
                ok, failures = run_tests(repo)
                errors = [] if ok else [f"테스트 실패: {f}" for f in failures]
            results.append({"bundle_id": bundle_id, "ok": not errors, "errors": errors, "files": changed})
            if not errors:
                applicable.append(p)

    with sandbox(root) as repo:
        integration_errors = []
        for p in applicable:
            try:
                apply_patch(repo, p["diff"])
            except PatchError as e:
                integration_errors.append(f"{p.get('bundle_id')}: 통합 적용 실패 (다른 묶음과 충돌): {e}")
        generated = write_generated_tests(repo, test_cases)
        ok, failures = run_tests(repo)
        integration_errors += [f"테스트 실패: {f}" for f in failures] if not ok else []

    return {
        "results": results,
        "system": {
            "ok": not integration_errors,
            "errors": integration_errors,
            # 샘플 레포에 정답 위치 정보가 없으므로 어디를 놓쳤는지는 알 수 없다. 실패 내용은 errors에 있다.
            "missed_locations": [],
        },
        "tests": {
            "generated": generated,
            "skipped_without_code": sum(1 for tc in test_cases if not tc.get("code")),
        },
    }
