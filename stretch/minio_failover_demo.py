"""Exercise the real failover path with a MinIO snapshot in isolated state."""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dr import failover
from state import snapshot
from state.seed_vectors import seed
from stretch.minio_benchmark import _ensure_bucket

PORTS = {"a": 18011, "b": 18012, "edge": 18081}
BUCKET = "day23-stretch-failover"


def _wait(url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=0.5).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"service did not start: {url}")


def main() -> None:
    _ensure_bucket(BUCKET)
    original_cwd = pathlib.Path.cwd()
    original_bucket = snapshot.BUCKET
    original_urls = failover.URL.copy()
    original_log = failover.LOG
    processes: list[subprocess.Popen] = []
    with tempfile.TemporaryDirectory(prefix="day23-minio-failover-") as scratch:
        os.chdir(scratch)
        try:
            pathlib.Path("edge").mkdir()
            pathlib.Path("edge/active_region").write_text("a")
            seed("a", 200, 2)
            seed("b", 0, 0)
            snapshot.BUCKET = BUCKET
            manifest = snapshot.put("a", "minio")
            failover.URL = {
                region: f"http://127.0.0.1:{PORTS[region]}" for region in ("a", "b")
            }
            failover.LOG = pathlib.Path("reports/failover-events.jsonl")
            env = {**os.environ, "PYTHONPATH": str(ROOT), "WARMUP_SECONDS": "1"}
            services = [
                (
                    "a",
                    "serving.app:app",
                    {"REGION": "a", "STATE_DIR": "state/region-a"},
                ),
                (
                    "b",
                    "serving.app:app",
                    {"REGION": "b", "STATE_DIR": "state/region-b"},
                ),
                (
                    "edge",
                    "edge.proxy:app",
                    {
                        "REGION_A_URL": failover.URL["a"],
                        "REGION_B_URL": failover.URL["b"],
                    },
                ),
            ]
            for name, app, extra in services:
                proc = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        app,
                        "--host",
                        "127.0.0.1",
                        "--port",
                        str(PORTS[name]),
                        "--log-level",
                        "warning",
                    ],
                    env={**env, **extra},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                processes.append(proc)
                _wait(
                    f"http://127.0.0.1:{PORTS[name]}/healthz"
                    if name != "edge"
                    else f"http://127.0.0.1:{PORTS[name]}/edge/state"
                )
            before = httpx.get(f"http://127.0.0.1:{PORTS['edge']}/v1/infer", timeout=3)
            before.raise_for_status()
            if before.json()["region"] != "a":
                raise RuntimeError("initial inference did not use region a")
            processes[0].terminate()
            processes[0].wait(timeout=5)
            outage_start = time.monotonic()
            recovery = failover.failover("b", "minio", wait=30)
            if not recovery["ok"]:
                raise RuntimeError(f"MinIO failover failed: {recovery}")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                after = httpx.get(
                    f"http://127.0.0.1:{PORTS['edge']}/v1/infer", timeout=3
                )
                if after.status_code == 200 and after.json()["region"] == "b":
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("edge did not serve region b after cutover")
            result = {
                "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "backend": "minio",
                "bucket": BUCKET,
                "before_region": "a",
                "after_region": "b",
                "failover_rto_s": round(time.monotonic() - outage_start, 2),
                "rpo_seconds": recovery["rpo_seconds"],
                "docs_lost": recovery["docs_lost"],
                "snapshot_at": manifest["snapshot_at"],
                "completed_steps": recovery["completed_steps"],
            }
            out = original_cwd / "stretch/results/minio-failover.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(result, indent=2))
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.terminate()
            for proc in processes:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            snapshot.BUCKET = original_bucket
            failover.URL = original_urls
            failover.LOG = original_log
            os.chdir(original_cwd)


if __name__ == "__main__":
    main()
