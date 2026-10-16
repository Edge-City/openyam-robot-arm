"""Open the OpenYAM CAN bus on macOS (gs_usb over libusb) or Linux (SocketCAN).

can_motor_control ships GsUsbBus only on macOS and SocketCanBus only on Linux.
On Linux the kernel gs_usb driver owns the adapter as can0; bring it up first:

    sudo ip link set can0 type can bitrate 1000000 && sudo ip link set can0 up

`port` is the adapter's USB serial on macOS, or the interface name on Linux
(default can0).
"""

from __future__ import annotations

import sys
from pathlib import Path

import can_motor_control as cmc


def open_bus(port: str | None = None):
    if sys.platform == "linux":
        iface = port or "can0"
        state = Path(f"/sys/class/net/{iface}/operstate")
        if not state.exists():
            sys.exit(f"No CAN interface {iface}. Is the adapter plugged in? (ip -br link)")
        if state.read_text().strip() == "down":
            sys.exit(f"{iface} is down. Run: sudo ip link set {iface} type can bitrate 1000000 "
                     f"&& sudo ip link set {iface} up")
        return cmc.SocketCanBus(iface)
    return cmc.GsUsbBus(vendor_id=0x1D50, product_id=0x606F, serial_number=port,
                        index=None if port else 0)


def bus_stats(bus, port: str | None = None) -> str:
    if sys.platform == "linux":
        s = Path(f"/sys/class/net/{port or 'can0'}/statistics")
        get = lambda n: int((s / n).read_text())
        return (f"CAN frames sent={get('tx_packets')} received={get('rx_packets')} "
                f"dropped={get('rx_dropped')} errors={get('rx_errors') + get('tx_errors')}")
    return f"CAN frames sent={bus.tx_completed} received={bus.rx_received} dropped={bus.rx_dropped}"


def rx_count(bus, port: str | None = None) -> int:
    if sys.platform == "linux":
        return int(Path(f"/sys/class/net/{port or 'can0'}/statistics/rx_packets").read_text())
    return bus.rx_received
