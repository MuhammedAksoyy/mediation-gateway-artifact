#!/usr/bin/env python3
"""SROS2 yetki matrisi testi -- GERCEK /mission/command topic'i ve GERCEK
StageCommand mesaj tipiyle. gateway_real enclave'i buna yazabilmeli;
rogue_real enclave'i (gecerli kimlik ama izin yok) REDDEDILMELI."""
import sys
import time
import rclpy
from rclpy.node import Node
from karamuhafiz_msgs.msg import StageCommand

node_name = sys.argv[1] if len(sys.argv) > 1 else "test_pub"

rclpy.init()
node = Node(node_name)
pub = node.create_publisher(StageCommand, "mission/command", 10)
time.sleep(1.5)
m = StageCommand()
m.command = StageCommand.CMD_START
m.target_stage = 8  # tehlikeli hedef -- gateway'i BYPASS edip dogrudan yazma girisimi
for _ in range(5):
    pub.publish(m)
    print(f"[{node_name}] published: CMD_START target_stage=8 (BYPASS DENEMESI)", flush=True)
    time.sleep(0.5)
node.destroy_node()
rclpy.shutdown()
