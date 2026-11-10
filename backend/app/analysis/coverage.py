"""
"Is anything still missed?" for the pattern rules.

Rules can only catch what they know. This report answers the other half: of the ERROR and WARN events
in a collection, how many does no rule match, and what do the unmatched ones look like (grouped by
message shape, with digits and ids folded together, most frequent first). A shape that keeps showing up
here is either a known issue that needs a rule or noise worth a rule of its own.

    python -m app.analysis.coverage <collection id>

Example messages are shown with PHI/PII masked, like everywhere else.
"""
import argparse
from collections import Counter
from typing import Optional

from sqlalchemy.orm import Session

from app.analysis.engine import iter_events
from app.analysis.rules import PROBLEM_LEVELS, RULES
from app.analysis.text import EXAMPLE_CHARS, masked, shape

MAX_SHAPES = 5000            # distinct shapes tracked; the rest are counted but not listed


def coverage_report(db: Session, job_id: str, rules: tuple = RULES, top: int = 20) -> dict:
    total = matched = problems = problems_matched = overflow = 0
    per_rule: Counter = Counter()
    shapes: dict = {}                       # (severity, shape) -> [count, example row]
    for row in iter_events(db, job_id):
        total += 1
        hit = [r.id for r in rules if r.matches(row.severity, row.message)]
        per_rule.update(hit)
        if hit:
            matched += 1
        if row.severity not in PROBLEM_LEVELS:
            continue
        problems += 1
        if hit:
            problems_matched += 1
            continue
        key = (row.severity, shape(row.message))
        if key in shapes:
            shapes[key][0] += 1
        elif len(shapes) < MAX_SHAPES:
            shapes[key] = [1, row]
        else:
            overflow += 1
    ranked = sorted(shapes.items(), key=lambda kv: -kv[1][0])[:top]
    unmatched = []
    for (severity, _), (count, row) in ranked:
        example = masked(row.message)[:EXAMPLE_CHARS]
        unmatched.append({"severity": severity, "count": count, "shape": shape(example),
                          "example_event_id": row.id, "example": example})
    return {
        "events": total,
        "matched_events": matched,
        "problem_events": problems,                      # ERROR and WARN
        "problem_matched": problems_matched,
        "problem_unmatched": problems - problems_matched,
        "per_rule": dict(per_rule),
        "unmatched": unmatched,
        "unmatched_not_listed": overflow,                # unmatched events beyond MAX_SHAPES shapes
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="ERROR/WARN events no pattern rule matches.")
    parser.add_argument("job_id", help="collection id")
    parser.add_argument("--top", type=int, default=20, help="how many unmatched shapes to list")
    args = parser.parse_args(argv)
    from app.db import SessionLocal
    with SessionLocal() as db:
        r = coverage_report(db, args.job_id, top=args.top)
    print(f"{r['events']} events; {r['problem_events']} ERROR/WARN, of which {r['problem_unmatched']} match no rule")
    for rid, n in sorted(r["per_rule"].items(), key=lambda kv: -kv[1]):
        print(f"  {n:>8}  {rid}")
    for u in r["unmatched"]:
        print(f"  {u['count']:>8}  {u['severity']:<5} {u['shape']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
