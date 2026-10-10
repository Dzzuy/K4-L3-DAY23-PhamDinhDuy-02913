"""Compare real MinIO and filesystem snapshot put/get in a temporary lab state."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from state import snapshot
from state.seed_vectors import seed


def _ensure_bucket(bucket: str) -> None:
    client = boto3.client(
        "s3",
        endpoint_url=os.environ.get("MINIO_URL", "http://127.0.0.1:9000"),
        aws_access_key_id=os.environ.get("MINIO_USER", "minioadmin"),
        aws_secret_access_key=os.environ.get("MINIO_PASS", "minioadmin"),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
            raise
        client.create_bucket(Bucket=bucket)


def _count(db: pathlib.Path) -> int:
    with sqlite3.connect(db) as connection:
        return connection.execute("SELECT COUNT(*) FROM docs").fetchone()[0]


def benchmark(samples: int, bucket: str) -> dict:
    if samples < 1:
        raise ValueError("samples must be positive")
    _ensure_bucket(bucket)
    snapshot.BUCKET = bucket
    original_cwd = pathlib.Path.cwd()
    with tempfile.TemporaryDirectory(prefix="day23-minio-") as scratch:
        os.chdir(scratch)
        try:
            seed("a", 200, 2)
            seed("b", 0, 0)
            results: dict[str, dict] = {}
            for backend in ("fs", "minio"):
                put_ms, get_ms = [], []
                for _ in range(samples):
                    started = time.perf_counter()
                    source = snapshot.put("a", backend)
                    put_ms.append(round((time.perf_counter() - started) * 1000, 2))
                    started = time.perf_counter()
                    restored = snapshot.get("b", backend)
                    get_ms.append(round((time.perf_counter() - started) * 1000, 2))
                    assert (
                        source["embed_model_version"] == restored["embed_model_version"]
                    )
                    assert _count(pathlib.Path("state/region-b/vectors.sqlite")) == 200
                    assert (
                        pathlib.Path("state/region-b/weights/model.bin").stat().st_size
                        == 2 * 1024 * 1024
                    )
                results[backend] = {
                    "put_ms": put_ms,
                    "get_ms": get_ms,
                    "put_median_ms": round(statistics.median(put_ms), 2),
                    "get_median_ms": round(statistics.median(get_ms), 2),
                    "restored_docs": 200,
                    "embed_model_version": source["embed_model_version"],
                }
        finally:
            os.chdir(original_cwd)
    return {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "bucket": bucket,
        "samples_per_backend": samples,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--bucket", default="day23-stretch-benchmark")
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("stretch/results/minio-vs-fs.json"),
    )
    args = parser.parse_args()
    result = benchmark(args.samples, args.bucket)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
