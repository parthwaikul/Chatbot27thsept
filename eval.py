"""Evaluation harness CLI (Phase 7, architecture.md K19, PRD §15).

    python eval.py                 # full table, exit 0 when green
    python eval.py --json          # machine-readable, same exit code
    python eval.py --only E-5 E-8  # re-run one or two rows

Exit code is 0 only when every check passes, so this is usable as a gate in CI
or in the P8 clean-clone run.

Thin by design (AD-5): the checks live in :mod:`src.eval.harness`, and this file
only parses arguments and formats output. It makes no judgement of its own about
what passes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from src.eval.harness import HarnessError, run  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the PRD section 15 acceptance checks (E-1 … E-9)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit machine-readable JSON instead of the table",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="E-N",
        help="run only these check ids, e.g. --only E-5 E-8",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(only=args.only)
    except HarnessError as exc:
        print(f"eval: cannot run the harness: {exc}", file=sys.stderr)
        return 2

    if not report.results:
        print(f"eval: no checks matched {args.only}", file=sys.stderr)
        return 2

    print(report.to_json() if args.as_json else report.render())

    for failure in report.failures():
        for reason in failure.failures:
            print(f"  {failure.check_id}: {reason}", file=sys.stderr)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
