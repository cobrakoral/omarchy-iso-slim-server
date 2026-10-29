#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import unittest
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
    def test_dhcp_and_ipv6_without_interface_name(self):
        text = config.network_config({})
        self.assertIn("DHCP=yes", text)
        self.assertIn("IPv6AcceptRA=yes", text)
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


if __name__ == "__main__":
    unittest.main()
