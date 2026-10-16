#!/usr/bin/env bash
# One-time root setup for driving the OpenYAM from Ubuntu 24.04.
#   sudo bash experiments/2026-10-16-linux-real-arm/linux-sudo-setup.sh
# Safe to re-run. Does not touch the motors.
set -euxo pipefail

# 1. System packages the DimOS installer wants (only these were missing on the lab laptop).
DEBIAN_FRONTEND=noninteractive apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y portaudio19-dev git-lfs libturbojpeg pre-commit ffmpeg v4l-utils can-utils

# 2. Let plugdev users open the CANable over libusb (can_motor_control.GsUsbBus).
#    Without this the device node is root:root 0664 and libusb cannot claim it.
cat > /etc/udev/rules.d/99-openyam-canable.rules <<'RULE'
# MKS CANable V2.0 / candleLight gs_usb adapter for the OpenYAM arm
SUBSYSTEM=="usb", ATTR{idVendor}=="1d50", ATTR{idProduct}=="606f", MODE="0660", GROUP="plugdev"
RULE
udevadm control --reload-rules
udevadm trigger --attr-match=idVendor=1d50

# 3. DimOS LCM transport buffers (the installer's sysctl step).
printf "# DimOS LCM transport buffers\nnet.core.rmem_max=67108864\nnet.core.rmem_default=67108864\n" > /etc/sysctl.d/99-dimos.conf
sysctl -p /etc/sysctl.d/99-dimos.conf

ls -l /dev/bus/usb/*/* | grep -i plugdev || true
echo "Done. Replug the CAN adapter if its node above is not group plugdev."
