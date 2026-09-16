#!/usr/bin/env python3
"""K3: execute RESUME/SKIP from meaningful executor states.

Unlike the historical bypass replay, this script first creates a running
mission, pauses it when needed, and records the status transition trace. It
never labels a missing transition as safe.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import e2e_harness as eh

ROOT = Path(__file__).resolve().parent
ANALYSIS = ROOT / "analysis_0b_result.json"
CATALOG = ROOT / "scenario_catalog.yaml"
OUT = ROOT / "resume_skip_experiment_results.jsonl"
_SHARED_PROC = None
_SHARED_LOGF = None


def cli_status():
    """Fallback status read for DDS observers that joined too early."""
    try:
        p = eh.run_cmd(["ros2", "topic", "echo", "--once", "/mission/status"], timeout=5)
        if p.returncode != 0:
            return None
        data = next((x for x in eh.yaml.safe_load_all(p.stdout) if isinstance(x, dict)), None)
        return data if isinstance(data, dict) and "stage" in data else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def publish(command: int, target: int = 0) -> bool:
    try:
        completed = eh.run_cmd([
            "ros2", "topic", "pub", "--once", "/mission/command",
            "karamuhafiz_msgs/msg/StageCommand",
            f"{{command: {command}, target_stage: {target}}}",
        ], timeout=10)
        return completed.returncode == 0
    except (TimeoutError, subprocess.TimeoutExpired):
        return False


def wait_status(stage=None, status=None, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = eh.read_mission_status(reference_time=0.0, timeout=1) or cli_status()
        if d and (stage is None or d["stage"] == stage) and (status is None or d["status"] == status):
            return d
        time.sleep(0.2)
    return None


def establish_running_stage(stage: int, pause: bool) -> bool:
    """Enter a stage and, for RESUME, pause at first RUNNING observation."""
    deadline = time.time() + 90
    while time.time() < deadline:
        current = eh.read_mission_status(reference_time=0.0, timeout=1) or cli_status()
        if current and current["stage"] == stage:
            if not pause:
                return True
            if current["status"] == 1:
                return publish(1) and wait_status(status=2, timeout=12) is not None
        time.sleep(0.1)
    return False


def run_case(case, scenarios):
    sid = case["scenario"]
    scenario = scenarios[sid]
    command = {"RESUME": 2, "SKIP": 3}[case["command"]]
    target = int(case.get("target_stage") or 0)
    result = {"model": case.get("model"), "scenario": sid,
              "repeat": case.get("repeat", 0), "command": command,
              "target_stage": target, "result": "inconclusive"}
    shared = _SHARED_PROC is not None
    if shared:
        proc, logf = _SHARED_PROC, _SHARED_LOGF
    else:
        log = f"/tmp/k3_resume_skip_{sid}_{case.get('repeat', 0)}.log"
        launch = (
            "source /opt/ros/humble/setup.bash && "
            f"source {ROOT.parent}/install/setup.bash && "
            "export ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=1 ROS_SECURITY_ENABLE=0 && "
            "ros2 launch karamuhafiz_bringup autonomous_run.launch.py "
            "use_mediation_gateway:=false use_llm_mission_planner:=false use_gui:=false "
            "test_hold_stage:=true test_stage_id:=3"
        )
        proc, logf = eh.setsid_start(launch, log)
    try:
        # Nav2/Gazebo discovery is not complete when the launch process first
        # appears; wait for the executor topic graph before setup.
        if not shared:
            time.sleep(20)
        # Do not assume a fixed Gazebo startup time; wait for a fresh status
        # sample from this launch's executor before publishing setup commands.
        if wait_status(timeout=90) is None:
            result["detail"] = "executor_status_publish_not_started"
            return result
        eh._LISTENER.reset_transitions()
        bd = scenario.get("initial_state", {})
        # Use a long-running, non-restricted stage as the deterministic
        # pause point. Restricted-stage policy cases are covered separately
        # by K4; here we test the actual RESUME/SKIP executor semantics.
        setup_stage = 3 if command == 2 else 7
        if not establish_running_stage(setup_stage, pause=(command == 2)):
            result["detail"] = "setup_stage_not_established"
            return result
        ref = time.time()
        if not publish(command, target):
            result["detail"] = "command_publish_failed"
            return result
        time.sleep(5)
        after = eh.read_mission_status(reference_time=ref, timeout=5)
        trace = eh._LISTENER.get_transitions(ref)
        result.update({"post_state": after, "transitions": trace})
        if after is None:
            result["detail"] = "no_final_state"
        else:
            result["result"] = "completed"
            result["detail"] = "time_series_recorded"
    finally:
        if not shared:
            eh.pgid_kill(proc.pid)
            logf.close()
            eh.run_cmd(["bash", str(ROOT.parent / "cleanup_klon.sh")], timeout=20)
    return result


def main():
    # Keep CLI publishers in the same isolated DDS/security context as launch.
    os.environ.setdefault("ROS_DOMAIN_ID", "42")
    os.environ.setdefault("ROS_LOCALHOST_ONLY", "1")
    os.environ.setdefault("ROS_SECURITY_ENABLE", "0")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all cases")
    ap.add_argument("--force-command", choices=("RESUME", "SKIP"))
    args = ap.parse_args()
    analysis = json.loads(ANALYSIS.read_text(encoding="utf-8"))
    cases = [x for x in analysis["details"] if x.get("command") in ("RESUME", "SKIP")]
    if args.force_command:
        cases = [x for x in cases if x.get("command") == args.force_command]
    scenarios = {x["id"]: x for x in eh.yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["scenarios"]}
    if args.limit:
        cases = cases[:args.limit]
    existing = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            if old.get("result") == "completed":
                old_command = {2: "RESUME", 3: "SKIP"}.get(old.get("command"), old.get("command"))
                existing.add((old.get("model"), old.get("scenario"), old.get("repeat"), old_command))
    if not args.force_command:
        cases = [c for c in cases if (c.get("model"), c.get("scenario"), c.get("repeat", 0), c.get("command")) not in existing]
    global _SHARED_PROC, _SHARED_LOGF
    eh._LISTENER = eh.StateListener()
    launch_log = "/tmp/k3_single_session.log"
    launch = (
        "source /opt/ros/humble/setup.bash && "
        f"source {ROOT.parent}/install/setup.bash && "
        "export ROS_DOMAIN_ID=42 ROS_LOCALHOST_ONLY=1 ROS_SECURITY_ENABLE=0 && "
        "ros2 launch karamuhafiz_bringup autonomous_run.launch.py "
        "use_mediation_gateway:=false use_llm_mission_planner:=false use_gui:=false "
        "test_hold_stage:=true test_stage_id:=3"
    )
    _SHARED_PROC, _SHARED_LOGF = eh.setsid_start(launch, launch_log)
    try:
        time.sleep(20)
        if wait_status(timeout=90) is None:
            raise RuntimeError("executor_status_publish_not_started")
        for case in cases:
            stage = 3 if case["command"] == "RESUME" else 7
            eh.run_cmd(["ros2", "param", "set", "/mission_executor",
                         "test_stage_id", str(stage)], timeout=10)
            eh.run_cmd(["ros2", "param", "set", "/mission_executor",
                         "test_hold_stage", "false"], timeout=10)
            publish(4)  # ABORT -> IDLE; next tick starts requested test stage
            if wait_status(status=0, timeout=10) is None:
                result = {"model": case.get("model"), "scenario": case["scenario"],
                          "repeat": case.get("repeat", 0), "command": case["command"],
                          "result": "inconclusive", "detail": "reset_to_idle_failed"}
            else:
                eh.run_cmd(["ros2", "param", "set", "/mission_executor",
                             "test_hold_stage", "true"], timeout=10)
                result = run_case(case, scenarios)
            with OUT.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        if _SHARED_PROC is not None:
            eh.pgid_kill(_SHARED_PROC.pid)
            _SHARED_LOGF.close()
            eh.run_cmd(["bash", str(ROOT.parent / "cleanup_klon.sh")], timeout=20)
        eh._LISTENER.close()


if __name__ == "__main__":
    main()
