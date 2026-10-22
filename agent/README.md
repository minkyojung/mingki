# Dumbo Agent

법령 개정을 분석해 상품별 변경 제안을 만들고, 검증 후 사람이 검토한다.
서비스 이름은 Dumbo이며, 기존 DB·패키지·설정 키 등 내부 식별자는 호환성을 위해 유지한다.

```text
요건 분석 → 상품 영향 → 제안 작성 → 검증 → 사람 검토
                          ↑ 자동 보완·수정 요청 ┘
사람 검토 → 승인: apply → 보고서 / 거절: 보고서
```

## 실행 및 설정

`agent/`에서 실행한다. `.env.example`을 참고해 `.env`에 `OPENAI_API_KEY`, `OPENAI_MODEL`을 설정한다.

```bash
uv sync
uv run python run.py --article 8                 # 실제 LLM + 로컬 자료
uv run python run.py --mode live --article 8     # 실제 LLM + MCP
AGENT_MODE=live uv run uvicorn app.server:create_app --factory --port 8000
uv run pytest -q
```

| 설정 | 용도 |
|---|---|
| `AGENT_MODE` | 기본 `llm`: 로컬 자료. `live`: 규정·업무 MCP. 둘 다 실제 LLM 사용 |
| `REGULATION_MCP_URL`, `BUSINESS_MCP_URL` | 기본 `http://localhost:8001/mcp`, `http://localhost:8003/mcp`. Docker는 서비스 주소 사용 |
| `INTERNAL_TOKEN` | Spring·Agent·업무 MCP 간 내부 토큰. 배포 시 개발 기본값 변경 |
| `AGENT_CHECKPOINT_DB` | Postgres 체크포인터 연결. 미설정 시 메모리 저장으로 재시작 복구 불가 |
| `REGULATION_ID`, `AGENT_ARTICLE_IDS` | 등록 규정 ID(기본 `credit-loan-ops`)와 분석 조항 제한(미설정 시 변경 조항 전체) |

HTTP는 기존 환경변수가 `.env`보다 우선하며 CLI는 `.env`를 우선한다. `.env`는 커밋하지 않는다.
live는 MCP 조회 자료를 사용하고, 누락 자료를 로컬 파일로 대체하지 않는다. 서버 실행은 [MCP 안내](../mcp/README.md)를 참고한다.

## 백엔드 연동 계약

공유 명세는 [BACKEND_API.md](../docs/BACKEND_API.md), JSON 예시는 [contracts](../docs/contracts/)에서 관리한다.
백엔드 수정 요청 기능은 해당 계약과 백엔드 구현의 병합·배포 상태를 함께 확인한다.

| Spring → Agent | 핵심 입력·역할 |
|---|---|
| `POST /run` | taskId·threadId·documents·callbackBaseUrl로 최초 분석. comment 없음 |
| `POST /resume` / `REQUEST_CHANGES` | comment + payload의 proposalId·baseVersion으로 한 제안 재작성 |
| `POST /resume` / `APPROVE` | approvalId + payload의 approvedProposalKeys·excludedProposalKeys로 선택 반영 |
| `POST /resume` / `REJECT` | 전체 거절 후 보고서 생성 |
| `POST /continue` | taskId·threadId·callbackBaseUrl로 중단된 노드 실행 복구. 검토 대기는 /resume 사용 |
| `GET /health` | 프로세스 응답 확인. 외부 서비스 연결 상태 검사는 아님 |

실행 요청에는 `X-Internal-Token` 헤더가 필요하다. 재개에는 `step: "REVIEW"`와 기존 taskId·threadId·callbackBaseUrl을 보낸다.
202는 접수 응답이며 결과는 callbackBaseUrl(`/internal/tasks/{taskId}`까지 포함) 아래로 전송한다.

| Agent → Spring | 저장·알림 |
|---|---|
| `PUT /requirements` | 추출 요건 |
| `PUT /proposals` | 제안 행. 수정 결과도 같은 API 사용 |
| `POST /events` | STEP, APPROVAL_REQUIRED, DONE, FAILED 등 |
| `PUT /report` | 최종 보고서 |

### 개별 수정 요청

- 공개 `POST /api/ai/tasks/{id}/revisions` 접수·조회·이력·상태 관리는 백엔드 담당이다. 백엔드는 DESIGNING 전이 후 Agent를 호출한다.
- Agent는 현재 제안·정수 기준 버전·구체적인 의견을 접수 전에 검사한다. 잘못된 입력은 400, 검토 대기가 아니면 409다. 공개 API의 버전 충돌 409와 구분한다.
- 같은 thread에서 기존 근거를 재사용하고, 선택 제안의 글만 재작성·검증한다. 요건·영향 분석은 다시 실행하지 않으며 판정·규칙·값·범위는 유지한다.
- `proposalId`는 상품 × 요건 ID(`REQ-8:P001`)이며 규칙 반영 키(`P001:DSR_LIMIT`)와 다르다.
- 제안 행에 `version`, `revisionOutcome`, `revisionResponse`, `changedFields`를 전달한다. 사용자 수정은 version +1, 자동 보완은 같은 버전에서 round(=attempt) 증가다.
- 현재 제안은 묶음별 최고 version, 그 안에서 최고 round다. 작성 직후 PENDING 행이 먼저 저장될 수 있으므로 새 버전 존재만으로 완료 판단하지 않는다.
- 결과는 revised / cannot_apply / needs_clarification / failed이며 모두 다시 검토를 기다린다. 자동 승인하지 않는다. 미반영·추가 확인은 기존 본문·검증 상태를 유지하고 최종 작성·검증 실패는 승인을 막는다.
- `recommendation → proposed`, `actions + optional_actions → followUp`으로 전송한다. 주의사항·범위·조건은 현재 제안 행의 독립 필드가 아니며 별도 표시에는 계약 확장이 필요하다.

### 승인 및 실제 반영

승인·제외 목록은 현재 변경 행 전체를 중복 없이 나눠야 한다. 승인 대상은 검증·본문·필수 조치를 통과해야 한다.
`apply`는 업무 MCP의 `apply_rule_version(task_id, approval_id, proposal_key, idempotency_key)`로 접수하며 변경 값을 직접 보내지 않는다.
멱등키는 `{taskId}:apply:{proposalKey}`이고, 큐·워커는 백엔드가 관리한다.
Agent는 Spring `GET /internal/jobs/{jobId}`로 조회하며 전부 DONE이어야 완료다. 기본 대기 60초, 조회 간격 1초다.
실패·시간 초과는 FAILED이며 일부 반영의 자동 롤백을 의미하지 않는다. 복구에는 지속 체크포인터와 같은 멱등키를 사용한다.

## CLI 검토 및 검증

`v 번호` 상세, `k` 유지 근거, `p 번호` 선택 승인, `r 번호` 제외, `c 번호` 의견 재작성,
`e 번호` 직접 편집, `a` 전체 검토, `f` 검토 완료, `?` 도움말을 사용한다.
직접 편집은 CLI 전용이다. CLI는 실행별 메모리 상태이며 백엔드 작업에 저장하거나 실제 DB를 반영하지 않는다.
검증은 값·단위·시행일·출처·인용을 검사하고 보완 가능한 실패만 한 번 더 작성한다. 법령 적용·예외 판단은 사람이 확인한다.
자동 테스트는 실제 LLM API와 DB 쓰기를 사용하지 않는다.

## 코드 위치

| 파일 | 담당 |
|---|---|
| `app/server.py`, `app/backend.py` | HTTP 접수·요청 모델·전송 행 변환 |
| `app/runner.py`, `app/graph.py`, `app/state.py` | 실행·재개·thread 잠금·노드 연결·상태 |
| `app/nodes/`, `app/validation.py` | 분석·작성·검증·사람 검토·반영·보고 |
| `app/tools/`, `app/llm.py`, `prompts/` | 로컬/MCP 도구와 LLM 연결 |
| `app/bridge.py`, `app/spring.py`, `app/checkpoint.py` | 백엔드 콜백·큐 조회·체크포인트 |

같은 thread 실행은 프로세스 내에서 직렬화하며 다른 작업은 병렬 실행 가능하다. 분산 실행 잠금은 별도 구현이 필요하다.
