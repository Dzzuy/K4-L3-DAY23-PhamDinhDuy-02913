"""Live active-active edge and concurrent-ingest reconciliation smoke test."""

from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from state.seed_vectors import seed
from stretch.active_active import merge_regions

PORTS = {"a": 18001, "b": 18002, "edge": 18080}


def _wait(url: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=0.5).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"not ready: {url}")


def _write(db: pathlib.Path, doc_id: str, body: str, ts: float) -> None:
    with sqlite3.connect(db) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?)",
            (doc_id, body, b"embed", ts),
        )


def main() -> None:
    original_cwd = pathlib.Path.cwd()
    processes: list[subprocess.Popen] = []
    with tempfile.TemporaryDirectory(prefix="day23-active-active-") as scratch:
        os.chdir(scratch)
        try:
            pathlib.Path("edge").mkdir()
            pathlib.Path("edge/active_region").write_text("a")
            seed("a", 200, 2)
            seed("b", 200, 2)
            pathlib.Path("state/region-b/pool_state").write_text("full")
            a_db = pathlib.Path("state/region-a/vectors.sqlite")
            b_db = pathlib.Path("state/region-b/vectors.sqlite")
            _write(a_db, "shared", "A-old", 100.0)
            _write(b_db, "shared", "B-new", 101.0)
            _write(a_db, "a-concurrent", "A-only", 102.0)
            _write(b_db, "b-concurrent", "B-only", 103.0)
            merge = merge_regions(a_db, b_db)
            env = os.environ.copy()
            env["PYTHONPATH"] = str(ROOT)
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
                        "EDGE_ROUTING_MODE": "active-active",
                        "REGION_A_URL": f"http://127.0.0.1:{PORTS['a']}",
                        "REGION_B_URL": f"http://127.0.0.1:{PORTS['b']}",
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
                    f"http://127.0.0.1:{PORTS[name]}/edge/state"
                    if name == "edge"
                    else f"http://127.0.0.1:{PORTS[name]}/readyz"
                )
            responses = [
                httpx.get(f"http://127.0.0.1:{PORTS['edge']}/v1/infer", timeout=3)
                for _ in range(20)
            ]
            if any(response.status_code != 200 for response in responses):
                raise RuntimeError("active-active inference failed")
            served = Counter(response.json()["region"] for response in responses)
            if served != {"a": 10, "b": 10}:
                raise RuntimeError(f"unbalanced routing: {served}")
            with sqlite3.connect(a_db) as connection:
                winner = connection.execute(
                    "SELECT body FROM docs WHERE doc_id='shared'"
                ).fetchone()[0]
            result = {
                "measured_at_utc": datetime.now(timezone.utc).isoformat(),
                "routing_mode": "active-active",
                "requests": 20,
                "served_by": dict(served),
                "errors": 0,
                "merge": merge,
                "conflict_winner": winner,
                "conflict_rule": "newer ingested_at; equal timestamp chooses region b",
            }
            out = original_cwd / "stretch/results/active-active.json"
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
            os.chdir(original_cwd)


if __name__ == "__main__":
    main()
