# Dumbo · Frontend

Vue 3 + Vite + TypeScript + Tailwind CSS v4 + shadcn-vue

## 요구 사항

- Node 22 LTS (`.nvmrc`)
- pnpm 10

## 시작

```bash
pnpm install
pnpm dev          # http://localhost:5173, /api는 백엔드(localhost:8080)로 전달
```

## 시연 실행

```bash
docker compose up -d     # 레포 루트에서: 백엔드 · Agent · MCP · DB
pnpm -C frontend dev     # http://localhost:5173
```

한 페이지에서 시작 → AI 작업 중 → 확인(체크 후 바꾸기) → 완료 순서로 진행한다. 로그인 화면 없이 시연 계정(officer1)으로 동작하고, 규정 원문은 `data/regulations/credit-loan-ops/`의 v3 · v4를 그대로 올린다. [초기화]는 업무 데이터와 작업 기록을 처음 상태로 되돌린다.

## 명령

| 명령                                | 설명                                |
| ----------------------------------- | ----------------------------------- |
| `pnpm dev`                          | 개발 서버                           |
| `pnpm build`                        | 타입 검사 + 프로덕션 빌드 (`dist/`) |
| `pnpm typecheck`                    | 타입 검사만                         |
| `pnpm lint` / `pnpm lint:fix`       | ESLint                              |
| `pnpm format` / `pnpm format:check` | Prettier                            |
| `pnpm test` / `pnpm test:watch`     | Vitest                              |

## 환경 변수

| 변수            | 개발 기본값 | 설명                                                |
| --------------- | ----------- | --------------------------------------------------- |
| `VITE_API_BASE` | `/api`      | 백엔드 API 기본 경로                                |
| `VITE_USE_MOCK` | `false`     | `true`면 MSW가 API 요청을 가로채 가짜 데이터로 응답 |

개발 서버는 `/api`를 `http://localhost:8080`으로 전달한다 (`vite.config.ts`).

## 폴더 구조

```
src/
├─ domain/        # 도메인 타입 (요건·영향 위치·패치·검증·승인·작업 상태)
├─ api/           # 백엔드 호출 + 응답→도메인 변환. 백엔드 변경은 여기만 영향
├─ mocks/         # MSW 핸들러 + 시나리오 fixture
├─ stores/        # Pinia
├─ components/
│  ├─ ui/         # shadcn-vue 생성 컴포넌트 (pnpm dlx shadcn-vue@latest add <name>)
│  └─ domain/     # 도메인 데이터를 그리는 컴포넌트
├─ pages/         # 라우트 단위 화면
├─ router/
└─ composables/
```

## 규칙

- 화면과 컴포넌트는 `api/`를 통해서만 서버와 통신한다. `fetch`를 직접 쓰지 않는다.
- `components/ui/`는 shadcn-vue CLI로 추가하고, 필요하면 직접 수정한다.
- 라우터는 history 모드다. 배포 시 Spring에 SPA fallback(모든 경로 → `index.html`)이 필요하다.
