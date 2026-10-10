# Optional Day 23 stretch goals

These demonstrations are isolated from the graded `reports/` evidence and leave the default active-passive behavior intact. Run commands from the repository root. Use `.venv/bin/python` explicitly so the MinIO demos can import `boto3` from `requirements.txt`. If the environment has not been installed yet, run `.venv/bin/python -m pip install -r requirements.txt`. Generated results live in `stretch/results/`.

For screenshots, capture each exercise command and its live output. The JSON files are retained machine-readable evidence, not a substitute for a live-run screenshot.

## 1. Real MinIO object store

The old `minio/minio` and `quay.io/minio/minio:latest` image names no longer pull on this machine. `stretch/minio/Dockerfile` builds a pinned community release from MinIO's [official archived source](https://github.com/minio/minio/releases/tag/RELEASE.2025-09-07T16-13-09Z) using Go 1.24.2. The service binds to localhost only. This is for a local lab, not a production image.

```bash
docker compose build minio
docker compose up -d minio
.venv/bin/python stretch/minio_benchmark.py --samples 5
.venv/bin/python stretch/minio_failover_demo.py
docker compose stop minio
```

The benchmark seeds 200 documents and 2 MiB model weights in a temporary directory, then performs five `snapshot.put/get` cycles with both `fs` and `minio`. Each restore verifies the document count and model version. The output gives individual and median `put`/`get` latencies. The second demo takes a MinIO snapshot, stops A, executes the actual five-step failover, and confirms that the edge serves B after its TTL expires. Both use isolated state and logs. They use the local credentials already in `docker-compose.yml`; override `MINIO_URL`, `MINIO_USER`, `MINIO_PASS`, or the benchmark's `--bucket` flag in a different environment. See MinIO's [container documentation](https://github.com/minio/minio/blob/master/docs/docker/README.md).

The screenshoted local run measured filesystem put/get medians of 0.98/0.74 ms and MinIO put/get medians of 44.06/17.32 ms. The MinIO-backed failover took 4.89 s from A stopping to the first successful response from B, with RPO 0 s and no lost documents. These are local lab timings and will vary between runs.

## 2. PostgreSQL metadata PITR

```bash
bash stretch/postgres_pitr.sh
```

The script starts an isolated PostgreSQL 16 container, enables WAL archiving, creates an incident metadata table, takes a `pg_basebackup`, commits rows on each side of a recovery target, then restores to that target in a second container. It verifies the earlier row exists and the later row does not. `metadata_rto_s` covers backup-file copy, recovery startup, and the first successful query after the simulated outage. Temporary containers and data are removed after the result is written. This follows PostgreSQL's [continuous archiving and PITR procedure](https://www.postgresql.org/docs/16/continuous-archiving.html).

The screenshoted local run measured metadata RTO of 1.967 s, and only rows 1 and 2 were present after PITR to the selected timestamp.

## 3. Active-active routing and concurrent ingest

```bash
.venv/bin/python stretch/active_active_demo.py
```

Set `EDGE_ROUTING_MODE=active-active` to make the edge alternate requests 50/50 between ready regions A and B. The default remains `active-passive`. The isolated demo seeds both regions with `pool_state=full`, starts real local serving and edge processes on ports 18001, 18002, and 18080, makes 20 inference calls, and verifies a 10/10 split with zero errors. `stretch/active_active.py` reconciles concurrent SQLite document writes: a later `ingested_at` wins, and equal timestamps choose B. The same winning set is written to both DBs. That policy is deterministic but assumes aligned clocks; it is not a general distributed transaction system.

Measured locally: 10/10 requests served by A/B, zero errors, and the newer B write won the conflict.

## 4. Terraform replication draft (write-only)

```bash
terraform fmt -check -recursive stretch/terraform
terraform -chdir=stretch/terraform init -backend=false
terraform -chdir=stretch/terraform validate
```

The configuration creates two versioned S3 buckets in separate Regions, an IAM replication role, and `aws_s3_bucket_replication_configuration` for all objects written by `state/snapshot.py` (`vectors.sqlite`, `model.bin`, and `MANIFEST.json`). `MANIFEST.json` carries snapshot time, latest document timestamp, source Region, and embedding model version; S3 version IDs would retain prior manifests and payloads. This draft is deliberately **not applied** to AWS. AWS requires [versioning on both buckets and an IAM role](https://docs.aws.amazon.com/AmazonS3/latest/userguide/replication-requirements.html); the [Terraform resource](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/s3_bucket_replication_configuration) is validated locally.

## 5. Five randomized chaos runs

```bash
.venv/bin/python stretch/randomized_chaos.py --seed 23
```

Each run creates its own temporary state, services, snapshot, traffic, checker, and runbook. The kill delay is randomized from 6–10s, and both `stop` and `netblock` modes occur. The automation waits for A's `UNHEALTHY` event before cutover. Raw logs for each trial are retained under `stretch/results/chaos-trials/`, along with the mean, sample standard deviation, min, and max RTO. It uses ports 8001, 8002, and 8080, so stop any other lab stack first. The original graded logs are not changed.

The screenshoted local run with seed 23 had five valid trials, all recovered on B; RTOs were 26.5, 21.8, 28.8, 22.6, and 26.5 s. Mean RTO was 25.24 s and sample standard deviation was 2.94 s.

## 6. Maturity assessment

Read [maturity.md](maturity.md). It rates the core lab at provisional Level 2 and states the concrete change needed for Level 3. The course slide was unavailable, so its Level 0–4 labels must be reconciled with the slide if supplied later.

For optional screenshots, capture the live command output for goals 1–3 and 5, `terraform validate` success for goal 4, and the maturity table for goal 6. The lab's graded evidence remains the original `reports/` logs and tests.
