#!/usr/bin/env python3
"""Opt-in live-network acceptance of an existing, stopped server ISO installation.

Creates COW disks, uses only QEMU user networking and host-loopback fixtures.
Unlike the offline suites, this explicitly permits guest egress for real DNS/TLS.
"""

import argparse
from contextlib import contextmanager
import hashlib
import http.server
import json
from pathlib import Path
import shutil
import socket
import threading
import time
import subprocess

from network_devices import UserNetwork, DHCPNetwork
from dhcp_fixture import dhcp_peer
from dns_fixture import dns_peer
from qemu import ROOT, VM, qmp, run
from repository import health

MODELS = {"virtio": "virtio-net-pci", "e1000": "e1000", "rtl8139": "rtl8139"}
SOURCES = (
    "test/server/networking.py",
    "test/server/network_guest.py",
    "test/server/network_devices.py",
    "test/server/dhcp_fixture.py",
    "test/server/dns_fixture.py",
    "test/server/qemu.py",
    "builder/server/config.py",
)


class ProofHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/network-proof":
            self.send_error(404)
            return
        body = b'{"proof":"omarchy-disposable-network"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class IPv6Server(http.server.ThreadingHTTPServer):
    address_family = socket.AF_INET6


@contextmanager
def fixtures():
    ipv4 = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ProofHandler)
    try:
        ipv6 = IPv6Server(("::1", ipv4.server_port), ProofHandler)
    except BaseException:
        ipv4.server_close()
        raise
    servers = (ipv4, ipv6)
    threads = []
    try:
        for server in servers:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            threads.append(thread)
        yield ipv4.server_port
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def reserve():
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host below 8 GiB free; stop without deleting evidence")


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("--model", choices=("all", *MODELS), default="all")
    parser.add_argument("--port", type=int, default=2442)
    parser.add_argument(
        "--external",
        action="store_true",
        help="Also fetch and verify the real HTTPS channel from the guest",
    )
    args = parser.parse_args()
    base = args.base.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use an accepted, stopped disposable server ISO installation")
    baseline = json.loads((base / "acceptance-pass.json").read_text())
    if baseline["case"] != "base":
        raise ValueError(
            "Networking matrix currently requires the default UEFI baseline"
        )
    disk_source = base / "system.qcow2"
    original_hash = sha(disk_source)
    source_hashes = {name: sha(ROOT / name) for name in SOURCES}
    models = MODELS if args.model == "all" else (args.model,)
    for model in models:
        reserve()
        directory = (
            ROOT
            / "test-runs/server"
            / (time.strftime("%Y%m%d-%H%M%S") + "-network-" + model)
        )
        directory.mkdir(mode=0o700)
        shutil.copy2(base / "key", directory / "key")
        disk = directory / "system.qcow2"
        run(
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(disk_source),
            str(disk),
        )
        media = ROOT / ".server-work/round4" / (directory.name + "-media")
        media.mkdir(parents=True, mode=0o700)
        shutil.copy2(ROOT / "test/server/network_guest.py", media)
        shutil.copy2(ROOT / "builder/server/config.py", media / "config.py")
        results = {}
        vm = None
        with fixtures() as port, dhcp_peer() as peer, dns_peer(port) as dns_events:

            def boot(ipv4=True):
                reserve()
                networks = [
                    UserNetwork(restricted=False),
                    UserNetwork(
                        model=MODELS[model],
                        mac="52:54:00:12:34:57",
                        subnet="10.0.3.0/24",
                        prefix="fd00:3::/64",
                        restricted=False,
                        ipv4=ipv4,
                    ),
                    DHCPNetwork(peer.port),
                ]
                guest = VM(
                    directory, disk, args.port, [], share=media, networks=networks
                )
                try:
                    guest.wait_ssh()
                    guest.sudo(
                        "mkdir -p /run/network-acceptance; mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/network-acceptance"
                    )
                except BaseException:
                    guest.stop()
                    raise
                return guest

            def phase(name):
                print("NETWORK_PHASE", model, name, flush=True)
                text = vm.sudo(
                    f"python3 /run/network-acceptance/network_guest.py {name} --port {port}"
                )
                (directory / (name + ".txt")).write_text(text)
                # A phase may emit multiple JSON records (recovery also renews).
                records = [
                    json.loads(line)
                    for line in text.splitlines()
                    if line.startswith("{")
                ]
                assert records and all(r["passed"] for r in records)
                for result in records:
                    results[result["phase"]] = result

            try:
                vm = boot()
                phase("baseline")
                data_link = next(
                    link
                    for link in results["baseline"]["links"]
                    if link.get("HardwareAddress") == [82, 84, 0, 18, 52, 87]
                )
                assert (
                    data_link["Driver"]
                    in {
                        "virtio": ("virtio_net",),
                        "e1000": ("e1000",),
                        "rtl8139": ("8139cp", "8139too"),
                    }[model]
                )
                denied = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "PubkeyAuthentication=no",
                        "-o",
                        "PreferredAuthentications=password,keyboard-interactive",
                        "-p",
                        str(args.port),
                        "admin@127.0.0.1",
                        "true",
                    ],
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
                assert (
                    denied.returncode == 255
                    and "Permission denied (publickey)" in denied.stderr
                ), denied.stderr
                results["password-rejected"] = {
                    "passed": True,
                    "negotiated_result": denied.stderr,
                }
                phase("configure")
                phase("renew")
                assert "return" in qmp(
                    vm.qmp, "set_link", {"name": "nic1", "up": False}
                )
                phase("lost")
                assert "return" in qmp(vm.qmp, "set_link", {"name": "nic1", "up": True})
                phase("recovered")
                if args.external and model == "virtio":
                    phase("external")
                vm.stop()
                vm = boot()
                phase("preferred")
                phase("static")
                vm.stop()
                vm = boot()
                phase("static-check")
                normal_health = health(vm)
                assert normal_health["packages"] == 176
                if model == "virtio":
                    phase("ipv6-configure")
                    vm.stop()
                    vm = boot(ipv4=False)
                    vm.sudo(
                        f"systemd-run --unit=server-ipv6-acceptance --collect /usr/bin/python3 /run/network-acceptance/network_guest.py ipv6-only --port {port}"
                    )
                    assert "return" in qmp(
                        vm.qmp, "set_link", {"name": "nic0", "up": False}
                    )
                    assert "return" in qmp(
                        vm.qmp, "set_link", {"name": "nic2", "up": False}
                    )
                    try:
                        index = vm.child.expect(
                            ["IPV6_ONLY_ACCEPTANCE_PASS", "IPV6_ONLY_ACCEPTANCE_FAIL"],
                            timeout=100,
                        )
                        assert (
                            index == 0
                        ), "IPv6-only phase failed; see guest journal and serial evidence"
                    finally:
                        qmp(vm.qmp, "set_link", {"name": "nic0", "up": True})
                        qmp(vm.qmp, "set_link", {"name": "nic2", "up": True})
                    vm.wait_ssh()
                    result = json.loads(
                        vm.sudo("cat /var/log/server-network-acceptance/ipv6-only.json")
                    )
                    assert result["passed"]
                    results["ipv6-only"] = result
                    assert dns_events and any(
                        event["answer_count"] > 0 for event in dns_events
                    )
                    results["ipv6-health"] = health(vm)
                assert {
                    name: sha(ROOT / name) for name in SOURCES
                } == source_hashes, "Test source changed during the run"
                assert sha(disk_source) == original_hash, "Backing installation changed"
                assert any(
                    event["message"] == "REQUEST" and event["ciaddr"] == "10.0.4.15"
                    for event in peer.events
                ), "No unicast DHCP renewal observed"
                record = {
                    "passed": True,
                    "model": model,
                    "base": str(base),
                    "base_disk_sha256": original_hash,
                    "iso": baseline["iso"],
                    "iso_sha256": sha(Path(baseline["iso"])),
                    "source_files": source_hashes,
                    "source_revision": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                    ).strip(),
                    "health": normal_health,
                    "phases": results,
                    "dhcp_peer_events": peer.events,
                    "ipv6_dns_peer_events": dns_events,
                    "limitations": [
                        "QEMU NIC models, not physical hardware",
                        "No upstream IPv6 route on host; public IPv6 Internet and Secure Boot unverified",
                    ],
                    "completed_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                (directory / "network-pass.json").write_text(
                    json.dumps(record, indent=2) + "\n"
                )
                print("NETWORK_CASE_PASS", model, directory, flush=True)
            finally:
                if vm is not None:
                    vm.stop()


if __name__ == "__main__":
    main()
