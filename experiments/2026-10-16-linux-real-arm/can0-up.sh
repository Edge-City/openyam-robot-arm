#!/usr/bin/env bash
# Bring the CANable's SocketCAN interface up at 1 Mbit/s (classical CAN), and
# install a udev rule so it comes up by itself every time the adapter is plugged in.
#   sudo bash experiments/2026-10-16-linux-real-arm/can0-up.sh
# Does not send anything to the motors.
set -euxo pipefail
cat > /etc/udev/rules.d/80-openyam-can-up.rules <<'RULE'
# OpenYAM: bring gs_usb CAN interfaces up at 1 Mbit/s with a deeper TX queue
ACTION=="add", SUBSYSTEM=="net", KERNEL=="can*", DRIVERS=="gs_usb", RUN+="/usr/sbin/ip link set %k type can bitrate 1000000 restart-ms 100", RUN+="/usr/sbin/ip link set %k txqueuelen 1000", RUN+="/usr/sbin/ip link set %k up"
RULE
udevadm control --reload-rules
ip link set can0 down || true
ip link set can0 type can bitrate 1000000 restart-ms 100
ip link set can0 txqueuelen 1000
ip link set can0 up
ip -details -statistics link show can0
