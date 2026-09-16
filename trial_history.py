#!/usr/bin/env python3
"""trial_history.py -- GPT audit round 2, item 8: an 'only the best
record' table could look better than it is due to operational
persistence. This script takes the RAW e2e_results.jsonl (NOT the
frozen/deduplicated one) and, for each (model,scenario,repeat) cell,
produces a separate table with the number of attempts, the first
attempt's result, and which attempt (if any) succeeded -- transparently
showing the actual operational cost (how many retries were needed) BEHIND
the coverage claim (359/434).
"""
import json
from collections import defaultdict
from pathlib import Path

SOURCE_PATH = Path("<WORKSPACE_ROOT>/experiment_catalog/e2e_results.jsonl")
DEST_PATH = Path("<WORKSPACE_ROOT>/experiment_catalog/trial_history.jsonl")


def main():
    attempts = defaultdict(list)
    with open(SOURCE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            key = (r.get("model"), r.get("scenario_id"), r.get("repeat", 0))
            attempts[key].append(r["result"])

    summary = []
    multi_attempt_count = 0
    for (model, sid, repeat), results in sorted(attempts.items()):
        attempt_count = len(results)
        if attempt_count > 1:
            multi_attempt_count += 1
        first_success_index = next(
            (i for i, s in enumerate(results) if s == "completed"), None)
        summary.append({
            "model": model, "scenario_id": sid, "repeat": repeat,
            "attempt_count": attempt_count,
            "attempt_sequence_results": results,
            "succeeded_on_attempt_number": (first_success_index + 1
                                           if first_success_index is not None else None),
        })

    with open(DEST_PATH, "w", encoding="utf-8") as f:
        for o in summary:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"Total cells: {len(summary)}")
    print(f"Cells requiring more than one attempt: {multi_attempt_count}")
    never_succeeded = sum(1 for o in summary if o["succeeded_on_attempt_number"] is None)
    print(f"Cells never succeeding in any attempt: {never_succeeded}")
    print(f"Output: {DEST_PATH}")


if __name__ == "__main__":
    main()
