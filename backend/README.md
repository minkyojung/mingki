# Dumbo · Backend

> 서비스 이름은 Dumbo다. 패키지(`com.ruleripple`) · 설정 키(`ruleripple.*`) · DB 이름 · 큐 이름(`rr.jobs.*`) 같은 내부 식별자는 초기 이름 Rule Ripple을 그대로 쓴다.

Spring Boot 3.3 · Java 21 · PostgreSQL 16 · RabbitMQ

규정 개정 → 상품 규칙 변경 제안 → 사람 확인 1번 → 반영까지의 **작업 상태 · 제안 · 승인 · 반영 기록**을 갖는 서버다.
프론트에는 `/api/**`(JWT)를, Agent와 business-mcp에는 `/internal/**`(`X-Internal-Token`)을 제공한다.
업무 규칙(상품 · 규칙 · 계약)은 이 서버 한 곳에만 있고, MCP는 가공 없이 감싼다.

- API 계약: [docs/BACKEND_API.md](../docs/BACKEND_API.md) — 10절이 현재(v2) 흐름, 예시 JSON은 [docs/contracts/](../docs/contracts/)
- 흐름 · 결정 사항: [docs/SIMPLIFICATION.md](../docs/SIMPLIFICATION.md)
- 품질 특성별 설계 근거: [docs/QUALITY_25010.md](../docs/QUALITY_25010.md)

## 실행

```bash
# 루트에서 postgres · rabbitmq · backend · MCP 2개(regulation · business) · Agent
docker compose up --build

# 로컬 개발: 인프라만 docker로, 백엔드는 gradle
docker compose up -d postgres rabbitmq
cd backend && ./gradlew bootRun

# 테스트. 단위 테스트는 DB 없이 돌고, 통합 테스트(FlowIntegrationTest)는 Docker 로 Postgres · RabbitMQ 를 띄운다.
# Docker 가 없으면 통합 테스트만 건너뛴다
cd backend && ./gradlew test
```

| 주소 | 용도 |
|---|---|
| http://localhost:8080/actuator/health | 헬스 체크 |
| http://localhost:15672 | RabbitMQ 콘솔 (guest / guest) |

시드 계정은 `officer1` · `planner1` · `dev1` · `dev2`, 비밀번호는 모두 `password`다. 역할은 없어서 네 계정이 할 수 있는 일이 같다.

업무 데이터(상품 12 · 규칙 82 · 계약 120)는 기동할 때 `data/business/*.json`에서 **비어 있는 테이블에만** 넣는다 (`BusinessDataLoader`). JSON을 고친 뒤 다시 넣으려면 `POST /api/demo/reset`을 부르거나 `docker compose down -v`로 DB를 비운다.

## 빠른 확인

Agent(`agent/`)가 떠 있어야 끝까지 간다. Agent 없이 띄우면 작업이 곧바로 `FAILED (Agent 호출 실패)`가 되는데, 이건 정상이다.

```bash
B=localhost:8080
TOKEN=$(curl -s $B/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"officer1","password":"password"}' | jq -r .token)
H="Authorization: Bearer $TOKEN"

# 1. 규정 신·구 업로드 → 202 {taskId}
T=$(curl -s $B/api/ai/tasks -H "$H" \
  -F oldFile=@../data/regulations/credit-loan-ops/v3.md \
  -F newFile=@../data/regulations/credit-loan-ops/v4.md | jq -r .taskId)

# 2. 진행 상황 (WAITING_REVIEW 가 될 때까지)
curl -s $B/api/ai/tasks/$T -H "$H" | jq '{status: .task.status, pendingAction}'
curl -s "$B/api/ai/tasks/$T/events?after=0" -H "$H"        # 또는 /stream (SSE)

# 3. 제안 보기 — 상품 × 규칙 한 줄씩, change_required | keep | needs_check
curl -s $B/api/ai/tasks/$T/proposals -H "$H" | jq '.proposals[] | {proposalKey, impact, currentValue, proposedValue, verifyStatus}'

# 4. 확인 1번 — 반영할 제안만 남기고 나머지는 excludedProposalKeys 로 제외
curl -s $B/api/ai/tasks/$T/review -H "$H" -H 'Content-Type: application/json' \
  -d '{"decision":"APPROVE","excludedProposalKeys":[]}'

# 5. 반영 결과 — PUBLISHING → DONE, 새 규칙 행 R083~ 과 전달 기록
curl -s $B/api/ai/tasks/$T/report -H "$H" | jq '{status: .task.status, deliveries}'

# 다시 처음부터 (작업 기록 삭제 + 업무 데이터 JSON 시드로 복원)
curl -s -X POST $B/api/demo/reset -H "$H"
```

Agent가 도중에 죽었으면 `POST /api/ai/tasks/{id}/retry`로 멈춘 자리에서 잇는다 (BACKEND_API 10.4).

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DB_HOST` · `DB_PORT` · `DB_NAME` · `DB_USER` · `DB_PASSWORD` | localhost · 5432 · ruleripple · ruleripple · ruleripple | PostgreSQL |
| `RABBIT_HOST` · `RABBIT_PORT` · `RABBIT_USER` · `RABBIT_PASSWORD` | localhost · 5672 · guest · guest | RabbitMQ |
| `AGENT_BASE_URL` | http://agent:8000 | FastAPI Agent. 호스트에서 띄우면 `http://host.docker.internal:8000` |
| `CALLBACK_BASE_URL` | http://backend:8080 | Agent가 콜백할 Spring 주소 |
| `INTERNAL_TOKEN` | rr-internal-dev-token | `/internal/**` 헤더 `X-Internal-Token`. 유일한 내부 경계이므로 공유 환경에서는 바꾼다 |
| `JWT_SECRET` | (개발용) | 32바이트 이상. 토큰 만료 480분 |
| `JOB_FAILURE_RATE` | 0.3 | 큐 워커 실패 주입 확률. 재시도 확인용이고 마지막 시도에서는 주입하지 않는다. 0이면 끔 |
| `RETRY_QUIET_SECONDS` | 300 | 진행 중인 작업은 마지막 변화 뒤 이 시간이 지나야 "다시 시도"를 받는다. 돌고 있는 Agent를 겹쳐 부르지 않게. 0이면 끔 |
| `DEMO_RESET_ENABLED` | true | `POST /api/demo/reset` 허용. 운영에서는 false(404) |
| `BUSINESS_DATA_DIR` | ../data/business | 업무 시드 JSON 폴더. docker에서는 `/data/business`로 마운트 |

고정값은 `application.yml`에 있다. 업로드 파일 1MB · `.md` `.txt`만, Agent 호출 타임아웃 연결 3초 · 읽기 10초, 큐 재시도 3초 간격 최대 3회.

## 패키지

```
com.ruleripple.backend
├─ auth/      User · JwtService · JwtAuthFilter(/api/**) · InternalTokenFilter(/internal/**) · AuthController
├─ task/      AnalysisTask · TaskStatus(전이 표 agentCanMoveTo) · RegulationDocument · Requirement
│             RuleProposal(상품 × 규칙 제안 행) · Proposals(제안 규칙: 유효성 · 최신 회차 · canApprove)
│             Approval · Delivery · TaskEvent
│             TaskController(공개 API) · TaskService(확인 · 재시도) · TaskEventService(이벤트 + 상태 전이)
│             AgentClient(/run · /resume · /continue) · AgentDispatcher(커밋 후 비동기 호출) · SseBroadcaster · TaskDtos
├─ internal/  InternalTaskController(Agent 콜백) · AgentResultService(제안 upsert, 확인 대기부터 고정)
│             InternalBusinessController(business-mcp 조회 · 반영 접수) · BusinessQueries(조회 계산, 순수)
│             Product · ProductRule(isValidOn) · LoanContract · BusinessDataLoader(JSON 시드 · reseed)
│             ApprovalGuard(승인 기록 재검증) · RuleVersions(반영 가능 판단, 순수) · RuleVersionService(접수 · 반영)
├─ queue/     Job · JobPublisher(멱등키) · JobWorker(재시도 · DLQ, PermanentJobFailure → DEAD) · JobController
├─ demo/      DemoController · DemoResetService
├─ config/    SecurityConfig · RabbitConfig · RestClientConfig · RuleRippleProperties
└─ common/    ApiExceptionHandler  — 모든 오류는 {error, message}
```

## 설계 포인트

자세한 근거는 [docs/QUALITY_25010.md](../docs/QUALITY_25010.md)에 있다.

| 무엇 | 어떻게 |
|---|---|
| 긴 작업 | 업로드 · 확인은 202를 바로 돌려주고 Agent 호출은 커밋 뒤 비동기. 진행은 이벤트 폴링 또는 SSE |
| 상태 전이 | Agent 이벤트는 `TaskStatus.agentCanMoveTo` 표에 있는 전이만 허용(409). 확인 대기 뒤 제안은 고정 |
| 사람 확인 | 1번. 제안마다 반영 / 제외. 근거 검사를 통과한 변경 제안만 승인 가능 |
| 반영 | Agent는 값을 보내지 않고 `approvalId` · `proposalKey`만 보낸다. 서버가 승인 기록을 재검증하고 저장된 제안에서 값을 꺼내 큐 작업으로 넣는다 |
| 반영 안전 | 지금 값이 제안 당시와 다르면 거절, 미래 버전이 겹치면 거절. 기존 행은 지우지 않고 `effective_to`로 닫고 새 행(`R083~`) 추가 |
| 큐 | 멱등키 `{taskId}:apply:{proposalKey}`, 일시 실패는 3초 간격 3회 재시도 → DLQ, 영구 실패는 재시도 없이 DEAD. 반영과 작업 완료는 한 트랜잭션 |
| 복구 | Agent가 죽으면 `/retry`가 멈춘 자리(continue → resume → run)에서 잇는다 |
| 유효 기간 | 규칙 유효 여부는 `effectiveTo == null`이 아니라 날짜(`asOf`)로 판단 |
| 스키마 | Flyway V1 ~ V7, Hibernate는 `validate`만. V7이 단순화 전 흐름의 테이블 · 컬럼을 지운다 (BACKEND_API 10.7) |
