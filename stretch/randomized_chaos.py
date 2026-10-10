"""Five isolated, randomized A-outage drills using the real bare-mode stack."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import random
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.measure_rto import measure

PYTHON = pathlib.Path(sys.executable)


def _command(
    workspace: pathlib.Path, *args: str, env: dict | None = None, stdout=None
) -> subprocess.Popen:
    process_env = os.environ.copy()
    process_env["PYTHONPATH"] = str(ROOT)
    process_env.update(env or {})
    return subprocess.Popen(
        [str(PYTHON), *args],
        cwd=workspace,
        env=process_env,
        stdout=stdout,
        stderr=subprocess.STDOUT,
    )


def _wait_ready(url: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=0.5).status_code == 200:
                return
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"service did not become ready: {url}")


def _copy_logs(workspace: pathlib.Path, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for source in [
        workspace / "chaos/chaos-events.jsonl",
        *workspace.glob("reports/*.jsonl"),
    ]:
        if source.exists():
            shutil.copy2(source, destination / source.name)


def trial(index: int, mode: str, delay: float, output: pathlib.Path) -> dict:
    with tempfile.TemporaryDirectory(prefix=f"day23-chaos-{index:02d}-") as scratch:
        workspace = pathlib.Path(scratch)
        (workspace / "run").mkdir()
        (workspace / "edge").mkdir()
        (workspace / "reports").mkdir()
        processes: list[subprocess.Popen] = []
        log_streams = []
        try:
            for region, docs, weights in (("a", 200, 2), ("b", 0, 0)):
                subprocess.run(
                    [
                        str(PYTHON),
                        str(ROOT / "state/seed_vectors.py"),
                        "--region",
                        region,
                        "--docs",
                        str(docs),
                        "--weights-mb",
                        str(weights),
                    ],
                    cwd=workspace,
                    check=True,
                    stdout=subprocess.DEVNULL,
                )
            (workspace / "edge/active_region").write_text("a")
            services = (
                (
                    "region-a",
                    8001,
                    {"REGION": "a", "STATE_DIR": "state/region-a"},
                    "serving.app:app",
                ),
                (
                    "region-b",
                    8002,
                    {"REGION": "b", "STATE_DIR": "state/region-b"},
                    "serving.app:app",
                ),
                (
                    "edge",
                    8080,
                    {"EDGE_TTL_SECONDS": "5", "EDGE_ROUTING_MODE": "active-passive"},
                    "edge.proxy:app",
                ),
            )
            for name, port, extra_env, app in services:
                log = (workspace / f"run/{name}.log").open("w")
                log_streams.append(log)
                proc = _command(
                    workspace,
                    "-m",
                    "uvicorn",
                    app,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--log-level",
                    "warning",
                    env=extra_env,
                    stdout=log,
                )
                processes.append(proc)
                (workspace / f"run/{name}.pid").write_text(str(proc.pid))
                _wait_ready(
                    f"http://127.0.0.1:{port}/edge/state"
                    if name == "edge"
                    else f"http://127.0.0.1:{port}/healthz"
                )
            ingest = _command(
                workspace,
                str(ROOT / "state/ingest.py"),
                "--region",
                "a",
                "--rate",
                "0.5",
                "--duration",
                "70",
                stdout=subprocess.DEVNULL,
            )
            replicate = _command(
                workspace,
                str(ROOT / "state/replicate.py"),
                "--every",
                "10",
                "--duration",
                "70",
                "--backend",
                "fs",
                stdout=subprocess.DEVNULL,
            )
            processes.extend((ingest, replicate))
            manifest = workspace / "state/_replica/dr-artifacts/MANIFEST.json"
            until = time.monotonic() + 10
            while not manifest.exists() and time.monotonic() < until:
                time.sleep(0.1)
            if not manifest.exists():
                raise RuntimeError("first snapshot was not created")
            traffic = _command(
                workspace,
                str(ROOT / "loadgen/traffic.py"),
                "--duration",
                "55",
                "--rps",
                "3",
                "--out",
                "reports/traffic.jsonl",
                stdout=subprocess.DEVNULL,
            )
            checker = _command(
                workspace,
                str(ROOT / "dr/health_checker.py"),
                "--interval",
                "5",
                "--threshold",
                "3",
                "--duration",
                "55",
                "--out",
                "reports/health-events.jsonl",
                stdout=subprocess.DEVNULL,
            )
            processes.extend((traffic, checker))
            time.sleep(delay)
            kill = subprocess.run(
                [
                    str(PYTHON),
                    str(ROOT / "chaos/kill_region.py"),
                    "--region",
                    "a",
                    "--mode",
                    mode,
                    "--mock",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if kill.returncode:
                raise RuntimeError(kill.stdout + kill.stderr)
            health_log = workspace / "reports/health-events.jsonl"
            until = time.monotonic() + 35
            detected = False
            while time.monotonic() < until:
                if health_log.exists():
                    detected = any(
                        json.loads(line).get("region") == "a"
                        and json.loads(line).get("to") == "UNHEALTHY"
                        for line in health_log.read_text().splitlines()
                    )
                if detected:
                    break
                time.sleep(0.1)
            if not detected:
                raise RuntimeError("health checker did not detect A outage")
            runbook = subprocess.run(
                [
                    str(PYTHON),
                    str(ROOT / "dr/runbook.py"),
                    "--primary",
                    "a",
                    "--target",
                    "b",
                    "--backend",
                    "fs",
                    "--auto",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=75,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                check=False,
            )
            if runbook.returncode:
                raise RuntimeError(runbook.stdout + runbook.stderr)
            traffic.wait(timeout=65)
            checker.wait(timeout=65)
            result = measure(
                workspace / "reports/traffic.jsonl",
                workspace / "chaos/chaos-events.jsonl",
                health_log,
                workspace / "reports/failover-events.jsonl",
                300,
            )
            if (
                not result["valid"]
                or result["warnings"]
                or result["rto_verdict"] != "PASS"
            ):
                raise RuntimeError(f"invalid trial {index}: {result}")
            _copy_logs(workspace, output / f"trial-{index:02d}")
            return {
                "trial": index,
                "mode": mode,
                "kill_delay_s": round(delay, 2),
                "rto_s": result["rto_measured_s"],
                "rpo_s": result["rpo_at_restore_s"],
                "docs_lost": result["docs_lost"],
                "requests_failed": result["requests_failed"],
                "valid": result["valid"],
                "warnings": result["warnings"],
                "recovered_by": result["recovered_by_region"],
            }
        except Exception:
            _copy_logs(workspace, output / f"trial-{index:02d}")
            raise
        finally:
            for proc in processes:
                if proc.poll() is None:
                    try:
                        os.kill(proc.pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                    proc.terminate()
            for proc in processes:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            for stream in log_streams:
                stream.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        default=pathlib.Path("stretch/results/randomized-chaos.json"),
    )
    args = parser.parse_args()
    rng = random.Random(args.seed)
    modes = ["stop", "stop", "netblock", "netblock", "netblock"]
    rng.shuffle(modes)
    trials = []
    for index, mode in enumerate(modes, 1):
        delay = rng.uniform(6, 10)
        result = trial(index, mode, delay, args.out.parent / "chaos-trials")
        trials.append(result)
        print(json.dumps(result), flush=True)
    rtos = [item["rto_s"] for item in trials]
    summary = {
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": args.seed,
        "trials": trials,
        "mean_rto_s": round(statistics.mean(rtos), 2),
        "sample_stddev_rto_s": round(statistics.stdev(rtos), 2),
        "min_rto_s": min(rtos),
        "max_rto_s": max(rtos),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
