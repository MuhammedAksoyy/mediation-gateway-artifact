# Reproducibility Artifact (anonymized for double-anonymous review)

This archive backs the evidence layers reported in "Toward Complete
Mediation for LLM-Directed Commands in an Autonomous Ground Vehicle"
(submitted to IEEE ICRA 2027). It contains the raw logs, analysis
scripts, policy file, scenario catalogue, and result files for every
evidence layer in Table II of the paper, plus the interlock-fix
verification described in Section VII.

## Anonymization note

This is a redacted copy of the authors' internal experiment directory.
Only the following were changed, and nothing else:
- The local absolute workspace path was replaced with `<WORKSPACE_ROOT>`.
- The local home directory path was replaced with `<HOME>`.
- One local machine hostname was replaced with `<REDACTED_HOST>`.

No measurement, timestamp, log line, decision record, or numeric result
was altered. `MANIFEST_SHA256.txt` in this directory is a fresh SHA-256
manifest computed on these redacted files; it is intentionally different
from the authors' internal (non-redacted) manifest for the reason above,
not because any experimental content changed.

## Layout

- `risk_policy.yaml`, `scenario_catalog.yaml` -- versioned policy and
  scenario inputs.
- `e2e_harness.py`, `e2e_sonuclar*.jsonl` -- end-to-end corpus (policy
  agreement layer, Table II row 1).
- `analiz_0b.py`, `analiz_0b_sonuc.json`, `bagimsiz_oracle_kontrolu.py`,
  `bagimsiz_oracle_sonuc.json` -- independent external check against the
  course specification (Table II row 2).
- `bypass_deneyi.py`, `bypass_deneyi_sonuclari.jsonl`,
  `bypass_kirilim.py`, `bypass_kirilim_sonuc.json` -- bypass/necessity
  experiment (Table II row 3).
- `k3_gateway_kritik_matrisi*.py/.jsonl`, `k3_batarya_stres_sonuclari.jsonl`,
  `k3_ozet.json` -- live critical-case matrix (Table II row 4).
- `k4_regresyon.py`, `k4_regresyon_sonuc.json` -- policy regression
  (Table II row 5).
- `sros2_gateway_test/` -- SROS2 Enforce transport-authorization
  experiment, raw pub/sub logs included (Table II row 6).
- `gateway_gecikme_deneyi.py`, `gateway_gecikme_sonuclari.json` --
  single-host round-trip latency measurement (Table II row 7).
- `role_mekanizmasi_dogrulama_sonuc.json` -- deliberate reproduction of
  the incomplete-mediation relay finding and the runtime-interlock fix
  verification described in Section VII (both temporal orderings, plus
  the post-fix re-attack confirmation).
- `k1_deney_manifesti.json`, `k1_manifest.py` -- the authors' internal
  file inventory and hashing tool (retained for transparency about the
  data-management process; see the anonymization note above for why its
  recorded hashes will not match this redacted copy byte-for-byte).

All claims in the paper are bounded by a single simulator, a single
course, and the recorded test cases here; this archive should not be
read as evidence of full-system or physical-vehicle safety.
