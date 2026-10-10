# Day 23 — Disaster Recovery and High Availability reflection

**Student:** Phạm Đình Duy

**Student ID (MSSV):** 2A202602913

## 1. Which RTO component can be reduced without increasing flapping risk, and at what cost?

The graded drill measured a 28.2-second RTO. Its largest component was health detection (15.0 seconds), followed by GPU pool warm-up (about 6.2 seconds), runbook confirmation (about 3.9 seconds), and edge TTL plus traffic sampling (about 3.1 seconds). To reduce RTO without changing the three-consecutive-failure threshold, keep Region B's compute pool warm and ready. That removes most of the measured warm-up delay while preserving the health check's resistance to flapping. The cost is paying for idle GPU/compute capacity in B. The local `pool_state` switch only simulates that cost; no real GPU was provisioned.

Evidence: `reports/rto-evidence.md` (RTO breakdown), `reports/health-events.jsonl:2`, and `reports/failover-events.jsonl:4` in the repository root.

## 2. Who detects a serving process failure?

The health checker must run independently of the serving API. If it shared the serving process, that process dying would also stop its checker, so it could not record an `UNHEALTHY` transition or trigger the runbook. `dr/health_checker.py` makes HTTP requests to each region's `/readyz` endpoint and does not import anything from `serving/`. In the graded drill, three consecutive failed probes at five-second intervals produced the `UNHEALTHY` event at +15.0 seconds.

Evidence: `dr/health_checker.py` and `reports/health-events.jsonl:2` in the repository root.

## 3. How do we verify the five-minute RTO claim?

Run `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` from the repository root. The result is recorded in `reports/measure-drill-2.json`: `valid: true`, no warnings, recovery by Region B, measured RTO 28.2 seconds, and `rto_verdict: PASS`. The measurement uses the outage and first successful B request timestamps in the real chaos and traffic logs; `reports/rto-evidence.md` points to the exact log lines. The baseline `reports/measure-drill-1.json` shows `NO_RECOVERY`, so the comparison is also based on measured requests.

Evidence: `reports/measure-drill-2.json`, `reports/drill-2-withdr.jsonl:39`, `chaos/chaos-events.jsonl:5`, and `reports/rto-evidence.md` in the repository root.
