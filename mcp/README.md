# Rule Ripple · MCP 서버

Agent가 쓰는 도구 서버 3개. 설계는 [docs/PROJECT.md](../docs/PROJECT.md) 7절, 백엔드 계약은 [docs/BACKEND_API.md](../docs/BACKEND_API.md) 7 · 9절.

| 서버 | 주소 (compose 안) | 볼 수 있는 것 |
|---|---|---|
| regulation-mcp | `http://regulation-mcp:8001/mcp` | `data/regulations/{id}/v{N}.md` 만 |
| code-mcp | `http://code-mcp:8002/mcp` | 샘플 레포(`SAMPLE_REPO_DIR`) 만 |
| business-mcp | `http://business-mcp:8003/mcp` | Spring `/internal/**` 응답 만 |

`eval/` · `docs/` 는 어느 서버도 읽지 못한다. 막는 곳은 두 겹이다.

1. 코드: 모든 경로 입력을 허용 폴더 기준으로 검사한다 (`../` · 절대 경로 · 바깥 심볼릭 링크 · `path_glob` 거절, 규정은 `{id}/v{N}.md` 형식만).
2. compose: 각 서버에 필요한 폴더만 읽기 전용으로 마운트한다. 컨테이너 안에는 `eval/` · `docs/` 가 아예 없다.

`check_patch` 는 Agent가 보낸 코드(패치 · 테스트)를 pytest로 **실행**하므로 1번으로는 막을 수 없다 (절대 경로로 파일을 열 수 있음).
그래서 컨테이너 안에서만 돌고, 로컬에서는 `RR_ALLOW_LOCAL_SANDBOX=1` 일 때만 허용한다. **Agent와 연결할 때는 반드시 Docker로 띄운다.**

## 실행 · 테스트

```bash
# 전체 (루트에서)
docker compose up --build regulation-mcp code-mcp business-mcp

# 서버 하나 로컬 실행 (각 폴더에서)
cd mcp/code-mcp
uv sync
uv run python -m code_mcp.server            # HTTP :8002/mcp   (--stdio 도 가능)
uv run pytest -q
```

연결 예시 (Agent의 fastmcp Client):

```python
from fastmcp import Client
async with Client("http://code-mcp:8002/mcp") as c:
    hits = (await c.call_tool("search_code", {"query": "780"})).data
```

도구가 실패하면 `ToolError`(MCP `isError`)로 이유 한 줄을 돌려준다. 예: `백엔드 403 FORBIDDEN: 승인 시 제외된 위치입니다: [S2]`

## 도구

### regulation-mcp (읽기)

| 도구 | 입력 | 출력 |
|---|---|---|
| `diff_versions` | `regulation_id, old_version, new_version` | `{regulation_id, old_version, new_version, effective_from, changes: [{article_id, title, change, old_text, new_text, old_uri, new_uri}]}` |
| `search_regulation` | `query, regulation_id="credit-loan-ops", version="v4"` | `[{uri, article_id, title, text}]` |
| `get_article` | `regulation_id, version, article_id` | `{uri, article_id, title, text}` |
| `list_articles` | `regulation_id, version` | `[{uri, article_id, title}]` |
| Resource | `reg://{regulation_id}/{version}/{article_id}` | 조항 원문 (`reg://{id}/{version}` 은 규정 제목) |

- `change`: `added` · `removed` · `modified`. 표현만 바뀐 조항도 나온다 (실질 여부는 Agent가 판단).
- 조항 ID: `15`, `15-2`(제15조의2), `supp-2`(부칙 제2조). Agent `app/data.py` 와 같은 규칙.
- `search_regulation` 은 지금 키워드 검색(공백으로 나눈 단어 모두 포함). Vector DB가 준비되면 반환 형식은 그대로 두고 내부만 바꾼다.

### code-mcp

| 도구 | 성격 | 입력 | 출력 |
|---|---|---|---|
| `search_code` | 읽기 | `query, regex=False, path_glob="**/*", max_results=200` | `[{file, line, symbol, snippet, kind}]` |
| `get_file` | 읽기 | `path` | 파일 내용 (str) |
| `find_usages` | 읽기 | `symbol` | `[{file, line, symbol, snippet, kind}]` (식별자 정확히 일치) |
| `check_patch` | 샌드박스 | `patches: [{bundle_id, diff}], test_cases=[]` | `{results: [{bundle_id, ok, errors, files}], system: {ok, errors, missed_locations}, tests: {generated, skipped_without_code}}` |
| `create_draft_pr` | 쓰기 · 승인 | `task_id, approval_id, idempotency_key, title, patches, spec_keys=[]` | `{jobId, status, duplicate, branch, commit}` |

- `kind`: `code` · `config` · `ui` · `test`. `symbol`: 그 줄이 속한 함수 · 클래스 · 상수 · 설정 키.
- `diff` 형식: `"@@ 파일:줄 @@\n-원래 줄\n+새 줄"` (design 노드 형식, hunk 여러 개 가능) 또는 unified diff.
  줄은 앞뒤 공백을 무시하고 비교하며, 줄 번호가 어긋나도 같은 내용이 파일에 한 곳뿐이면 그 위치에 적용한다.
- `check_patch`: 샘플 레포 **복사본**에서 묶음별(적용 → 문법 → pytest) 검증 후, 통과한 묶음을 합쳐 통합 검증. 원본은 바뀌지 않는다.
  - pytest 환경 변수는 PATH 등 최소한만 넘긴다 (토큰 없음), 60초 제한.
  - `test_cases` 중 `code`(pytest 소스) 가 있는 것만 실행한다. `kind: system` 은 통합 검증에서만 돈다.
- `create_draft_pr`: 복사본에 브랜치 `rule-ripple/{task_id 앞 8자}` · 커밋을 준비 → 백엔드 `POST /internal/jobs` (`CREATE_DRAFT_PR`) 가 승인 검증 → **통과했을 때만** `PUBLISH_DIR` 에 남긴다. PR 링크는 `GET /internal/jobs/{jobId}` 결과에 있다.

### business-mcp

응답은 백엔드 그대로 (camelCase). 업무 규칙은 Spring 한 곳에만 두고 MCP에서 가공하지 않는다.

| 도구 | 성격 | Spring | 출력 |
|---|---|---|---|
| `get_products` | 읽기 | `GET /internal/products` | `[{productId, name, description, …}]` |
| `get_product_rules` | 읽기 | `GET /internal/product-rules?key=` · `GET /internal/products/{id}/rules` | `[{ruleId, productId, ruleKey, value, unit, effectiveFrom, effectiveTo}]` |
| `get_db_schema` | 읽기 | `GET /internal/db/schema` | `[{table, columns: [{name, type, nullable}]}]` |
| `get_contract_impact` | 읽기 | `GET /internal/contracts/impact` | `{ageFrom, ageTo, total, byProduct, grandfathered?, contracts}` |
| `create_work_ticket` | 쓰기 · 승인 | `POST /internal/work-tickets` | `{jobId, status, duplicate}` |

## Agent mock(`app/tools/mock.py`)과 다른 점

| 도구 | mock | 실제 |
|---|---|---|
| `get_product_rules` | snake_case + `product_name` | camelCase, 상품명은 `get_products` |
| `get_db_schema(table)` | `{table: [컬럼명]}` | 인자 없음, `[{table, columns}]` |
| `create_work_ticket` | `(title, body, approval_id, idempotency_key)` → `{url}` | `task_id` 필수, `approval_id` 는 숫자, `body` → `description`, 결과는 `{jobId, …}` |
| `create_draft_pr` | `(title, patches, approval_id, idempotency_key)` → `{url}` | `task_id` 필수, `spec_keys` 추가, 결과는 `{jobId, branch, commit, …}` |
| `check_patch` | `system.missed_locations` 로 놓친 위치를 알려줌 | 샘플 레포에 정답 정보가 없어 항상 `[]`. 대신 `system.ok` · `system.errors` 를 본다 |
| `read_resource` | 메서드 | MCP Resource (`client.read_resource`) 또는 `get_article` 도구 |
| (없음) | | `get_products`, `get_contract_impact`, `get_article`, `list_articles` 추가 |

## 남은 일

- [ ] 실제 샘플 코드 레포로 교체 (`mcp/sample_repo` 는 Agent mock 위치에 맞춘 임시본) → compose `SAMPLE_REPO_DIR` 마운트만 바꾸면 된다
- [ ] `search_regulation` 을 Vector DB 검색으로 교체
- [ ] test_agent가 실행 가능한 테스트(`code`)를 내도록 Agent와 합의
- [ ] 실제 백엔드와 연결 확인 (`docker compose up`)
