#!/usr/bin/env python3
"""Freeze experiment inputs and produce a reproducibility manifest.

The manifest deliberately records hashes and sizes only; it never stores
credentials, prompts containing personal data, or live connection details.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "k1_experiment_manifest.json"
FILES = [
    "scenario_catalog.yaml",
    "risk_policy.yaml",
    "analysis_0b.py",
    "independent_oracle_check.py",
    "bypass_necessity_check.py",
    "bypass_experiment.py",
    "e2e_harness.py",
    "e2e_results_frozen.jsonl",
    "bypass_experiment_results.jsonl",
    "bypass_necessity_check_result.json",
    "resume_skip_experiment.py",
    "k3_gateway_critical_matrix.py",
    "k3_gateway_critical_matrix_results.jsonl",
    "k3_battery_stress_results.jsonl",
    "k3_gateway_critical_matrix_mixed_round_raw.jsonl",
    "k3_gateway_critical_matrix_first_round_raw.jsonl",
    "k3_gateway_critical_matrix_second_round_raw.jsonl",
    "k3_gateway_critical_matrix_third_round_raw.jsonl",
    "k3_gateway_critical_matrix_fourth_round_raw.jsonl",
    "k4_regression.py",
    "k4_regression_result.json",
    "gateway_latency_experiment.py",
    "gateway_latency_experiment_results.json",
    "relay_mechanism_verification_result.json",
    "sros2_gateway_test/run_test.sh",
    "sros2_gateway_test/k5_sros2_report.json",
    "sros2_gateway_test/raw_logs/sros2r_test1_pub.log",
    "sros2_gateway_test/raw_logs/sros2r_test1_sub.log",
    "sros2_gateway_test/raw_logs/sros2r_test2_pub.log",
    "sros2_gateway_test/raw_logs/sros2r_test2_sub.log",
    "sros2_gateway_test/raw_logs/sros2r_test3_pub.log",
    "sros2_gateway_test/raw_logs/sros2r_test3_sub.log",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "-C", str(ROOT.parent), *args], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> None:
    artifacts = {}
    missing = []
    for name in FILES:
        path = ROOT / name
        if not path.is_file():
            missing.append(name)
            continue
        artifacts[name] = {
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        }

    manifest = {
        "schema": "k1-manifest/v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "host": os.uname().nodename,
        "ros_domain_id_expected": 42,
        "ros_localhost_only_expected": True,
        "git_revision": git_value("rev-parse", "HEAD"),
        "git_dirty": bool(git_value("status", "--porcelain")),
        "artifacts": artifacts,
        "missing": missing,
        "status": "blocked" if missing else "ready_for_replay",
    }
    OUT.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
