"""Orchestrator: runs the pipeline in a continuous, resumable loop.

Fully local and offline. Each cycle advances every stage by one batch:

    1. download  a batch of pending/failed tables
    2. ocr       a batch of downloaded tables (if OCR deps are available)
    3. validate  a batch of OCR'd tables (vote-sum check)
    4. sleep     CYCLE_SLEEP_SECONDS, then repeat

Idempotent: every stage only picks up its own pending work, so killing and
restarting resumes cleanly. Stages with missing optional deps (OCR) are skipped
with a notice instead of crashing the loop. Nothing leaves the machine; build
reports from the local database with report.py.

    python agent.py [--dept 16] [--batch 200] [--once]
"""
import argparse
import subprocess
import sys
import time

import config

CYCLE_SLEEP_SECONDS = 15


def run_stage(name: str, argv: list[str]) -> int:
    """Run a pipeline script as a subprocess; return its exit code.

    Stages are isolated subprocesses so a crash in one never kills the loop.
    """
    print(f"\n[{name}] {' '.join(argv)}", flush=True)
    try:
        return subprocess.call([sys.executable, *argv])
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[{name}] error: {exc}", file=sys.stderr)
        return 1


def cycle(args) -> None:
    dept = ["--dept", args.dept] if args.dept else []
    batch = ["--limit", str(args.batch)]

    run_stage("download", ["download.py", "--status", "11", *dept, *batch])
    run_stage("ocr", ["ocr.py", *dept, *batch])
    run_stage("validate", ["validate.py", *dept, *batch])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept", help="Restrict the loop to one department code")
    ap.add_argument("--batch", type=int, default=200,
                    help="Rows advanced per stage per cycle")
    ap.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    args = ap.parse_args()

    print("vote-verify agent starting. Ctrl+C to stop.")
    cycles = 0
    try:
        while True:
            cycles += 1
            print(f"\n===== cycle {cycles} =====", flush=True)
            cycle(args)
            if args.once:
                break
            time.sleep(CYCLE_SLEEP_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped by user.")


if __name__ == "__main__":
    main()
