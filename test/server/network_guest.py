#!/usr/bin/env python3
"""Guest-side assertions for networking.py, run only on disposable overlays."""

import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

MANAGEMENT_MAC = "52:54:00:12:34:56"
DATA_MAC = "52:54:00:12:34:57"
RESULTS = Path("/var/log/server-network-acceptance")


def command(*args):
    return subprocess.check_output(
        args, text=True, stderr=subprocess.STDOUT, timeout=60
    ).strip()


def eventually(check, seconds=60):
    end = time.monotonic() + seconds
    last = None
    while time.monotonic() < end:
        try:
            result = check()
            if result:
                return result
        except (
            AssertionError,
            OSError,
            KeyError,
            subprocess.CalledProcessError,
        ) as error:
            last = error
        time.sleep(1)
    raise AssertionError(f"Network assertion timed out: {last}")


def interfaces():
    return json.loads(command("networkctl", "--json=short", "status"))["Interfaces"]


def interface(mac):
    expected = bytes.fromhex(mac.replace(":", ""))
    return next(
        i for i in interfaces() if bytes(i.get("HardwareAddress", [])) == expected
    )


def route4():
    return json.loads(command("ip", "-j", "-4", "route", "get", "1.1.1.1"))[0]["dev"]


def http(address, port):
    # Avoid host/user proxy environment; the endpoint belongs to this test only.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    host = f"[{address}]" if ":" in address else address
    with opener.open(f"http://{host}:{port}/network-proof", timeout=10) as reply:
        body = json.load(reply)
    assert body == {"proof": "omarchy-disposable-network"}, body
    return body


def dns(expected):
    command("resolvectl", "flush-caches")
    text = command("resolvectl", "query", "--protocol=dns", "--type=A", "github.com")
    assert f"link: {expected}" in text, text
    return text


def write(name, content):
    path = Path("/etc/systemd/network") / name
    path.write_text(content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reload():
    command("networkctl", "reload")
    command(
        "networkctl",
        "reconfigure",
        interface(MANAGEMENT_MAC)["Name"],
        interface(DATA_MAC)["Name"],
    )


def record(phase, body):
    RESULTS.mkdir(mode=0o700, exist_ok=True)
    result = {"phase": phase, "passed": True, **body}
    (RESULTS / (phase + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


def baseline(args):
    def ready():
        links = [interface(mac) for mac in (MANAGEMENT_MAC, DATA_MAC)]
        return (
            links
            if all("Lease" in link.get("DHCPv4Client", {}) for link in links)
            else None
        )

    links = eventually(ready)
    assert all(i["NetworkFile"].endswith("20-omarchy-server.network") for i in links)
    config = command("sshd", "-T").lower()
    assert "passwordauthentication no" in config
    assert "kbdinteractiveauthentication no" in config
    assert "permitrootlogin no" in config
    listeners = command("ss", "-H", "-lntup")
    # Record the complete IPv4/IPv6 exposure, including stock resolved multicast.
    assert "0.0.0.0:22" in listeners and "[::]:22" in listeners, listeners
    record(
        "baseline",
        {
            "links": links,
            "listeners": listeners,
            "services": command(
                "systemctl", "is-active", "sshd", "systemd-networkd", "systemd-resolved"
            ),
        },
    )


def configure(args):
    write(
        "10-network-acceptance-management.network",
        f"""[Match]
MACAddress={MANAGEMENT_MAC}
[Network]
DHCP=ipv4
IPv6AcceptRA=no
LinkLocalAddressing=ipv6
DNSDefaultRoute=yes
[DHCPv4]
RouteMetric=2000
UseDNS=yes
""",
    )
    write(
        "11-network-acceptance-data.network",
        f"""[Match]
MACAddress={DATA_MAC}
[Network]
DHCP=yes
IPv6AcceptRA=yes
DNSDefaultRoute=yes
Domains=~.
[DHCPv4]
RouteMetric=100
UseDNS=yes
[IPv6AcceptRA]
RouteMetric=100
""",
    )
    write(
        "12-network-acceptance-renewal.network",
        """[Match]
MACAddress=52:54:00:12:34:58
[Network]
DHCP=ipv4
IPv6AcceptRA=no
LinkLocalAddressing=no
DNSDefaultRoute=no
[DHCPv4]
RouteMetric=3000
UseDNS=no
""",
    )
    reload()
    command("networkctl", "reconfigure", interface("52:54:00:12:34:58")["Name"])
    preferred(args, "configured")


def preferred(args, phase="preferred"):
    data = interface(DATA_MAC)["Name"]
    eventually(lambda: route4() == data)
    text = eventually(lambda: dns(data))
    record(
        phase,
        {
            "preferred_interface": data,
            "dns": text,
            "http": http("10.0.3.2", args.port),
            "routes": command("ip", "-4", "route"),
            "resolved": command("resolvectl", "status"),
        },
    )


def renew(args):
    mac = "52:54:00:12:34:58"
    eventually(lambda: "Lease" in interface(mac).get("DHCPv4Client", {}))
    name = interface(mac)["Name"]
    before = interface(mac)["DHCPv4Client"]["Lease"]["LeaseTimestampUSec"]
    command("networkctl", "renew", name)

    def renewed():
        lease = interface(mac).get("DHCPv4Client", {}).get("Lease", {})
        return lease if lease.get("LeaseTimestampUSec", 0) > before else None

    after = eventually(renewed)
    assert after["Message"]["yiaddr"] == ipaddress.IPv4Address("10.0.4.15").packed.hex()
    record(
        "renew",
        {
            "lease_before_usec": before,
            "renewed_lease": after,
            "http": http("10.0.3.2", args.port),
            "dns": dns(interface(DATA_MAC)["Name"]),
        },
    )


def lost(args):
    management = interface(MANAGEMENT_MAC)["Name"]
    eventually(lambda: interface(DATA_MAC)["CarrierState"] == "no-carrier")
    eventually(lambda: route4() == management)
    record(
        "lost",
        {
            "fallback_interface": management,
            "dns": eventually(lambda: dns(management)),
            "http": http("10.0.2.2", args.port),
            "routes": command("ip", "-4", "route"),
        },
    )


def recovered(args):
    preferred(args, "recovered")
    renew(args)


def external(args):
    data = interface(DATA_MAC)["Name"]
    text = dns(data)
    url = "https://raw.githubusercontent.com/cobrakoral/omarchy-iso-slim-server/server-packages/stable/x86_64/"
    dest = Path("/run/network-channel")
    dest.mkdir(exist_ok=True)
    for name in (
        "repository.json",
        "repository.json.sig",
        "omarchy-server.db",
        "omarchy-server.db.sig",
    ):
        command(
            "curl",
            "--noproxy",
            "*",
            "--ipv4",
            "--fail",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "10",
            "--max-time",
            "45",
            "--output",
            str(dest / name),
            url + name,
        )
    # Installed trust only: no import, lsign or trust-policy weakening.
    manifest_sig = command(
        "pacman-key",
        "--verify",
        str(dest / "repository.json.sig"),
        str(dest / "repository.json"),
    )
    database_sig = command(
        "pacman-key",
        "--verify",
        str(dest / "omarchy-server.db.sig"),
        str(dest / "omarchy-server.db"),
    )
    provenance = json.loads((dest / "repository.json").read_text())
    fingerprint = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"
    assert provenance["maintainer_fingerprint"] == fingerprint
    for signature, signed in (
        ("repository.json.sig", "repository.json"),
        ("omarchy-server.db.sig", "omarchy-server.db"),
    ):
        status = command(
            "gpg",
            "--homedir",
            "/etc/pacman.d/gnupg",
            "--batch",
            "--status-fd=1",
            "--verify",
            str(dest / signature),
            str(dest / signed),
        )
        assert any(
            line.startswith("[GNUPG:] VALIDSIG ") and line.split()[-1] == fingerprint
            for line in status.splitlines()
        ), status
    for name in ("omarchy-server.db", "omarchy-server.db.sig"):
        assert (
            hashlib.sha256((dest / name).read_bytes()).hexdigest()
            == provenance["files"][name]
        )
    record(
        "external",
        {
            "dns": text,
            "url": url,
            "manifest_sha256": hashlib.sha256(
                (dest / "repository.json").read_bytes()
            ).hexdigest(),
            "manifest_signature": manifest_sig,
            "database_signature": database_sig,
            "manifest_source": provenance["source_revision"],
        },
    )


def static(args):
    # This is the actual installer generator from the source tree under test.
    import config

    content = config.network_config(
        dict(
            network="static",
            mac=DATA_MAC,
            address="10.0.3.40/24",
            gateway="10.0.3.2",
            dns="10.0.3.3",
        )
    )
    digest = write("11-network-acceptance-data.network", content)
    reload()
    eventually(
        lambda: any(
            a.get("AddressString") == "10.0.3.40"
            for a in interface(DATA_MAC).get("Addresses", [])
        )
    )
    # Operator DNS preference is independent of the generated single-link profile.
    command("resolvectl", "domain", interface(DATA_MAC)["Name"], "~.")
    record(
        "static",
        {
            "configuration_sha256": digest,
            "configuration": content,
            "http": http("10.0.3.2", args.port),
            "routes": command("ip", "-4", "route"),
        },
    )


def static_check(args):
    import config

    expected = config.network_config(
        dict(
            network="static",
            mac=DATA_MAC,
            address="10.0.3.40/24",
            gateway="10.0.3.2",
            dns="10.0.3.3",
        )
    )
    assert (
        Path("/etc/systemd/network/11-network-acceptance-data.network").read_text()
        == expected
    )

    def ready():
        link = interface(DATA_MAC)
        assert any(
            a.get("AddressString") == "10.0.3.40" and a.get("ConfigSource") == "static"
            for a in link.get("Addresses", [])
        )
        assert not link.get("DHCPv4Client", {}).get("Lease")
        return link

    link = eventually(ready)
    assert route4() == link["Name"]
    record(
        "static-check",
        {
            "link": link,
            "http": http("10.0.3.2", args.port),
            "dns_servers": command("resolvectl", "dns", link["Name"]),
            "ssh": command("sshd", "-T"),
        },
    )


def ipv6_configure(args):
    content = f"""[Match]
MACAddress={DATA_MAC}
[Network]
DHCP=no
IPv6AcceptRA=no
Address=fd00:3::15/64
Gateway=fd00:3::2
DNS=[fd00:3::2]:{args.port}
Domains=~.
DNSDefaultRoute=yes
"""
    record(
        "ipv6-configure",
        {
            "configuration_sha256": write(
                "11-network-acceptance-data.network", content
            ),
            "configuration": content,
        },
    )


def ipv6_only(args):
    # Called by a detached guest unit while the host drops the management cable.
    # No SSH/reverse tunnel is used during these assertions.
    try:
        management = interface(MANAGEMENT_MAC)["Name"]
        eventually(lambda: interface(MANAGEMENT_MAC)["CarrierState"] == "no-carrier")
        eventually(
            lambda: interface("52:54:00:12:34:58")["CarrierState"] == "no-carrier"
        )

        def no_ipv4():
            links = json.loads(command("ip", "-j", "-4", "address"))
            return (
                links
                if all(not l.get("addr_info") for l in links if l["ifname"] != "lo")
                else None
            )

        v4 = eventually(no_ipv4)
        data = interface(DATA_MAC)["Name"]
        prior = json.loads((RESULTS / "ipv6-configure.json").read_text())
        digest = hashlib.sha256(
            Path("/etc/systemd/network/11-network-acceptance-data.network").read_bytes()
        ).hexdigest()
        assert digest == prior["configuration_sha256"]
        routes = json.loads(command("ip", "-j", "-6", "route", "show", "default"))
        assert any(r["dev"] == data for r in routes), routes
        command("resolvectl", "flush-caches")
        text = command(
            "resolvectl", "query", "--protocol=dns", "--type=AAAA", "one.one.one.one"
        )
        assert f"link: {data}" in text, text
        with socket.create_connection(("fd00:3::15", 22), timeout=5) as connection:
            banner = connection.recv(100).decode().strip()
        assert banner.startswith("SSH-2.0-"), banner
        record(
            "ipv6-only",
            {
                "configuration_sha256": digest,
                "management_down": management,
                "ipv4": v4,
                "ipv6_routes": routes,
                "http_over_ipv6": http("fd00:3::2", args.port),
                "dns_aaaa_over_ipv6_proxy": text,
                "ssh_ipv6_banner": banner,
                "external_ipv6_internet": "not validated: host has no upstream IPv6 route",
            },
        )
        marker = "IPV6_ONLY_ACCEPTANCE_PASS"
    except BaseException as error:
        marker = "IPV6_ONLY_ACCEPTANCE_FAIL " + repr(error)
        raise
    finally:
        with open("/dev/ttyS0", "w") as serial:
            serial.write("\n" + marker + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "baseline",
            "configure",
            "preferred",
            "renew",
            "lost",
            "recovered",
            "external",
            "static",
            "static-check",
            "ipv6-configure",
            "ipv6-only",
        ),
    )
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if command("cat", "/etc/omarchy-edition") != "server":
        raise ValueError("Guest networking acceptance requires a server edition")
    if socket.gethostname() != "slim-test":
        raise ValueError("Only the disposable acceptance hostname may be changed")
    globals()[args.phase.replace("-", "_")](args)


if __name__ == "__main__":
    main()
