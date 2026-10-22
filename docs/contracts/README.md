# 계약 예시

`docs/BACKEND_API.md` 10절의 요청 · 응답 예시. 값은 `data/business`(#19)와 정답지(#21) DSR 시나리오를 따른다.

각 영역 테스트가 이 파일을 그대로 읽는다. 백엔드는 요청 예시를 받아들이는지, Agent · MCP · 프론트는 응답 예시를 처리하는지 확인한다. 모양을 바꾸려면 이 파일과 10절을 같은 PR로 고친다.

| 파일 | 방향 | API |
|---|---|---|
| `proposals.put.json` | Agent → Spring | `PUT {callbackBaseUrl}/proposals` |
| `review.post.json` | 화면 → Spring | `POST /api/ai/tasks/{id}/review` (일부 제외하고 승인) |
| `review.post.reject.json` | 화면 → Spring | 같은 API로 반려. `comment` 필수 |
| `revisions.post.json` | 화면 → Spring | `POST /api/ai/tasks/{id}/revisions` (제안 하나 수정 요청, 10.3) |
| `apply.post.json` | business-mcp → Spring | `POST /internal/product-rules/versions` |
| `apply.job.json` | Spring → business-mcp · Agent | `GET /internal/jobs/{jobId}` 결과 |
| `rule-keys.get.json` | Spring → business-mcp | `GET /internal/product-rules/keys` |
| `contract-impact.get.json` | Spring → business-mcp | `GET /internal/contracts/impact?dsrFrom=0.35&dsrTo=0.40&appliedBefore=2026-12-01` |

## 화면 응답 예시 (프론트용)

실제 서버 응답을 그대로 받아 작업 ID만 바꿨다. DSR 시나리오에서 P001 · P005 변경, P003 유지, P010 추가 확인이 나온 뒤 P005를 제외하고 승인해 P001을 반영한 흐름이다.

| 파일 | API | 시점 |
|---|---|---|
| `task.get.json` | `GET /api/ai/tasks/{id}` | 확인 대기 (`pendingAction`, `counts.proposals`) |
| `proposals.get.json` | `GET /api/ai/tasks/{id}/proposals` | 확인 대기. 현재 회차만 추림 (`latest: true`). `productName` 포함, `canApprove: true` |
| `proposals.get.done.json` | 같은 API | 반영 뒤. `decision`(APPLY · EXCLUDE)과 `apply`(큐 작업 · 새 `rule_id`) 붙음 |
| `contract-impacts.task.get.json` | `GET /api/ai/tasks/{id}/contract-impacts` | Agent 판단 + 서버가 붙인 계약 정보 (`contractFound` · `productName` · `appliedAt` · `dsrAtApproval` …) |
| `report.get.json` | `GET /api/ai/tasks/{id}/report` | 완료. `review`(가장 최근 확인 기록), `deliveries`에 `RULE_VERSION` |
| `report.get.rejected.json` | 같은 API | 반려 뒤. `review.decision: REJECT`와 사유, 전달 기록 없음. 다른 작업의 실제 응답이고 `report`는 `status` · `summary` · `markdown`만 남겼다 |
| `events.get.json` | `GET /api/ai/tasks/{id}/events?after=0` | 업로드부터 `DONE`까지 이벤트 순서. `STEP` · `APPROVAL_REQUIRED` · `RESUMED` · `TOOL_START/END` · `DONE` |
