"""Print the score distribution used to choose the retrieval floor (Q3).

Run this before setting ``RETRIEVAL_TOP_K`` and ``SIMILARITY_FLOOR`` and read
the gap between the weakest supported question and the strongest unsupported
one. That gap is the floor. Setting it anywhere else means guessing.

    python scripts/inspect_scores.py
    python scripts/inspect_scores.py --k 8

The script needs neither setting to be configured; if they are set it reports
the value in force alongside the distribution.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.eval.cases import (  # noqa: E402
    COVERED_FACT_QUERIES,
    FACT_QUERIES,
    UNSUPPORTED_QUERIES,
)
from src.rag.retriever import Retriever  # noqa: E402


def _fmt(row: Sequence[object], widths: Sequence[int]) -> str:
    return "  ".join(str(cell).ljust(w) for cell, w in zip(row, widths)).rstrip()


def _table(rows: List[Sequence[object]], headers: Sequence[str]) -> str:
    widths = [max(len(str(r[i])) for r in rows + [headers]) + 2 for i in range(len(headers))]
    out = [_fmt(headers, widths), _fmt(["-" * (w - 2) for w in widths], widths)]
    out.extend(_fmt(row, widths) for row in rows)
    return "\n".join(out)


def _report(
    retriever: Retriever,
    k: int,
    label: str,
    cases: Sequence[object],
    fact_type_of=lambda case: None,
) -> List[float]:
    """Score each case. ``cases`` holds :class:`FactQuery` or plain strings."""
    rows: List[Sequence[object]] = []
    tops: List[float] = []
    for case in cases:
        question = case.question if hasattr(case, "question") else str(case)
        expected = fact_type_of(case)
        if expected:
            hits = retriever.retrieve_for_fact_type(question, expected, k=k)
        else:
            hits = retriever.retrieve(question, k=k)
        best = next((h for h in hits if h.fact_type_match), None)
        top = best.similarity if best is not None else (hits[0].similarity if hits else 0.0)
        tops.append(top)
        rows.append(
            (
                f"{top:.3f}",
                expected or "(any)",
                "ok" if best is not None else "no fact-type match",
                (best.chunk_id if best is not None else "-")[:40],
                question[:58],
            )
        )
    print(f"\n{label}  (k={k})")
    print(_table(rows, ["top", "fact_type", "status", "best chunk_id", "question"]))
    return tops


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--k",
        type=int,
        default=8,
        help="candidates to score per question (default: 8)",
    )
    args = parser.parse_args(argv)

    retriever = Retriever()
    print(f"collection: {retriever.store.collection_name!r}  chunks: {retriever.store.count()}")

    supported = _report(
        retriever,
        args.k,
        "FACT queries (answerable, excluding the Q1 gap)",
        COVERED_FACT_QUERIES,
        fact_type_of=lambda case: case.fact_type,
    )
    _report(
        retriever,
        args.k,
        "FACT query carrying the known corpus gap (Q1)",
        tuple(case for case in FACT_QUERIES if case not in COVERED_FACT_QUERIES),
        fact_type_of=lambda case: case.fact_type,
    )
    unsupported = _report(retriever, args.k, "UNSUPPORTED queries (must stay below the floor)", UNSUPPORTED_QUERIES)

    # The gap that decides SIMILARITY_FLOOR: weakest supported vs strongest unsupported.
    weakest_supported = min(supported)
    strongest_unsupported = max(unsupported)
    midpoint = (weakest_supported + strongest_unsupported) / 2

    print("\n" + "=" * 78)
    print("separation")
    print("=" * 78)
    print(f"  weakest supported fact query : {weakest_supported:.3f}")
    print(f"  strongest unsupported query  : {strongest_unsupported:.3f}")
    print(f"  gap                         : {weakest_supported - strongest_unsupported:+.3f}")
    if weakest_supported <= strongest_unsupported:
        print(
            "  -> supported and unsupported OVERLAP. No threshold separates them,\n"
            "     so the floor cannot be chosen from this comparison alone."
        )
        print("     Use the off-domain control set below instead:")
    else:
        print(f"  midpoint of the gap         : {midpoint:.3f}")
    print()
    return _floor_report(retriever, supported, unsupported)


OFF_DOMAIN_PROBES: Sequence[str] = (
    "What is the weather in Mumbai today?",
    "How do I open a bank account?",
    "Who won the cricket match?",
    "Explain the water cycle",
    "What is the capital of France?",
    "How do I cook biryani?",
    "What is the speed of light?",
    "Who wrote Hamlet?",
    "What is the best laptop under 50000?",
    "What is the price of gold today?",
    "What is VAT in the UK?",
    "What is inflation?",
    "How do I install Linux?",
    "How do I learn guitar?",
    "How do I start a blog?",
)


def _floor_report(retriever: Retriever, supported: List[float], unsupported: List[float]) -> int:
    """Report the comparison that actually sets the floor: in-domain vs off-domain.

    Supported and unsupported questions overlap completely, so a floor tuned on
    them is arbitrary. The floor's measurable job is rejecting questions from
    outside the domain, which separates cleanly.
    """
    in_domain = supported + unsupported
    off_domain = [
        max(h.similarity for h in retriever.retrieve(q, k=1)) for q in OFF_DOMAIN_PROBES
    ]
    print(f"  in-domain minimum (n={len(in_domain):>2})      : {min(in_domain):.3f}")
    print(f"  off-domain maximum (n={len(off_domain):>2})      : {max(off_domain):.3f}")
    if min(in_domain) > max(off_domain):
        midpoint = (min(in_domain) + max(off_domain)) / 2
        print(f"  -> clean separation, gap {min(in_domain) - max(off_domain):+.3f}")
    else:
        print("  -> these overlap too; no threshold is clean.")
    print()
    print("  candidate floors")
    header = "    floor  off-domain admitted  unsupported admitted  supported kept"
    print(header)
    for candidate in (0.35, 0.40, 0.42, 0.45, 0.48, 0.55, 0.60):
        print(
            f"    {candidate:.2f}"
            f"{sum(1 for x in off_domain if x > candidate):>21}"
            f"{sum(1 for x in unsupported if x > candidate):>24}"
            f"{sum(1 for x in supported if x > candidate):>18}"
        )
    try:
        print(f"\n  in force: RETRIEVAL_TOP_K={retriever.top_k}, SIMILARITY_FLOOR={retriever.similarity_floor}")
    except Exception:  # noqa: BLE001 - settings may still be PENDING while tuning
        print("\n  in force: not set yet (this is the run to set them from)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
