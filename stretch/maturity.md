# DR maturity self-assessment (provisional Level 0–4 scale)

The course slide defining Levels 0–4 was not present in this repository when this was written. The scale below is an explicit working rubric; replace its labels if the §6 slide defines them differently.

| Level | Working definition |
|---|---|
| 0 | No backup and no recovery procedure |
| 1 | Backups exist, recovery is manual and unmeasured |
| 2 | Scripted failover and restoration, operator approval, measured RTO/RPO in a drill |
| 3 | Recovery is regularly exercised across independent infrastructure with monitoring, alerts, and bounded RTO/RPO distributions |
| 4 | Continuously verified resilience with tested automatic policy, multi-region data consistency, and enforced SLOs |

**Current core lab rating: Level 2.** The committed A→B drill has a thresholded health checker, five ordered failover steps, a seven-step semi-automated runbook, and timestamped evidence. It measured RTO 28.2s and RPO 4.0s / 2 documents lost. The optional randomized chaos exercise broadens testing but does not make the core service independent across machines or establish a continuous alerting and backup assurance process.

**Next concrete step to Level 3:** deploy A, B, and the edge on separate failure domains; schedule snapshots and synthetic probes; alert on snapshot age and sustained `/readyz` failures; run at least five recurring, automated game-day drills and publish p95 RTO, maximum RPO, and failure rates. Keep operator approval until the false-positive rate supports a circuit breaker and automatic cutover.

**Current limitations:** both regions and the edge share one host; the filesystem snapshot backend shares that host; the active-active merge demo uses last-writer-wins timestamps and assumes aligned clocks; the Terraform draft has not been applied to AWS. These are reasons not to claim Level 3 or 4 from the optional demos alone.
