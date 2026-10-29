"""本地网关压测：MOCK 全链路（create -> poll -> completed）。

用法：
  python stress_test.py --url http://127.0.0.1:8979 --concurrency 8 --total 80
"""

from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import requests

PROMPT = "a neon cat walking through rainy Tokyo street, cinematic"


def one_job(base: str, verify: bool) -> dict:
    t0 = time.perf_counter()
    r = requests.post(f"{base}/v1/jobs", json={"prompt": PROMPT, "mode": "mock"}, timeout=15)
    r.raise_for_status()
    job_id = r.json()["job_id"]
    created = time.perf_counter() - t0

    completed = None
    if verify:
        deadline = time.time() + 30
        while time.time() < deadline:
            j = requests.get(f"{base}/v1/jobs/{job_id}", timeout=10).json()
            if j.get("status") in {"COMPLETED", "FAILED", "BLOCKED_NO_WORKER"}:
                completed = j.get("status")
                break
            time.sleep(0.25)
    return {"create": created, "end_to_end": time.perf_counter() - t0, "status": completed}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return ordered[idx]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8979")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--total", type=int, default=64)
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args()

    verify = not args.no_verify
    print(f"target={args.url} concurrency={args.concurrency} total={args.total} verify_pipeline={verify}")

    results, errors = [], 0
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(one_job, args.url, verify) for _ in range(args.total)]
        for fut in futures:
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001 - 压测需统计任何失败
                errors += 1
                print("  error:", repr(exc)[:160])
    wall = time.perf_counter() - t0

    creates = [r["create"] for r in results]
    e2e = [r["end_to_end"] for r in results]
    statuses: dict[str, int] = {}
    for r in results:
        key = str(r["status"])
        statuses[key] = statuses.get(key, 0) + 1

    print(f"\nrequests={len(results)} errors={errors} wall={wall:.2f}s rps={len(results)/wall:.1f}")
    print(f"create  p50={pct(creates,50)*1000:.1f}ms p95={pct(creates,95)*1000:.1f}ms max={(max(creates)*1000) if creates else 0:.1f}ms")
    if verify:
        print(f"e2e     p50={pct(e2e,50):.2f}s p95={pct(e2e,95):.2f}s")
        print(f"status  {statuses}")
        ok = statuses.get("COMPLETED", 0)
        print(f"全链路成功率: {ok}/{len(results)} = {ok/len(results)*100:.1f}%")
    print(f"mean create: {statistics.mean(creates)*1000:.1f}ms" if creates else "")


if __name__ == "__main__":
    main()
