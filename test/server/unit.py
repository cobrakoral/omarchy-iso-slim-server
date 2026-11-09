#!/usr/bin/env python3
import importlib.util
import shutil
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

    def test_dhcp_and_ipv6_without_interface_name(self):
        text = config.network_config({})
        self.assertIn("DHCP=yes", text)
        self.assertIn("IPv6AcceptRA=yes", text)
        self.assertIn("LLMNR=no", text)
        self.assertIn("MulticastDNS=no", text)
        self.assertNotIn("Name=", text)

    def test_static_ipv4_and_ipv6_dns(self):
        text = config.network_config(
            dict(
                network="static",
                mac="52:54:00:12:34:56",
                address="192.0.2.4/24",
                gateway="192.0.2.1",
                dns="192.0.2.53 2001:db8::53",
            )
        )
        self.assertIn("DHCP=ipv6", text)
        self.assertIn("DNS=2001:db8::53", text)
        self.assertIn("DHCP=ipv6", text)
        self.assertIn("LLMNR=no", text)
        self.assertIn("MulticastDNS=no", text)

    def test_refuse_bad_network_before_disk_work(self):
        for kw in [
            dict(address="bad"),
            dict(gateway="198.51.100.1"),
            dict(dns="1.1.1.1\n[Match]"),
        ]:
            args = dict(
                network="static",
                mac="52:54:00:12:34:56",
                address="192.0.2.4/24",
                gateway="192.0.2.1",
                dns="192.0.2.53",
            )
            args.update(kw)
            with self.assertRaises(ValueError):
                config.network_config(args)

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
        for name, answers in (("dhcp", {}), ("static", dict(
            network="static", mac="52:54:00:12:34:56", address="192.0.2.4/24",
            gateway="192.0.2.1", dns="192.0.2.53 2001:db8::53"))):
            with self.subTest(mode=name):
                text = config.network_config(answers)
                self.assertIn("LLMNR=no", text)
                self.assertIn("MulticastDNS=no", text)
                self.assertIn("IPv6AcceptRA=yes", text)

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
                        server.configure_network_security(ctx)
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


if __name__ == "__main__":
    unittest.main()
