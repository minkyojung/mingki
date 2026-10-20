"""search_regulation 검색 평가: 키워드 검색 vs 의미 검색(Vector DB).

eval/retrieval.yaml 의 질의로 recall@k, MRR, not_top1 위반 건수를 잰다.
의미 검색은 OPENAI_API_KEY가 있을 때만 실행한다 (임베딩 호출 비용 발생, 1회 수십 건).

실행 (레포 루트에서):
    uv run --project mcp/regulation-mcp --with pyyaml python eval/run_retrieval.py
"""

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp" / "regulation-mcp"))

from regulation_mcp import regulations as reg
from regulation_mcp.vector import TOP_K, VectorIndex, openai_embedder

SPEC = yaml.safe_load((Path(__file__).parent / "retrieval.yaml").read_text(encoding="utf-8"))
K = SPEC["k"]
REGULATION_ID = "credit-loan-ops"


def evaluate(name, search):
    hits, rr, violations, rows = 0, 0.0, 0, []
    for q in SPEC["regulation"]:
        ranked = [h["article_id"] for h in search(q["query"], REGULATION_ID, q["version"])][:TOP_K]
        rank = next((i + 1 for i, aid in enumerate(ranked) if aid in q["expected"]), None)
        hit = rank is not None and rank <= K
        violated = bool(ranked) and ranked[0] in q.get("not_top1", [])
        hits += hit
        rr += 1 / rank if rank else 0
        violations += violated
        mark = ("hit " if hit else "MISS") + (" !top1" if violated else "")
        rows.append(f"  {q['id']} {mark:11} {str(ranked[:K]):28} {q['query']}")

    n = len(SPEC["regulation"])
    print(f"\n[{name}] recall@{K} {hits}/{n} ({hits / n:.0%}) · MRR {rr / n:.2f} · not_top1 위반 {violations}")
    print("\n".join(rows))


evaluate("키워드 검색", reg.search)
if os.environ.get("OPENAI_API_KEY"):
    evaluate("의미 검색 (Vector DB)", VectorIndex(openai_embedder).search)
else:
    print("\n[의미 검색] OPENAI_API_KEY가 없어 건너뜀")
