# Reproducibility Artifact (anonymized for double-anonymous review)

This archive backs the evidence layers reported in "Toward Complete
Mediation for LLM-Directed Commands in an Autonomous Ground Vehicle"
(submitted to IEEE ICRA 2027). It contains the raw logs, analysis
scripts, policy file, scenario catalogue, and result files for every
evidence layer in Table II of the paper, plus the relay-mechanism
reproduction described in Section VII.

## Anonymization note (paths/host)

This is a redacted copy of the authors' internal experiment directory.
The following were changed for anonymity, and nothing else:
- The local absolute workspace path was replaced with `<WORKSPACE_ROOT>`.
- The local home directory path was replaced with `<HOME>`.
- One local machine hostname was replaced with `<REDACTED_HOST>`.
- The name of the real-world competition this platform was built for, and
  the Turkish word for its written rules ("sartname"), were generalized to
  "course" / "specification" to match the paper's own anonymized phrasing.

No measurement, timestamp, log line, decision record, or numeric result
was altered by this redaction pass. `MANIFEST_SHA256.txt` is a fresh
SHA-256 manifest computed on the current (redacted, translated) files; it
is intentionally different from the authors' internal, non-redacted
manifest for the reasons stated here and below, not because any
experimental outcome changed.

## Translation note (Turkish -> English)

The authors' original working files (script identifiers, comments, and
JSON/JSONL/YAML field names) were in Turkish. They have been translated
to English for reviewer accessibility. Only labels, keys, comments, and
documentary/free-text fields authored by the researchers themselves were
translated -- numeric values, booleans, array lengths, timestamps, and
decision outcomes are unchanged, and every `.jsonl` file has the same
number of records before and after translation.

**Deliberately left untranslated** (this is evidence, not documentation --
translating it would misrepresent what the running system or the LLM
actually produced):
- `planner_record.task` and `planner_record.raw_output` in the E2E
  corpus files -- the actual Turkish-language natural-language prompt
  sent to the LLM planner, and the LLM's actual raw response (including
  its own Turkish `reasoning` text). The underlying robot software's
  operator interface is Turkish-language, so the real experimental
  stimulus and the model's real output are in Turkish; see
  `scenario_catalog.yaml` for an English paraphrase of each scenario next
  to its original prompt.
- `stage_name`, `status_detail`, and `fault_detail` inside any `prior_state`
  /`post_state`/`before`/`after` block -- these mirror the ROS 2
  `MissionStatus` message's actual string fields, populated by the
  (unpublished) vehicle control software; they are raw captured evidence.
- `gerekceler` and `gateway_line` values -- the literal reasons array and
  log line published by the real (unpublished) `mediation_gateway.py`
  process on `/mediation/rejected`, e.g. `"R4:resume_yuksek_risk_asama=8_ret"`.
  These are quoted directly, in this same untranslated form, in the paper
  itself. A short glossary of the recurring terms:
  - `R1`/`R2`/`R3`/`R4` -- the four gateway rule families (Table I in the paper).
  - `asama` = stage, `yuksek_risk` = high-risk, `dusuk` = low, `ortak_kanal` = shared channel,
    `estop_aktifken` = while E-STOP is active, `varsayilan_ret` = denied by default,
    `yetersiz` = insufficient, `batarya` = battery, `ret` = deny.
- Turkish stage names inside those same raw blocks (e.g. `"Yan Eğim"`,
  `"Dik Engel"`) -- see `risk_policy.yaml` for the English name of every
  numbered stage.

## Layout

- `risk_policy.yaml`, `scenario_catalog.yaml` -- versioned policy and
  scenario inputs (translated; see note above for what stayed untranslated
  inside `scenario_catalog.yaml`'s prompts).
- `e2e_harness.py`, `e2e_results.jsonl`, `e2e_results_frozen.jsonl`,
  `e2e_results.jsonl.before_dedup_backup` -- end-to-end corpus (policy
  agreement layer, Table II row 1).
- `analysis_0b.py`, `analysis_0b_result.json`, `independent_oracle_check.py`,
  `independent_oracle_result.json` -- independent external check against
  the course specification (Table II row 2).
- `bypass_experiment.py`, `bypass_experiment_results.jsonl`,
  `bypass_necessity_check.py`, `bypass_necessity_check_result.json` --
  bypass/necessity experiment (Table II row 3).
- `k3_gateway_critical_matrix*.py/.jsonl`, `k3_battery_stress_results.jsonl`,
  `k3_summary.json` -- live critical-case matrix (Table II row 4).
- `k4_regression.py`, `k4_regression_result.json` -- policy regression
  (Table II row 5). Its private attribute/method names (e.g.
  `_r4_start_tek_asama_modu`) are the real, unpublished
  `mediation_gateway.py` class's actual internal names and were
  deliberately left as-is so this test remains runnable against that
  class; only this script's own comments and output were translated.
- `sros2_gateway_test/` -- SROS2 Enforce transport-authorization
  experiment, raw pub/sub logs included verbatim (Table II row 6).
- `gateway_latency_experiment.py`, `gateway_latency_experiment_results.json`
  -- single-host round-trip latency measurement (Table II row 7).
- `relay_mechanism_verification_result.json` -- deliberate, 2/2
  reproduction of the incomplete-mediation relay finding described in
  Section VII.
- `trial_history.py`/`.jsonl`, `freeze_and_deduplicate.py`, `backfill.py`
  -- bookkeeping utilities used while assembling the E2E corpus.
- `k1_experiment_manifest.json`, `k1_manifest.py` -- the authors' internal
  file inventory and hashing tool (retained for transparency about the
  data-management process; its recorded hashes will not match this
  redacted, translated copy byte-for-byte, for the reasons stated above).

All claims in the paper are bounded by a single simulator, a single
course, and the recorded test cases here; this archive should not be
read as evidence of full-system or physical-vehicle safety.
