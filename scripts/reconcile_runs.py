#!/usr/bin/env python
"""Reconcile interrupted runs in a durable orchestrator run store.

Lists runs still recorded as outcome=executing (interrupted before terminal
persistence) and, with --apply, marks them outcome=uncertain. Reconciliation
NEVER re-executes work and NEVER clears idempotency keys: an uncertain run
means a human must decide what happened; the client retries with a NEW key.

Usage:
    .venv/bin/python scripts/reconcile_runs.py --store PATH [--apply]

Exit 0 = no stale runs (or applied cleanly); exit 3 = stale runs remain.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.run_store import SQLiteRunStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="path to runs.sqlite")
    parser.add_argument(
        "--apply", action="store_true",
        help="mark stale executing runs as uncertain (default: dry run)",
    )
    args = parser.parse_args(argv)
    store = SQLiteRunStore(Path(args.store).expanduser())
    stale = store.reconcile_stale_executing(dry_run=not args.apply)
    if not stale:
        print("no interrupted runs require reconciliation")
        return 0
    for run_id in stale:
        record = store.load_run(run_id)
        print(
            f"{'marked uncertain' if args.apply else 'would mark uncertain'}: "
            f"{run_id} owner={record.owner_uid} workflow={record.workflow} "
            f"status={record.status}"
        )
    if not args.apply:
        print("dry run only; rerun with --apply to record the uncertain outcome")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
