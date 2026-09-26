"""Bounded hosted worker; the workflow dispatches its successor on completion."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

from .alert_store import AlertStore, GitState
from .main import load_state
from .scanner import cycle


def code_changed() -> bool:
    subprocess.run(["git", "fetch", "--quiet", "origin", "main"], check=True, timeout=90)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "origin/main", "--", "src", "companies.json",
         "requirements.txt", ".github/workflows/announcement-alert.yml"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return bool(result.stdout.strip())


def run_worker(cycles: int, interval: int = 300) -> int:
    if not 1 <= cycles <= 60 or interval < 300:
        raise ValueError("Worker accepts 1–60 cycles with an interval of at least 300 seconds")
    remote = subprocess.check_output(["git", "remote", "get-url", "origin"], text=True).strip()
    deadline = time.monotonic() + 5 * 3600
    with tempfile.TemporaryDirectory(prefix="bse-alert-worker-") as work:
        store = AlertStore(Path(work) / "alerts.sqlite3")
        try:
            state = GitState(Path(work) / "state", remote)
            if not state.restore(store):
                store.seed_legacy(load_state())
                state.save(store)
                print("Migrated legacy IDs; initialized transactional state branch.", flush=True)
            for index in range(cycles):
                started = time.monotonic()
                if code_changed():
                    print("Deployment changed; handing off to a worker using current main.", flush=True)
                    return 0
                try:
                    report = cycle(store, lambda: state.save(store))
                except Exception as exc:
                    print(f"::error::Cycle aborted: {type(exc).__name__}: {exc}", flush=True)
                    time.sleep(max(0, interval - (time.monotonic() - started)))
                    return 1
                if os.getenv("GITHUB_STEP_SUMMARY"):
                    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
                        summary.write(f"### Cycle {index + 1}\n\n```json\n{json.dumps(report, indent=2)}\n```\n\n")
                failed = bool(report["failed"] or report["email_failed"])
                if failed:
                    print("::error::Cycle incomplete; progress retained; watchdog will retry.", flush=True)
                    return 1
                # Wait after the last scan too: no extra immediate successor poll.
                time.sleep(max(0, min(deadline - time.monotonic(), interval - (time.monotonic() - started))))
                if time.monotonic() >= deadline:
                    break
            return 0
        finally:
            store.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=60)
    args = parser.parse_args()
    try:
        result = run_worker(args.cycles)
    except Exception as exc:
        print(f"::error::Worker initialization/recovery failed: {type(exc).__name__}: {exc}", flush=True)
        result = 1
    raise SystemExit(result)


if __name__ == "__main__":
    main()
