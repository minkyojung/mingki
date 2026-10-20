"""샘플 레포 접근 범위. code-mcp가 읽는 파일은 전부 여기를 거친다.

허용 범위: SAMPLE_REPO_DIR 아래 파일만. ../ · 절대 경로 · 바깥을 가리키는 심볼릭 링크는 거절한다.
"""

import os
from collections.abc import Iterator
from pathlib import Path

def _default_dir() -> Path:
    # 로컬: mcp/code-mcp/code_mcp/ → mcp/sample_repo. Docker에서는 환경 변수로 준다.
    return Path(__file__).resolve().parents[2] / "sample_repo"


SAMPLE_REPO_DIR = Path(os.environ.get("SAMPLE_REPO_DIR") or _default_dir()).resolve()

SKIP_DIRS = frozenset({".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"})
MAX_FILE_BYTES = 512_000


class RepoError(ValueError):
    """Agent에게 그대로 보여줄 수 있는 입력 오류."""


def safe_path(rel: str, root: Path = SAMPLE_REPO_DIR) -> Path:
    """레포 기준 상대 경로 → 실제 경로. 레포 밖이면 RepoError."""
    if not rel or Path(rel).is_absolute() or "\x00" in rel:
        raise RepoError(f"레포 기준 상대 경로가 필요합니다: {rel!r} (예: src/loan/eligibility.py)")
    root = root.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or SKIP_DIRS.intersection(path.relative_to(root).parts):
        raise RepoError(f"허용된 샘플 레포 밖입니다: {rel}")
    return path


def rel(path: Path, root: Path = SAMPLE_REPO_DIR) -> str:
    return path.relative_to(root.resolve()).as_posix()


def iter_files(root: Path = SAMPLE_REPO_DIR, glob: str = "**/*") -> Iterator[Path]:
    """검색 대상 텍스트 파일 (정렬, 레포 밖 링크 · 큰 파일 제외)."""
    root = root.resolve()
    for path in sorted(root.glob(glob)):
        if not path.is_file() or SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        if not path.resolve().is_relative_to(root) or path.stat().st_size > MAX_FILE_BYTES:
            continue
        yield path


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # 바이너리
