"""Explicit user-mode NICs for opt-in networking acceptance; no host bridge."""

from dataclasses import dataclass
import ipaddress
import re


@dataclass(frozen=True)
class UserNetwork:
    model: str = "virtio-net-pci"
    mac: str = "52:54:00:12:34:56"
    subnet: str = "10.0.2.0/24"
    prefix: str = "fec0::/64"
    restricted: bool = True
    ipv4: bool = True

    def arguments(self, index, port=None):
        if self.model not in ("virtio-net-pci", "e1000", "rtl8139"):
            raise ValueError("Unsupported test NIC model")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", self.mac):
            raise ValueError("Invalid NIC MAC address")
        net = ipaddress.IPv4Network(self.subnet)
        prefix = ipaddress.IPv6Network(self.prefix)
        if net.prefixlen != 24 or prefix.prefixlen != 64:
            raise ValueError("Acceptance networks require /24 and /64 prefixes")
        if port is not None and (not self.ipv4 or not 1024 <= port <= 65535):
            raise ValueError("Management requires IPv4 and an unprivileged SSH port")
        ident = f"net{index}"
        backend = (
            f"user,id={ident},restrict={'on' if self.restricted else 'off'},"
            f"ipv4={'on' if self.ipv4 else 'off'},ipv6=on,ipv6-net={prefix}"
        )
        if self.ipv4:
            backend += f",net={net}"
        if port is not None:
            backend += f",hostfwd=tcp:127.0.0.1:{port}-:22"
        return [
            "-netdev",
            backend,
            "-device",
            f"{self.model},netdev={ident},id=nic{index},mac={self.mac}",
        ]


@dataclass(frozen=True)
class DHCPNetwork:
    port: int

    def arguments(self, index, port=None):
        if port is not None or not 1024 <= self.port <= 65535:
            raise ValueError("DHCP peer requires a loopback port and no SSH forward")
        return [
            "-netdev",
            f"socket,id=net{index},connect=127.0.0.1:{self.port}",
            "-device",
            f"virtio-net-pci,netdev=net{index},id=nic{index},mac=52:54:00:12:34:58",
        ]
