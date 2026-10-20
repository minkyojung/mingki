"""Draft PR용 브랜치 · 커밋 만들기.

백엔드 JobWorker는 PR 번호 · 링크만 만든다 (시연용 목). 브랜치와 커밋은 code-mcp가
샘플 레포 복사본(PUBLISH_DIR/{task_id})에 만든다. 원본 샘플 레포는 건드리지 않는다.
"""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from code_mcp.repo import SAMPLE_REPO_DIR, SKIP_DIRS
from code_mcp.sandbox import PatchError, apply_patch

PUBLISH_DIR = Path(os.environ.get("PUBLISH_DIR", Path(tempfile.gettempdir()) / "rule-ripple-publish"))
_GIT = ["git", "-c", "user.name=Rule Ripple", "-c", "user.email=rule-ripple@example.com", "-c", "commit.gpgsign=false"]


def branch_name(task_id: str) -> str:
    return "rule-ripple/" + (re.sub(r"[^A-Za-z0-9-]", "", task_id)[:8] or "task")


def workspace_for(task_id: str) -> Path:
    return PUBLISH_DIR / re.sub(r"[^A-Za-z0-9-]", "_", task_id)


def _git(repo: Path, *args: str) -> str:
    res = subprocess.run([*_GIT, *args], cwd=repo, text=True, capture_output=True, timeout=30)
    if res.returncode != 0:
        raise PatchError(f"git {args[0]} 실패: {res.stderr.strip()[:300]}")
    return res.stdout.strip()


def build_branch(task_id: str, title: str, patches: list[dict], root: Path = SAMPLE_REPO_DIR) -> dict:
    """임시 폴더에 기준 커밋 → 브랜치 → 패치 커밋을 만든다. 아직 PUBLISH_DIR로 옮기지 않는다."""
    branch = branch_name(task_id)
    tmp = Path(tempfile.mkdtemp(prefix="rr-pr-"))
    repo = tmp / "repo"
    try:
        shutil.copytree(root, repo, ignore=shutil.ignore_patterns(*SKIP_DIRS), symlinks=True)
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "base: sample repo")
        _git(repo, "checkout", "-q", "-b", branch)
        files: list[str] = []
        for p in patches:
            files += apply_patch(repo, p.get("diff", ""))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", title)
        return {"branch": branch, "commit": _git(repo, "rev-parse", "HEAD"), "files": sorted(set(files)), "_tmp": tmp}
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def keep(task_id: str, built: dict) -> None:
    """승인 검증을 통과한 뒤에만 PUBLISH_DIR에 남긴다."""
    dest = workspace_for(task_id)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(built["_tmp"] / "repo"), dest)
    shutil.rmtree(built["_tmp"], ignore_errors=True)


def discard(built: dict) -> None:
    shutil.rmtree(built["_tmp"], ignore_errors=True)


def existing(task_id: str) -> dict | None:
    repo = workspace_for(task_id)
    if not (repo / ".git").is_dir():
        return None
    return {"branch": _git(repo, "rev-parse", "--abbrev-ref", "HEAD"), "commit": _git(repo, "rev-parse", "HEAD")}
