"""코드 검색 · 파일 읽기 · 사용처 찾기. 결과 한 줄 형식은 Agent mock(app/tools/mock.py)과 같다.

    {"file", "line", "symbol", "snippet", "kind"}
"""

import ast
import re
from functools import lru_cache
from pathlib import Path

from code_mcp.repo import SAMPLE_REPO_DIR, RepoError, iter_files, read_text, rel, safe_path

CONFIG_EXT = {".yaml", ".yml", ".json", ".toml", ".ini", ".properties", ".env"}
UI_EXT = {".vue", ".html", ".tsx", ".jsx", ".css", ".scss"}
MAX_RESULTS = 200


def kind_of(path: str) -> str:
    p = Path(path)
    if "tests" in p.parts or p.name.startswith("test_"):
        return "test"
    if p.suffix in CONFIG_EXT:
        return "config"
    if p.suffix in UI_EXT:
        return "ui"
    return "code"


@lru_cache(maxsize=256)
def _python_symbols(text: str) -> tuple[tuple[int, int, str], ...]:
    """(시작 줄, 끝 줄, 이름): 함수 · 클래스 · 모듈 수준 상수."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ()
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            spans.append((node.lineno, node.end_lineno or node.lineno, node.name))
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        for t in targets:
            if isinstance(t, ast.Name):
                spans.append((node.lineno, node.end_lineno or node.lineno, t.id))
    return tuple(spans)


_YAML_KEY = re.compile(r"^([A-Za-z_][\w.-]*)\s*:")
_UI_NAME = re.compile(r'(?:class|id|name)="([\w-]+)')


def symbol_at(path: str, lines: list[str], lineno: int, text: str) -> str | None:
    """그 줄이 속한 가장 안쪽 함수 · 클래스 · 상수 · 설정 키 이름."""
    suffix = Path(path).suffix
    if suffix == ".py":
        inside = [s for s in _python_symbols(text) if s[0] <= lineno <= s[1]]
        return min(inside, key=lambda s: s[1] - s[0])[2] if inside else None
    if suffix in {".yaml", ".yml"}:
        for line in reversed(lines[:lineno]):
            if m := _YAML_KEY.match(line):
                return m[1]
        return None
    if suffix in UI_EXT:
        for line in reversed(lines[max(0, lineno - 6):lineno]):
            if m := _UI_NAME.search(line):
                return m[1]
    return None


def _scan(pattern: re.Pattern, root: Path, glob: str, max_results: int) -> list[dict]:
    hits = []
    for path in iter_files(root, glob):
        text = read_text(path)
        if text is None:
            continue
        lines = text.splitlines()
        file = rel(path, root)
        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                hits.append({
                    "file": file,
                    "line": i,
                    "symbol": symbol_at(file, lines, i, text),
                    "snippet": line.strip(),
                    "kind": kind_of(file),
                })
                if len(hits) >= max_results:
                    return hits
    return hits


def search_code(
    query: str,
    regex: bool = False,
    path_glob: str = "**/*",
    max_results: int = MAX_RESULTS,
    root: Path = SAMPLE_REPO_DIR,
) -> list[dict]:
    if not query:
        raise RepoError("query가 비어 있습니다")
    if ".." in Path(path_glob).parts or Path(path_glob).is_absolute():
        raise RepoError(f"path_glob은 레포 안 상대 패턴이어야 합니다: {path_glob}")
    try:
        pattern = re.compile(query if regex else re.escape(query))
    except re.error as e:
        raise RepoError(f"정규식 오류: {e}") from e
    return _scan(pattern, root, path_glob, min(max_results, MAX_RESULTS))


def find_usages(symbol: str, root: Path = SAMPLE_REPO_DIR) -> list[dict]:
    """식별자 단위로 정확히 일치하는 곳 (정의 포함). SENIOR_AGE는 SENIOR_AGE_MONTHS와 구분된다."""
    if not re.fullmatch(r"[A-Za-z_][\w-]*", symbol or ""):
        raise RepoError(f"식별자 하나를 주세요: {symbol!r} (예: is_senior, SENIOR_AGE)")
    return _scan(re.compile(rf"(?<![\w-]){re.escape(symbol)}(?![\w-])"), root, "**/*", MAX_RESULTS)


def get_file(path: str, root: Path = SAMPLE_REPO_DIR) -> str:
    target = safe_path(path, root)
    if not target.is_file():
        raise RepoError(f"파일이 없습니다: {path}")
    text = read_text(target)
    if text is None:
        raise RepoError(f"텍스트 파일이 아닙니다: {path}")
    return text
