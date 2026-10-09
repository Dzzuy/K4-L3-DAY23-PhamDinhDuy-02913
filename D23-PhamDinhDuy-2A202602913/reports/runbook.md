# Region A outage runbook

Run from the repository root with the virtual environment active. The on-call engineer owns the incident. The incident commander approves cutover and any rollback. Keep Region A isolated until its state has been reconciled.

| # | Step | Copy-paste command | Completion signal | Owner | Rollback or stop condition |
|---|---|---|---|---|---|
| 1 | Confirm outage | `python3 chaos/kill_region.py status` | Region A is not ready on three checks; Region B is alive | on-call | Stop if A is ready or B is not alive |
| 2 | Announce incident | `date -u +%FT%TZ` | UTC start time recorded in incident channel; `chaos/chaos-events.jsonl` has kill event | on-call | Stop if outage cannot be confirmed |
| 3 | Restore and scale target | `python3 dr/runbook.py --primary a --target b --backend fs` | Runbook logs `scale_gpu_pool` with `ok:true`; this command runs the five failover substeps once | incident commander | Abort automatically if snapshot is missing or B fails readiness deadline |
| 4 | Verify replica | `curl -sS localhost:8002/v1/state` | `count` > 0 and `weights:true` | on-call | Stop cutover if state or model version is wrong |
| 5 | Verify cutover | `curl -sS localhost:8080/edge/state` | `active_region:b` after the edge TTL | on-call | Incident commander may reverse only after A passes readiness and its state is reconciled |
| 6 | Check golden signals | `python3 -c "import httpx; r=[httpx.get('http://127.0.0.1:8002/v1/infer',timeout=3) for _ in range(10)]; print({'p95_ms':sorted(x.elapsed.total_seconds()*1000 for x in r)[9], 'error_rate':sum(x.status_code!=200 for x in r)/10})"` | Ten real requests, error rate 0; p95 latency recorded | on-call | Incident commander pauses traffic or initiates controlled rollback if errors persist |
| 7 | Measure and close | `python3 tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | `valid:true`, no warnings, `rto_verdict:PASS`; postmortem links each milestone | incident commander | Keep incident open if evidence is invalid or RTO misses target |

For a graded drill only, add `--auto` to step 3 after the health checker has logged `UNHEALTHY` for A. For a real incident, the default confirmation prompt is required. `dr/runbook.py` logs the seven response steps in `reports/runbook-run.jsonl`; `dr/failover.py` logs its five ordered substeps in `reports/failover-events.jsonl`.

**Rollback authority:** The incident commander approves returning traffic to A only after A has passed `/readyz` consistently, data and model versions are reconciled, and ten direct inference requests succeed. Use the same runbook with `--primary b --target a` only after a fresh A snapshot or other verified state restoration; never flip `edge/active_region` manually during the drill.
