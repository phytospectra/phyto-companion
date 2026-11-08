"""CLI: separate key-free predictions from private evaluator commands."""
import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "benchmark.paired_hsqc"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fixture = sub.add_parser("synthetic", help="generate invented software-contract inputs only")
    fixture.add_argument("--bundle", required=True)
    fixture.add_argument("--keys", required=True)
    for command in ("lock", "run", "score"):
        p = sub.add_parser(command)
        p.add_argument("--bundle", required=True)
        p.add_argument("--output", required=True)
        if command in ("lock", "score"):
            p.add_argument("--keys", required=True)
        if command in ("run", "score"):
            p.add_argument("--lock", required=True)
        if command == "score":
            p.add_argument("--records", required=True)
    args = parser.parse_args()
    try:
        if args.command == "synthetic":
            from .harness import external
            from .synthetic import make_fixture
            external(args.keys)
            make_fixture(args.bundle, args.keys)
            print("SYNTHETIC software contracts generated; no measured results")
        elif args.command == "run":
            from .harness import run_predictions
            result = run_predictions(args.bundle, args.lock, args.output)
            print(f"Prediction slots written: {len(result['records'])}; no keys loaded")
        elif args.command == "lock":
            from .scorer import freeze
            freeze(args.bundle, args.keys, args.output)
            print("Snapshot written; protocol REVIEW/LOCK PENDING, not scientific approval")
        else:
            from .scorer import score
            result = score(args.bundle, args.lock, args.keys, args.records, args.output)
            print(f"Scored {result['planned_test_cases']} planned test cases; review/lock pending")
    except Exception as exc:
        # Avoid leaking private content/path through library error strings.
        print(f"Aborted ({type(exc).__name__}); validate inputs privately; no scientific result", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
