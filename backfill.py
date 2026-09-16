#!/usr/bin/env python3
"""backfill.py -- fills in missing/failed (model, scenario) pairs.

For the two kinds of gaps in the 150-run:
  1) Transient gateway-timing misses (GATEWAY_TIMEOUT_S raised 15->30) --
     RETRIED with the SAME model.
  2) 15 scenarios left empty for kimi-k3 due to a persistent NVIDIA
     rate-limit (429) -- filled in with a NEW model (deepseek-ai/
     deepseek-v3.1).

Results are APPENDED to the SAME e2e_results.jsonl -- the 150-run's 120
successful lines are PRESERVED, not re-run.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import e2e_harness as eh

# FIX (morning of 2026-08-30): some of last night's partial backfill
# ALREADY got fixed (nemotron-nano/G2, nemotron-super/NM2+G1) -- only what
# is STILL inconclusive (confirmed by today's file scan) remains.
# (model, provider, scenario_id) -- to be retried with the same model
RETRY_SAME_MODEL = [
    ("gemma3:4b", "ollama", "O1"),
    ("gemma3:4b", "ollama", "O2"),
    ("gemma3:4b", "ollama", "O3"),
    ("aya-expanse:8b", "ollama", "G2"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia", "O2"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia", "Y3"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia", "O2"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia", "O3"),
    ("openai/gpt-oss-120b", "nvidia", "O3"),
    ("openai/gpt-oss-120b", "nvidia", "G1"),
    ("openai/gpt-oss-120b", "nvidia", "Y3"),
]

# Model replacing kimi-k3 -- ALL 15 scenarios (first tried yesterday with
# the INVALID model name "deepseek-ai/deepseek-v3.1", 3 records were
# deleted and corrected -- correct name: deepseek-v4-flash-0731)
NEW_MODEL = ("deepseek-ai/deepseek-v4-flash-0731", "nvidia")


def run_single(model, provider, scenario_id, scenarios_by_id):
    scenario = scenarios_by_id[scenario_id]
    label = f"backfill_{model.replace('/', '_').replace(':', '_')}_{scenario_id}"
    launch_log = f"/tmp/e2e_harness_{label}.log"
    cmd = (
        "source /opt/ros/humble/setup.bash && "
        "source <WORKSPACE_ROOT>/install/setup.bash && "
        "export ROS_DOMAIN_ID=42 && export ROS_LOCALHOST_ONLY=1 && "
        "ros2 launch karamuhafiz_bringup autonomous_run.launch.py "
        "use_mediation_gateway:=true use_llm_mission_planner:=true "
        "use_gui:=false "
        f"llm_provider:={provider} llm_model:={model}"
    )
    proc, logf = eh.setsid_start(cmd, launch_log)
    time.sleep(35)
    try:
        result = eh.run_scenario(scenario, model, provider, launch_log)
    except Exception as e:
        result = {"scenario_id": scenario_id, "model": model, "provider": provider,
                  "result": "error", "detail": str(e)}
    eh.pgid_kill(proc.pid)
    logf.close()
    eh.run_cmd(["bash", "<WORKSPACE_ROOT>/cleanup_klon.sh"], timeout=20)
    with open(eh.RESULT_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(f"  [backfill] {model} {scenario_id} -> {result['result']}"
          f" (gateway={result.get('gateway_decision', '-')})", flush=True)


def main():
    catalog = eh.yaml.safe_load(open(eh.CATALOG_PATH, encoding="utf-8"))
    scenarios_by_id = {s["id"]: s for s in catalog["scenarios"]}

    eh._LISTENER = eh.StateListener()

    print(f"=== BACKFILL: {len(RETRY_SAME_MODEL)} same-model retries + "
          f"15 deepseek (replacing kimi) ===", flush=True)

    for model, provider, sid in RETRY_SAME_MODEL:
        run_single(model, provider, sid, scenarios_by_id)

    model, provider = NEW_MODEL
    print(f"\n=== NEW MODEL (replacing kimi-k3): {model} ===", flush=True)
    for sid in scenarios_by_id:
        run_single(model, provider, sid, scenarios_by_id)

    eh._LISTENER.close()
    print("\n=== BACKFILL COMPLETE ===", flush=True)


if __name__ == "__main__":
    main()
