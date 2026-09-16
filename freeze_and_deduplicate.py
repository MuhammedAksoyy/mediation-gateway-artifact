#!/usr/bin/env python3
"""freeze_and_deduplicate.py -- freezes the FINAL, deduplicated version of
the E2E results. Issue found during the GPT audit: there are MULTIPLE
records for the same (model,scenario,repeat) triple (first a failure,
then a success on retry) -- counting them TOGETHER double-counts.

Rule: for each (model,scenario,repeat), keep the BEST record (completed >
others; among others, keep the LAST attempt). This way every cell
represents EXACTLY ONE observation -- preserving the independent-trial
assumption the repeated statistics need.
"""
import json
from pathlib import Path

SOURCE_PATH = Path("<WORKSPACE_ROOT>/experiment_catalog/e2e_results.jsonl")
DEST_PATH = Path("<WORKSPACE_ROOT>/experiment_catalog/e2e_results_frozen.jsonl")


def main():
    records = []
    with open(SOURCE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    best = {}
    for r in records:
        key = (r.get("model"), r.get("scenario_id"), r.get("repeat", 0))
        current = best.get(key)
        if current is None:
            best[key] = r
        elif r.get("result") == "completed" and current.get("result") != "completed":
            best[key] = r  # completed takes priority over others
        elif r.get("result") != "completed" and current.get("result") != "completed":
            best[key] = r  # if both failed, keep the LATEST attempt (records are in file order)

    clean = list(best.values())
    with open(DEST_PATH, "w", encoding="utf-8") as f:
        for r in clean:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    from collections import Counter
    overall = Counter(r["result"] for r in clean)
    print(f"Raw records: {len(records)}  ->  Deduplicated cells: {len(clean)}")
    print(f"Overall distribution: {dict(overall)}")
    print()
    per_model = {}
    for r in clean:
        per_model.setdefault(r["model"], Counter())
        per_model[r["model"]][r["result"]] += 1
    for m, c in sorted(per_model.items()):
        total = sum(c.values())
        print(f"  {m:38s} completed={c.get('completed',0):3d}/{total:3d}  "
              f"inconclusive={c.get('inconclusive',0):3d}  timeout={c.get('timeout',0):3d}  "
              f"error={c.get('error',0):3d}")
    print(f"\nFrozen file: {DEST_PATH}")


if __name__ == "__main__":
    main()
