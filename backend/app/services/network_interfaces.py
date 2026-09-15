"""Enumerates the machine ConfigCollector's backend runs on - not the
devices being managed - so the user can pick which of this host's own
network interfaces devices should use to reach the firmware-upgrade
transfer server (TFTP/FTP/SCP). This matters because a multi-homed host
(e.g. one NIC on an IT segment, another on OT) can be reachable from a
target device on only one of its addresses - there's no single "right"
answer to guess, so it's a per-job choice rather than a fixed setting.
"""

import socket
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class NetworkInterface:
    name: str
    address: str


def list_network_interfaces() -> list[NetworkInterface]:
    """Every non-loopback IPv4 address on this machine, one entry per
    (interface, address) pair - a NIC with multiple IPv4 addresses appears
    once per address. Loopback (127.0.0.0/8) is excluded since a device on
    the network could never actually reach it."""
    interfaces: list[NetworkInterface] = []
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            if addr.address.startswith("127."):
                continue
            interfaces.append(NetworkInterface(name=name, address=addr.address))
    return interfaces
