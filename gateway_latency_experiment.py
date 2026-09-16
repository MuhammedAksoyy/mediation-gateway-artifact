#!/usr/bin/env python3
"""K6/D2 -- Latency cost (same principle as the SINCONF D2 methodology):
N=1000 repeats, multiple flows, p50/p95 reported. The distinction here
corresponding to SINCONF's "local-analysis-only" / "sidecar-round" /
"full HITL wait" distinction is: cheap-path (R1 short-circuit),
expensive-path (R1's real TF2 lookup ACTUALLY RUNS), and NO-gateway
baseline (command_relay, a plain passthrough that runs no logic at all).

The measurement is BLACK-BOX: we PUBLISH to /mission/command_raw, and
wait for a RESPONSE on /mission/command OR /mediation/rejected. We are
measuring the real ROS2/DDS transport + the real Python REGL evaluation,
not a synthetic/isolated micro-benchmark.

Flows:
  1. resume_allow_gateway  -- RESUME @ low-risk stage=3, gateway ON. R1
     short-circuits (not CMD_START), only the R2/R3/R4 predicate check
     runs. Safe: repeated RESUME is a no-op in the executor after the
     first call.
  2. resume_allow_relay    -- the SAME command, gateway OFF (command_relay
     plain passthrough). Zero logic -- baseline ("how long would this
     take without a gateway").
  3. skip_deny_gateway     -- SKIP, gateway ON. R4 ALWAYS rejects it; the
     gateway NEVER forwards it to the executor (safe, repeatable).
  4. start8_deny_gateway_expensive -- START target_stage=8, gateway ON.
     R4-a rejects this BUT since the code does NOT short-circuit, R1's
     REAL TF2 lookup (tf_buffer.lookup_transform) still runs -- the most
     expensive path. Since R4 already rejects it, it never reaches the
     executor, so it's safe.
"""
from __future__ import annotations

import argparse
import json
import statistics
import threading
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import StageCommand, MissionStatus

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "gateway_latency_experiment_results.json"

CMD_START, CMD_PAUSE, CMD_RESUME, CMD_SKIP, CMD_ABORT, CMD_ESTOP = range(6)


class LatencyObserver(Node):
    def __init__(self):
        super().__init__("robovis_latency_observer")
        self._lock = threading.Lock()
        self._last_response_t = None
        self._last_response_topic = None
        self._raw = self.create_publisher(StageCommand, "/mission/command_raw", 10)
        self.create_subscription(StageCommand, "/mission/command", self._on_command, 50)
        self.create_subscription(
            __import__("std_msgs.msg", fromlist=["String"]).String,
            "/mediation/rejected", self._on_rejected, 50)
        self.create_subscription(MissionStatus, "/mission/status", lambda m: None, 10)

    def _on_command(self, msg):
        with self._lock:
            self._last_response_t = time.perf_counter()
            self._last_response_topic = "command"

    def _on_rejected(self, msg):
        with self._lock:
            self._last_response_t = time.perf_counter()
            self._last_response_topic = "rejected"

    def one_trial(self, command: int, target_stage: int, timeout: float = 2.0):
        with self._lock:
            self._last_response_t = None
            self._last_response_topic = None
        msg = StageCommand()
        msg.command = int(command)
        msg.target_stage = int(target_stage)
        t0 = time.perf_counter()
        self._raw.publish(msg)
        deadline = t0 + timeout
        while time.perf_counter() < deadline:
            with self._lock:
                if self._last_response_t is not None:
                    return (self._last_response_t - t0) * 1000.0, self._last_response_topic
            time.sleep(0.0003)
        return None, "timeout"


def summarize(values):
    if not values:
        return {"n": 0}
    s = sorted(values)
    n = len(s)
    def pct(p):
        idx = min(n - 1, int(round(p * (n - 1))))
        return s[idx]
    return {
        "n": n, "mean_ms": statistics.mean(s), "stdev_ms": statistics.pstdev(s) if n > 1 else 0.0,
        "min_ms": s[0], "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99), "max_ms": s[-1],
    }


def run_flow(observer, name, command, target_stage, n, expected_topic):
    print(f"=== {name}: starting {n} trials ===", flush=True)
    values = []
    timeout_count = 0
    wrong_topic_count = 0
    for i in range(n):
        ms, topic = observer.one_trial(command, target_stage)
        if ms is None:
            timeout_count += 1
            continue
        if topic != expected_topic:
            wrong_topic_count += 1
        values.append(ms)
        if (i + 1) % 200 == 0:
            print(f"  [{name}] {i+1}/{n}", flush=True)
    s = summarize(values)
    s.update({"flow": name, "timeout": timeout_count, "unexpected_topic": wrong_topic_count})
    print(f"=== {name} RESULT: p50={s.get('p50_ms', 0):.2f}ms p95={s.get('p95_ms', 0):.2f}ms "
          f"n={s['n']} timeout={timeout_count} ===", flush=True)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--mode", choices=("gateway", "relay"), required=True)
    args = ap.parse_args()

    rclpy.init()
    observer = LatencyObserver()
    thread = threading.Thread(target=rclpy.spin, args=(observer,), daemon=True)
    thread.start()
    time.sleep(2.0)

    results = []
    try:
        if args.mode == "gateway":
            results.append(run_flow(
                observer, "resume_allow_gateway", CMD_RESUME, 0, args.n, "command"))
            results.append(run_flow(
                observer, "skip_deny_gateway", CMD_SKIP, 0, args.n, "rejected"))
            results.append(run_flow(
                observer, "start8_deny_gateway_expensive_R1_TF_lookup", CMD_START, 8, args.n, "rejected"))
        else:
            results.append(run_flow(
                observer, "resume_allow_relay_baseline", CMD_RESUME, 0, args.n, "command"))
    finally:
        observer.destroy_node()
        rclpy.shutdown()
        thread.join(timeout=5)

    previous = []
    if OUT.exists():
        previous = json.loads(OUT.read_text(encoding="utf-8")).get("flows", [])
    previous = [x for x in previous if x["flow"] not in {s["flow"] for s in results}]
    all_flows = previous + results
    OUT.write_text(json.dumps({"schema": "gateway-latency/v1", "flows": all_flows},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOutput: {OUT}")


if __name__ == "__main__":
    main()
