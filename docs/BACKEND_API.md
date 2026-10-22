# Backend API 계약

Spring Boot 백엔드가 프론트(Vue), Agent(FastAPI), MCP 서버와 주고받는 형태.
흐름과 결정 사항은 [SIMPLIFICATION.md](./SIMPLIFICATION.md), 프론트 쪽 계획은 [FRONTEND_PLAN.md](./FRONTEND_PLAN.md).

> 흐름은 하나다. 규정 개정 → 상품 × 규칙 제안 → 사람 확인 1번 → 반영. 세부 계약은 [10절](#10-단순화-흐름-계약-v2)에 있다.
> 단순화 전 흐름(요건 확정 · 코드 패치 · 티켓 · Draft PR · 역할)은 V7 마이그레이션에서 지웠다 (10.7).

## 1. 누가 누구를 부르나

```
Vue ──(/api/**, JWT)──▶ Spring ──(/run · /resume · /continue)──▶ Agent ──▶ MCP 서버 2개 (regulation · business)
                          ▲                                         │
                          └──────(/internal/**, 내부 토큰)───────────┘   ← Agent 콜백, business-mcp
```

- Agent · MCP · DB · RabbitMQ는 docker compose 안에서 서로 부른다. 개발 편의로 DB · RabbitMQ 포트를 열어 두고, 호스트에서 Agent를 띄울 때는 `docker-compose.agent-local.yml`로 MCP 포트를 127.0.0.1에 연다.
- `/internal/**`은 `X-Internal-Token` 헤더가 맞아야 통과한다. 네트워크로 막지 않으므로 이 토큰이 유일한 경계다.

## 2. 자주 묻는 것

| 질문 | 답 |
|---|---|
| 프론트는 Spring만 호출하나? | 네. Agent · MCP는 직접 호출하지 않는다. |
| 세션 쿠키 vs JWT | JWT (`Authorization: Bearer`). 시드 계정 `officer1` · `planner1` · `dev1` · `dev2`, 비밀번호 `password`. 역할은 없다. |
| 폴링 vs SSE | 둘 다 된다. `GET /tasks/{id}` + `GET /tasks/{id}/events?after=` 폴링으로 충분. SSE는 `/stream?access_token=` (EventSource는 헤더를 못 보냄). |
| 사람 확인은 몇 번? | 최종 결정은 한 번. 제안마다 반영 / 제외를 고른다 (`excludedProposalKeys`). 그 전에 상품 하나씩 수정 요청을 보낼 수 있다 (10.3, `POST /revisions`). AI는 그 제안의 글만 다시 쓴다. |
| 근거 검사를 통과 못 한 제안도 승인되나? | 아니다. 반영으로 고른 제안이 하나라도 `FAILED`면 서버가 409로 막는다. 제외한 제안은 보지 않는다. `canApprove`는 승인할 수 있는 제안이 있는지 알려준다. |
| 규정 파일 형식 | `.md` / `.markdown` / `.txt`, UTF-8, 파일당 1MB 이하. |
| 반영 결과는 어디에 남나? | `product_rules`에 새 행(`R083`~), `deliveries`에 `RULE_VERSION` 기록 (10.4). |
| 명세서 출력 형식 | `GET /tasks/{id}/report`의 `report.markdown`. 화면 렌더링 + 다운로드. |
| history 모드 SPA fallback | frontend 컨테이너(nginx)의 `try_files $uri /index.html`로 처리. Spring은 `/api`만 담당. |

## 3. 인증

역할은 없다. 로그인한 사용자는 모두 같은 일을 할 수 있다.

```http
POST /api/auth/login   {"username":"officer1","password":"password"}
→ {"token":"…","userId":1,"username":"officer1","displayName":"김준법"}
GET  /api/auth/me
→ {"userId":1,"username":"officer1","displayName":"김준법"}
```

## 4. 작업 상태

상태 그림과 노드 → 상태 표는 [10.1](#101-상태--노드)에 있다.

`GET /tasks/{id}`의 `pendingAction`이 지금 사람이 할 일을 알려준다. 확인 대기면 `{step:"REVIEW", path}`, 아니면 `null`.
라우터 가드는 이 값과 `status`만 보면 된다.

## 5. 공개 API (`/api/ai/tasks`)

에러는 항상 `{"error":"CONFLICT","message":"…"}` 형태.
400 입력 오류 · 401 미인증 · 403 승인 검증 실패 · 404 없음 · 409 상태 충돌 · 413 파일 크기.

| 프론트 함수 | Method · Path | 요청 | 응답 |
|---|---|---|---|
| `createTask(files)` | `POST /api/ai/tasks` | multipart: `oldFile`, `newFile`, `title?`, `oldVersion?`(기본 v3), `newVersion?`(기본 v4) | **202** `{taskId, status}` |
| `listTasks()` | `GET /api/ai/tasks?mine=false` | `mine=true`면 내가 만든 것만 | `TaskSummary[]` |
| `getTask(id)` | `GET /api/ai/tasks/{id}` | | `{task, errorMessage, maxVerifyRounds, documents, pendingAction, counts:{requirements, proposals}, approvals, deliveries}` ([예시](./contracts/task.get.json)) |
| `getRequirements(id)` | `GET /api/ai/tasks/{id}/requirements` | | `{task, documents, requirements[]}` — 요건마다 `oldText` · `newText` (신·구 원문) |
| `getProposals(id)` | `GET /api/ai/tasks/{id}/proposals` | | 10.2 |
| `getContractImpacts(id)` | `GET /api/ai/tasks/{id}/contract-impacts` | | 10.6 |
| `reviewProposals(id, d)` | `POST /api/ai/tasks/{id}/review` | `{decision:"APPROVE"\|"REJECT", comment?, excludedProposalKeys?}` — 반려는 `comment` 필수 | `Approval` — 10.3 |
| `requestRevision(id, r)` | `POST /api/ai/tasks/{id}/revisions` | `{proposalId, baseVersion, comment, revisionId?}` — 제안 하나의 글을 AI가 다시 쓰게 한다 | **202** `{revisionId, proposalId, baseVersion, status:"DESIGNING"}` — 10.3 |
| `getRevision(id, rid)` | `GET /api/ai/tasks/{id}/revisions/{revisionId}` | | `{revisionId, proposalId, baseVersion, comment, requestedBy, createdAt, state, version, outcome, response, changedFields, proposals[]}` — 10.3 |
| `getReport(id)` | `GET /api/ai/tasks/{id}/report` | | `{task, report, review, deliveries[]}` — `review`는 가장 최근 확인 기록 (결정 · 사유 · 확인한 사람), 확인 전이면 null ([예시](./contracts/report.get.json), [반려](./contracts/report.get.rejected.json)) |
| `getEvaluation(id)` | `GET /api/ai/tasks/{id}/evaluation` | | `{task, evaluation}` |
| (폴링) | `GET /api/ai/tasks/{id}/events?after={seq}` | | `{seq, type, payload, createdAt}[]` |
| (SSE) | `GET /api/ai/tasks/{id}/stream?access_token={jwt}` | `Last-Event-ID` 또는 `?after=` | `event: {type}` / `data: {seq,type,payload}` |
| `retryTask(id)` | `POST /api/ai/tasks/{id}/retry` | | **202** `{taskId, status, accepted, how}` — "다시 시도". 진행 중(Agent가 도중에 죽음) 또는 `FAILED`(Agent 호출 실패) 작업만. `how`는 Agent에 보낸 것: `continue`(반영 도중 죽음) · `resume`(승인 직후 죽음, 저장된 승인 재전송) · `run`(업로드 직후 죽음) · `waiting`(확인 대기로 되돌림. 확인 대기에서 FAILED가 된 작업이거나, 저장된 승인을 Agent가 400으로 받지 않은 경우). FAILED였으면 상태를 멈추기 전 자리로 되돌린다. **진행 중인 작업은 마지막 변화 뒤 `RETRY_QUIET_SECONDS`(기본 300초)가 지나야 받는다** — 돌고 있는 Agent를 겹쳐 부르면 멀쩡한 작업이 깨지기 때문. 그 전엔 409(남은 초 포함). Agent가 423(아직 실행 중)이면 409, 확인 대기 · 정상 종료 작업은 409, Agent 연결 실패는 503(작업은 다시 FAILED). 다시 시도가 실패하면 사유 뒤에 `(처음 실패: …)`로 맨 처음 원인을 붙여 `errorMessage`와 응답에 남긴다 |
| `resetDemo()` | `POST /api/demo/reset` | | `{tasks, jobs, products, productRules, loanContracts}` — 작업 기록을 지우고 업무 데이터를 `data/business` JSON으로 되돌린다. 계정은 남긴다. `DEMO_RESET_ENABLED=false`면 404 |

### 주요 객체

```jsonc
// Requirement
{"id":1,"reqKey":"R1","summary":"DSR 한도 40% → 35%","target":"개인신용대출 전 상품",
 "effectiveDate":"2026-12-01","transitional":"시행일 이후 신청분부터, 기존 계약은 종전 기준",
 "articleRef":"제8조","oldText":"…","newText":"…","detail":{}}

// Approval — 사람 확인 기록 (10.3)
{"id":1,"step":"REVIEW","decision":"APPROVE","approverId":1,
 "approver":{"userId":1,"username":"officer1","displayName":"김준법"},"comment":"P005는 화면 문구 정비 후 반영",
 "payload":{"approvedProposalKeys":["P001:DSR_LIMIT"],"excludedProposalKeys":["P005:DSR_LIMIT"]},
 "createdAt":"…"}
```

제안(`RuleProposal`)은 10.2, 반영 기록(`Delivery`)은 10.4.

### 진행 이벤트 `type`

| type | payload | 화면 |
|---|---|---|
| `STEP` | `{node, round?, message?}` | 단계 표시. `node=verify`면 `round`회차 |
| `APPROVAL_REQUIRED` | `{step:"REVIEW", summary?}` | 확인 화면으로 이동 버튼. 다른 step은 400 |
| `RESUMED` | `{step, decision, by, approvalId}` | `step:"RETRY"`면 "다시 시도" |
| `TOOL_START` / `TOOL_END` (큐) | `{tool, attempt?, ok?, retrying?, dead?, error?}` | 큐 재시도 표시 |
| `TOOL_START` (Agent) | `{node, tool, args}` — 단계 코드가 도구를 부르기 직전. ID 목록은 개수로 줄인다 | AI 작업 기록 |
| `TOOL_END` (Agent) | `{node, tool, ok, ms, summary?, error?}` — `summary`는 결과 건수 요약("12건") | AI 작업 기록 |
| `TOOL_PLAN` | `{node, requirementId, lookups:[{tool, regulationId, articleId, query, reason, regulationTitle, articleTitle}]}` — 영향 판단 전에 모델이 고른 조회와 이유 (요건마다). `regulationTitle` · `articleTitle`은 화면 표시용 이름 (모르면 null). 실행은 이어지는 `TOOL_START` · `TOOL_END` | AI 도구 선택 기록 |
| `DECISION` | `{node, requirementId, productId, model:{impact, targetRuleIds, reason}\|null, final:{impact, targetRuleIds}, checks:[…]}` — 상품 영향 판단 직후 상품마다. `model`은 모델 판단, `final`은 코드 검사 뒤 판단, `checks`는 검사가 바꾼 이유 | AI 판단 기록 |
| `TOKEN` | `{text}` | (선택) 스트리밍 |
| `STATUS` | `{status, reason?}` | `reason`이 있으면 `errorMessage`로 남는다 |
| `DONE` | `{outcome:"DONE"\|"NO_CHANGE"\|"REJECTED"}` | 종료 |
| `FAILED` | `{reason}` | 오류 화면 |

Agent가 보내는 `TOOL_*` · `DECISION`은 상태를 바꾸지 않는 기록이다. 큐의 `TOOL_*`와는 `node` 유무로 구분한다. 기록 전송이 실패해도 Agent는 작업을 계속한다.

## 6. Agent ↔ Spring

### Spring → Agent

셋 다 바로 202를 돌려주고, 실제 작업은 백그라운드에서 한다.

```http
POST {AGENT_BASE_URL}/run
{"taskId","threadId","title","documents":[{"side":"OLD","versionLabel":"v3","filename","sha256"},…],
 "callbackBaseUrl":"http://backend:8080/internal/tasks/{taskId}"}

POST {AGENT_BASE_URL}/resume
{"taskId","threadId","step":"REVIEW","decision":"APPROVE"|"REJECT"|"REQUEST_CHANGES","approvalId","comment","payload","callbackBaseUrl"}

POST {AGENT_BASE_URL}/continue      ← 사용자 "다시 시도" (POST /api/ai/tasks/{id}/retry)가 먼저 보냄. Agent 가 409(이어갈 게 없음)면 저장된 승인으로 /resume, 그것도 없으면 /run 을 다시 보냄.
                                      Agent 가 그 thread 를 아직 돌리고 있으면 423 으로 거절해야 한다 (백엔드는 사용자에게 "아직 실행 중" 409). 받은 순간부터 끝날 때까지가 "실행 중"이다 — 원문을 받는 구간도 포함
{"taskId","threadId","callbackBaseUrl"}
```

`/resume`의 payload는 확인 기록과 같다. `{approvedProposalKeys:[…], excludedProposalKeys:[…]}` (REJECT면 `approvedProposalKeys` 없음). `REQUEST_CHANGES`면 `{proposalId, baseVersion}`이고 `approvalId`는 없다 (10.3).

### Agent → Spring (`{callbackBaseUrl}` 아래)

| Method · Path | 언제 | 본문 |
|---|---|---|
| `GET /documents` | 시작 | → `[{side, versionLabel, filename, sha256, content}]` |
| `GET ` (빈 경로) | 아무 때 | → `{id, title, status, currentNode, verifyRound, threadId}` |
| `GET /approvals` | 재개 후 | → 확인 기록 |
| `POST /events` | 노드 진입마다 | `{type, payload}` — 위 이벤트 표 |
| `PUT /requirements` | requirement_agent | `{items:[{reqKey, summary, target, effectiveDate, transitional, articleRef, oldText, newText, detail}]}` — 전체 교체 |
| `PUT /proposals` | proposal_agent · verify | 10.2 |
| `PUT /contract-impacts` | product_impact_agent 이후 | 10.6 |
| `PUT /report` | report | `{markdown, summary, …}` 자유 형식 |
| `PUT /evaluation` | 평가 스크립트 | 자유 형식 (`{…}`) |

**순서 규칙**
1. 노드에 들어갈 때 `STEP {node}` → 상태가 자동으로 바뀐다 (10.1 표).
2. interrupt 직전에 제안을 저장하고 `APPROVAL_REQUIRED {step:"REVIEW"}`를 보낸다.
3. 바꿀 상품이 없으면 `PUT /report` 후 `DONE {outcome:"NO_CHANGE"}`.
4. 끝나면 `PUT /report` 후 `DONE {outcome}`. 오류는 `FAILED {reason}`.
5. 10.1 그림에 없는 전이는 409. `WAITING_REVIEW`에서 나가는 전이는 확인 API · 수정 요청 API만 한다 (Agent가 보낸 `FAILED`도 409, 10.4). 수정 요청 때는 백엔드가 먼저 `DESIGNING`으로 옮긴 뒤 `/resume`을 보내므로, Agent가 보내는 `STEP proposal_agent`는 같은 상태로의 전이다. 종료 상태는 같은 outcome의 `DONE`만 받는다.
6. 산출물 저장 가능 상태 (그 밖에는 409)

| 경로 | 상태 |
|---|---|
| `PUT /requirements` | EXTRACTING |
| `PUT /proposals` | DESIGNING · VERIFYING (확인 대기부터 고정. 수정 요청으로 다시 `DESIGNING`이 되면 새 버전을 받는다) |
| `PUT /contract-impacts` | LOCATING · DESIGNING · VERIFYING |

## 7. 쓰기 도구 (승인 필요)

쓰기 도구는 business-mcp `apply_rule_version` 하나다. 서버가 승인 기록을 다시 검증하고 저장된 제안에서 값을 꺼낸다 (10.4).
큐에 작업을 넣는 일반 API는 없다. `/internal/jobs`는 조회(`GET /{jobId}`, `GET ?taskId=`)만 한다.

## 8. 큐 (가산점)

```
rr.jobs (direct) ─ job ──▶ rr.jobs.q ──▶ JobWorker ─ 성공 ─▶ DONE
                                             └ 실패 ─▶ rr.jobs.retry (TTL 3초) ─▶ rr.jobs.q   (최대 3회)
                                                        └ 3회 실패 ─▶ rr.jobs.dlq  (DEAD)
```

- 처리 중 `TOOL_START` / `TOOL_END {ok, attempt, retrying, dead}` 이벤트가 화면으로 간다.
- 재시도를 확인하려고 `JOB_FAILURE_RATE`(기본 0.3) 확률로 일부러 실패시킨다. 마지막 시도에서는 주입하지 않아서, 주입한 실패만으로 작업이 `DEAD`가 되지는 않는다. `DEAD`는 진짜 오류와 다시 해도 같은 실패("이미 바뀐 규칙")에서만 나온다. `0`이면 주입하지 않는다.
- RabbitMQ 콘솔 http://localhost:15672 (guest/guest)에서 retry · DLQ 큐를 보여줄 수 있다.

## 9. business-mcp 읽기 도구

| 도구 | Spring |
|---|---|
| `get_product_rules(key?)` | `GET /internal/product-rules?key=ELIGIBLE_MIN_AGE` (key 없으면 전체) |
| | `GET /internal/products`, `GET /internal/products/{productId}/rules` |
| `get_db_schema()` | `GET /internal/db/schema` |
| (경과조치 판단) | `GET /internal/contracts/impact?ageFrom=60&ageTo=64&appliedBefore=2026-12-01` |

데이터와 스키마는 [data/business/README.md](../data/business/README.md)를 따른다. 기동할 때 `data/business/*.json`을 읽어 비어 있는 테이블에만 넣는다.

```jsonc
// GET /internal/product-rules — effectiveTo가 null이면 현재 유효
{"ruleId":"R010","productId":"P002","ruleKey":"ELIGIBLE_MIN_AGE","value":"65","unit":"age",
 "effectiveFrom":"2024-01-01","effectiveTo":null}

// GET /internal/contracts/impact?ageFrom=60&ageTo=64&appliedBefore=2026-12-01
{"ageFrom":60,"ageTo":64,"total":2,"byProduct":{"P001":2},"appliedBefore":"2026-12-01","grandfathered":2,
 "contracts":[{"contractId":"C0002","productId":"P001","status":"ACTIVE","appliedAt":"2025-06-11",
               "ageAtApplication":62,"grandfathered":true}, …]}
```

- `unit`: `ratio` · `percent_point` · `krw` · `age` · `score` · `bool`
- 경과조치는 기존 행을 `effectiveTo = 2026-11-30`으로 닫고 `effectiveFrom = 2026-12-01` 행을 추가한다.
- `contracts/impact`의 나이는 `birth_date`와 `applied_at`으로 계산한 신청일 기준 만 나이다. `REJECTED`와 만기 지난 계약은 세지 않는다. `appliedBefore`보다 먼저 신청한 건이 `grandfathered`(종전 기준 유지)다.

시드의 규칙 키는 시나리오 대상과 함정을 같이 담는다.

| rule_id | 키 · 값 | 판단 | 이유 |
|---|---|---|---|
| R001, R011 | `DSR_LIMIT 0.40` | 변경 대상 | 제8조 40% → 35% |
| R010 | P002 `ELIGIBLE_MIN_AGE 65` | 변경 대상 | 제15조 고령자 기준. 키에 SENIOR가 없다 |
| R015 | P003 `DSR_LIMIT 0.35` | 대상 아님 | 상품 기준이 이미 신 기준 이하 |
| R007, R018 | `MORTGAGE_LTV_LIMIT 0.40` | 함정 | 같은 0.40이지만 LTV(제10조) |
| R008 | `INCOME_EST_RETIREMENT_AGE 65` | 함정 | 같은 65지만 소득 추정용 정년(제7조) |
| R005, R013, R019 | `SENIOR_RATE_DISCOUNT 0.3` | 대상 아님 | 우대 폭은 그대로, 적용 대상만 넓어짐 |

## 10. 단순화 흐름 계약 (v2)

기준: [SIMPLIFICATION.md](./SIMPLIFICATION.md) v2. 요청 · 응답 예시는 [`docs/contracts/`](./contracts/)에 있고, 각 영역 테스트가 같은 파일을 읽는다.
화면이 받는 응답은 [`docs/contracts/README.md`의 "화면 응답 예시"](./contracts/README.md)에 실제 서버 응답으로 있다 (`task.get.json` · `proposals.get.json` · `proposals.get.done.json` · `contract-impacts.task.get.json` · `report.get.json` · `events.get.json`).

### 10.1 상태 · 노드

```
EXTRACTING ─▶ LOCATING ─▶ DESIGNING ⇄ VERIFYING ─▶ WAITING_REVIEW ─(APPROVE)─▶ PUBLISHING ─▶ DONE
     │            │            ▲                          ├(REJECT)▶ REJECTED
     │            │            └──(REQUEST_CHANGES, 제안 하나 다시 작성)──┘
     └▶ NO_CHANGE ┘ (전 상품 유지)                    어디서든 오류 ▶ FAILED
```

| 노드 (`STEP {node}`) | 상태 |
|---|---|
| `requirement_agent` | `EXTRACTING` |
| `impact_agent` (`product_impact_agent`도 받음) | `LOCATING` |
| `proposal_agent` | `DESIGNING` |
| `verify` (`round` 포함) | `VERIFYING` |
| interrupt 직전 `APPROVAL_REQUIRED {step:"REVIEW"}` | `WAITING_REVIEW` |
| `apply` | `PUBLISHING` |
| `report` 뒤 `DONE {outcome}` | `DONE` · `NO_CHANGE` · `REJECTED` |

- 역할 검사는 없다. 로그인한 사용자 누구나 확인할 수 있다.

### 10.2 제안 (`rule_proposals`)

제안 1건 = 상품 1개 × 규칙 1개. 새 규칙(제15조의2 등)도 상품마다 1건이다.

| 필드 | 형식 | 규칙 |
|---|---|---|
| `proposalKey` | 문자열 | Agent가 정한다. 작업 안에서 고유 (권장: `{productId}:{ruleKey}`) |
| `reqKey` | 문자열 | 근거 요건 |
| `productId` | `P001` | 필수 |
| `ruleId` | `R001` \| null | 기존 규칙 변경 · 유지면 필수, 새 규칙이면 null |
| `ruleKey` | 문자열 | `change_required`면 필수 |
| `impact` | `change_required` \| `keep` \| `needs_check` | 필수 |
| `current` · `proposed` | 문자열 | 화면용 글 ("DSR 한도 40%") |
| `unit` | `ratio` · `percent` · … | `change_required`면 필수. 기존 규칙의 단위를 바꾸지 않는다 |
| `currentValue` · `proposedValue` | 문자열 | `change_required`면 `proposedValue` 필수, 기존 규칙이면 `currentValue`도 필수 |
| `effectiveDate` | `YYYY-MM-DD` | `change_required`면 필수 |
| `basis` | 문자열 | 근거 조항 (`reg://credit-loan-ops/v4/8`). 여러 개면 쉼표로 구분 (예: 유지 근거인 적용 예외 조항). 200자 이하 |
| `reason` | 문자열 | 필수 (`keep` · `needs_check`는 왜 그런지) |
| `transition` | 문자열 | 경과조치 |
| `followUp` | 문자열 배열 | 후속 작업 (화면 문구 · 심사 절차 · 설명서) |
| `version` | 1 이상 | 제안 버전. 수정 요청으로 다시 쓸 때마다 +1 (Agent `ProductProposal.version`). 없으면 1 |
| `round` | 1 이상 | 같은 버전 안의 작성 회차 (근거 검사 실패 재시도, 최대 2) |
| `verifyStatus` · `verifyLog` | `PENDING` \| `PASSED` \| `FAILED` | 근거 검사 결과 |
| `revisionOutcome` | `none` \| `revised` \| `cannot_apply` \| `needs_clarification` \| `failed` | 수정 요청 결과 (10.3). 없으면 `none` |
| `revisionResponse` | 문자열 \| null | AI의 반영 · 미반영 설명 |
| `changedFields` | 문자열 배열 | 이전 버전과 달라진 칸 (`recommendation` · `actions` · `optional_actions` · `precautions`). 없으면 `[]` |

**Agent → Spring** `PUT {callbackBaseUrl}/proposals` `{items:[…]}` ([예시](./contracts/proposals.put.json))
- `(proposalKey, version, round)` 기준 upsert. 근거 검사 결과만 보낼 때도 같은 항목을 보낸다.
- `DESIGNING` · `VERIFYING`에서만 받는다. `WAITING_REVIEW`부터 고정 (사용자가 본 제안과 반영할 제안이 같아야 한다). 수정 요청이 들어오면 다시 `DESIGNING`이 되고, 그 제안 묶음의 행이 새 `version`으로 들어온다. 다른 묶음의 행은 다시 와도 같은 내용이다.

**화면** `GET /api/ai/tasks/{id}/proposals`
- `{task, maxVerifyRounds, latestRound, canApprove, proposals[]}`
- `proposals`는 버전 · 회차 전체이고 현재 것은 `latest:true`. 서버가 `productName`(products 조인), `decision`(`APPLY` · `EXCLUDE` · null), 반영 결과 `apply {jobId, status, closedRuleId, newRuleId}`를 붙인다. 이전 버전 행은 화면이 변경 전후 비교에 쓴다.
- 현재 제안 = 제안 묶음(`reqKey` × `productId`)마다 가장 높은 `version`, 그 안에서 가장 높은 `round`의 행. 다시 쓴 버전 · 회차에서 빠진 규칙 행은 이전 것에 남지만 현재 제안이 아니다 (키마다 최고를 고르면 1회차에만 있던 `FAILED` 행이 승인을 영원히 막는다).
- `latestRound`는 현재 제안 행들의 최고 회차다. 이전 버전 행은 세지 않는다.
- `canApprove`: `WAITING_REVIEW` + 현재 `change_required` 제안 중 `PASSED`가 1건 이상. 승인 요청은 반영으로 고른 제안만 검사한다 (10.3).

### 10.3 사용자 확인

`POST /api/ai/tasks/{id}/review` ([승인 예시](./contracts/review.post.json), [반려 예시](./contracts/review.post.reject.json))

```json
{"decision": "APPROVE", "excludedProposalKeys": ["P005:DSR_LIMIT"], "comment": "P005는 화면 문구 정비 후 반영"}
```

| 결정 | 검사 | 결과 |
|---|---|---|
| `APPROVE` | `WAITING_REVIEW`(아니면 409). 제외 키는 현재 `change_required` 제안이어야 함(400). 제외하고 남는 게 없으면 400 (REJECT로 보낸다). 남은 제안 중 `FAILED`가 있으면 409 (응답에 막힌 키) | `PUBLISHING`. 기록 payload `{approvedProposalKeys, excludedProposalKeys}` |
| `REJECT` | `comment`(반려 사유) 필수, 비면 400 | `REJECTED` |

**수정 요청** `POST /api/ai/tasks/{id}/revisions` ([예시](./contracts/revisions.post.json))

```json
{"proposalId": "REQ-8:P001", "baseVersion": 1,
 "comment": "기존 대출을 연장하는 고객은 종전 기준이라는 안내를 조치에 넣어줘"}
```

최종 결정 전에 제안 하나를 AI가 다시 쓰게 한다. `proposalId`는 Agent 제안 ID(`{reqKey}:{productId}`)이고, 그 제안에 딸린 규칙 행 전부가 새 버전으로 바뀐다. AI는 글(제안 문장 · 조치 · 주의사항)만 다시 쓴다. 판정 · 바꿀 규칙 · 값 · 적용 범위는 Agent 검사가 고정한다.

| 검사 | 응답 |
|---|---|
| `WAITING_REVIEW`가 아님 | 409 |
| `proposalId`가 현재 제안 묶음에 없음, `comment` 비어 있음 | 400 |
| `baseVersion`이 현재 버전과 다름 | 409 (응답에 현재 버전) |
| `revisionId`를 보냈고 같은 값이 이미 있음 | 같은 내용이면 처음 접수를 그대로 돌려준다, 다르면 409 |

- 응답 **202** `{revisionId, proposalId, baseVersion, status:"DESIGNING"}`. 접수이지 완료가 아니다. `revisionId`는 서버가 만든다 (확인 기록 id). 화면이 보낸 값은 저장만 한다.
- 기록은 확인 기록(`approvals`)에 `decision: REQUEST_CHANGES`, payload `{proposalId, baseVersion, revisionId?}`로 남는다. `RESUMED {decision:"REQUEST_CHANGES"}` 이벤트 뒤 백엔드가 `STEP proposal_agent`를 남겨 `DESIGNING`으로 옮기고 Agent에 `/resume`을 보낸다.
- 이후는 보통 흐름과 같다. `PUT /proposals`(새 `version`, `round` 1 · 2) → `STEP verify` → `APPROVAL_REQUIRED` → `WAITING_REVIEW`. 몇 번이든 반복할 수 있다.
- 결과는 현재 제안 행의 `revisionOutcome` · `revisionResponse` · `changedFields`에 있다.

| `revisionOutcome` | 뜻 | 승인 |
|---|---|---|
| `revised` | 요청을 본문에 반영 (`changedFields`에 바뀐 칸) | 가능 |
| `cannot_apply` | 원문과 충돌해 반영 못 함 (예: 35% 대신 30%). 본문은 이전 그대로 | 가능 (이전 본문) |
| `needs_clarification` | 모호하거나 대상 재판단 요청. 본문은 이전 그대로, `revisionResponse`에 확인할 질문 | 가능 (이전 본문), 또는 제외 |
| `failed` | 두 번 써도 검사 실패. 본문은 이전으로 되돌림, `verifyStatus: FAILED` | 그 제안은 불가, 제외만 |

- `GET /api/ai/tasks/{id}/revisions/{revisionId}`: 접수 내용과 결과. `state`는 `processing`(작업이 `DESIGNING` · `VERIFYING`) · `done`(새 버전 행이 있음) · `rejected`(Agent가 받지 않음, 아래) · `failed`(작업이 `FAILED`). `proposals`는 그 제안 묶음의 이전 · 새 버전 행.
- 처리 중에는 확인 API가 409다 (`WAITING_REVIEW`가 아니므로). 수정 요청 뒤의 승인은 새 버전 기준이다. 수정 요청 전에 만든 승인 기록은 뒤에 온 결정 때문에 반영에 쓸 수 없다 (10.4 `ApprovalGuard`).
- Agent 재개: `POST /resume {step:"REVIEW", decision:"REQUEST_CHANGES", comment, payload:{proposalId, baseVersion}}`. Agent는 그래프를 돌리기 전에 거부할 수 있다: 검토 대기 thread가 아니면 409, `proposalId`가 없거나 버전이 다르거나 `comment`가 모호("다시 써줘" · "수정해줘")하면 400. 400이면 백엔드는 확인 대기로 되돌리고 사유를 `errorMessage`에 남긴다 (아래 Agent 400 규칙과 같다). 409 · 연결 실패는 FAILED이고 "다시 시도"가 수정 요청을 다시 보낸다.
- **반려는 작업을 끝낸다.** "다시 시도" 대상이 아니다. 같은 규정 · 같은 데이터로 다시 올리면 같은 판정이 나온다 (의도한 재현성). 결과를 바꾸는 길은 셋이다.
  - 일부만 빼려면 반려 대신 `APPROVE` + `excludedProposalKeys`
  - 제안 글이 아쉬우면 반려 대신 수정 요청 (`POST /revisions`)
  - 데이터 · 상품 문서가 틀렸으면 고친 뒤 새로 올린다
  - 판정이 틀렸으면 Agent를 고친다 (M3)
- 반려 사유는 리포트 응답의 `review.comment`에 나온다. 모인 사유가 Agent 판정 오류 목록이 된다.
- 작업 상세(`GET /api/ai/tasks/{id}`)의 `pendingAction`은 `{step:"REVIEW", path}`이고, `counts.proposals`는 현재 회차 제안 수다.
- Agent 재개: `POST /resume {step:"REVIEW", decision, approvalId, payload}` — payload는 위 기록과 같다. Agent는 그래프를 돌리기 전에 거부할 수 있다: 검토 대기 중인 thread가 아니면(없거나 이미 끝남) 409, `approvedProposalKeys ∪ excludedProposalKeys`가 Agent가 보낸 현재 `change_required` 키와 다르거나 APPROVE에 `approvalId`가 없거나 승인할 수 없는 제안이 있으면 400. 202 뒤에는 `apply` → `report` → `DONE` 또는 `FAILED`가 콜백으로 온다.
- Agent가 400으로 결정을 받지 않으면 백엔드는 작업을 FAILED로 만들지 않고 확인 대기로 되돌린다. Agent는 그대로 확인을 기다리고 있기 때문이다. 사유는 `errorMessage`와 `STATUS {status:"WAITING_REVIEW", reason}` 이벤트에 남고, 사용자는 문제 제안을 빼거나 반려해 다시 결정한다. 새 결정이 들어오면 `errorMessage`는 지운다. 409 · 연결 실패는 지금처럼 FAILED이고 "다시 시도" 대상이다.

### 10.4 반영 (쓰기 도구 · 승인 필요)

business-mcp `apply_rule_version(task_id, approval_id, proposal_key, idempotency_key)` → Spring:

```http
POST /internal/product-rules/versions
{"taskId", "approvalId", "proposalKey", "idempotencyKey": "{taskId}:apply:{proposalKey}"}

→ 202 {"jobId", "status": "QUEUED", "duplicate": false}
```

**Agent는 값을 보내지 않는다.** 서버가 `rule_proposals`의 현재 제안에서 지금 값 · 제안 값 · 단위 · 시행일을 꺼낸다. 사용자가 확인한 값과 다른 값은 들어갈 수 없다.

접수 검사 (실패 시 403 · 409)
1. `approvalId`가 이 작업의 REVIEW/APPROVE 기록이고, 가장 최근 REVIEW 결정이다 (403)
2. 작업 상태가 `PUBLISHING`이다 (409)
3. `proposalKey`가 `approvedProposalKeys`에 있고, 현재 제안이 `change_required` · `PASSED`다 (403)
4. 기존 규칙이면 그 행이 지금 유효하고 값이 `currentValue`와 같고, 시행일 이후에 유효하거나 시작하는 다른 행(이미 반영된 미래 버전)이 없다. 새 규칙이면 같은 상품 · 규칙 키에 그런 행이 없다. 아니면 409 "이미 바뀐 규칙". 값은 **숫자로 비교**한다 (`0.4` = `0.40`)
5. 같은 `idempotencyKey`면 새로 만들지 않고 기존 `jobId`를 `duplicate:true`로 돌려준다. 그 작업의 `taskId` · `approvalId` · `proposalKey`가 요청과 다르면 409 (키는 호출자가 정하므로 다른 제안에 같은 키를 쓴 실수를 막는다)

대기열 작업 `APPLY_RULE_VERSION` (8절 큐 · 재시도 · DLQ 그대로)
- 행을 잠그고 4번을 다시 검사한다. 다르면 **재시도하지 않고 바로 `DEAD`** (`lastError` = "이미 바뀐 규칙"). 다시 해도 결과가 같기 때문이다.
- 규칙 행 · 전달 기록 · 작업 완료(`DONE`, `result`)를 한 트랜잭션에 커밋한다. 커밋 뒤 워커가 죽어 메시지를 다시 받아도 작업이 `DONE`이라 반영을 반복하지 않는다.
- 기존 행: `effective_to = effectiveDate - 1일`
- 새 행: `rule_id` = 기존 최대 번호 다음(`R083`부터), `value = proposedValue`, `unit`, `effective_from = effectiveDate`, `effective_to = null`, `basis_articles` = 기존 행 그대로
- 새 규칙은 닫는 행 없이 새 행만 추가한다. `basis_articles`는 제안 `basis`의 첫 조항(`reg://{규정}/{버전}/{조항}` → `{규정}/{조항}`)
- 결과 `GET /internal/jobs/{jobId}` → `{status, result: {proposalKey, productId, closedRuleId, newRuleId, effectiveFrom}}` ([예시](./contracts/apply.job.json)). 새 규칙이면 `closedRuleId: null`.
- 전달 기록(`deliveries`)에 `RULE_VERSION`으로 남긴다.
- 큐에 작업을 넣는 일반 API는 없다. 제안 · 지금 값 검사를 건너뛰지 못하게 반영은 이 API로만 받는다.

Agent가 `apply` 도중 죽어 `PUBLISHING`에 멈추면 사용자가 `POST /api/ai/tasks/{id}/retry`로 이어간다. Agent는 같은 멱등키로 다시 요청하므로 큐 작업이 중복 생기지 않는다.

"다시 시도"는 Agent가 **멈춘** 작업을 위한 것이다. 돌고 있는 작업에 겹쳐 보내면 같은 thread를 두 번 돌리게 되고, 뒤늦게 난 오류가 멀쩡한 작업을 FAILED로 만든다. 세 겹으로 막는다.
- 백엔드: 진행 중인 작업은 마지막 변화 뒤 `RETRY_QUIET_SECONDS`(기본 300초)가 지나야 받는다. FAILED 작업은 바로 받는다.
- 백엔드: 확인 대기(`WAITING_REVIEW`) 중 Agent가 보낸 `FAILED`는 409로 거절한다. 기다리는 Agent는 아무것도 실행하지 않으므로 그 FAILED는 겹친 요청의 뒷처리다.
- Agent: 요청을 받은 순간부터 끝날 때까지 그 thread를 "실행 중"으로 보고, 그동안 `/continue`는 423으로 거절한다. 백엔드는 423이면 `/resume` · `/run`으로 넘어가지 않고 "아직 실행 중" 409를 돌려준다.

Agent `apply` 노드: 승인된 제안마다 한 번씩 호출하고 `/internal/jobs/{jobId}`가 `DONE` · `DEAD`가 될 때까지 기다린다. 모두 `DONE`이면 `DONE {outcome:"DONE"}`, 하나라도 `DEAD`면 `FAILED {reason}`.

### 10.5 business-mcp 읽기 (보강)

| Spring | 추가 |
|---|---|
| `GET /internal/products` | `?status=ON_SALE\|SALE_ENDED` |
| `GET /internal/product-rules` | `?productId=`, `?asOf=YYYY-MM-DD` — 그날 유효한 행만 (`effectiveFrom ≤ asOf`, `effectiveTo`가 null이거나 `≥ asOf`) |
| 규칙 행 (신규 칸) | `basisArticles: ["{regulation_id}/{article_id}"]` — 이 규칙이 따르는 규정 조항 (예: `credit-loan-ops/15`, `senior-protection/5`). 버전 없이 조항만. 비어 있으면 상품 자체 기준이거나 미확인. Agent는 바뀐 조항을 따르지 않는 규칙을 변경 대상에서 뺀다 |
| `GET /internal/product-rules/keys` (신규) | `?asOf=` (기본 오늘) → `[{ruleKey, units, productIds, activeRows, closedRows, upcomingRows}]`. #23 `list_rule_keys` 모양에 `upcomingRows`(아직 시작 전인 행, 반영 직후 생김)를 더했다. `productIds`는 `asOf`에 유효한 행이 있는 상품 ([예시](./contracts/rule-keys.get.json)) |
| `GET /internal/contracts/impact` | `?dsrFrom=&dsrTo=` — `dsr_at_approval`이 (dsrFrom, dsrTo] 안. 나이 조건과 함께 주면 둘 다 만족. 계약마다 `dsrAtApproval` · `repaymentType` · `maturityDate` · `extensionCount` · `maturesAfterEffective`(만기 ≥ `appliedBefore`) 추가. 연장 사례(C0006)는 만기일시 · 연장 이력으로 Agent가 판단한다 ([예시](./contracts/contract-impact.get.json)) |

**"지금 유효"는 `effectiveTo == null`이 아니다.** 반영하면 기존 행은 `effective_to = 2026-11-30`(오늘은 아직 유효), 새 행은 `effective_from = 2026-12-01`(오늘은 아직 아님, `effectiveTo` null)이 된다. 유효 여부는 `asOf`로 판단한다.

### 10.6 계약 영향 저장 · 조회

- Agent: `PUT {callbackBaseUrl}/contract-impacts` `{items:[{contractId, reqKey, reason, grandfathered, maturesAfterEffective}]}` — 전체 교체, `LOCATING` ~ `VERIFYING`.
- 화면: `GET /api/ai/tasks/{id}/contract-impacts` → `{task, items}`. 서버가 `contractId`로 `loan_contracts`를 조인해 `productId` · `productName` · `status` · `appliedAt` · `ageAtApplication` · `amount` · `repaymentType` · `maturityDate` · `extensionCount` · `dsrAtApproval` 등을 붙인다(Agent가 보낸 키는 그대로, 없는 키만 채움). 못 찾으면 `contractFound: false`. 계약 값은 바꾸지 않는다.

### 10.7 옛 흐름 정리 (완료)

단순화 전 흐름은 V7 마이그레이션(`V7__drop_old_flow.sql`)에서 지웠다.

| 지운 것 | 무엇 |
|---|---|
| 공개 API | `POST /requirements/approval`, `GET /change-specs`, `GET /patches` |
| Agent 콜백 | `PUT /change-specs`, `POST /test-cases`, `PUT /patches` |
| 내부 API | `POST /internal/work-tickets`, `POST /internal/jobs` |
| 큐 작업 | `CHECK_PATCH` · `CREATE_DRAFT_PR` · `CREATE_TICKET` (남은 건 `APPLY_RULE_VERSION`) |
| 상태 · 결정 | `WAITING_REQUIREMENT_APPROVAL` · `HELD`, 확인 단계 `REQUIREMENTS`, 결정 `CONFIRMED` · `HELD` · `REQUEST_CHANGES`(요건 확정 단계의 것), `excludedSpecKeys`. `REQUEST_CHANGES`는 V8에서 제안 수정 요청(10.3)으로 다시 쓴다. 뜻이 다르다 |
| 역할 | `OFFICER` · `DEVELOPER` (`users.role`, JWT `role`, 로그인 · `me` · 승인자 응답의 `role`, `pendingAction.role`) |
| 응답 필드 | `counts.targets` · `nonTargets` · `patches` · `testCases`, 요건 `status`, 리셋 응답 `workTickets` |
| 테이블 | `change_specs` · `code_patches` · `test_cases` · `db_migrations` · `work_tickets` |
| 전달 종류 | `PR` · `TICKET` (남은 건 `RULE_VERSION`) |

V7은 옛 흐름의 흔적(위 상태 · 결정 · 테이블 · 큐 작업 · 전달 종류)이 있는 작업을 통째로 지운다. 단순화 흐름 작업과 업무 데이터는 그대로 둔다.
