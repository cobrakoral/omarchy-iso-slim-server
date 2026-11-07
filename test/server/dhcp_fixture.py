"""Small DHCP/ARP peer on QEMU's framed Ethernet socket, never a host LAN.

Only the disposable client's fixed MAC/address is served. OFFER/ACK and actual
unicast renewal requests are retained so a renewal cannot pass on exit status.
"""

from contextlib import contextmanager
import ipaddress
import socket
import struct
import threading
import time

SERVER_IP = ipaddress.IPv4Address("10.0.4.2").packed
CLIENT_IP = ipaddress.IPv4Address("10.0.4.15").packed
SERVER_MAC = bytes.fromhex("525400123459")
CLIENT_MAC = bytes.fromhex("525400123458")
COOKIE = b"\x63\x82\x53\x63"


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!" + "H" * (len(data) // 2), data))
    while total >> 16:
        total = (total & 65535) + (total >> 16)
    return (~total) & 65535


def response(packet, events):
    if len(packet) < 42 or packet[6:12] != CLIENT_MAC:
        return None
    kind = packet[12:14]
    if kind == b"\x08\x06":
        arp = packet[14:42]
        if arp[:8] != b"\0\1\x08\0\x06\x04\0\1" or arp[24:28] != SERVER_IP:
            return None
        return (
            CLIENT_MAC
            + SERVER_MAC
            + kind
            + b"\0\1\x08\0\x06\x04\0\2"
            + SERVER_MAC
            + SERVER_IP
            + CLIENT_MAC
            + arp[14:18]
        )
    if kind != b"\x08\x00" or packet[23] != 17:
        return None
    ihl = (packet[14] & 15) * 4
    udp = packet[14 + ihl :]
    if len(udp) < 8 + 240 or udp[:4] != struct.pack("!HH", 68, 67):
        return None
    request = udp[8:]
    if (
        request[:3] != b"\1\1\6"
        or request[28:34] != CLIENT_MAC
        or request[236:240] != COOKIE
    ):
        return None
    options = {}
    index = 240
    while index < len(request) and request[index] != 255:
        tag = request[index]
        index += 1
        if tag == 0:
            continue
        if index >= len(request):
            return None
        length = request[index]
        index += 1
        options[tag] = request[index : index + length]
        index += length
    message = options.get(53, b"")[0:1]
    if message not in (b"\1", b"\3"):
        return None
    if (
        options.get(54, SERVER_IP) != SERVER_IP
        or options.get(50, CLIENT_IP) != CLIENT_IP
    ):
        return None
    ciaddr = request[12:16]
    events.append(
        {
            "message": "DISCOVER" if message == b"\1" else "REQUEST",
            "ciaddr": str(ipaddress.IPv4Address(ciaddr)),
            "xid": request[4:8].hex(),
            "monotonic_ns": time.monotonic_ns(),
        }
    )
    reply = bytearray(240)
    reply[:4] = b"\2\1\6\0"
    reply[4:8] = request[4:8]
    reply[10:16] = request[10:16]
    reply[16:20] = CLIENT_IP
    reply[20:24] = SERVER_IP
    reply[28:44] = request[28:44]
    reply[236:240] = COOKIE
    reply += b"\x35\1" + (b"\2" if message == b"\1" else b"\5")
    reply += b"\x36\4" + SERVER_IP + b"\x01\4\xff\xff\xff\0"
    for tag, seconds in ((51, 120), (58, 60), (59, 105)):
        reply += bytes((tag, 4)) + struct.pack("!I", seconds)
    reply += b"\xff"
    datagram = struct.pack("!HHHH", 67, 68, 8 + len(reply), 0) + reply
    destination = ciaddr if ciaddr != bytes(4) else b"\xff" * 4
    header = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        20 + len(datagram),
        0,
        0,
        64,
        17,
        0,
        SERVER_IP,
        destination,
    )
    header = header[:10] + struct.pack("!H", checksum(header)) + header[12:]
    return CLIENT_MAC + SERVER_MAC + kind + header + datagram


class Peer:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.5)
        self.port = self.listener.getsockname()[1]
        self.events = []
        self.errors = []
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)

    def read(self, sock, length):
        data = b""
        while len(data) < length and not self.done.is_set():
            try:
                block = sock.recv(length - len(data))
            except socket.timeout:
                continue
            if not block:
                return None
            data += block
        return data if len(data) == length else None

    def serve(self):
        while not self.done.is_set():
            try:
                connection, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(0.5)
                try:
                    while not self.done.is_set():
                        prefix = self.read(connection, 4)
                        if prefix is None:
                            break
                        length = struct.unpack("!I", prefix)[0]
                        if not 14 <= length <= 65536:
                            raise ValueError("Invalid framed Ethernet length")
                        frame = self.read(connection, length)
                        if frame is None:
                            break
                        reply = response(frame, self.events)
                        if reply:
                            connection.sendall(struct.pack("!I", len(reply)) + reply)
                except OSError:
                    pass  # A stopped guest closes its framed stream.
                except Exception as error:
                    self.errors.append(repr(error))


@contextmanager
def dhcp_peer():
    peer = Peer()
    peer.thread.start()
    try:
        yield peer
        assert not peer.errors, peer.errors
    finally:
        peer.done.set()
        peer.listener.close()
        peer.thread.join(timeout=3)
