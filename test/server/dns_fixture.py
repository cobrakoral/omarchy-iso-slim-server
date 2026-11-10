"""Local DNS fixture for the disposable networking acceptance matrix.

Serve synthetic A and AAAA records over host-loopback IPv4/IPv6. No public
resolver or Internet access is needed for mandatory network qualification.
"""

from contextlib import contextmanager
import socket
import struct
import threading

QNAME = b"\x03dns\x07omarchy\x04test\x00"
ANSWERS = {
    1: socket.inet_pton(socket.AF_INET, "192.0.2.53"),
    28: socket.inet_pton(socket.AF_INET6, "2001:db8::53"),
}


def answer(query):
    if len(query) < 12 or query[2] & 128 or query[4:6] != b"\x00\x01":
        return None
    end = 12 + len(QNAME)
    if len(query) < end + 4:
        return None
    if query[12:end].lower() != QNAME or query[end + 2:end + 4] != b"\x00\x01":
        return None
    record_type = int.from_bytes(query[end:end + 2])
    address = ANSWERS.get(record_type)
    if address is None:
        return None
    # Echo the question and return one synthetic address record. Keep the
    # reply compatible with systemd-resolved's normal recursive DNS requests.
    flags = 0x8480 | (int.from_bytes(query[2:4]) & 0x0100)
    header = struct.pack("!HHHHHH", int.from_bytes(query[:2]), flags, 1, 1, 0, 0)
    question = query[12:end + 4]
    record = b"\xc0\x0c" + struct.pack("!HHIH", record_type, 1, 60, len(address)) + address
    return header + question + record


@contextmanager
def dns_peer(port):
    endpoints = []
    events = []
    done = threading.Event()
    threads = []
    try:
        for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
            endpoint = socket.socket(family, socket.SOCK_DGRAM)
            endpoints.append(endpoint)
            if family == socket.AF_INET6:
                endpoint.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            endpoint.bind((host, port))
            endpoint.settimeout(0.5)

        def serve(endpoint):
            while not done.is_set():
                try:
                    query, client = endpoint.recvfrom(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                response = answer(query)
                if response is None:
                    continue
                endpoint.sendto(response, client)
                record_type = int.from_bytes(query[12 + len(QNAME):14 + len(QNAME)])
                events.append({
                    "query": "dns.omarchy.test",
                    "type": "A" if record_type == 1 else "AAAA",
                    "guest_transport": "IPv4 UDP" if endpoint.family == socket.AF_INET else "IPv6 UDP",
                    "answer_count": 1,
                })

        for endpoint in endpoints:
            thread = threading.Thread(target=serve, args=(endpoint,), daemon=True)
            thread.start()
            threads.append(thread)
        yield events
    finally:
        done.set()
        for thread in threads:
            thread.join(timeout=5)
        for endpoint in endpoints:
            endpoint.close()
