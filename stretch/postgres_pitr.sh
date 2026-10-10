#!/usr/bin/env bash
# Local PostgreSQL 16 metadata PITR using pg_basebackup and archived WAL.
set -euo pipefail
cd "$(dirname "$0")/.."
output="${1:-stretch/results/postgres-pitr.json}"
mkdir -p "$(dirname "$output")"
workdir=$(mktemp -d /tmp/day23-pitr.XXXXXX)
primary="day23-pitr-primary-$$"
recovered="day23-pitr-recovered-$$"
pgpassword=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
cleanup() {
  docker rm -f "$primary" "$recovered" >/dev/null 2>&1 || true
  docker run --rm --user 0 --entrypoint sh -v "$workdir:/work" \
    postgres:16-alpine -c 'rm -rf /work/primary /work/backup /work/archive /work/recovered' \
    >/dev/null 2>&1 || true
  rmdir "$workdir" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$workdir/primary" "$workdir/backup" "$workdir/archive" "$workdir/recovered"
chmod 777 "$workdir" "$workdir/primary" "$workdir/backup" "$workdir/archive" "$workdir/recovered"

docker run -d --name "$primary" -e POSTGRES_PASSWORD="$pgpassword" \
  -e POSTGRES_DB=metadata -v "$workdir/primary:/var/lib/postgresql/data" \
  -v "$workdir/backup:/backup" -v "$workdir/archive:/archive" \
  postgres:16-alpine -c wal_level=replica -c archive_mode=on \
  -c "archive_command=test ! -f /archive/%f && cp %p /archive/%f" \
  -c archive_timeout=1s >/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$primary" pg_isready -U postgres -d metadata >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$primary" pg_isready -U postgres -d metadata >/dev/null
sql() { docker exec -e PGPASSWORD="$pgpassword" "$primary" psql -U postgres -d metadata -AtX -v ON_ERROR_STOP=1 -c "$1"; }
sql "CREATE TABLE incident_meta(id integer PRIMARY KEY, status text NOT NULL, changed_at timestamptz NOT NULL DEFAULT clock_timestamp()); INSERT INTO incident_meta(id,status) VALUES (1,'base-backup');" >/dev/null
docker exec -u postgres -e PGPASSWORD="$pgpassword" "$primary" \
  pg_basebackup -h 127.0.0.1 -U postgres -D /backup -Fp -Xs -P >/dev/null
sql "INSERT INTO incident_meta(id,status) VALUES (2,'before-target');" >/dev/null
target=$(sql "SELECT to_char(clock_timestamp() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') || '+00';")
sleep 1
sql "INSERT INTO incident_meta(id,status) VALUES (3,'after-target');" >/dev/null
wal_file=$(sql "SELECT pg_walfile_name(pg_current_wal_lsn());")
sql "SELECT pg_switch_wal();" >/dev/null
for attempt in $(seq 1 30); do
  if [ -f "$workdir/archive/$wal_file" ]; then break; fi
  sleep 1
done
[ -f "$workdir/archive/$wal_file" ]
docker stop -t 5 "$primary" >/dev/null
outage_ns=$(date +%s%N)
docker run --rm --user 0 --entrypoint sh \
  -v "$workdir/backup:/backup:ro" -v "$workdir/recovered:/recovered" \
  postgres:16-alpine -c 'cp -a /backup/. /recovered/ && touch /recovered/recovery.signal && chown -R postgres:postgres /recovered' >/dev/null
docker run -d --name "$recovered" -v "$workdir/recovered:/var/lib/postgresql/data" \
  -v "$workdir/archive:/archive:ro" postgres:16-alpine \
  -c "restore_command=cp /archive/%f %p" \
  -c "recovery_target_time=$target" -c recovery_target_action=promote >/dev/null
ready=false
for attempt in $(seq 1 60); do
  if docker exec "$recovered" pg_isready -U postgres -d metadata >/dev/null 2>&1; then
    ready=true
    break
  fi
  sleep 0.5
done
if [ "$ready" != true ]; then docker logs "$recovered" >&2; exit 1; fi
rows=$(docker exec "$recovered" psql -U postgres -d metadata -AtX -v ON_ERROR_STOP=1 \
  -c "SELECT string_agg(id::text, ',' ORDER BY id) FROM incident_meta;")
if [ "$rows" != '1,2' ]; then docker logs "$recovered" >&2; echo "unexpected rows: $rows" >&2; exit 1; fi
recovery_ns=$(date +%s%N)
python3 - "$output" "$target" "$outage_ns" "$recovery_ns" "$rows" <<'PY'
import datetime
import json
import pathlib
import sys
path, target, outage_ns, recovery_ns, rows = sys.argv[1:]
result = {
    "postgres_version": "16-alpine",
    "recovery_target_utc": target,
    "rows_after_recovery": [int(row) for row in rows.split(",")],
    "excluded_after_target": 3,
    "metadata_rto_s": round((int(recovery_ns) - int(outage_ns)) / 1e9, 3),
    "measured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
pathlib.Path(path).write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
PY
