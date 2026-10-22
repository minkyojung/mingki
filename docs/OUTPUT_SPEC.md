# AI 출력 규격 (교재 규격과의 대응)

설계서 STEP 05 "AI 출력 규격"과 검증 결과서가 그대로 쓰는 표다. 교재는 `{status: ok | needs_review | denied, answer, sources[{doc, clause}]}`를 요구하고, "ok인데 출처가 없으면 규격 위반"으로 본다. 우리 서비스의 AI 출력은 질문 하나에 답 하나가 아니라 **상품 12개 × 판정 1개**라서, 상품 행 하나가 교재의 답 하나에 해당한다. 행의 모양은 [BACKEND_API 10.2](./BACKEND_API.md)다.

## 1. 출력 한 건 (상품 행)

```json
{
  "productId": "P001",
  "impact": "change_required",                       // 판정: change_required · keep · needs_check
  "change": {"ruleId": "R001", "ruleKey": "DSR_LIMIT", "currentValue": "0.40",
             "proposedValue": "0.35", "unit": "ratio", "effectiveDate": "2026-12-01"},   // keep · needs_check 면 없음
  "proposed": "DSR 한도를 40%에서 35%로 낮추고 …",       // 제안 문장 (= answer)
  "reason": "현재 상품 기준이 개정 한도를 초과함",
  "basis": "reg://credit-loan-ops/v4/8, reg://credit-loan-ops/v4/supp-2",   // 근거 조항 (= sources)
  "followUp": ["화면 문구 'DSR 40% 이내' 수정"],
  "verifyStatus": "PASSED",                           // 코드 검사: PENDING · PASSED · FAILED
  "revisionOutcome": "none"                           // 수정 요청 결과: none · revised · cannot_apply · needs_clarification · failed
}
```

| 교재 필드 | 우리 필드 | 비고 |
|---|---|---|
| `status` | `impact` + `verifyStatus` + `revisionOutcome` 로 계산 (2절) | 화면이 세 값으로 상태를 그린다 |
| `answer` | `proposed` · `reason` · `followUp` | 변경이면 `change`의 값 · 단위 · 시행일이 붙는다 |
| `sources[{doc, clause}]` | `basis` = `reg://{규정}/{버전}/{조항}` 목록 | `doc` = 규정 ID, `clause` = 조항. 상품 문서 근거는 `doc://{상품}` |
| `synthetic` | 없음 | 데이터는 전부 가상 "리플은행" (`data/`) |

## 2. status 대응표

| 우리 상태 | 교재 `status` | 뜻 | 사람이 할 수 있는 일 |
|---|---|---|---|
| `change_required` + `PASSED` | **ok** | 바꿀 규칙 · 값 · 근거가 조회 자료와 맞음 | 반영 / 제외 / 수정 요청 |
| `keep` (근거 조항 있음) | **ok** | 바꾸지 않아도 되는 이유와 조항 | 없음 (유지는 제안이 아님) |
| `needs_check` | **needs_review** | 자료에 없거나 적용 대상이 불확실. 지어내지 않고 보류 | 제외 / 수정 요청 |
| `change_required` + `FAILED` | **needs_review** | 값 · 단위 · 인용이 조회 자료와 달라 두 번 써도 검사 실패. 승인 불가 | 제외 / 수정 요청 |
| `revisionOutcome: needs_clarification` | **needs_review** | 수정 요청이 모호하거나 재분석이 필요. 본문은 이전 그대로 | 다시 요청 / 제외 |
| `revisionOutcome: cannot_apply` | **denied** | 요청이 개정 원문과 충돌 (예: 35% 대신 30%). 본문 그대로, 사유 표시 | 이전 본문으로 반영 |
| 승인 없는 반영 요청 (`apply_rule_version`) | **denied** | 서버가 승인 기록을 재검증해 403 | 해당 없음 (Agent · 도구가 받는 응답) |
| `keep` · `needs_check`인데 근거 조항이 비어 있음 | 규격 위반 (교재의 "ok인데 출처 없음") | 서버는 막지 않고 채점기가 "유지 · 추가 확인 근거" 지표로 깎는다 | 개선 후보 (`docs/experiments.md` P009) |

`denied`는 교재에서 "권한 밖 문서"이지만 우리 서비스에는 문서 권한이 없다(단일 테넌트, 4절). 그래서 `denied`를 "요청 거절"로 읽어 원문과 충돌하는 수정 요청과 승인 없는 반영에 대응시켰다.

## 3. 출처 규칙과 검사 위치

| 규칙 | 어디서 | 어기면 |
|---|---|---|
| 변경 제안의 근거 조항 · 시행일은 요건의 것과 같아야 한다 | `agent/app/validation.py` `validate_proposal` | `FAILED`, 재작성 1회 뒤에도 실패면 승인 불가 |
| 값 · 단위는 개정 원문 숫자를 규칙 단위로 바꾼 것이어야 한다 (ratio 0.35 · percent 35) | 같은 곳 `expected_value` | `FAILED` |
| 적용 조건의 인용문은 조회한 원문에 실제로 있어야 한다 | `condition_errors` | `FAILED` |
| 판정의 근거 조항은 이번에 조회한 조항만 남긴다 | `agent/app/nodes/impact.py` (`basis_refs` 필터) | 조회하지 않은 조항은 근거에서 빠진다 |
| 조회하지 못한 자료는 `data_gaps`로 명시하고 지어내지 않는다 | `impact.py` · 프롬프트 | 해당 상품은 `needs_check` |
| 행의 필수 칸 (`productId` · `impact` · `reason`, 변경이면 `ruleKey` · `unit` · `proposedValue` · `effectiveDate`) | 백엔드 `Proposals.invalid` | 저장 거부 400 |
| 반영은 승인 기록 · 작업 상태 · 선택 여부를 서버가 다시 본다 | `internal/ApprovalGuard` | 403 |

## 4. 실패 네 가지 대응 (교재 9쪽)

| 교재 실패 | 우리 서비스 | 처리 경로 | 결과 |
|---|---|---|---|
| 접근 권한 부족 | 문서 권한은 없다 (은행 한 곳, 담당자 모두 같은 자료). 대신 **도구 호출 권한**이 있다 | 쓰기 도구 `apply_rule_version`은 승인 기록 ID만 받고 서버가 재검증. `/internal/**`은 내부 토큰 | 403, 작업은 그대로 |
| 검색 결과 없음 | 바뀐 조항이 0건 / 상품 문서 · 규칙이 조회되지 않음 | 요건 0건이면 리포트로 바로 (`NO_CHANGE`). 자료가 없는 상품은 `data_gaps`에 적고 `needs_check` | `NO_CHANGE` · `needs_review` |
| 근거 부족 | 판정은 했지만 근거 조항 · 인용이 조회 자료와 맞지 않음 | 코드 검사 실패 → 같은 버전에서 1회 재작성 → 그래도 실패면 `FAILED`로 확인 화면에 보이고 승인 불가 | `needs_review` (승인 불가) |
| 외부 시스템 오류 | LLM · MCP 호출 실패, Agent 프로세스 종료, 반영 큐 실패 | 조회는 재시도, 제안 작성 실패는 `generation_error`로 `FAILED`. Agent가 죽으면 작업 `FAILED` → "다시 시도"가 멈춘 자리부터. 반영 큐는 3초 뒤 재시도 3회 → DLQ | `FAILED` + 사유, 다시 시도 가능 |

- 폴백은 권한을 올리지 않는다. 다시 시도도 저장된 승인 · 수정 요청을 그대로 다시 보낸다.
- 네 가지 모두 작업 이벤트(`STEP` · `TOOL_START/END` · `DECISION` · `STATUS` · `FAILED`)에 남아 어느 경로를 탔는지 `GET /events`로 본다.

## 5. STEP 05에 옮길 때

- "AI 출력 규격" 칸에는 1절 JSON과 2절 표를 넣는다. `status`는 저장 칸이 아니라 세 값에서 계산한다고 적는다.
- "처리 실패 시 응답"은 4절 표의 결과 열이다.
- 검증 결과서 "AI 출력 품질 점검"의 기대 결과 열은 정답지의 판정 + 근거 조항이고, 충족 기준은 `docs/experiments.md`의 채점 지표다.
