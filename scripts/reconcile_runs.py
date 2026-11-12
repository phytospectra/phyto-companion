#!/usr/bin/env python
"""Reconcile interrupted runs in a durable orchestrator run store.

Lists admitted/executing runs whose lease has expired (or, with
--confirm-quiesced, any admitted/executing run — the operator attests that
every worker has been stopped) and, with --apply, records them
outcome=uncertain together with the operator, reason and evidence.
Waiting-for-review runs are never selected. Reconciliation NEVER re-executes
work and NEVER clears idempotency keys.

An uncertain outcome means a human must decide what happened. The resolution
path is ``--resolve RUN_ID=abandoned``: it records the operator decision and
marks the run failed, after which (and only after which) a new operation with
a NEW key is appropriate. Never resubmit with a new key while the original
outcome is unknown: a refund or a 409 does not prove no external effect
occurred.

Orphan claims (an idempotency key with no run row) and approvals admitted but
never completed are listed as read-only evidence; they are resolved through
the same --resolve decision path.

Usage:
    .venv/bin/python scripts/reconcile_runs.py --store PATH                    # dry run
    .venv/bin/python scripts/reconcile_runs.py --store PATH --apply \
        --operator me@example.org --reason "crash 2026-11-12, uvicorn SIGKILL"
    .venv/bin/python scripts/reconcile_runs.py --store PATH --run-id run-...   # scope
    .venv/bin/python scripts/reconcile_runs.py --store PATH --confirm-quiesced # post-shutdown
    .venv/bin/python scripts/reconcile_runs.py --store PATH --resolve run-abc=abandoned \
        --operator me@example.org --reason "no executor evidence of completion"

Exit 0 = nothing stale (or applied/resolved cleanly); exit 3 = stale runs
remain; exit 2 = usage/input error.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orchestrator.run_store import SQLiteRunStore  # noqa: E402

DEFAULT_HEARTBEAT_TTL_SECONDS = 60.0


def _require(args, name: str, detail: str) -> None:
    if not getattr(args, name):
        raise SystemExit(f"error: {detail}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", required=True, help="path to runs.sqlite")
    parser.add_argument("--apply", action="store_true",
                        help="record stale admitted/executing runs as uncertain")
    parser.add_argument("--confirm-quiesced", action="store_true",
                        help="operator attestation that every worker is stopped: "
                             "any admitted/executing run is stale regardless of lease")
    parser.add_argument("--run-id", action="append", default=[],
                        help="scope reconciliation to these run ids (repeatable)")
    parser.add_argument("--resolve", action="append", default=[],
                        help="resolve one run: RUN_ID=abandoned (records the "
                             "operator decision; marks the run failed; never "
                             "re-executes)")
    parser.add_argument("--complete-approval", action="append", default=[],
                        help="close an admitted approval whose run reached a "
                             "terminal outcome: RUN_ID:CHECKPOINT")
    parser.add_argument("--operator", help="who is performing this action "
                        "(required with --apply/--resolve)")
    parser.add_argument("--reason", help="why (required with --apply/--resolve)")
    parser.add_argument("--evidence", help="evidence path or note, recorded with --resolve")
    parser.add_argument("--heartbeat-ttl-seconds", type=float,
                        default=DEFAULT_HEARTBEAT_TTL_SECONDS,
                        help="worker liveness window (default 60)")
    args = parser.parse_args(argv)
    store = SQLiteRunStore(Path(args.store).expanduser())

    exit_code = 0

    # -- operator resolutions (read-write decisions, never re-execution) ----
    for spec in args.resolve:
        if args.apply:
            raise SystemExit("error: --resolve and --apply are separate actions; "
                             "run them separately\n")
        _require(args, "operator", "--resolve requires --operator")
        _require(args, "reason", "--resolve requires --reason")
        if "=" not in spec:
            raise SystemExit(f"error: --resolve expects RUN_ID=abandoned, got {spec!r}\n")
        run_id, decision = spec.split("=", 1)
        if decision != "abandoned":
            raise SystemExit(
                f"error: only decision 'abandoned' is supported (got {decision!r}); "
                "completion can never be asserted without stored evidence\n"
            )
        if not store.resolve_run(
            run_id, operator=args.operator, reason=args.reason, evidence=args.evidence
        ):
            record = store.load_run(run_id)
            outcome = record.outcome if record else "missing"
            print(f"resolve skipped: {run_id} outcome={outcome} "
                  "(already terminal or unknown run)")
        else:
            print(f"resolved: {run_id} -> failed (decision abandoned, recorded)")

    # -- read-only evidence: orphan claims and admitted approvals ----------
    for spec in args.complete_approval:
        if ":" not in spec:
            raise SystemExit(f"error: --complete-approval expects RUN_ID:CHECKPOINT, "
                             f"got {spec!r}\n")
        run_id, checkpoint = spec.split(":", 1)
        record = store.load_run(run_id)
        if record is None or record.outcome not in ("completed", "failed"):
            raise SystemExit(
                f"error: approval can only be closed when the run is terminal "
                f"(run {run_id} outcome={record.outcome if record else 'missing'})\n"
            )
        if store.complete_approval(run_id, checkpoint):
            print(f"approval closed: {run_id} {checkpoint} -> executed")
        else:
            print(f"approval close skipped: {run_id} {checkpoint} not admitted")

    orphans = store.orphan_claims()
    for claim in orphans:
        print(
            "orphan idempotency claim (no run row): "
            f"run_id={claim['run_id']} user_uid={claim['user_uid']} "
            f"idem_key={claim['idem_key']!r} request_hash={claim['request_hash']}"
        )
    for approval in store.admitted_approvals():
        print(
            "approval admitted but continuation never completed: "
            f"run_id={approval['run_id']} checkpoint={approval['checkpoint']} "
            f"approved_by={approval['approved_by']}"
        )

    # -- stale-work reconciliation (dry run or recorded apply) --------------
    _require(args, "operator", "--apply requires --operator") if args.apply else None
    _require(args, "reason", "--apply requires --reason") if args.apply else None
    stale = store.reconcile_stale(
        dry_run=not args.apply,
        run_ids=args.run_id or None,
        require_quiesced=args.confirm_quiesced,
        operator=args.operator,
        reason=args.reason,
        heartbeat_ttl_seconds=args.heartbeat_ttl_seconds,
    )
    if not stale and not orphans and not args.resolve:
        print("no interrupted runs require reconciliation")
        return 0
    for entry in stale:
        run_id = entry["run_id"]
        record = store.load_run(run_id)
        print(
            f"{'marked uncertain' if args.apply else 'would mark uncertain'}: "
            f"{run_id} owner={record.owner_uid} workflow={record.workflow} "
            f"status={record.status} reason={entry['reason']}"
        )
    if stale and not args.apply:
        print(
            "dry run only; rerun with --apply --operator <who> --reason <why> "
            "to record the uncertain outcomes"
        )
        exit_code = 3
    if stale:
        print(
            "uncertain outcomes must be resolved before any resubmission: "
            "determine whether effects occurred, then --resolve RUN_ID=abandoned "
            "(a new key is only appropriate after that recorded decision)."
        )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
