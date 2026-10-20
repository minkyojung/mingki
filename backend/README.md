# Rule Ripple · Backend

Spring Boot 3.3 · Java 21 · PostgreSQL · RabbitMQ

레퍼런스 아키텍처의 Spring Boot 상자. 프론트에는 작업 · 승인 API를, Agent와 MCP에는 `/internal/**`을 제공한다.
API 계약 전체는 [docs/BACKEND_API.md](../docs/BACKEND_API.md).

## 실행

```bash
# 루트에서 postgres · rabbitmq · backend
docker compose up --build backend

# 로컬 개발: 인프라만 docker로
docker compose up -d postgres rabbitmq
cd backend && ./gradlew bootRun
```

| 주소 | 용도 |
|---|---|
| http://localhost:8080/actuator/health | 헬스 체크 |
| http://localhost:15672 | RabbitMQ 콘솔 (guest / guest) |

시드 계정 (비밀번호 모두 `password`)

| 계정 | 역할 |
|---|---|
| `officer1`, `planner1` | OFFICER (요건 확정) |
| `dev1`, `dev2` | DEVELOPER (검토 · 승인) |

샘플 업무 데이터(상품 · 규칙 · 계약)는 기동할 때 `data/business/*.json`에서 비어 있는 테이블에만 넣는다. JSON을 고친 뒤 다시 넣으려면 세 테이블을 비우고 재기동한다.

## 빠른 확인

```bash
TOKEN=$(curl -s localhost:8080/api/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"officer1","password":"password"}' | jq -r .token)

curl -s localhost:8080/api/ai/tasks -H "Authorization: Bearer $TOKEN" \
  -F oldFile=@../data/regulations/credit-loan-ops/v3.md \
  -F newFile=@../data/regulations/credit-loan-ops/v4.md      # → 202 {taskId}

curl -s localhost:8080/api/ai/tasks/{taskId} -H "Authorization: Bearer $TOKEN"
```

Agent 없이 띄우면 작업이 곧바로 `FAILED (Agent 호출 실패)`가 된다. 정상이다.

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DB_HOST` · `DB_PORT` · `DB_NAME` · `DB_USER` · `DB_PASSWORD` | localhost · 5432 · ruleripple · ruleripple · ruleripple | PostgreSQL |
| `RABBIT_HOST` · `RABBIT_PORT` · `RABBIT_USER` · `RABBIT_PASSWORD` | localhost · 5672 · guest · guest | RabbitMQ |
| `AGENT_BASE_URL` | http://agent:8000 | FastAPI Agent |
| `CALLBACK_BASE_URL` | http://backend:8080 | Agent가 콜백할 Spring 주소 |
| `INTERNAL_TOKEN` | rr-internal-dev-token | `/internal/**` 헤더 `X-Internal-Token` |
| `JWT_SECRET` | (시연용) | 32바이트 이상 |
| `JOB_FAILURE_RATE` | 0.3 | 큐 워커 실패 주입 확률 (시연용) |
| `DEMO_RESET_ENABLED` | true | `POST /api/demo/reset`(시연 리셋) 허용. 운영에서는 false |
| `BUSINESS_DATA_DIR` | ../data/business | 업무 시드 JSON 폴더. docker에서는 `/data/business`로 마운트 |

## 패키지

```
com.ruleripple.backend
├─ auth/      User(OFFICER·DEVELOPER), JwtService, JwtAuthFilter, InternalTokenFilter, AuthController
├─ task/      AnalysisTask · TaskStatus(12개) · RegulationDocument · Requirement · ChangeSpec · CodePatch · TestCase
│             · Approval · Delivery · TaskEvent
│             TaskController(공개 API) · TaskService(①② 승인) · TaskEventService(이벤트 + 상태 전이)
│             AgentClient · AgentDispatcher(커밋 후 비동기 호출) · SseBroadcaster · TaskDtos
├─ internal/  InternalTaskController(Agent 콜백) · AgentResultService · InternalBusinessController(business-mcp)
│             ApprovalGuard(쓰기 도구 검증) · Product · ProductRule · LoanContract · WorkTicket
│             BusinessDataLoader(data/business JSON 시드)
├─ demo/      DemoController · DemoResetService (시연 리셋)
├─ queue/     Job · JobPublisher(멱등키) · JobWorker(재시도 · DLQ) · JobController
├─ config/    SecurityConfig · RabbitConfig · RestClientConfig · RuleRippleProperties
└─ common/    ApiExceptionHandler
```

## 평가표 대응

| 항목 | 백엔드에서 |
|---|---|
| 아키텍처 | 202 + 이벤트 폴링/SSE, `/internal/**` 격리, 업무 규칙은 Spring 한곳 |
| Harness | 상태 12개 저장 · 재개, interrupt ①② 기록, 진행 이벤트, 검증 실패 publish 차단 |
| MCP | business-mcp 읽기 · 쓰기 도구의 데이터 원천, 쓰기 도구는 `approval_id` 서버 검증 + 멱등키 |
| 인증 | JWT, 역할 2개 (① OFFICER, ② DEVELOPER) |
| 가산점 | RabbitMQ 큐, 3초 간격 3회 재시도, DLQ |
