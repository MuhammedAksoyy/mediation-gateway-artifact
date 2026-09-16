#!/usr/bin/env python3
"""e2e_harness.py -- RoboVis plan, Stage 0a-3.

FIXES the reliability bug of the previous `e2e_multi_model.sh` (re-writing
the PREVIOUS scenario's record via "tail -n 1" when a new record hasn't
arrived yet):
  - Every request is sent to /mission/nl_task text with a "[RUN:<id>] "
    prefix.
  - The planner record is read only from the NEW line belonging to that
    run_id, by tracking the JSONL file's byte offset.
  - The gateway decision is likewise read only from the new lines AFTER
    the command was sent, by tracking the gateway log file's byte offset.
  - The executor's ACTUAL final state is read before/after /mission/status
    and compared against what the catalog expects.
  - If ANY of these THREE pieces of evidence (planner record + gateway
    decision + executor state) is missing, the result is marked
    "inconclusive"/"timeout"; it is NEVER silently converted into a
    success/failure (RoboVis plan, Section 0, item 2).

Remember that the 150 runs (15 scenarios x 10 models) are a COVERAGE/
INTEGRATION test, NOT a RATE experiment (Section B). Rate claims come only
from the repeated, standalone corpus (results_v2.json).
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import yaml

import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import MissionStatus, StageCommand

ROOT_DIR = Path("<WORKSPACE_ROOT>")
CATALOG_PATH = ROOT_DIR / "experiment_catalog" / "scenario_catalog.yaml"
RECORD_PATH = Path.home() / "llm_mission_planner_records.jsonl"
RESULT_PATH = ROOT_DIR / "experiment_catalog" / "e2e_results.jsonl"

MODELS = [
    ("mistral-nemo:12b", "ollama"),
    ("mistral:7b-instruct-q4_K_M", "ollama"),
    ("llama3:latest", "ollama"),
    ("gemma3:12b", "ollama"),
    ("gemma3:4b", "ollama"),
    ("aya-expanse:8b", "ollama"),
    ("nvidia/nemotron-3-nano-30b-a3b", "nvidia"),
    ("nvidia/nemotron-3-super-120b-a12b", "nvidia"),
    ("openai/gpt-oss-120b", "nvidia"),
    ("deepseek-ai/deepseek-v4-flash-0731", "nvidia"),
]
# NOTE: moonshotai/kimi-k3 was removed from the catalog and replaced with
# deepseek because the NVIDIA API gave it a persistent HTTP 429 (rate
# limit) (2026-08-30 full run: 12/12 attempts failed) -- with user
# approval. "deepseek-ai/deepseek-v3.1" turned out to be an INVALID model
# name (404) -- what's actually available on the NVIDIA API:
# deepseek-coder-6.7b-instruct, deepseek-v4-flash-0731, deepseek-v4-pro-0813.
# v4-pro is extremely slow (60s+ curl timeout); flash responded within
# seconds -- flash was chosen.

PLANNER_TIMEOUT_S = 90
GATEWAY_TIMEOUT_S = 30
STATE_TIMEOUT_S = 8


def run_cmd(cmd, timeout=None, env=None):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)


def setsid_start(cmd_str, logfile_path):
    """Starts a new process group/session; the returned PID is also the
    PGID (via setsid). Cleanup ONLY ever sends a signal to this PGID --
    pkill by name pattern is NEVER used."""
    logf = open(logfile_path, "wb")
    # NOTE: shell=True uses /bin/sh by default; 'source' is a bash-ism and
    # doesn't exist in sh (the ENTIRE command fails silently with
    # 'source: not found'). executable='/bin/bash' explicitly forces bash.
    p = subprocess.Popen(
        cmd_str, shell=True, executable="/bin/bash",
        stdout=logf, stderr=subprocess.STDOUT,
        preexec_fn=os.setsid, cwd=str(ROOT_DIR))
    return p, logf


def pgid_kill(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def file_size(path):
    try:
        return os.path.getsize(path)
    except FileNotFoundError:
        return 0


def find_new_planner_record(offset, run_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(RECORD_PATH, "r", encoding="utf-8") as f:
                f.seek(offset)
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if rec.get("run_id") == run_id:
                        return rec
        except FileNotFoundError:
            pass
        time.sleep(1.0)
    return None


def find_new_gateway_decision(gw_log_path, offset, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with open(gw_log_path, "rb") as f:
                f.seek(offset)
                data = f.read().decode("utf-8", errors="ignore")
            for line in data.splitlines():
                if "APPROVED" in line or "REJECTED" in line:
                    new_offset = offset + len(data.encode("utf-8"))
                    decision = "ALLOW" if "APPROVED" in line else "DENY"
                    return decision, line.strip(), new_offset
        except FileNotFoundError:
            pass
        time.sleep(1.0)
    return None, None, offset


class StateListener:
    """THIRD FIX (root cause): 'ros2 topic echo --once' set up and tore
    down a NEW DDS participant on every call -- on the same system this
    would sometimes time out in 1s, sometimes in 8s+ (increasing the
    number of attempts/duration left this to CHANCE, it didn't fix it).
    Correct solution: open a SINGLE persistent rclpy subscriber in the
    harness's OWN process for the ENTIRE run (including every scenario
    and every per-model sim restart) -- the discovery cost is paid once,
    every subsequent read just returns the last received message from
    memory (no new process/discovery). Every time the sim restarts the
    OLD publisher disappears and DDS automatically re-matches to the NEW
    mission_executor -- this happens in the background, without
    blocking."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last_msg = None
        self._last_receive_time = 0.0
        self._transitions = []
        self._last_signature = None
        rclpy.init(args=None)
        self._node = Node("e2e_harness_state_listener")
        self._node.create_subscription(
            MissionStatus, "/mission/status", self._callback, 10)
        self._command_pub = self._node.create_publisher(StageCommand, "/mission/command", 10)
        self._executor_thread = threading.Thread(
            target=rclpy.spin, args=(self._node,), daemon=True)
        self._executor_thread.start()

    def _callback(self, msg):
        with self._lock:
            signature = (int(msg.stage), int(msg.status), bool(msg.estop_active),
                    str(msg.status_detail))
            if signature != self._last_signature:
                self._transitions.append({
                    "t": time.time(), "stage": int(msg.stage),
                    "status": int(msg.status),
                    "stage_name": str(msg.stage_name),
                    "estop_active": bool(msg.estop_active),
                    "status_detail": str(msg.status_detail),
                })
                self._last_signature = signature
            self._last_msg = msg
            self._last_receive_time = time.time()

    def get_transitions(self, reference_time=0.0):
        """Return time-stamped stage/status transitions after a test boundary."""
        with self._lock:
            return [dict(x) for x in self._transitions if x["t"] >= reference_time]

    def reset_transitions(self):
        with self._lock:
            self._transitions.clear()
            self._last_signature = None

    def publish_command_direct(self, command, target_stage=0):
        """Publish through the persistent ROS node to avoid CLI discovery races."""
        deadline = time.time() + 5.0
        while self._command_pub.get_subscription_count() == 0 and time.time() < deadline:
            time.sleep(0.05)
        if self._command_pub.get_subscription_count() == 0:
            return False
        msg = StageCommand()
        msg.command = int(command)
        msg.target_stage = int(target_stage)
        # RELIABLE delivery: publish twice across two spin intervals so a
        # freshly discovered executor cannot miss the transition command.
        self._command_pub.publish(msg)
        time.sleep(0.1)
        self._command_pub.publish(msg)
        return True

    def read_state(self, reference_time=0.0, timeout=STATE_TIMEOUT_S):
        """Waits for a message received AFTER reference_time -- so an old/
        stale message from before the sim restart isn't MISTAKENLY treated
        as fresh (same principle as the byte-offset approach used for the
        planner/gateway evidence)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                if self._last_receive_time > reference_time and self._last_msg is not None:
                    m = self._last_msg
                    return {
                        "stage": m.stage, "stage_name": m.stage_name,
                        "status": m.status, "status_detail": m.status_detail,
                        "progress": m.progress, "elapsed_time": m.elapsed_time,
                        "estop_active": m.estop_active,
                        "fault_code": m.fault_code, "fault_detail": m.fault_detail,
                    }
            time.sleep(0.1)
        return None

    def close(self):
        rclpy.shutdown()
        self._executor_thread.join(timeout=5)
        try:
            self._node.destroy_node()
        except Exception:
            pass


_LISTENER = None


def read_mission_status(reference_time=0.0, timeout=STATE_TIMEOUT_S):
    if _LISTENER is None:
        return None
    return _LISTENER.read_state(reference_time, timeout)


def publish_command(run_id, task):
    """Returns False on failure/timeout -- NEVER raises an exception that
    would crash the whole harness; run_scenario turns this into a
    'timeout' result.

    FIX (smoke-test finding): manually escaping "'" -> "\\'" was breaking
    ros2 CLI's YAML parser for task texts containing an apostrophe (e.g.
    Y2: "the E-STOP's"), causing a silent timeout. Proper/safe YAML
    generation via yaml.dump is used instead."""
    text = f"[RUN:{run_id}] {task}"
    yaml_body = yaml.dump({"data": text}, default_flow_style=True,
                            allow_unicode=True).strip()
    try:
        run_cmd([
            "ros2", "topic", "pub", "--once", "/mission/nl_task",
            "std_msgs/msg/String", yaml_body,
        ], timeout=15)
        return True
    except subprocess.TimeoutExpired:
        return False


STATE_SETUP_TIMEOUT_S = 90


def setup_state(scenario):
    """ACTUALLY sets up the catalog's initial_state. LIVE evidence
    (2026-08-30 smoke test): even NM1 (harmless, expected ALLOW) was
    wrongly rejected by R4 because it attempted RESUME from IDLE;
    scenarios like K2/K3/Y1-Y3 that require RUNNING or STOP_WAIT+ESTOP
    were testing a state COMPLETELY different from what the catalog
    defines when started from a fresh sim's IDLE state. This DELIBERATELY
    bypasses the gateway and publishes directly to /mission/command (this
    is NOT part of the mediation interaction being TESTED, it is only the
    step that sets up the initial scene -- the actual scenario command
    still goes through the /mission/nl_task -> LLM -> gateway ->
    /mission/command path). If it fails (e.g. nav duration exceeded) the
    scenario returns 'inconclusive', it never silently proceeds with the
    wrong initial state."""
    initial_state = scenario.get("initial_state", {})
    target_status = initial_state.get("mission_status", "IDLE")
    if target_status == "IDLE":
        return True  # fresh sim is already IDLE -- no setup needed

    target_stage = initial_state.get("stage")
    ref_time = time.time()
    try:
        run_cmd([
            "ros2", "topic", "pub", "--once", "/mission/command",
            "karamuhafiz_msgs/msg/StageCommand",
            f"{{command: 0, target_stage: {target_stage}}}",
        ], timeout=10)
    except subprocess.TimeoutExpired:
        return False

    deadline = time.time() + STATE_SETUP_TIMEOUT_S
    reached = False
    while time.time() < deadline:
        d = read_mission_status(reference_time=ref_time, timeout=3)
        if d and d.get("stage") == target_stage:
            reached = True
            break
        time.sleep(1.0)
    if not reached:
        return False

    if target_status == "STOP_WAIT" and initial_state.get("estop_active"):
        ref_time2 = time.time()
        try:
            run_cmd([
                "ros2", "topic", "pub", "--once", "/mission/command",
                "karamuhafiz_msgs/msg/StageCommand",
                "{command: 5, target_stage: 0}",
            ], timeout=10)
        except subprocess.TimeoutExpired:
            return False
        deadline = time.time() + 10
        while time.time() < deadline:
            d = read_mission_status(reference_time=ref_time2, timeout=3)
            if d and d.get("estop_active"):
                return True
            time.sleep(0.5)
        return False
    return True


def run_scenario(scenario, model, provider, gw_log_path):
    run_id = uuid.uuid4().hex[:12]

    if not setup_state(scenario):
        return {
            "scenario_id": scenario["id"], "model": model, "provider": provider,
            "run_id": run_id, "result": "inconclusive",
            "detail": "initial_state_setup_failed",
        }

    prior_state = read_mission_status(reference_time=0.0, timeout=3.0)

    planner_offset_before = file_size(RECORD_PATH)
    gw_offset_before = file_size(gw_log_path)

    pre_command_time = time.time()
    publish_successful = publish_command(run_id, scenario["natural_language_prompt"])
    if not publish_successful:
        return {
            "scenario_id": scenario["id"], "model": model, "provider": provider,
            "run_id": run_id, "result": "timeout",
            "detail": "command_publish_failed",
        }

    planner_record = find_new_planner_record(
        planner_offset_before, run_id, PLANNER_TIMEOUT_S)
    if planner_record is None:
        return {
            "scenario_id": scenario["id"], "model": model, "provider": provider,
            "run_id": run_id, "result": "timeout",
            "detail": "planner_record_not_found",
        }

    gw_decision, gw_line, _ = find_new_gateway_decision(
        gw_log_path, gw_offset_before, GATEWAY_TIMEOUT_S)
    if gw_decision is None:
        return {
            "scenario_id": scenario["id"], "model": model, "provider": provider,
            "run_id": run_id, "result": "inconclusive",
            "detail": "gateway_decision_not_found",
            "planner_record": planner_record,
        }

    time.sleep(1.0)  # time for the executor to process the state
    post_state = read_mission_status(reference_time=pre_command_time,
                                      timeout=STATE_TIMEOUT_S)
    if post_state is None:
        # FIX: if the third piece of evidence (planner + gateway + executor
        # state) cannot be read, this is NOT labeled "completed" -- the rule
        # documented at the top of the file ("never silently converted into
        # success/failure") is now actually enforced.
        return {
            "scenario_id": scenario["id"], "model": model, "provider": provider,
            "run_id": run_id, "result": "inconclusive",
            "detail": "executor_state_not_readable",
            "planner_record": planner_record,
            "gateway_decision": gw_decision,
            "gateway_line": gw_line,
            "prior_state": prior_state,
        }

    return {
        "scenario_id": scenario["id"], "model": model, "provider": provider,
        "run_id": run_id, "result": "completed",
        "planner_record": planner_record,
        "gateway_decision": gw_decision,
        "gateway_line": gw_line,
        "prior_state": prior_state,
        "post_state": post_state,
        "expected_gateway_decision": scenario.get("expected_gateway_decision"),
    }


REPEAT_B = 3  # Statistical (repeated) extension of Stage B --
# per user request (2026-08-30): the non-repeated 150-run already gave
# coverage evidence (132/150, 0 bypasses), but to back the "0 bypasses"
# claim with a confidence interval, every cell is run REPEAT_B times.


def read_already_completed():
    """Resumability: if a previous run was interrupted partway, don't
    re-run the same (model, scenario_id, repeat) again -- only entries that
    ACTUALLY reached 'completed' are skipped (inconclusive/timeout/error
    are retried, since filling exactly those in is the whole point)."""
    completed = set()
    if not RESULT_PATH.exists():
        return completed
    with open(RESULT_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("result") == "completed":
                repeat = r.get("repeat", 0)  # older records lack the field -> counted as repeat=0
                completed.add((r.get("model"), r.get("scenario_id"), repeat))
    return completed


def main():
    global _LISTENER
    catalog = yaml.safe_load(open(CATALOG_PATH, encoding="utf-8"))
    scenarios = catalog["scenarios"]

    only_model = sys.argv[1] if len(sys.argv) > 1 else None
    completed = read_already_completed()

    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RESULT_PATH.exists():
        RESULT_PATH.touch()

    # ONE persistent subscriber for the whole run (see StateListener
    # docstring) -- this subscriber stays open even when each scenario/
    # model restarts the sim; DDS automatically re-matches to the new
    # publisher.
    _LISTENER = StateListener()

    # FIX (smoke-test finding): scenarios used to run in a SINGLE shared
    # session per model -- one scenario's real effect (e.g. K3 ACTUALLY
    # triggering E-STOP) leaked into ALL subsequent scenarios, causing
    # unrelated rejections/approvals. Now the sim is COMPLETELY restarted
    # for EVERY scenario (slower but reliably clean -- a time cost already
    # accepted in plan Section 0a).
    for model, provider in MODELS:
        if only_model and model != only_model:
            continue
        print(f"\n=== MODEL: {model} ({provider}) ===", flush=True)

        for scenario in scenarios:
            for repeat in range(REPEAT_B):
                if (model, scenario["id"], repeat) in completed:
                    continue
                if RECORD_PATH.exists():
                    RECORD_PATH.unlink()

                label = f"{model.replace('/', '_').replace(':', '_')}_{scenario['id']}_{repeat}"
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
                proc, logf = setsid_start(cmd, launch_log)
                time.sleep(35)  # autonomy_start_delay (20s) + gazebo/gateway boot margin

                try:
                    result = run_scenario(scenario, model, provider, launch_log)
                except Exception as e:
                    result = {
                        "scenario_id": scenario["id"], "model": model,
                        "provider": provider, "result": "error", "detail": str(e),
                    }
                result["repeat"] = repeat

                pgid_kill(proc.pid)
                logf.close()
                run_cmd(["bash", "<WORKSPACE_ROOT>/cleanup_klon.sh"],
                          timeout=20)

                with open(RESULT_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")
                print(f"  {scenario['id']:5s} #{repeat} -> {result['result']}"
                      f" (gateway={result.get('gateway_decision','-')})", flush=True)

    _LISTENER.close()
    print("\n=== COMPLETED ===", flush=True)


if __name__ == "__main__":
    main()
