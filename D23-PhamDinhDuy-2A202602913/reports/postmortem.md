# Blameless postmortem — Region A outage drill

## Timeline

| UTC | Event | Evidence |
|---|---|---|
| 2026-10-09 04:21:33 | Region A paused; Region B alive | `chaos/chaos-events.jsonl:5` |
| 2026-10-09 04:21:33 | First user request to A fails | `reports/drill-2-withdr.jsonl:25` |
| 2026-10-09 04:21:48 | Checker records A UNHEALTHY after three consecutive failures | `reports/health-events.jsonl:2` |
| 2026-10-09 04:21:52 | Runbook confirms and announces incident | `reports/runbook-run.jsonl:1` and `reports/runbook-run.jsonl:2` |
| 2026-10-09 04:21:52 | Snapshot restored to B; RPO measured | `reports/failover-events.jsonl:2` |
| 2026-10-09 04:21:58 | B ready, then DNS/LB cutover | `reports/failover-events.jsonl:4` and `reports/failover-events.jsonl:5` |
| 2026-10-09 04:22:02 | First successful request served by B | `reports/drill-2-withdr.jsonl:39` |

## RTO/RPO gap analysis

- RTO target 300s; measured 28.2s; margin 271.8s. Evidence: `reports/measure-drill-2.json`.
- RPO target 300s; measured 4.0s and 2 documents lost; margin 296.0s. Evidence: `reports/failover-events.jsonl:2`.
- Largest RTO component: health detection at 15.0s, 53.2% of total. The remaining time includes runbook confirmation (about 3.9s), warm-up (6.17s), and edge cache/traffic sampling (about 3.1s). Evidence: `reports/health-events.jsonl:2`, `reports/runbook-run.jsonl:1`, `reports/failover-events.jsonl:4`, `reports/drill-2-withdr.jsonl:39`.
- Baseline gap: without the DR path, recovery never occurred during the 40s traffic run; 16 requests failed. Evidence: `reports/measure-drill-1.json`.

## Root cause — five whys

1. Why did inference fail? The edge continued routing to Region A after A stopped answering.
2. Why did it continue routing there? The active-region file still selected A until failover completed.
3. Why was failover delayed? The checker required three failed probes at a five-second interval and the runbook independently confirmed the outage.
4. Why require repeated probes? A single slow request can be transient; a threshold prevents unstable cutovers.
5. Why did some data fail to reach B? Replication is periodic, so documents ingested after the last snapshot were absent from the restored copy. The measured loss was 2 documents over 4.0s, not an estimate.

The drill deliberately paused A to expose these design limits. No individual action caused the reliability gap.

## Action items

| # | Action item | Owner | Deadline | Expected effect and validation |
|---|---|---|---|---|
| 1 | Evaluate 3s checker interval with the same three-failure threshold across five chaos runs | SRE | 2026-10-16 | Potentially reduce detection by about 6s; compare false positives and RTO distribution |
| 2 | Evaluate 10s snapshot cadence and record document loss over five drills | Data platform | 2026-10-16 | Bound expected RPO nearer 10s; validate using `docs_lost` and `rpo_seconds` logs |
| 3 | Parallelize three runbook confirmation probes without removing the threshold | AI platform | 2026-10-23 | Reduce the measured 3.9s confirmation gap; keep no-premature-cutover test passing |

## Required questions

1. At 5s × 3, the detection floor is 15s, or 53.2% of 28.2s RTO. A five-minute target leaves 285s for all later steps, so an interval must be chosen with their measured budget in mind.
2. A 1s interval with threshold 3 gives a nominal 3s floor, about 12s less. It increases probe traffic and may make correlated short failures look like an outage; repeated drills are needed before changing it.
3. If A were permanently lost after six hours, `docs_lost=2` means two documents ingested after the last completed snapshot would be absent from B in this run. A six-hour outage would require measuring the actual lag and missing document count again; this number cannot be extrapolated safely.
