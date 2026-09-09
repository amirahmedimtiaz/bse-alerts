from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any


def _as_sets(raw: Any) -> dict[str, set[str]]:
    if isinstance(raw, list):
        # Preserve compatibility with the original single-company state file.
        return {"544524": {str(news_id) for news_id in raw}}
    if not isinstance(raw, dict):
        raise ValueError("announcement state must be a JSON object or list")

    state: dict[str, set[str]] = {}
    for key, news_ids in raw.items():
        if not isinstance(news_ids, list):
            raise ValueError(f"announcement state for {key!r} must be a list")
        state[str(key)] = {str(news_id) for news_id in news_ids}
    return state


def merge_states(local: Any, remote: Any) -> dict[str, list[str]]:
    local_sets = _as_sets(local)
    remote_sets = _as_sets(remote)
    keys = set(local_sets) | set(remote_sets)
    return {
        key: sorted(local_sets.get(key, set()) | remote_sets.get(key, set()))
        for key in sorted(keys)
    }


def _read_remote_state(remote_ref: str, state_path: str) -> Any:
    result = subprocess.run(
        ["git", "show", f"{remote_ref}:{state_path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Union local and remote announcement state after a push race."
    )
    parser.add_argument(
        "--state-path",
        default="state/seen_announcements.json",
        help="Path to the checked-out state file.",
    )
    parser.add_argument(
        "--remote-ref",
        default="origin/main",
        help="Git ref containing the newer state file.",
    )
    args = parser.parse_args()

    path = Path(args.state_path)
    local = json.loads(path.read_text())
    remote = _read_remote_state(args.remote_ref, args.state_path)
    merged = merge_states(local, remote)
    path.write_text(json.dumps(merged, indent=2) + "\n")


if __name__ == "__main__":
    main()
