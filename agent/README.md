# Rule Ripple Agent

법령 개정을 분석해 상품별 변경 제안을 만들고, 검증 후 사람이 검토한다.

```text
요건 분석 → 상품 영향 판단 → 제안 작성 → 검증 → 사람 검토
                              ↑ 보완·수정 요청 ┘       ├─ CLI: 보고서
                                                     └─ 백엔드 승인: apply → 보고서
```

## 실행

`agent/.env`에 `OPENAI_API_KEY`와 `OPENAI_MODEL`을 설정하고 `agent/`에서 실행한다.
설정 예시는 `.env.example`을 참고한다. CLI는 이 `.env`를 기존 환경변수보다 우선한다.

```bash
uv sync
uv run python run.py --article 8                 # LLM + 로컬 자료, DSR 변경
uv run python run.py                             # 전체 변경 조항
uv run python run.py --mode live --article 8      # LLM + 규정·업무 MCP
uv run pytest -q
```

두 모드 모두 실제 LLM을 사용한다. 기본값은 `llm`이다.
로컬 자료는 `data/`의 규정·상품 규칙·상품 문서이며, `live`는 MCP가 반환한 자료만 사용한다.
MCP 주소는 `REGULATION_MCP_URL`, `BUSINESS_MCP_URL`로 설정한다.
규칙은 개정 시행일 전날에 유효한 행을 사용한다. `--verbose`로 요청 로그를 확인한다.

## 검토

| 명령 | 기능 |
|---|---|
| `v 3` / `k` | 제안 상세 / 유지 판단 근거 |
| `p 1,3` / `r 2` | 선택 승인 / 제외 |
| `c 2` / `e 2` | 구체적 의견으로 재작성 / 문구 직접 수정 |
| `a` / `f` | 남은 항목 전체 검토 후 완료 / 이미 결정한 검토 완료 |
| `?` | 검토 명령 도움말 |

승인 항목은 숨기고 번호는 유지한다. 수정한 항목만 새 버전으로 검토한다.
검증은 값·단위·시행일·출처·인용문을 검사하고, 실패한 항목은 한 번 보완한다.
법령의 적용 범위와 예외 판단은 사람이 근거와 함께 확인한다.
CLI 승인만으로 실제 DB를 변경하지 않는다.

`a`는 남은 항목이 모두 승인 가능하면 승인 후 종료한다. 승인 불가 항목이 있으면 번호·상품·사유를
표시하고, 해당 항목을 제외한 나머지 승인으로 완료할지 한 번 확인한다. `y`에 동의해야 제외·승인을
함께 반영하며, 빈 입력·`n`은 전체 명령을 취소하고 기존 상태를 유지한다. 실패 항목의 재작성은 선택이다.
`p 번호`는 실패 항목을 승인하지 않으며, 이미 승인한 항목만 다시 선택하면 중복 기록을 만들지 않는다.
생성 결과의 본문이 비어 있어도 제외·거절하거나 재작성을 요청할 수 있다. 빈 본문 승인은 계속 차단한다.

`c 번호` 또는 `e 번호` 처리 뒤에는 해당 제안의 변경 전후를 자동으로 보여준다.
재작성 결과는 `revised`(본문 수정·검증), `cannot_apply`(근거와 충돌해 미반영),
`needs_clarification`(의견·자료·영향 재분석 필요), `failed`(작성·검증 실패)로 구분한다.
반영 불가·추가 확인은 기존 본문과 기존 검증 상태를 유지하고, 작성·검증 실패는 사유를 표시하며 승인을 막는다.

CLI는 실행별 메모리 체크포인터를 사용한다. CLI에서 수정한 제안은 백엔드 작업에 저장되지 않으며,
`scripts/smoke.sh`가 만드는 별도 작업으로 이어지지 않는다. 종료 후 동일 상태를 복구하는 CLI 기능은 없다.

## 백엔드 반영 연결

- `AgentRunner.resume_backend()`는 `APPROVE`와 승인·제외 목록을 내부 검토 결정으로 변환한다.
- `proposal_items()` / `proposal_rows()`는 제안을 상품 × 규칙 행으로 변환한다. 반영 키는 `상품ID:규칙키`다.
- `apply`는 검증·승인된 규칙에 `apply_rule_version(task_id, approval_id, proposal_key, idempotency_key)`을 호출한다. 변경 값은 보내지 않는다.
- 멱등키는 `{task_id}:apply:{proposal_key}`로 고정한다. 작업 ID와 결과는 보고서에 남긴다.
- 큐 접수 후 `QUEUED`·`RUNNING`·`RETRYING`을 조회하며, 전부 `DONE`일 때만 완료다. `DEAD`·시간 초과·조회 실패는 `FAILED`다.
- 완료 조회는 `build_graph(..., job_reader=spring.job)`로 연결한다. `spring.job`은 `GET /internal/jobs/{jobId}`를 호출하는 백엔드 어댑터 함수다. 기본 대기는 60초, 조회 간격은 1초다.

실제 반영에는 백엔드에 저장된 제안·유효한 승인 ID·MCP 쓰기 도구·작업 조회 연결이 모두 필요하다.
현재 business-mcp의 `apply_rule_version`, HTTP 서버·Spring 콜백·Postgres 체크포인터 연결은 구현되어 있다.
Docker 시연은 이 경로로 승인한 규칙을 실제 반영한다. HTTP의 `AGENT_CHECKPOINT_DB`가 없으면 메모리 체크포인터를 사용한다.
Agent의 HTTP 어댑터는 승인 대상의 검증 상태·본문·수행 항목도 접수 전에 확인해, 승인 불가 항목과 사유를 400으로 반환한다.
현재 Spring의 `canApprove`는 제외하기 전 모든 변경 제안의 통과를 요구한다. 이 정책은 유지하며,
실패 항목을 제외한 부분 반영까지 지원하려면 검사 대상을 승인 목록으로 한정하는 것이 맞는지 백엔드 담당과 검토한다.
이번 변경은 백엔드 코드와 승인·거절 API를 변경하지 않는다.

## 파일

| 위치 | 기능 |
|---|---|
| `run.py`, `app/cli.py` | CLI 실행·검토 화면 |
| `app/graph.py`, `app/state.py`, `app/runner.py` | 그래프·상태·시작·재개 |
| `app/nodes/` | 요건·영향·제안·검증·검토·반영·보고 |
| `app/llm.py`, `prompts/` | 구조화된 LLM 호출 |
| `app/validation.py` | 제안·입력 검증 |
| `app/tools/` | 로컬 자료·MCP 조회 및 반영 호출 |
| `app/backend.py`, `app/schemas.py` | 백엔드 변환·데이터 모델 |

현재 조회 순서는 코드가 정한다. 모델이 MCP 도구를 자유롭게 선택하는 루프는 아직 없다.
`live`는 `REGULATION_MCP_URL`, `BUSINESS_MCP_URL`을 쓴다. code-mcp는 사용하지 않는다.
live는 최신 MCP의 `list_regulations` → `list_articles` · `get_article`, `get_product_doc`, `get_products`, `get_product_rules(as_of=기준일)`을 호출한다.
관련 규정은 개정 시행일에 유효한 버전을 고르고, 상품 문서는 서버가 준 `doc://상품#섹션` URI를 인용 근거로 유지한다.
시작할 때 서버 도구 목록을 검사한다. 구버전 MCP면 최신 main에서 서버를 다시 실행하라는 오류를 낸다.
문서가 없으면 자료 누락으로 표시하며 로컬 자료로 대체하지 않는다. 백엔드 DB는 Git의 data 파일을 받는 것만으로 갱신되지 않는다.

Docker를 사용한다면 최신 main을 받은 뒤 규정·업무 MCP 이미지를 다시 빌드한다. 서버 실행 설정은 [MCP 안내](../mcp/README.md)를 참고한다.

## 백엔드 연결 (HTTP 어댑터)

Spring이 `/run` · `/resume`을 부르면 바로 202를 돌려주고 그래프는 백그라운드에서 돈다. 진행 상황과 산출물은 `callbackBaseUrl` 아래 `/internal/tasks/{taskId}/**`로 보낸다. 계약은 `docs/BACKEND_API.md` 10절.

```bash
AGENT_MODE=llm AGENT_CHECKPOINT_DB=postgresql://ruleripple:ruleripple@localhost:5432/ruleripple \
uv run uvicorn app.server:create_app --factory --port 8000
```

| 변수 | 기본값 | 설명 |
|---|---|---|
| `AGENT_MODE` | `llm` | `llm`(로컬 자료) · `live`(MCP). 둘 다 실제 LLM 키 필요 |
| `AGENT_CHECKPOINT_DB` | 없음 | 있으면 Postgres 체크포인터(`agent` 스키마). 승인 대기 중 재시작해도 재개된다 |
| `INTERNAL_TOKEN` | `rr-internal-dev-token` | Spring과 주고받는 `X-Internal-Token`. 기본값은 공개된 개발용이라 운영에서는 반드시 바꾼다 (기본값이면 시작할 때 경고) |
| `REGULATION_ID` | `credit-loan-ops` | 분석할 등록 규정. 업로드 원문 sha256이 로컬 원문과 다르면 FAILED |
| `AGENT_ARTICLE_IDS` | 없음 (전부) | 분석할 조항 제한, 쉼표 구분 (예: `8` = DSR 제8조만). M1 · M2 시연용 |

| 파일 | 역할 |
|---|---|
| `app/server.py` | FastAPI `/run` · `/resume` · `/continue` · `/health`, 토큰 검사 |
| `app/bridge.py` | 노드 시작 · 완료를 Spring 콜백으로. 백엔드 확인을 제안별 결정 + `apply` 승인으로 바꾼다 |
| `app/backend.py` | Agent 제안(상품 × 요건, `changes[]`)을 백엔드 행(상품 × 규칙)으로 펼친다. 유지 판정은 `keep` 행 |
| `app/spring.py` | Spring `/internal/**` 클라이언트. 연결 실패 · 5xx 는 짧게 재시도, 4xx 는 바로 실패 |
| `app/checkpoint.py` | Postgres 체크포인터 (연결 풀, 끊긴 연결은 꺼낼 때 검사해 다시 맺는다) |

백엔드의 `APPROVE + approvedProposalKeys · excludedProposalKeys`를 제안별 `approve` · `reject` + `finish`로 바꾸고, `approvalId`와 반영 키를 `apply_approval`로 넘긴다. 승인 · 제외 키는 Agent가 보낸 현재 변경 제안 행과 정확히 같아야 한다(아니면 400). 검토 대기 중이 아닌 thread는 409. 직접 편집은 CLI 전용이다.

### 개별 수정 요청 (10/22 백엔드 확정안)

Agent `/resume`은 아래 요청도 받는다. 백엔드가 작업을 `DESIGNING`으로 옮긴 뒤 같은 thread로 보내며, 수정 접수·조회 API와 요청 이력은 백엔드가 관리한다.

```json
{
  "taskId": "기존 작업 ID",
  "threadId": "기존 thread ID",
  "step": "REVIEW",
  "decision": "REQUEST_CHANGES",
  "comment": "기존 대출 연장은 종전 기준이라는 안내를 조치에 추가해줘",
  "payload": {"proposalId": "REQ-8:P001", "baseVersion": 1},
  "callbackBaseUrl": "http://backend:8080/internal/tasks/기존작업ID"
}
```

- `proposalId`는 상품 × 요건의 Agent ID다. 반영 키(`P001:DSR_LIMIT`)와 다르다.
- 현재 제안·정수 기준 버전·구체적인 의견을 모델 실행 전에 검사한다. 잘못된 입력은 Agent에서 400이며 그래프를 재개하거나 실패 콜백을 보내지 않는다. 공개 수정 API의 상태·버전 충돌 409 검사는 백엔드 담당이다.
- 기존 `request_changes` 경로를 `finish=false`, `apply_approval=None`으로 재개한다. 판정·규칙·값·범위는 고정하고 선택 제안의 글만 재작성·검증한다. 다른 제안과 유지 판단은 보존한다.
- 결과는 기존 `PUT /proposals`로 보낸다. 각 행에 `version`, `revisionOutcome`, `revisionResponse`(없으면 null), `changedFields`를 추가한다. `round`는 해당 제안의 `attempt`다. 유지 판단은 `version=1, round=1`로 고정한다.
- `revised` · `cannot_apply` · `needs_clarification` · `failed` 모두 다시 `APPROVAL_REQUIRED`를 보내 검토를 기다린다. `revised`도 검증 통과를 확인해야 성공이다. 새 버전은 자동 승인·반영하지 않는다.
- 재개·`/continue`마다 체크포인트에서 Bridge의 유지 판단과 검증 회차를 복원한다. Agent가 `revisionId`를 결과 콜백으로 전달하는 별도 API는 없다.

이 Agent 변경은 Spring의 V8·수정 접수/조회·상태 전이·최신 버전 선택·부분 승인 정책 구현과 함께 연결해야 한다. 백엔드 계약 PR이 합쳐지기 전의 `docs/BACKEND_API.md`와 `docs/contracts/`에는 이전 계약이 남아 있을 수 있다. 백엔드 코드는 이 변경에 포함하지 않는다.

승인 뒤 `apply` 노드가 반영 키마다 business-mcp `apply_rule_version`을 부르고(멱등키 `{taskId}:apply:{proposalKey}`), 이 요청의 콜백 주소로 `/internal/jobs/{id}`를 `DONE` · `DEAD`까지 조회한다. 큐 조회 함수는 그래프가 아니라 재개할 때 `config["configurable"]["job_reader"]`로 넘긴다. 한 건이 접수에서 거절돼도 나머지 접수와 이미 접수된 작업의 확인은 계속하고, 조회가 잠깐 실패하면 다음 주기에 다시 조회한다(연속 3회면 포기). 반영 도중 Agent가 죽어 백엔드가 `PUBLISHING`에 멈추면 `POST /continue {taskId, threadId, callbackBaseUrl}`로 멈춘 노드부터 이어간다 — 같은 멱등키를 다시 쓰므로 큐 작업이 중복 생기지 않는다.

잠금은 thread(작업) 단위다. 한 작업의 LLM 호출이 다른 작업의 시작 · 재개를 막지 않는다.

판정 · 상태 대응: `no_change → keep`, `needs_review → needs_check`, 리포트 `DONE → DONE`, `FAILED → FAILED {reason}`, `NO_ACTION → NO_CHANGE`, `REJECTED → REJECTED`. 반영 없이 `APPROVED`로 끝나면 종료 상태가 아니라 `FAILED`로 보고한다.

확정안의 현재 제안 선택은 묶음 `reqKey × productId`마다 최고 `version`, 그 안에서 최고 `round`다. `v1/attempt2` 다음 `v2/attempt1`이 최신이어야 하므로 백엔드의 선택·저장 확장이 필요하다. 같은 상품 · 규칙을 두 요건이 함께 바꾸면 백엔드 행 하나로 보낼 수 없어 `FAILED`로 끝낸다.

`tests/test_server.py`가 가짜 Spring으로 콜백 순서 · 제안 펼치기 · 확인 번역 · 반영 · 종료 상태 · 재시도를 확인한다.

커밋은 매번 사용자의 명시적인 허락을 받은 뒤 수행한다.
