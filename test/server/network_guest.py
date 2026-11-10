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
    if not (body == {"proof": "omarchy-disposable-network"}):
        raise AssertionError(body)
    return body


def dns(expected):
    command("resolvectl", "flush-caches")
    text = command("resolvectl", "query", "--protocol=dns", "--type=A", "github.com")
    if not (f"link: {expected}" in text):
        raise AssertionError(text)
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
    for link in links:
        expected = f"10-{link['Name']}.network"
        if not link["NetworkFile"].endswith(expected):
            raise AssertionError(f"Expected {expected}, got {link['NetworkFile']}")
        content = Path(link["NetworkFile"]).read_text()
        for setting in ("DHCP=ipv4", "IPv6AcceptRA=yes", "UseHostname=no"):
            if setting not in content:
                raise AssertionError(f"Missing {setting} from {expected}")
    config = command("sshd", "-T").lower()
    if not ("passwordauthentication no" in config):
        raise AssertionError
    if not ("kbdinteractiveauthentication no" in config):
        raise AssertionError
    if not ("permitrootlogin no" in config):
        raise AssertionError
    listeners = command("ss", "-H", "-lntup")
    # Only SSH is deliberately exposed. Resolved must not bind 5353/5355.
    if not ("0.0.0.0:22" in listeners and "[::]:22" in listeners):
        raise AssertionError(listeners)
    if not (not any(f":{port} " in listeners for port in (5353, 5355))):
        raise AssertionError(listeners)
    if not ("Global: no" in command("resolvectl", "llmnr")):
        raise AssertionError
    if not ("Global: no" in command("resolvectl", "mdns")):
        raise AssertionError
    command("systemctl", "is-enabled", "nftables")
    rules = command("nft", "list", "table", "inet", "omarchy_server_filter")
    if not ("policy drop" in rules and "tcp dport 22 accept" in rules):
        raise AssertionError
    if not ("udp sport 67 udp dport 68 accept" in rules):
        raise AssertionError
    if not ("udp sport 547 udp dport 546 accept" in rules):
        raise AssertionError
    if not ("icmpv6 type" in rules):
        raise AssertionError
    record(
        "baseline",
        {
            "links": links,
            "dns_a": command("resolvectl", "query", "--protocol=dns", "--type=A", "github.com"),
            "dns_aaaa": command("resolvectl", "query", "--protocol=dns", "--type=AAAA", "one.one.one.one"),
            "listeners": listeners,
            "services": command(
                "systemctl", "is-active", "sshd", "systemd-networkd", "systemd-resolved"
            ),
        },
    )


def configure(args):
    # Override the installed 10-<interface> files on disposable guest disks.
    write(
        "09-network-acceptance-management.network",
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
UseHostname=no
""",
    )
    write(
        "09-network-acceptance-data.network",
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
UseHostname=no
[DHCPv6]
UseHostname=no
[IPv6AcceptRA]
RouteMetric=100
""",
    )
    write(
        "09-network-acceptance-renewal.network",
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
UseHostname=no
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
    if not (after["Message"]["yiaddr"] == ipaddress.IPv4Address("10.0.4.15").packed.hex()):
        raise AssertionError
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
    if not (provenance["maintainer_fingerprint"] == fingerprint):
        raise AssertionError
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
        if not (
            any(
            line.startswith("[GNUPG:] VALIDSIG ") and line.split()[-1] == fingerprint
            for line in status.splitlines()
        )
        ):
            raise AssertionError(status)
    for name in ("omarchy-server.db", "omarchy-server.db.sig"):
        if not (
            hashlib.sha256((dest / name).read_bytes()).hexdigest()
            == provenance["files"][name]
        ):
            raise AssertionError
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


def ipv4_only(args):
    """Verify DNS A/AAAA over IPv4 with IPv6 disabled on the data link."""
    import config

    name = interface(DATA_MAC)["Name"]
    profile = config.network_config(name, {"ipv6": "disabled"})
    digest = write("09-network-acceptance-data.network", profile)
    reload()

    def ready():
        link = interface(DATA_MAC)
        if not link.get("DHCPv4Client", {}).get("Lease"):
            return None
        v6 = json.loads(command("ip", "-j", "-6", "addr", "show", "dev", name))
        if any(item.get("addr_info") for item in v6):
            return None
        return link

    eventually(ready)
    command("resolvectl", "domain", name, "~.")
    a = eventually(lambda: dns(name))
    aaaa = command("resolvectl", "query", "--protocol=dns", "--type=AAAA", "one.one.one.one")
    record("ipv4-only", {
        "configuration_sha256": digest,
        "dns_a_over_ipv4": a,
        "dns_aaaa_over_ipv4": aaaa,
        "routes": command("ip", "-4", "route"),
        "ipv6_disabled_on": name,
    })


def static(args):
    # This is the actual installer generator from the source tree under test.
    import config

    content = config.network_config(interface(DATA_MAC)["Name"], {
        "ipv4": "static", "address4": "10.0.3.40/24",
        "gateway4": "10.0.3.2", "dns4": "10.0.3.3",
    })
    digest = write("09-network-acceptance-data.network", content)
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

    expected = config.network_config(interface(DATA_MAC)["Name"], {
        "ipv4": "static", "address4": "10.0.3.40/24",
        "gateway4": "10.0.3.2", "dns4": "10.0.3.3",
    })
    if not (
        Path("/etc/systemd/network/09-network-acceptance-data.network").read_text()
        == expected
    ):
        raise AssertionError

    def ready():
        link = interface(DATA_MAC)
        if not (
            any(
            a.get("AddressString") == "10.0.3.40" and a.get("ConfigSource") == "static"
            for a in link.get("Addresses", [])
        )
        ):
            raise AssertionError
        if not (not link.get("DHCPv4Client", {}).get("Lease")):
            raise AssertionError
        return link

    link = eventually(ready)
    if not (route4() == link["Name"]):
        raise AssertionError
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
Name={interface(DATA_MAC)['Name']}
[Network]
DHCP=no
IPv6AcceptRA=no
LinkLocalAddressing=ipv6
Address=fd00:3::15/64
Gateway=fd00:3::2
DNS=[fd00:3::2]:{args.port}
Domains=~.
DNSDefaultRoute=yes
[DHCPv4]
UseHostname=no
"""
    record(
        "ipv6-configure",
        {
            "configuration_sha256": write(
                "09-network-acceptance-data.network", content
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
            Path("/etc/systemd/network/09-network-acceptance-data.network").read_bytes()
        ).hexdigest()
        if not (digest == prior["configuration_sha256"]):
            raise AssertionError
        routes = json.loads(command("ip", "-j", "-6", "route", "show", "default"))
        if not (any(r["dev"] == data for r in routes)):
            raise AssertionError(routes)
        command("resolvectl", "flush-caches")
        text = command(
            "resolvectl", "query", "--protocol=dns", "--type=AAAA", "one.one.one.one"
        )
        if not (f"link: {data}" in text):
            raise AssertionError(text)
        with socket.create_connection(("fd00:3::15", 22), timeout=5) as connection:
            banner = connection.recv(100).decode().strip()
        if not (banner.startswith("SSH-2.0-")):
            raise AssertionError(banner)
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
            "ipv4-only",
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
