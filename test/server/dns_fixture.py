"""Loopback-only IPv6 DNS endpoint forwarding to the host's IPv4 resolver.

The Omarchy VM has no upstream IPv6. This tests the guest's IPv6-only DNS
transport with real public AAAA answers, without claiming public IPv6 routing.
"""

from contextlib import contextmanager
import ipaddress
from pathlib import Path
import socket
import threading


@contextmanager
def dns_peer(port):
    nameservers = []
    for line in Path("/etc/resolv.conf").read_text().splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[0] == "nameserver":
            address = ipaddress.ip_address(fields[1])
            if address.version == 4:
                nameservers.append(str(address))
    if not nameservers:
        raise RuntimeError("IPv6 DNS fixture requires a working host IPv4 resolver")
    endpoint = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
    endpoint.bind(("::1", port))
    endpoint.settimeout(0.5)
    done = threading.Event()
    events = []

    def serve():
        while not done.is_set():
            try:
                query, client = endpoint.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(query) < 12 or query[2] & 128 or query[4:6] != b"\0\1":
                continue
            labels, index = [], 12
            while index < len(query) and 0 < query[index] < 64:
                length = query[index]
                labels.append(query[index + 1 : index + 1 + length])
                index += length + 1
            name = b".".join(labels).lower()
            if (
                name != b"one.one.one.one"
                or query[index : index + 5] != b"\0\0\x1c\0\1"
            ):
                continue
            for resolver in nameservers:
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                        upstream.settimeout(3)
                        upstream.connect((resolver, 53))
                        upstream.send(query)
                        reply = upstream.recv(4096)
                    if len(reply) < 12 or reply[:2] != query[:2]:
                        continue
                    endpoint.sendto(reply, client)
                    events.append(
                        {
                            "query": "one.one.one.one",
                            "type": "AAAA",
                            "guest_transport": "IPv6 UDP",
                            "upstream_transport": "IPv4 UDP",
                            "upstream_resolver": resolver,
                            "answer_count": int.from_bytes(reply[6:8]),
                        }
                    )
                    break
                except OSError:
                    continue

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield events
    finally:
        done.set()
        worker.join(timeout=5)
        endpoint.close()
