# Vector DB 색인 스펙

`regulation-mcp`의 `search_regulation`(의미 검색)이 쓰는 Vector DB를 정리합니다. 평가표의 **rag-mcp 역할 = regulation-mcp**입니다.

구현: `mcp/regulation-mcp/regulation_mcp/vector.py`

## 1. 구성

| 항목 | 선택 | 이유 |
|---|---|---|
| 저장소 | Chroma, regulation-mcp 프로세스 안(메모리) | 조각이 50개 미만이라 별도 서버가 필요 없음. 컨테이너에 마운트된 원문만 보므로 정답 노출 범위도 그대로 |
| 임베딩 | OpenAI `text-embedding-3-small` (`EMBEDDING_MODEL`로 변경 가능) | 팀이 이미 OpenAI 키를 씀. 한국어 품질 |
| 색인 시점 | (규정, 버전)별 첫 검색 때. 원문 해시가 같으면 다시 만들지 않음 | 원문을 바꾸면 재시작 없이 다시 색인 |
| 대체 수단 | `OPENAI_API_KEY`가 없거나 임베딩 호출 실패 시 키워드 검색 | 키 없는 로컬 개발 · 테스트, 네트워크 장애 |

## 2. 색인 대상

| 원본 | 조각 단위 |
|---|---|
| `data/regulations/*/v*.md` | 조항 1개 |

**색인하지 않는 것**: 각 폴더의 `README.md`, `docs/`, `eval/`. 정답과 판단 근거가 들어 있습니다. regulation-mcp는 `{id}/v{N}.md`만 읽을 수 있어서 구조적으로 막혀 있습니다.

## 3. 조각

조항 헤더 정규식으로 자릅니다(`data/regulations/README.md`). 임베딩 텍스트는 규정 제목과 조항 제목을 앞에 붙입니다.

```
개인신용대출 운영기준 > 제15조(고령자 기준)
이 기준에서 "고령자"란 신청일 현재 만 60세 이상인 자를 말한다.
```

| 메타데이터 | 예 |
|---|---|
| `regulation_id` | `credit-loan-ops` |
| `version` | `v4` |
| `article_id` | `15`, `15-2`, `supp-2` |
| `title` | `고령자 기준` |
| `effective_from` | `2026-12-01` (frontmatter) |
| `uri` | `reg://credit-loan-ops/v4/15` |

## 4. 검색 인터페이스

```
search_regulation(query, regulation_id="credit-loan-ops", version="v4")
  -> [{ uri, article_id, title, text }]   관련도 순, 최대 5개 (SEARCH_TOP_K)
```

- 키워드 검색과 반환 형식이 같습니다. Agent 쪽 변경은 없습니다.
- `version` 필터로 버전 하나 안에서만 찾습니다. v3와 v4는 대부분 문장이 같아서 섞이면 같은 조항이 두 번 나옵니다.

## 5. 평가

`eval/retrieval.yaml` 질의 16개(원문 단어를 쓴 12개 + 바꿔 쓴 4개)로 잽니다.

```
uv run --project mcp/regulation-mcp --with pyyaml python eval/run_retrieval.py
```

| 검색 | recall@3 | MRR | not_top1 위반 |
|---|---|---|---|
| 키워드 (이전) | 1/16 (6%) | 0.06 | 0 |
| 의미 검색 (Vector DB) | 15/16 (94%) | 0.92 | 0 |

2026-10-20 측정. 놓친 1건은 Q13 "노인으로 분류되는 연령"(기대: 제15조)입니다. `not_top1`은 같은 숫자 · 비슷한 단어 때문에 엉뚱한 조항이 1위로 나오는 경우(고령자 기준 질의에 제7조 정년 등)를 잡습니다.
