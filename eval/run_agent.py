"""Agent 정확도 측정: 실행 중인 스택으로 Agent를 여러 번 돌리고 회차마다 채점한다.

회차마다: 초기화(POST /api/demo/reset) → 규정 v3 · v4 업로드 → 확인 대기까지 기다림 → 제안 저장 · 채점.
반영(확인)은 하지 않는다. 채점에는 제안만 필요하고, 업무 데이터도 바뀌지 않는다.
초기화는 작업 기록과 업무 데이터를 처음 상태로 되돌리므로 시연용 로컬 스택에서만 쓴다.
실행할 때마다 Agent가 OpenAI를 호출한다 (비용 발생).

실행 (레포 루트에서, docker compose up 뒤):
    uv run --with pyyaml python eval/run_agent.py --runs 3
기본(--expected all)은 결과를 요건별로 나눠 정답 파일마다 채점한다. 조항 하나만 띄웠으면 정답 파일을 지정해도 된다:
    15   → --expected eval/expected_proposals.senior-age.yaml
    15-2 → --expected eval/expected_proposals.senior-explanation.yaml

회차마다 화면 응답을 eval/results/ 에 저장한다. score_proposals.py 로 다시 채점할 수 있다.
모든 회차가 통과하면 종료 코드 0, 아니면 1.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from score_proposals import COUNT_METRICS, load_proposals, score

ROOT = Path(__file__).resolve().parents[1]
REGULATION = ROOT / "data" / "regulations" / "credit-loan-ops"
RESULTS = Path(__file__).parent / "results"
STOP = {"WAITING_REVIEW", "DONE", "NO_CHANGE", "REJECTED", "FAILED"}


class Api:
    def __init__(self, base):
        self.base, self.token = base.rstrip("/"), None

    def call(self, method, path, body=None, content_type="application/json"):
        headers = {"Content-Type": content_type} if body is not None else {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if isinstance(body, dict):
            body = json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                return json.loads(res.read() or b"null")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} → {e.code} {e.read().decode(errors='replace')[:200]}") from None

    def login(self):
        self.token = self.call("POST", "/api/auth/login", {"username": "officer1", "password": "password"})["token"]

    def upload(self, old, new):
        boundary = uuid.uuid4().hex
        parts = []
        for field, path in (("oldFile", old), ("newFile", new)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
                         f"Content-Type: text/markdown\r\n\r\n".encode() + path.read_bytes() + b"\r\n")
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self.call("POST", "/api/ai/tasks", body, f"multipart/form-data; boundary={boundary}")["taskId"]


def run_once(api, timeout):
    """초기화 → 업로드 → 멈출 때까지 기다림. 확인 대기면 제안 응답을 돌려준다."""
    api.call("POST", "/api/demo/reset")
    task_id = api.upload(REGULATION / "v3.md", REGULATION / "v4.md")
    started, seen = time.monotonic(), []
    while True:
        detail = api.call("GET", f"/api/ai/tasks/{task_id}")
        status = detail["task"]["status"]
        if not seen or seen[-1] != status:
            seen.append(status)
            print(f"    {' → '.join(seen)}", end="\r", flush=True)
        if status in STOP or time.monotonic() - started > timeout:
            break
        time.sleep(2)
    seconds = round(time.monotonic() - started)
    print(f"    {' → '.join(seen)} ({seconds}초)")
    run = {"taskId": task_id, "status": status if status in STOP else "TIMEOUT", "seconds": seconds,
           "error": detail.get("errorMessage")}
    if status == "WAITING_REVIEW":
        run["response"] = api.call("GET", f"/api/ai/tasks/{task_id}/proposals")
    return run


def scenarios(expected_arg):
    """[(정답, 요건 키)]. all 이면 정답 파일마다 그 요건(REQ-{조항})의 제안만 채점한다. 하나면 요건 키 없이 전부."""
    if expected_arg != "all":
        return [(yaml.safe_load(Path(expected_arg).read_text(encoding="utf-8")), None)]
    out = []
    for path in sorted(Path(__file__).parent.glob("expected_proposals*.yaml")):
        exp = yaml.safe_load(path.read_text(encoding="utf-8"))
        out.append((exp, "REQ-" + exp["requirement"]["article"].split("/", 1)[1]))
    return out


def summarize(runs, expected, req_key=None):
    """회차별 채점 결과와 평균 · 상품별 정답 횟수. req_key가 있으면 그 요건의 제안만 채점한다."""
    rows, per_product, totals = [], {}, {}
    for i, run in enumerate(runs, 1):
        if "response" not in run:
            rows.append({"run": i, "status": run["status"], "seconds": run["seconds"], "error": run.get("error"), "result": None})
            continue
        response = run["response"]
        if req_key:
            response = {"proposals": [p for p in response["proposals"] if p.get("reqKey") == req_key]}
        result = score(load_proposals(response), expected)
        rows.append({"run": i, "status": run["status"], "seconds": run["seconds"], "result": result})
        for p in result["products"]:
            per_product.setdefault(p["product_id"], 0)
            per_product[p["product_id"]] += p["ok"]
        for name, v in result["core"].items():
            t = totals.setdefault(name, [0, 0])
            t[0] += v["ok"]
            t[1] += v["total"]
    scored = sum(r["result"] is not None for r in rows)
    average = {name: (ok / scored, total / scored) for name, (ok, total) in totals.items()} if scored else {}
    passed = sum(bool(r["result"] and r["result"]["passed"]) for r in rows)
    return {"rows": rows, "scored": scored, "passed": passed, "average": average, "per_product": per_product}


def print_summary(summary, scenario):
    names = list(summary["average"])
    print(f"\n시나리오: {scenario}  ·  통과 {summary['passed']}/{len(summary['rows'])}회\n")
    print("회차  " + "  ".join(names) + "  시간")
    for r in summary["rows"]:
        if r["result"] is None:
            print(f"{r['run']:<4}  {r['status']} ({r['seconds']}초) {r.get('error') or ''}")
            continue
        core = r["result"]["core"]
        # 회차마다 지표 집합이 다를 수 있다 (예: 변경 대상을 하나도 못 찾으면 값 · 단위 지표가 없다). 없으면 "-"
        cells = ["-" if n not in core else f"{core[n]['ok']}건" if n in COUNT_METRICS else f"{core[n]['ok']}/{core[n]['total']}"
                 for n in names]
        print(f"{r['run']:<4}  " + "  ".join(f"{c:>{len(n)}}" for c, n in zip(cells, names)) + f"  {r['seconds']}초"
              + ("  통과" if r["result"]["passed"] else ""))
    if summary["scored"]:
        avg = [f"{ok:.1f}건" if n in COUNT_METRICS else f"{ok:.1f}/{total:.0f}" for n, (ok, total) in summary["average"].items()]
        print("평균  " + "  ".join(f"{c:>{len(n)}}" for c, n in zip(avg, names)))
        print(f"\n상품별 정답 횟수 (채점된 {summary['scored']}회 중)")
        print("  " + " · ".join(f"{pid} {ok}" for pid, ok in sorted(summary["per_product"].items())))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runs", type=int, default=1, help="실행 횟수 (기본 1)")
    ap.add_argument("--base", default=os.environ.get("BASE", "http://localhost:8080"), help="백엔드 주소")
    ap.add_argument("--timeout", type=int, default=300, help="회차마다 확인 대기까지 기다릴 초 (기본 300)")
    ap.add_argument("--expected", default="all", help="all(기본, 요건마다 정답 파일) 또는 정답 YAML 하나")
    args = ap.parse_args()
    targets = scenarios(args.expected)
    label = "all" if args.expected == "all" else targets[0][0]["scenario"]

    api = Api(args.base)
    api.login()
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = []
    for i in range(1, args.runs + 1):
        print(f"[{i}/{args.runs}] 실행")
        run = run_once(api, args.timeout)
        runs.append(run)
        if "response" in run:
            path = RESULTS / f"{stamp}-{label}-{i}.json"
            path.write_text(json.dumps({**run["response"], "run": {k: v for k, v in run.items() if k != "response"}},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    passed = True
    for expected, req_key in targets:
        summary = summarize(runs, expected, req_key)
        print_summary(summary, expected["scenario"] + (f" ({req_key})" if req_key else ""))
        passed = passed and summary["passed"] == len(runs)
    print(f"\n회차별 결과: {RESULTS.relative_to(ROOT)}/{stamp}-*.json")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
