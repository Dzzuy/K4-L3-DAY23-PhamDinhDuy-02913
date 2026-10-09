# RTO/RPO evidence — Lab 23

All timestamps and values below come from this run's JSONL logs. `reports/measure-drill-1.json` and `reports/measure-drill-2.json` are the outputs of `tools/measure_rto.py`.

## Drill 1 — no disaster recovery

| Milestone | Value | Evidence |
|---|---:|---|
| Outage, Region A | 2026-10-09T04:20:31Z | `chaos/chaos-events.jsonl:3` |
| First failed request | +0.4s | `reports/drill-1-nodr.jsonl:18` |
| Recovery | None; 16 failed requests | `reports/measure-drill-1.json` |
| RTO verdict | NO_RECOVERY | `reports/measure-drill-1.json` |

## Drill 2 — replicated failover

| Milestone | Offset from outage | Evidence |
|---|---:|---|
| Outage, Region A | 0s at 2026-10-09T04:21:33Z | `chaos/chaos-events.jsonl:5` |
| First failed request | +0.0s | `reports/drill-2-withdr.jsonl:25` |
| Health checker marks A UNHEALTHY | +15.0s | `reports/health-events.jsonl:2` |
| Incident confirmed and announced | +18.9s | `reports/runbook-run.jsonl:2` |
| Snapshot restored to B | +18.9s | `reports/failover-events.jsonl:2` |
| B ready | +25.1s | `reports/failover-events.jsonl:4` |
| Edge cutover | +25.1s | `reports/failover-events.jsonl:5` |
| First successful request served by B | +28.2s | `reports/drill-2-withdr.jsonl:39` |

| Metric | Measured | Objective | Result | Evidence |
|---|---:|---:|---|---|
| RTO, inference API | 28.2s | ≤300s | PASS | `reports/measure-drill-2.json` |
| RPO, vector DB | 4.0s; 2 documents lost | ≤300s | PASS | `reports/failover-events.jsonl:2` |
| Health checker | interval 5.0s × threshold 3 = 15.0s detection floor | — | observed +15.0s | `reports/health-events.jsonl:2` |

## RTO breakdown

The four infrastructure components requested by the guide do not alone sum to this run's measured RTO: the runbook also spent about 3.9s confirming the outage before invoking failover. Showing that time separately keeps the breakdown faithful to the logs. Rounded components sum to 28.2s.

| Component | Time | Evidence | Improvement |
|---|---:|---|---|
| Health detection | 15.0s | `reports/health-events.jsonl:2` | Shorter polling interval, balanced against probe load and false alarms |
| Runbook confirmation and invocation | 3.9s | `reports/runbook-run.jsonl:1` | Faster timeout or parallel probes, while retaining three confirmations |
| Snapshot restore | <0.1s | `reports/failover-events.jsonl:2` and `reports/failover-events.jsonl:3` | No meaningful gain in this local run |
| GPU pool warm-up | 6.2s | `reports/failover-events.jsonl:4` | Keep spare capacity warm if cost permits |
| Edge TTL and next traffic sample | 3.1s | `reports/failover-events.jsonl:5` and `reports/drill-2-withdr.jsonl:39` | Lower edge TTL if request volume permits |
