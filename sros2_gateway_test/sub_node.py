#!/usr/bin/env python3
"""SROS2 yetki matrisi testi -- executor_real enclave'inin GERCEK
/mission/command'i (StageCommand) dinleyen karsiligi."""
import sys
import time
import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import StageCommand

node_name = sys.argv[1] if len(sys.argv) > 1 else "test_sub"
alinan = 0


def cb(msg):
    global alinan
    alinan += 1
    print(f"[{node_name}] RECEIVED: command={msg.command} target_stage={msg.target_stage}", flush=True)


rclpy.init()
node = Node(node_name)
node.create_subscription(StageCommand, "mission/command", cb, 10)
t0 = time.time()
while time.time() - t0 < 6.0:
    rclpy.spin_once(node, timeout_sec=0.2)
print(f"[{node_name}] TOPLAM_ALINAN={alinan}", flush=True)
node.destroy_node()
rclpy.shutdown()
