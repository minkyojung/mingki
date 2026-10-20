# Backend API 계약

Spring Boot 백엔드가 프론트(Vue), Agent(FastAPI), MCP 서버와 주고받는 형태.
설계 근거는 [PROJECT.md](./PROJECT.md), 프론트 쪽 계획은 [FRONTEND_PLAN.md](./FRONTEND_PLAN.md).

## 1. 누가 누구를 부르나

```
Vue ──(/api/**, JWT)──▶ Spring ──(/run, /resume)──▶ Agent ──▶ MCP 서버 3개
                          ▲                            │
                          └──(/internal/**, 내부 토큰)──┘   ← Agent 콜백, business-mcp, code-mcp
```

- 공개 포트는 frontend(:80)와 backend(:8080)뿐. Agent · MCP · DB · RabbitMQ는 docker network 안에만 있다.
- `/internal/**`은 `X-Internal-Token` 헤더가 맞아야 통과한다.

## 2. FRONTEND_PLAN.md "백엔드와 확인할 것" 답변

| 질문 | 답 |
|---|---|
| 프론트는 Spring만 호출하나? | 네. Agent · MCP는 직접 호출하지 않는다. |
| 세션 쿠키 vs JWT | JWT (`Authorization: Bearer`). 데모 역할 전환은 계정 두 개로 로그인 (`officer1` / `dev1`, 비밀번호 `password`). |
| 폴링 vs SSE | 둘 다 된다. MVP는 `GET /tasks/{id}` + `GET /tasks/{id}/events?after=` 폴링으로 충분. SSE는 `/stream?access_token=` (EventSource는 헤더를 못 보냄). |
| ① 요건 확정에서 내용 수정이 되나? | 확정 · 보류만. |
| ② "수정함"은 코드 직접 수정인가? | MVP는 코멘트로 수정 요청 (`REQUEST_CHANGES`). Agent가 반영하고 verify를 다시 돈다. |
| 검증 실패 패치의 publish 차단 예외? | 없음. 서버가 409로 막는다. 해당 위치를 `excludedSpecKeys`로 빼고 승인하거나 수정 요청한다. |
| 규정 파일 형식 | `.md` / `.markdown` / `.txt`, UTF-8, 파일당 1MB 이하. |
| 작업 티켓은 어디에 생기나? | 백엔드 `work_tickets` 테이블 (Jira 대신). 링크는 `/tickets/{id}`, 참조는 `RR-{id}`. |
| 명세서 출력 형식 | `GET /tasks/{id}/report`의 `report.markdown`. 화면 렌더링 + 다운로드. |
| history 모드 SPA fallback | frontend 컨테이너(nginx)의 `try_files $uri /index.html`로 처리. Spring은 `/api`만 담당. |

## 3. 인증 · 역할

| 역할 | 누구 | 할 수 있는 것 |
|---|---|---|
| `OFFICER` | 준법감시 · 상품기획 담당자 | 규정 업로드, ① 요건 확정 |
| `DEVELOPER` | 개발자 | 규정 업로드, ② 검토 · 승인 |

조회는 로그인한 사람 모두 가능. 역할이 다르면 403.

```http
POST /api/auth/login   {"username":"officer1","password":"password"}
→ {"token":"…","userId":1,"username":"officer1","displayName":"김준법","role":"OFFICER"}
GET  /api/auth/me
```

## 4. 작업 상태

FRONTEND_PLAN.md 작업 상태 표와 같은 이름을 쓴다.

```
EXTRACTING ─▶ WAITING_REQUIREMENT_APPROVAL ─(확정)─▶ LOCATING ─▶ DESIGNING ─▶ VERIFYING ─▶ WAITING_REVIEW ─(승인)─▶ PUBLISHING ─▶ DONE
     │                    │                                          ▲            │              │
     └▶ NO_CHANGE         └(전부 보류)▶ HELD                          └── 재시도 ──┘              ├(거절)▶ REJECTED
                                                                     ▲                          └(수정 요청)▶ DESIGNING
                                                                                  어디서든 오류 ▶ FAILED
```

단순화 흐름([SIMPLIFICATION.md](./SIMPLIFICATION.md))에서는 요건 확정 없이 `EXTRACTING ─▶ LOCATING`으로 바로 간다. 두 흐름 모두 허용한다.

`GET /tasks/{id}`의 `pendingAction`이 지금 사람이 할 일을 알려준다 (`{step, role, path}` 또는 `null`).
라우터 가드는 이 값과 `status`만 보면 된다.

## 5. 공개 API (`/api/ai/tasks`)

에러는 항상 `{"error":"CONFLICT","message":"…"}` 형태.
400 입력 오류 · 401 미인증 · 403 역할 · 404 없음 · 409 상태 충돌 · 413 파일 크기.

| 프론트 함수 | Method · Path | 요청 | 응답 |
|---|---|---|---|
| `createTask(files)` | `POST /api/ai/tasks` | multipart: `oldFile`, `newFile`, `title?`, `oldVersion?`(기본 v3), `newVersion?`(기본 v4) | **202** `{taskId, status}` |
| `listTasks()` | `GET /api/ai/tasks?mine=false` | `mine=true`면 내가 만든 것만 | `TaskSummary[]` |
| `getTask(id)` | `GET /api/ai/tasks/{id}` | | `{task, errorMessage, maxVerifyRounds, documents, pendingAction, counts, approvals, deliveries}` |
| `getRequirements(id)` | `GET /api/ai/tasks/{id}/requirements` | | `{task, documents, requirements[]}` — 요건마다 `oldText` · `newText` (신·구 원문) |
| `approveRequirements(id, d)` | `POST /api/ai/tasks/{id}/requirements/approval` | `{decisions:[{requirementId, decision:"CONFIRMED"\|"HELD"}], comment?}` — **모든 요건**에 결정 필요 | `Approval` |
| `getChangeSpecs(id)` | `GET /api/ai/tasks/{id}/change-specs` | | `{task, targets[], nonTargets[]}` — 대상 아님도 `reason` 포함 |
| `getPatches(id)` | `GET /api/ai/tasks/{id}/patches` | | `{task, maxVerifyRounds, latestRound, passed, failed, pending, canApprove, patches[], testCases[]}` — `patches`는 회차 전체, 현재 것은 `latest:true` |
| `reviewPatches(id, d)` | `POST /api/ai/tasks/{id}/review` | `{decision:"APPROVE"\|"REJECT"\|"REQUEST_CHANGES", comment?, excludedSpecKeys?}` — 수정 요청은 comment 필수 | `Approval` |
| `getReport(id)` | `GET /api/ai/tasks/{id}/report` | | `{task, report, deliveries[]}` |
| `getEvaluation(id)` | `GET /api/ai/tasks/{id}/evaluation` | | `{task, evaluation}` |
| (폴링) | `GET /api/ai/tasks/{id}/events?after={seq}` | | `{seq, type, payload, createdAt}[]` |
| (SSE) | `GET /api/ai/tasks/{id}/stream?access_token={jwt}` | `Last-Event-ID` 또는 `?after=` | `event: {type}` / `data: {seq,type,payload}` |
| `resetDemo()` | `POST /api/demo/reset` | | `{tasks, jobs, workTickets, products, productRules, loanContracts}` — 작업 기록을 지우고 업무 데이터를 `data/business` JSON으로 되돌린다. 계정은 남긴다. `DEMO_RESET_ENABLED=false`면 404 |

### 승인 규칙 (서버가 검사)

| 단계 | 누가 | 언제 | 검사 |
|---|---|---|---|
| ① 요건 확정 | OFFICER | `WAITING_REQUIREMENT_APPROVAL` | 모든 요건에 CONFIRMED/HELD. 하나라도 확정 → `LOCATING`, 전부 보류 → `HELD` |
| ② 검토 · 승인 | DEVELOPER | `WAITING_REVIEW` | APPROVE: 제외하지 않은 현재 패치가 **전부 PASSED**여야 함 (아니면 409). `canApprove`가 같은 판단을 미리 알려준다 |

### 주요 객체

```jsonc
// Requirement
{"id":1,"reqKey":"R1","summary":"고령자 기준 만 65세 → 만 60세","target":"개인신용대출 전 상품",
 "effectiveDate":"2026-12-01","transitional":"시행일 이후 신청분부터, 기존 계약은 종전 기준",
 "articleRef":"제15조","oldText":"…","newText":"…","status":"PROPOSED","detail":{}}

// ChangeSpec
{"id":3,"specKey":"S3","reqKey":"R1","locationKind":"CODE","filePath":"loan/pricing.py",
 "symbol":"SENIOR_AGE_MONTHS","lineNo":12,"snippet":"SENIOR_AGE_MONTHS = 780",
 "changeType":"VALUE","target":true,"reason":"월 단위로 표현된 같은 기준 (65×12)","bundle":"pricing","detail":{}}
// 대상 아님: "target":false, "changeType":null, "reason":"소득 추정용 은퇴 나이, 고령자 기준과 무관"

// Patch
{"id":7,"bundle":"eligibility","round":2,"specKeys":["S1","S4"],"filePath":"loan/eligibility.py",
 "diff":"--- a/…\n+++ b/…","verifyStatus":"PASSED","verifyLog":"…","failureKind":null,"latest":true,"detail":{}}

// Approval — ① 요건 확정은 요청 값 그대로 CONFIRMED(하나라도 확정) · HELD(전부 보류)
{"id":1,"step":"REQUIREMENTS","decision":"CONFIRMED","approverId":1,
 "approver":{"userId":1,"username":"officer1","displayName":"김준법","role":"OFFICER"},"comment":"R2 보류",
 "payload":{"decisions":[{"requirementId":1,"reqKey":"R1","decision":"CONFIRMED"},…],"confirmedReqKeys":["R1"]},
 "createdAt":"…"}
// ② 검토 · 승인은 step "REVIEW", decision APPROVE · REJECT · REQUEST_CHANGES
```

`changeType`: `VALUE`(①) · `LOGIC`(②) · `DB`(③) · `NEW`(④).
`locationKind`: `CODE` · `CONFIG` · `DB` · `PRODUCT` · `DOC` · `UI`.

### 진행 이벤트 `type`

| type | payload | 화면 |
|---|---|---|
| `STEP` | `{node, round?, message?}` | 단계 표시. `node=verify`면 `round`회차 |
| `APPROVAL_REQUIRED` | `{step:"REQUIREMENTS"\|"REVIEW", summary?}` | 승인 화면으로 이동 버튼 |
| `RESUMED` | `{step, decision, by, approvalId}` | |
| `TOOL_START` / `TOOL_END` | `{tool, attempt?, ok?, retrying?, dead?, error?}` | 큐 재시도 표시 |
| `TOKEN` | `{text}` | (선택) 스트리밍 |
| `STATUS` | `{status}` | |
| `DONE` | `{outcome:"DONE"\|"NO_CHANGE"\|"HELD"\|"REJECTED"}` | 종료 |
| `FAILED` | `{reason}` | 오류 화면 |

## 6. Agent ↔ Spring

### Spring → Agent

둘 다 바로 202를 돌려주고, 실제 작업은 백그라운드에서 한다.

```http
POST {AGENT_BASE_URL}/run
{"taskId","threadId","title","documents":[{"side":"OLD","versionLabel":"v3","filename","sha256"},…],
 "callbackBaseUrl":"http://backend:8080/internal/tasks/{taskId}"}

POST {AGENT_BASE_URL}/resume
{"taskId","threadId","step":"REQUIREMENTS"|"REVIEW","decision","approvalId","comment","payload","callbackBaseUrl"}
```

`decision`: REQUIREMENTS면 `CONFIRMED` · `HELD`, REVIEW면 `APPROVE` · `REJECT` · `REQUEST_CHANGES`.

`/resume`의 payload
- REQUIREMENTS: `{decisions:[{requirementId, reqKey, decision}], confirmedReqKeys:[…]}`
- REVIEW: `{excludedSpecKeys:[…], approvedPatchIds:[…]}` (APPROVE일 때만 approvedPatchIds)

### Agent → Spring (`{callbackBaseUrl}` 아래)

| Method · Path | 언제 | 본문 |
|---|---|---|
| `GET /documents` | 시작 | → `[{side, versionLabel, filename, sha256, content}]` |
| `GET ` (빈 경로) | 아무 때 | → `{id, title, status, currentNode, verifyRound, threadId}` |
| `GET /approvals` | 재개 후 | → interrupt 기록 |
| `POST /events` | 노드 진입마다 | `{type, payload}` — 위 이벤트 표 |
| `PUT /requirements` | requirement_agent | `{items:[{reqKey, summary, target, effectiveDate, transitional, articleRef, oldText, newText, detail}]}` — EXTRACTING에서만, 전체 교체 |
| `PUT /change-specs` | locate_agent | `{items:[{specKey, reqKey, locationKind, filePath, symbol, lineNo, snippet, changeType, target, reason, bundle, detail}]}` — specKey upsert |
| `POST /test-cases` | test_agent | `{items:[{testKey, reqKey, name, kind:"BOUNDARY"\|"EFFECTIVE_DATE"\|"SYSTEM"\|"UNIT", code, detail}]}` — **한 번만**, 두 번째는 409 |
| `PUT /patches` | design · verify | `{items:[{bundle, round, filePath, specKeys, diff, verifyStatus, verifyLog, failureKind, detail}]}` — (bundle, round, filePath) upsert. verify 결과만 보낼 땐 diff 생략 |
| `PUT /report` | report | `{markdown, summary, …}` 자유 형식 |
| `PUT /evaluation` | 평가 스크립트 | `{locate:{recall, precision}, trapErrors, verifyPassRate:{round1, round2}, keywordBaseline:{…}, elapsedSec}` |

**순서 규칙**
1. 노드에 들어갈 때 `STEP {node}` → 상태가 자동으로 바뀐다 (`requirement_agent`→EXTRACTING, `test_agent`·`locate_agent`→LOCATING, `plan`·`worker`·`merge`·`design_agent`→DESIGNING, `verify`→VERIFYING, `publish`→PUBLISHING).
2. interrupt 직전에 산출물을 저장하고 `APPROVAL_REQUIRED {step}`을 보낸다.
3. 실질 변경 0건이면 `PUT /report` 후 `DONE {outcome:"NO_CHANGE"}`.
4. 끝나면 `PUT /report` 후 `DONE {outcome}`. 오류는 `FAILED {reason}`.
5. 위 4절 그림에 없는 전이는 409. `WAITING_*`에서 나가는 전이는 승인 API만 한다 (Agent는 `FAILED`만 가능). 종료 상태는 같은 outcome의 `DONE`만 받는다.
6. 산출물 저장 가능 상태 (그 밖에는 409)

| 경로 | 상태 |
|---|---|
| `PUT /requirements` | EXTRACTING |
| `PUT /change-specs` | LOCATING · DESIGNING · VERIFYING |
| `POST /test-cases` | LOCATING |
| `PUT /patches` | DESIGNING · VERIFYING (검토 대기부터 고정) |

## 7. 쓰기 도구 (승인 필요)

`create_draft_pr` · `create_work_ticket`은 서버가 다시 검증한다. 그래프의 interrupt만 믿지 않는다.

```http
POST /internal/work-tickets      ← business-mcp create_work_ticket
{"taskId","approvalId","idempotencyKey":"{taskId}:ticket:S1","title","description","type":"CODE"|"CONFIG"|"DB"|"DOC"|"TEST","targetRef","specKeys":["S1"],"assignee"}

POST /internal/jobs              ← code-mcp create_draft_pr
{"taskId","type":"CREATE_DRAFT_PR","approvalId","idempotencyKey":"{taskId}:pr:{bundle}","payload":{"branch","specKeys":[…]}}

→ 202 {"jobId","status":"QUEUED","duplicate":false}
```

검증 (실패 시 403/409)
1. `approvalId`가 같은 작업의 REVIEW/APPROVE 기록
2. 그 작업의 **가장 최근** REVIEW 결정 (이후 수정 요청 · 거절로 뒤집히지 않음)
3. 작업 상태가 `PUBLISHING`
4. `specKeys`가 승인 때 제외한 위치가 아님

같은 `idempotencyKey`로 다시 오면 새로 만들지 않고 기존 `jobId`를 `duplicate:true`로 돌려준다.
publish 노드는 `GET /internal/jobs/{jobId}`로 `DONE`/`DEAD`를 기다린 뒤 report로 간다.

## 8. 큐 (가산점)

```
rr.jobs (direct) ─ job ──▶ rr.jobs.q ──▶ JobWorker ─ 성공 ─▶ DONE
                                             └ 실패 ─▶ rr.jobs.retry (TTL 3초) ─▶ rr.jobs.q   (최대 3회)
                                                        └ 3회 실패 ─▶ rr.jobs.dlq  (DEAD)
```

- 처리 중 `TOOL_START` / `TOOL_END {ok, attempt, retrying, dead}` 이벤트가 화면으로 간다.
- 시연용으로 `JOB_FAILURE_RATE`(기본 0.3) 확률로 일부러 실패한다. `0`이면 항상 성공.
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
