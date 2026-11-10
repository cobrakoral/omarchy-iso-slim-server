#!/usr/bin/env python3
import ast
import importlib.util
import os
import shutil
import socket
import struct
import subprocess
from pathlib import Path
import unittest
import sys
import types
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


config = load("config", "builder/server/config.py")
packages = load("packages", "builder/server/check-packages.py")


class ServerTests(unittest.TestCase):
    def test_cloud_config_survives_chroot_api_mounts(self):
        # arch-chroot binds /run from the live system. The temporary pacman
        # config must live on the target's persistent tree during its call.
        with tempfile.TemporaryDirectory() as name:
            target = Path(name)
            context = types.SimpleNamespace(
                target=target,
                omarchy_install={"server": {"cloud_init": True}},
                authorized_keys_path=None,
            )
            package = types.ModuleType("orchestrator")
            package.__path__ = []
            shared = types.SimpleNamespace(OFFLINE_MIRROR=Path("/offline"))
            with patch.dict(
                sys.modules,
                {"orchestrator": package, "orchestrator.phases_impl": shared},
            ):
                spec = importlib.util.spec_from_file_location(
                    "orchestrator.server_test",
                    ROOT
                    / "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py",
                )
                server = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(server)

            def chroot(ctx, *args):
                if args[0] == "pacman":
                    config_path = args[args.index("--config") + 1]
                    self.assertFalse(config_path.startswith("/run/"))
                    self.assertTrue((target / config_path.lstrip("/")).is_file())

            with patch.object(
                server, "chroot", side_effect=chroot
            ) as calls, patch.object(server.subprocess, "run"), patch.object(
                server.Path, "read_text", return_value="[offline]\n"
            ):
                server.cloud_init(context)
            self.assertTrue(
                any(call.args[1] == "pacman" for call in calls.call_args_list)
            )
            enabled = [
                call.args for call in calls.call_args_list if "enable" in call.args
            ]
            self.assertTrue(any("cloud-init-main.service" in call for call in enabled))
            self.assertFalse(
                (target / "etc/pacman.d/omarchy-server-offline.conf").exists()
            )

    def test_cloud_opt_out_does_not_run_pacman(self):
        package = types.ModuleType("orchestrator")
        package.__path__ = []
        with patch.dict(
            sys.modules,
            {
                "orchestrator": package,
                "orchestrator.phases_impl": types.SimpleNamespace(),
            },
        ):
            spec = importlib.util.spec_from_file_location(
                "orchestrator.server_test",
                ROOT / "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py",
            )
            server = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server)
        with patch.object(server, "chroot") as chroot, patch.object(
            server.subprocess, "run"
        ) as run:
            server.cloud_init(
                types.SimpleNamespace(omarchy_install={"server": {"cloud_init": False}})
            )
        chroot.assert_not_called()
        run.assert_not_called()

    def test_disk_picker_excludes_floppy_read_only_and_mounted_disks(self):
        def disk(name, size=16 * 1024**3, ro=False, children=None):
            return dict(
                path=name,
                type="disk",
                size=size,
                ro=ro,
                mountpoints=[None],
                children=children or [],
            )

        devices = [
            disk("/dev/fd0", size=0),
            disk("/dev/readonly", ro=True),
            disk("/dev/live", children=[dict(mountpoints=["/run/archiso/bootmnt"])]),
            disk("/dev/target"),
        ]
        self.assertEqual(
            [d["path"] for d in config.candidate_disks(devices)], ["/dev/target"]
        )

    def test_locale_picker_accepts_arch_whitespace(self):
        text = "# Configuration\n#en_US.UTF-8 UTF-8  \npt_BR.UTF-8 UTF-8\t\n#en_US ISO-8859-1  \n"
        self.assertEqual(config.supported_locales(text), ["en_US.UTF-8", "pt_BR.UTF-8"])

    def test_default_dual_stack_with_explicit_interface_name(self):
        text = config.network_config("ens18")
        self.assertIn("Name=ens18", text)
        self.assertIn("DHCP=ipv4", text)
        self.assertIn("IPv6AcceptRA=yes", text)
        self.assertIn("LinkLocalAddressing=ipv6", text)
        self.assertIn("UseHostname=no", text)
        self.assertIn("LLMNR=no", text)
        self.assertIn("MulticastDNS=no", text)
        self.assertNotIn("Path=*", text)
        self.assertNotIn("1.1.1.1", text)

    def test_static_ipv4_and_ipv6_dns(self):
        text = config.network_config("enp1s0", {
            "ipv4": "static", "address4": "192.0.2.4/24",
            "gateway4": "192.0.2.1", "dns4": "192.0.2.53",
            "ipv6": "auto", "dns6": "2001:db8::53",
        })
        self.assertIn("DHCP=no", text)
        self.assertIn("IPv6AcceptRA=yes", text)
        self.assertIn("Address=192.0.2.4/24", text)
        self.assertIn("Gateway=192.0.2.1", text)
        self.assertIn("DNS=2001:db8::53", text)

    def test_refuse_bad_network_before_disk_work(self):
        for kw in [
            dict(address4="bad"),
            dict(gateway4="198.51.100.1"),
            dict(dns4="1.1.1.1\n[Match]"),
        ]:
            args = dict(ipv4="static", address4="192.0.2.4/24",
                        gateway4="192.0.2.1", dns4="192.0.2.53")
            args.update(kw)
            with self.assertRaises(ValueError):
                config.network_config("eth0", args)

    def test_no_desktop_in_resolved_closure(self):
        packages.check(
            ["systemd", "openssh", "btrfs-progs", "limine-snapper-sync-server"]
        )
        for package in [
            "networkmanager",
            "hyprland",
            "wayland",
            "libnotify",
            "libx11",
            "cloud-init",
            "sddm",
            "pipewire",
            "docker",
            "qt6-base",
        ]:
            with self.subTest(package=package), self.assertRaises(ValueError):
                packages.check([package])

    @patch.object(config, "validate")
    def test_official_layout_and_opt_in_encryption(self, _):
        answers = dict(
            disk="/dev/vda",
            hostname="server",
            username="admin",
            password_hash="$6$test",
        )
        conf, creds = config.make_config(answers, 16 * 1024**3)
        disk = conf["disk_config"]
        parts = disk["device_modifications"][0]["partitions"]
        self.assertNotIn("disk_encryption", disk)
        self.assertEqual(
            [s["name"] for s in parts[1]["btrfs"]], ["@", "@home", "@log", "@pkg"]
        )
        self.assertNotIn("cloud_init", conf["omarchy_install"]["server"])
        self.assertEqual(creds["users"][0]["enc_password"], "$6$test")
        answers.update(encrypt=True, encryption_password="test-passphrase")
        conf, _ = config.make_config(answers, 16 * 1024**3)
        self.assertEqual(
            conf["disk_config"]["disk_encryption"]["encryption_type"], "luks"
        )

    @patch.object(config, "validate")
    def test_refuse_undersized_disk(self, _):
        with self.assertRaises(ValueError):
            config.make_config({}, 4 * 1024**3)


class ServerKeyringTests(unittest.TestCase):
    def module(self):
        package = types.ModuleType("orchestrator")
        package.__path__ = []
        with patch.dict(
            sys.modules,
            {
                "orchestrator": package,
                "orchestrator.phases_impl": types.SimpleNamespace(),
            },
        ):
            return load(
                "orchestrator.server_keyring",
                "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py",
            )

    def test_installs_explicit_acpi_poweroff_policy(self):
        server = self.module()
        policy = (ROOT / "builder/server/logind.conf").read_text()
        self.assertIn("HandlePowerKey=poweroff", policy.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            context = types.SimpleNamespace(target=target)
            with patch.object(server.Path, "read_text", return_value=policy):
                server.configure_power_button(context)
            installed = (
                target / "etc/systemd/logind.conf.d/90-omarchy-server-power-button.conf"
            )
            self.assertEqual(installed.read_text(), policy)
            self.assertEqual(installed.stat().st_mode & 0o777, 0o644)

    def test_populates_only_server_anchor_and_stops_agents(self):
        server = self.module()
        ctx = types.SimpleNamespace(target=Path("/mnt"))
        with patch.object(server, "chroot") as chroot, patch.object(
            server.subprocess, "run"
        ) as run:
            server.populate_server_keyring(ctx)
        chroot.assert_called_once_with(
            ctx, "pacman-key", "--populate", "omarchy-server"
        )
        self.assertIn("gpgconf", run.call_args.args[0])
        self.assertFalse(run.call_args.kwargs["check"])

    def test_populate_failure_still_stops_agents(self):
        server = self.module()
        ctx = types.SimpleNamespace(target=Path("/mnt"))
        with patch.object(
            server, "chroot", side_effect=RuntimeError("test failure")
        ), patch.object(server.subprocess, "run") as run:
            with self.assertRaises(RuntimeError):
                server.populate_server_keyring(ctx)
        run.assert_called_once()



class ServerInterfaceNetworkTests(unittest.TestCase):
    INTERFACES = [
        {"name": "enp1s0", "mac": "52:54:00:12:34:56"},
        {"name": "enp2s0", "mac": "52:54:00:12:34:57"},
    ]

    def test_detect_ethernet_and_exclude_wifi_bridge_veth(self):
        with tempfile.TemporaryDirectory() as directory:
            sysfs = Path(directory)
            links = [
                {"ifname": "enp2s0", "link_type": "ether", "address": "52:54:00:12:34:57"},
                {"ifname": "enp1s0", "link_type": "ether", "address": "52:54:00:12:34:56"},
                {"ifname": "wlp3s0", "link_type": "ether", "address": "52:54:00:12:34:58"},
                {"ifname": "br0", "link_type": "ether"},
                {"ifname": "veth0", "link_type": "ether"},
                {"ifname": "lo", "link_type": "loopback"},
            ]
            for name in ("enp1s0", "enp2s0", "wlp3s0"):
                (sysfs / name / "device").mkdir(parents=True)
            (sysfs / "wlp3s0" / "wireless").mkdir()
            self.assertEqual(config.ethernet_interfaces(links, sysfs), self.INTERFACES)

    def test_two_default_interfaces_have_individual_profiles(self):
        files = config.network_profiles(self.INTERFACES, {})
        self.assertEqual(list(files), ["10-enp1s0.network", "10-enp2s0.network"])
        for name, content in files.items():
            self.assertIn(f"Name={name[3:-8]}", content)
            self.assertEqual(content.count("[Match]"), 1)
            self.assertIn("DHCP=ipv4", content)
            self.assertIn("IPv6AcceptRA=yes", content)
            self.assertIn("[DHCPv4]\nUseHostname=no", content)
            self.assertIn("[DHCPv6]\nUseHostname=no", content)
            self.assertNotIn("Name=*", content)

    def test_ipv4_ipv6_modes_are_independent(self):
        variants = [
            ({"ipv4": "disabled"}, ["DHCP=no", "IPv6AcceptRA=yes", "LinkLocalAddressing=ipv6"]),
            ({"ipv6": "disabled"}, ["DHCP=ipv4", "IPv6AcceptRA=no", "LinkLocalAddressing=no"]),
            ({"ipv4": "disabled", "ipv6": "disabled"}, ["DHCP=no", "IPv6AcceptRA=no", "LinkLocalAddressing=no"]),
            ({"ipv4": "static", "address4": "192.0.2.8/24"}, ["DHCP=no", "Address=192.0.2.8/24", "IPv6AcceptRA=yes"]),
            ({"ipv6": "static", "address6": "2001:db8:1::2/64"}, ["DHCP=ipv4", "Address=2001:db8:1::2/64", "IPv6AcceptRA=no"]),
            ({"ipv4": "static", "address4": "192.0.2.8/24", "ipv6": "static", "address6": "2001:db8:1::2/64"}, ["DHCP=no", "IPv6AcceptRA=no", "Address=192.0.2.8/24", "Address=2001:db8:1::2/64"]),
        ]
        for spec, expected in variants:
            with self.subTest(spec=spec):
                content = config.network_config("ens18", spec)
                for line in expected:
                    self.assertIn(line, content)
                self.assertEqual(content.count("UseHostname=no"), 2)

    def test_optional_static_gateways_dns_and_ipv6_link_local_gateway(self):
        text = config.network_config("enp1s0", {
            "ipv4": "static", "address4": "192.0.2.50/24",
            "ipv6": "static", "address6": "2001:db8::20/64",
            "gateway6": "fe80::1", "dns4": "192.0.2.53 192.0.2.54",
            "dns6": ["2001:db8::53"],
        })
        self.assertNotIn("Gateway=192.0.2", text)
        self.assertIn("Gateway=fe80::1", text)
        self.assertIn("DNS=192.0.2.53", text)
        self.assertIn("DNS=2001:db8::53", text)

    def test_invalid_names_fields_addresses_modes_and_dns(self):
        invalid = [
            ("../evil", {}), ("eth0*", {}), ("eth0", {"ipv4": "auto"}),
            ("eth0", {"ipv6": "dhcp"}),
            ("eth0", {"address4": "192.0.2.5/24"}),
            ("eth0", {"ipv4": "static", "address4": "192.0.2.5"}),
            ("eth0", {"ipv4": "static", "address4": "192.0.2.5/99"}),
            ("eth0", {"ipv4": "static", "address4": "2001:db8::5/64"}),
            ("eth0", {"ipv6": "static", "address6": "192.0.2.5/24"}),
            ("eth0", {"ipv6": "static", "address6": "2001:db8::5/129"}),
            ("eth0", {"ipv6": "static", "address6": "2001:db8::5/64", "gateway6": "2001:db9::1"}),
            ("eth0", {"dns4": "1.1.1.1\n[Network]"}),
            ("eth0", {"dns6": "192.0.2.53"}),
            ("eth0", {"ipv4": "disabled", "dns4": "1.1.1.1"}),
            ("eth0", {"ipv4": "static", "address4": "192.0.2.5/24", "gateway4": 1}),
            ("eth0", {"ipv6": "static", "address6": "2001:db8::5/64", "gateway6": None}),
            ("eth0", {"ipv4": "static", "address4": ["192.0.2.5/24"]}),
            ("eth0", {"something": "unexpected"}),
        ]
        for name, spec in invalid:
            with self.subTest(name=name, spec=spec), self.assertRaises(ValueError):
                config.network_config(name, spec)

    def test_manual_overrides_are_per_link_only(self):
        files = config.network_profiles(self.INTERFACES, {"network_interfaces": {
            "enp2s0": {"ipv4": "disabled", "ipv6": "static", "address6": "2001:db8:2::5/64"},
        }})
        self.assertIn("DHCP=ipv4", files["10-enp1s0.network"])
        self.assertIn("IPv6AcceptRA=yes", files["10-enp1s0.network"])
        self.assertIn("DHCP=no", files["10-enp2s0.network"])
        self.assertIn("Address=2001:db8:2::5/64", files["10-enp2s0.network"])
        self.assertNotIn("192.0.2.", files["10-enp2s0.network"])

    def test_missing_interface_or_duplicate_interface_refused(self):
        with self.assertRaisesRegex(ValueError, "undetected"):
            config.network_profiles(self.INTERFACES, {"network_interfaces": {"eth99": {}}})
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            config.network_profiles(self.INTERFACES + [self.INTERFACES[0]], {})

    def test_legacy_unattended_static_mac_still_resolves(self):
        files = config.network_profiles(self.INTERFACES, {
            "network": "static", "mac": "52:54:00:12:34:57", "address": "192.0.2.5/24",
            "gateway": "192.0.2.1", "dns": "192.0.2.53 2001:db8::53",
        })
        self.assertIn("DHCP=ipv4", files["10-enp1s0.network"])
        self.assertIn("Address=192.0.2.5/24", files["10-enp2s0.network"])
        self.assertIn("DNS=2001:db8::53", files["10-enp2s0.network"])
        with self.assertRaisesRegex(ValueError, "MAC does not match"):
            config.network_profiles(self.INTERFACES, {
                "network": "static", "mac": "52:54:00:12:34:99", "address": "192.0.2.5/24",
            })

    def test_target_profiles_are_readable_and_legacy_file_is_removed(self):
        package = types.ModuleType("orchestrator")
        package.__path__ = []
        with patch.dict(sys.modules, {"orchestrator": package,
                                     "orchestrator.phases_impl": types.SimpleNamespace()}):
            server = load("orchestrator.network_test",
                          "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py")
        with tempfile.TemporaryDirectory() as name:
            target = Path(name)
            network_dir = target / "etc/systemd/network"
            network_dir.mkdir(parents=True)
            (network_dir / "20-omarchy-server.network").write_text("old catch-all")
            ctx = types.SimpleNamespace(
                target=target, state={"server_network_profiles": config.network_profiles(self.INTERFACES, {})},
            )
            before = os.umask(0o077)
            try:
                with patch.dict(sys.modules, {"server_config": config}):
                    server.write_network_profiles(ctx)
            finally:
                os.umask(before)
            self.assertEqual(sorted(p.name for p in network_dir.iterdir()),
                             ["10-enp1s0.network", "10-enp2s0.network"])
            self.assertEqual(network_dir.stat().st_mode & 0o777, 0o755)
            for path in network_dir.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                self.assertIn("UseHostname=no", path.read_text())


class NetworkSecurityPolicyTests(unittest.TestCase):
    """Source-only dual-stack security checks, never touching the host firewall."""

    @classmethod
    def setUpClass(cls):
        cls.policy = ROOT / "builder/server/security/nftables.conf"
        cls.resolver = ROOT / "builder/server/security/resolved.conf"

    def test_default_deny_inbound_both_protocol_families(self):
        rules = self.policy.read_text()
        self.assertIn("table inet omarchy_server_filter", rules)
        self.assertIn("type filter hook input priority filter; policy drop;", rules)
        self.assertIn("type filter hook forward priority filter; policy drop;", rules)
        self.assertIn("type filter hook output priority filter; policy accept;", rules)
        self.assertIn('iifname "lo" accept', rules)
        self.assertIn("ct state invalid drop", rules)
        self.assertIn("ct state { established, related } accept", rules)
        self.assertIn("tcp dport 22 accept", rules)
        self.assertIn("meta nfproto ipv4 udp sport 67 udp dport 68 accept", rules)
        self.assertIn("meta nfproto ipv6 udp sport 547 udp dport 546 accept", rules)
        self.assertIn("meta nfproto ipv4 icmp type", rules)
        self.assertIn("meta nfproto ipv6 icmpv6 type", rules)
        for name in ("nd-neighbor-solicit", "nd-neighbor-advert", "nd-router-advert",
                     "nd-router-solicit", "packet-too-big", "mld2-listener-report"):
            self.assertIn(name, rules)
        # Avoid clobbering unrelated firewall tables on future servers.
        self.assertNotIn("flush ruleset", rules)
        self.assertNotIn("policy accept;", rules.split("chain input", 1)[1].split("chain forward", 1)[0])
        self.assertNotIn("udp dport 5353 accept", rules)
        self.assertNotIn("udp dport 5355 accept", rules)

    def test_no_multicast_resolver_and_per_link_policies(self):
        resolved = self.resolver.read_text()
        self.assertIn("[Resolve]", resolved)
        self.assertIn("LLMNR=no", resolved)
        self.assertIn("MulticastDNS=no", resolved)
        for name, settings in (("dhcp", {}), ("static", dict(
            ipv4="static", address4="192.0.2.4/24",
            gateway4="192.0.2.1", dns4="192.0.2.53", dns6="2001:db8::53"))):
            with self.subTest(mode=name):
                text = config.network_config("enp2s0", settings)
                self.assertIn("LLMNR=no", text)
                self.assertIn("MulticastDNS=no", text)
                self.assertIn("IPv6AcceptRA=yes", text)

    def test_live_archiso_dhcp_cannot_change_hostname(self):
        # Exercise the actual staged-profile patch using synthetic ArchISO data.
        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary) / "20-ethernet.network"
            profile.write_text("""[Match]
Name=en*
Name=eth*
[Network]
DHCP=yes
MulticastDNS=yes
[DHCPv4]
RouteMetric=100
[IPv6AcceptRA]
RouteMetric=100
""")
            script = ROOT / "builder/server/security/configure-live-ethernet.sh"
            subprocess.run(["bash", str(script), str(profile)], check=True)
            output = profile.read_text()
            for expected in ("DHCP=ipv4", "IPv6AcceptRA=yes", "LinkLocalAddressing=ipv6",
                             "LLMNR=no", "MulticastDNS=no", "[DHCPv4]\nUseHostname=no",
                             "[DHCPv6]\nUseHostname=no", "RouteMetric=100"):
                self.assertIn(expected, output)
            self.assertNotIn("DHCP=yes", output)
            self.assertNotIn("MulticastDNS=yes", output)
            self.assertNotIn("UseHostname=yes", output)
            # An unknown upstream profile must fail rather than silently drift.
            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(["bash", str(script), str(profile)], check=True,
                               capture_output=True)

    def test_both_live_and_installed_policies_are_wired(self):
        build = (ROOT / "builder/server/build.sh").read_text()
        self.assertIn('"$work/airootfs/etc/nftables.conf"', build)
        self.assertIn('"$work/airootfs/usr/share/omarchy-iso/server-nftables.conf"', build)
        self.assertIn('"$work/airootfs/etc/systemd/resolved.conf.d/90-omarchy-server-no-multicast.conf"', build)
        self.assertIn('"$work/airootfs/etc/systemd/system/multi-user.target.wants/nftables.service"', build)
        for name in ("target.packages", "live.packages"):
            roots = (ROOT / "builder/server" / name).read_text().splitlines()
            self.assertIn("nftables", roots)

    def test_installer_provisions_target_without_live_host_mutation(self):
        package = types.ModuleType("orchestrator")
        package.__path__ = []
        with patch.dict(sys.modules, {"orchestrator": package,
                                     "orchestrator.phases_impl": types.SimpleNamespace()}):
            server = load("orchestrator.security_test",
                          "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            ctx = types.SimpleNamespace(target=target)
            with patch.object(server, "chroot") as chroot:
                # The live ISO contains these files under server-*.conf.
                # Mock only the path mapping, not the installed payload.
                with tempfile.TemporaryDirectory() as bundle:
                    bundle_path = Path(bundle)
                    shutil.copy2(self.policy, bundle_path / "server-nftables.conf")
                    shutil.copy2(self.resolver, bundle_path / "server-resolved.conf")
                    with patch.object(server, "SECURITY_CONFIG_DIR", bundle_path):
                        # Reproduce the installer's restrictive umask. Before
                        # this fix, mkdir inherited 0700 and resolved could
                        # not traverse the directory at boot.
                        prior_umask = os.umask(0o077)
                        try:
                            server.configure_network_security(ctx)
                        finally:
                            os.umask(prior_umask)
            dropins = target / "etc/systemd/resolved.conf.d"
            self.assertEqual(dropins.stat().st_mode & 0o777, 0o755)
            self.assertEqual((dropins / "90-omarchy-server-no-multicast.conf").stat().st_mode & 0o777, 0o644)
            self.assertEqual((target / "etc/nftables.conf").read_bytes(), self.policy.read_bytes())
            self.assertEqual(
                (target / "etc/systemd/resolved.conf.d/90-omarchy-server-no-multicast.conf").read_bytes(),
                self.resolver.read_bytes(),
            )
            self.assertEqual((target / "etc/nftables.conf").stat().st_mode & 0o777, 0o644)
            chroot.assert_called_once_with(ctx, "nft", "-c", "-f", "/etc/nftables.conf")

    def test_nft_parser_without_modifying_host_firewall(self):
        if not shutil.which("nft") or not Path("/proc/self/ns/net").exists():
            self.skipTest("nftables parser unavailable")
        # nft -c validates in kernel space; if unprivileged, the dedicated
        # isolated namespace integration check handles it on the VM.
        if not shutil.which("sudo"):
            self.skipTest("sudo unavailable for read-only nft syntax check")
        can_sudo = subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
        if not can_sudo:
            self.skipTest("noninteractive CAP_NET_ADMIN unavailable")
        result = subprocess.run(["sudo", "-n", "nft", "-c", "-f", str(self.policy)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class OptimizedAcceptanceTests(unittest.TestCase):
    def test_acceptance_checks_cannot_disappear_under_python_optimization(self):
        # Assert statements and their side effects disappear under -O/-OO.
        for name in (
            "operational.py", "operational_guest.py", "operational_workload.py",
            "network_guest.py", "networking.py", "failures.py",
            "prepare_repository.py", "dhcp_fixture.py", "dns_fixture.py",
        ):
            with self.subTest(file=name):
                path = ROOT / "test/server" / name
                code = path.read_text()
                tree = ast.parse(code, filename=str(path))
                self.assertFalse(
                    [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Assert)],
                    f"Acceptance checks in {name} must use explicit failures, not assert",
                )
                compile(code, str(path), "exec", optimize=2)


class DNSFixtureTests(unittest.TestCase):
    def test_local_ipv4_ipv6_dns_answers_without_upstream(self):
        fixture = load("dns_fixture", "test/server/dns_fixture.py")
        qname = fixture.QNAME
        # The fixture binds only loopback sockets; no public resolver is used.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with fixture.dns_peer(port) as events:
            for family, host, record in (
                (socket.AF_INET, "127.0.0.1", 1),
                (socket.AF_INET, "127.0.0.1", 28),
                (socket.AF_INET6, "::1", 1),
                (socket.AF_INET6, "::1", 28),
            ):
                with self.subTest(family=family, record=record), socket.socket(family, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(2)
                    query = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
                    query += qname + struct.pack("!HH", record, 1)
                    sock.sendto(query, (host, port))
                    reply, _ = sock.recvfrom(4096)
                    self.assertEqual(reply[:2], query[:2])
                    self.assertEqual(reply[6:8], b"\x00\x01")
                    self.assertIn(fixture.ANSWERS[record], reply)
        self.assertEqual({event["type"] for event in events}, {"A", "AAAA"})
        self.assertEqual({event["guest_transport"] for event in events}, {"IPv4 UDP", "IPv6 UDP"})

    def test_local_dns_fixture_rejects_other_names_and_invalid_queries(self):
        fixture = load("dns_fixture", "test/server/dns_fixture.py")
        header = struct.pack("!HHHHHH", 0x1234, 0x0100, 1, 0, 0, 0)
        query = header + fixture.QNAME + struct.pack("!HH", 1, 1)
        self.assertIsNotNone(fixture.answer(query))
        self.assertIsNone(fixture.answer(query.replace(b"omarchy", b"example")))
        self.assertIsNone(fixture.answer(header + fixture.QNAME + struct.pack("!HH", 16, 1)))
        self.assertIsNone(fixture.answer(query[:8]))


if __name__ == "__main__":
    unittest.main()
