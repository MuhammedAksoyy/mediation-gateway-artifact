#!/usr/bin/env bash
set +u
source /opt/ros/humble/setup.bash
source <WORKSPACE_ROOT>/install/setup.bash
DEMO=<WORKSPACE_ROOT>/sros2_gateway_test
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export ROS_DOMAIN_ID=99
export ROS_SECURITY_ENABLE=true
export ROS_SECURITY_STRATEGY=Enforce
export ROS_SECURITY_KEYSTORE="$DEMO/keystore"

echo "############################################"
echo "# TEST 1: gateway_real (yetkili) -> /mission/command yazabilmeli"
echo "############################################"
ROS_SECURITY_ENCLAVE_OVERRIDE=/executor_real python3 "$DEMO/sub_node.py" executor_sub_node > /tmp/sros2r_test1_sub.log 2>&1 &
SUBPID=$!
sleep 2
ROS_SECURITY_ENCLAVE_OVERRIDE=/gateway_real python3 "$DEMO/pub_node.py" gateway_pub_node > /tmp/sros2r_test1_pub.log 2>&1
wait $SUBPID
echo "--- pub ---"; cat /tmp/sros2r_test1_pub.log
echo "--- sub ---"; cat /tmp/sros2r_test1_sub.log

echo ""
echo "############################################"
echo "# TEST 2: rogue_real (gecerli kimlik, IZIN YOK) -> gateway'i BYPASS girisimi"
echo "############################################"
ROS_SECURITY_ENCLAVE_OVERRIDE=/executor_real python3 "$DEMO/sub_node.py" executor_sub_node > /tmp/sros2r_test2_sub.log 2>&1 &
SUBPID=$!
sleep 2
ROS_SECURITY_ENCLAVE_OVERRIDE=/rogue_real python3 "$DEMO/pub_node.py" rogue_pub_node > /tmp/sros2r_test2_pub.log 2>&1
wait $SUBPID
echo "--- pub ---"; cat /tmp/sros2r_test2_pub.log
echo "--- sub ---"; cat /tmp/sros2r_test2_sub.log

echo ""
echo "############################################"
echo "# TEST 3: kimliksiz saldirgan (SROS2 baglami yok) -> Enforce alanina karsi"
echo "############################################"
ROS_SECURITY_ENCLAVE_OVERRIDE=/executor_real python3 "$DEMO/sub_node.py" executor_sub_node > /tmp/sros2r_test3_sub.log 2>&1 &
SUBPID=$!
sleep 2
env -u ROS_SECURITY_ENABLE -u ROS_SECURITY_STRATEGY -u ROS_SECURITY_KEYSTORE -u ROS_SECURITY_ENCLAVE_OVERRIDE \
  RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DOMAIN_ID=99 \
  python3 "$DEMO/pub_node.py" plain_attacker > /tmp/sros2r_test3_pub.log 2>&1
wait $SUBPID
echo "--- pub ---"; cat /tmp/sros2r_test3_pub.log
echo "--- sub ---"; cat /tmp/sros2r_test3_sub.log

echo ""
echo "=== TAMAMLANDI ==="
