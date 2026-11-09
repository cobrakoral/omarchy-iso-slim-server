#!/usr/bin/env bash
# Disposable dual-stack smoke test. Never installs firewall rules on the host.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
policy="$repo/builder/server/security/nftables.conf"
if [[ ${1:-} != --isolated ]]; then
  command -v sudo >/dev/null
  exec sudo -n unshare --mount --net -- bash "$0" --isolated
fi
[[ $(id -u) == 0 ]] || exit 2
mount --make-rprivate /
peer="omarchy-fw-peer-$$"
work=$(mktemp -d /tmp/omarchy-fw-namespace.XXXXXX)
cleanup() {
  local status=$?
  trap - EXIT
  if ((status != 0)) && [[ -f $work/listener.log ]]; then
    echo 'Isolated listener diagnostics:' >&2
    tail -20 "$work/listener.log" >&2
  fi
  if [[ -n ${listener:-} ]]; then
    kill "$listener" 2>/dev/null || true
    wait "$listener" 2>/dev/null || true
  fi
  ip netns del "$peer" 2>/dev/null || true
  rm -rf -- "$work"
  exit "$status"
}
trap cleanup EXIT
ip netns add "$peer"
ip link add vethserver type veth peer name vethclient
ip link set vethclient netns "$peer"
ip link set lo up
ip addr add 198.18.0.1/30 dev vethserver
ip -6 addr add fd42:9999::1/64 dev vethserver nodad
ip link set vethserver up
ip netns exec "$peer" ip link set lo up
ip netns exec "$peer" ip addr add 198.18.0.2/30 dev vethclient
ip netns exec "$peer" ip -6 addr add fd42:9999::2/64 dev vethclient nodad
ip netns exec "$peer" ip link set vethclient up

cat > "$work/listener.py" <<'PY'
import select
import socket

listeners = []
for family, address in ((socket.AF_INET, '198.18.0.1'),
                        (socket.AF_INET6, 'fd42:9999::1')):
    for port in (22, 8080):
        sock = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((address, port))
        sock.listen()
        listeners.append(sock)
# Fake DHCP servers in the peer namespace send packets from the standard
# server ports; these UDP sockets model DHCP clients on the protected host.
for family, address, port in ((socket.AF_INET, '198.18.0.1', 68),
                              (socket.AF_INET6, 'fd42:9999::1', 546)):
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    sock.bind((address, port))
    listeners.append(sock)
print('LISTEN_READY', flush=True)
while True:
    readable, _, _ = select.select(listeners, [], [], 1)
    for sock in readable:
        if sock.type == socket.SOCK_DGRAM:
            data, address = sock.recvfrom(128)
            sock.sendto(b'ack:' + data, address)
        else:
            conn, _ = sock.accept()
            conn.close()
PY
python3 -u "$work/listener.py" > "$work/listener.log" 2>&1 &
listener=$!
for _ in {1..30}; do
  grep -qx LISTEN_READY "$work/listener.log" 2>/dev/null && break
  sleep 0.1
done
grep -qx LISTEN_READY "$work/listener.log"

cat > "$work/client.py" <<'PY'
import socket
import sys

mode = sys.argv[1]
for family, address in ((socket.AF_INET, '198.18.0.1'),
                        (socket.AF_INET6, 'fd42:9999::1')):
    for port in (22, 8080):
        expected = mode == 'baseline' or port == 22
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.5)
            try:
                sock.connect((address, port))
            except socket.timeout:
                success = False
            except OSError as error:
                raise AssertionError(f'unexpected network failure {family} {port}: {error}') from error
            else:
                success = True
            assert success == expected, (mode, family, port, success, expected)
        print('TCP', mode, 'IPv6' if family == socket.AF_INET6 else 'IPv4',
              port, 'accepted' if success else 'blocked', flush=True)
# Simulate DHCP replies from the server ports for IPv4 and IPv6.
if mode == 'filtered':
    for family, source, destination, server_port, client_port in (
        (socket.AF_INET, '198.18.0.2', '198.18.0.1', 67, 68),
        (socket.AF_INET6, 'fd42:9999::2', 'fd42:9999::1', 547, 546),
    ):
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.settimeout(2)
            sock.bind((source, server_port))
            sock.sendto(b'dhcp-fixture', (destination, client_port))
            received, _ = sock.recvfrom(128)
            assert received == b'ack:dhcp-fixture', (family, received)
        print('DHCP', 'IPv6' if family == socket.AF_INET6 else 'IPv4',
              'allowed', flush=True)
PY
printf 'BASELINE WITHOUT RULES (both ports, both families)\n'
ip netns exec "$peer" python3 "$work/client.py" baseline
printf '\nLOAD FIREWALL ONLY IN DISPOSABLE NAMESPACE\n'
nft -f "$policy"
nft list table inet omarchy_server_filter >/dev/null
ip netns exec "$peer" ping -4 -n -c 1 -W 2 198.18.0.1 >/dev/null
ip netns exec "$peer" ping -6 -n -c 1 -W 2 fd42:9999::1 >/dev/null
printf 'ICMP_ECHO_IPV4_IPV6_ACCEPTED\n'
ip netns exec "$peer" python3 "$work/client.py" filtered
printf 'NFT_DUAL_STACK_NAMESPACE_PASS\n'
