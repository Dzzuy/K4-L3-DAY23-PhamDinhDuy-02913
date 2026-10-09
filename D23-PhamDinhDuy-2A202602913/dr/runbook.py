"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr.health_checker import probe  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Record one incident response step."""
    record = {"ts": time.time(), "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "step": n, "name": name, **kw}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as stream:
        stream.write(json.dumps(record) + "\n")
    return record


def confirm(auto: bool, msg: str) -> bool:
    """Require an explicit operator confirmation outside automated drills."""
    if auto:
        return True
    return input(f"{msg} [y/N] ").strip().lower() in {"y", "yes"}


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """Confirm outage, execute failover once, and verify serving signals."""
    if primary not in URL or target not in URL or primary == target:
        return {"ok": False, "error": "invalid primary/target"}
    started = time.time()
    checks = []
    for _ in range(3):
        primary_ready, reason = probe(primary, 1)
        target_ready, _ = probe(target, 1)
        checks.append({"primary_ready": primary_ready, "target_ready": target_ready,
                       "reason": reason})
        if primary_ready:
            break
        time.sleep(0.2)
    outage_confirmed = len(checks) == 3 and all(not c["primary_ready"] for c in checks)
    step(1, "xac_nhan_outage", primary=primary, target=target,
         confirmed=outage_confirmed, probes=checks)
    if not outage_confirmed:
        return {"ok": False, "error": "outage_not_confirmed"}
    if not confirm(auto, f"Region {primary} is down. Fail over to {target}?"):
        return {"ok": False, "error": "operator_declined"}
    incident = step(2, "thong_bao_incident", primary=primary,
                    outage_ts=_latest_outage_ts(primary))
    result = fo.failover(target, backend, wait=60)
    step(3, "scale_gpu_pool", ok=result["ok"], completed_steps=result["completed_steps"])
    state = result.get("target_state", {})
    step(4, "verify_state_replica", count=state.get("count"), weights=state.get("weights"),
         rpo_seconds=result.get("rpo_seconds"), docs_lost=result.get("docs_lost"))
    step(5, "dns_cutover", ok=result["ok"], target=target)
    if not result["ok"]:
        step(7, "post_incident", ok=False, error=result.get("error"),
             elapsed_s=round(time.time() - started, 2))
        return result
    latencies = []
    errors = []
    for _ in range(10):
        t0 = time.monotonic()
        try:
            response = httpx.get(f"{URL[target]}/v1/infer", timeout=3)
            errors.append(response.status_code != 200)
        except httpx.RequestError:
            errors.append(True)
        latencies.append((time.monotonic() - t0) * 1000)
    ordered = sorted(latencies)
    p95 = round(ordered[9], 1)
    error_rate = sum(errors) / len(errors)
    step(6, "verify_golden_signals", requests=10, p95_latency_ms=p95,
         error_rate=error_rate, ok=error_rate == 0)
    step(7, "post_incident", ok=error_rate == 0,
         elapsed_s=round(time.time() - started, 2), incident_ts=incident["ts"],
         measure_command="python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300")
    return {**result, "ok": error_rate == 0, "p95_latency_ms": p95, "error_rate": error_rate}


def _latest_outage_ts(primary: str) -> float | None:
    path = pathlib.Path("chaos/chaos-events.jsonl")
    if not path.exists():
        return None
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return next((event["ts"] for event in reversed(events)
                 if event.get("action") == "kill" and event.get("region") == primary), None)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
