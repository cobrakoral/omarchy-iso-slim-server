#!/usr/bin/env python3
import importlib.util
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
