#!/usr/bin/env python3
"""Reject invalid server inputs before any disk-holder cleanup or formatting."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

config = load("server_config", "builder/server/config.py")

class InputValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.accounts = self.directory / "target-users"
        self.accounts.write_text("root\nnobody\nbin\ndaemon\nsystemd-network\ntarget-service\n")
        self.groups = self.directory / "target-groups"
        self.groups.write_text("root\nwheel\nusers\n")
        self.answers = dict(disk="/dev/test-only-A", hostname="server", username="admin",
                            password_hash="$6$testsalt$" + "A" * 86, timezone="UTC",
                            keyboard="us", locale="en_US.UTF-8")
        self.cleanup_calls = []
        self.cleanup_disk_seqs = []
        self.shared = types.SimpleNamespace(
            _install_disk=lambda ctx: ctx.user_configuration["disk_config"]["device_modifications"][0]["device"],
            release_install_disk=lambda ctx, disk_seq=None: (self.cleanup_calls.append(ctx), self.cleanup_disk_seqs.append(disk_seq)))
        package = types.ModuleType("orchestrator")
        package.__path__ = []
        self.modules = patch.dict(sys.modules, {"server_config": config, "orchestrator": package,
                                 "orchestrator.phases_impl": self.shared})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.server = load("orchestrator.server_test", "configs/airootfs/usr/share/omarchy-iso/orchestrator/server.py")
        self.inventory = []
        self.lsblk_calls = []
        self.disk_seq = 1
        self.disk_children = []
        original = subprocess.check_output
        def check(args, *a, **kw):
            if args[0] == "localectl":
                return "us\n"
            if args[0] == "lsblk":
                self.lsblk_calls.append(list(args))
                target = args[-1] if not args[-1].startswith("-") else None
                if target:
                    self.inventory.append(target)
                    canonical = "/dev/sda" if target.startswith("/dev/disk/by-path/") else target
                else:
                    canonical = "/dev/sda"
                block = dict(path=canonical, type="disk", ro=False, size=16 * 1024**3, mountpoints=[None], model="TEST", children=list(self.disk_children))
                if self.disk_seq is not None:
                    block["disk-seq"] = self.disk_seq
                return json.dumps({"blockdevices": [block]})
            if args[0] == "findmnt":
                return ""
            return original(args, *a, **kw)
        self.patchers = [patch.object(config, "SYSTEM_USERS", self.accounts),
                         patch.object(config, "SYSTEM_GROUPS", self.groups),
                         patch.object(subprocess, "check_output", side_effect=check)]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)
        conf, creds = config.make_config(self.answers, 16 * 1024**3)
        self.ctx = types.SimpleNamespace(user_configuration=conf, omarchy_install=conf["omarchy_install"],
            user_credentials=creds, username="admin", defer_provisioning=False, is_protected=False,
            encrypt=False, target=Path("/mnt"), authorized_keys_path=None,
            tailscale_authkey_path=None, state={},
            state_dir=self.directory / "state", arch_config_path=self.directory / "original-config",
            creds_path=self.directory / "original-creds")

    def test_credentials_cannot_override_configuration(self):
        for name in ("disk_config", "disk_encryption", "kernels", "auth_config", "root_enc_password", "!users", "custom_commands"):
            with self.subTest(name=name):
                self.ctx.user_credentials[name] = {"device": "/dev/never-validated-B"}
                with self.assertRaisesRegex(ValueError, "credentials fields"):
                    self.server.validate(self.ctx)
                self.ctx.user_credentials.pop(name)
        self.assertEqual(self.inventory, [])
        self.assertFalse(self.ctx.state_dir.exists())

    def test_sudo_requires_exact_true(self):
        for value in ("true", "false", 1, 0, [], None, False):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "boolean true"):
                self.ctx.user_credentials["users"][0]["sudo"] = value
                self.server.validate(self.ctx)
        self.assertEqual(self.inventory, [])

    def test_reject_legacy_plaintext_password_and_unknown_user_fields(self):
        for name in ("!password", "uid", "password", "shell"):
            self.ctx.user_credentials["users"][0][name] = "unexpected"
            with self.assertRaisesRegex(ValueError, "administrator credentials"):
                self.server.validate(self.ctx)
            self.ctx.user_credentials["users"][0].pop(name)

    def test_reject_additional_archinstall_override_fields(self):
        for name in ("disk_encryption", "!users", "root_enc_password", "profile_config", "custom_commands"):
            self.ctx.user_configuration[name] = {"unexpected": True}
            with self.assertRaisesRegex(ValueError, "configuration fields"):
                self.server.validate(self.ctx)
            self.ctx.user_configuration.pop(name)
        self.assertFalse(self.ctx.state_dir.exists())

    def test_target_accounts_are_rejected_before_disk_inventory(self):
        for name in ("bin", "daemon", "systemd-network", "target-service", "wheel"):
            self.ctx.username = name
            self.ctx.user_credentials["users"][0]["username"] = name
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "regular administrator"):
                self.server.validate(self.ctx)
        self.assertEqual(self.inventory, [])

    def test_missing_target_account_metadata_fails_closed(self):
        self.accounts.unlink()
        with self.assertRaises(FileNotFoundError):
            self.server.validate(self.ctx)
        self.assertEqual(self.inventory, [])

    def test_fixed_server_policy_rejects_overrides(self):
        for field, value in (
            ("bootloader_config", {"bootloader": "Grub"}),
            ("swap", True),
            ("mirror_config", {"custom_repositories": [{"sign_check": "Never"}]}),
            ("locale_config", {"kb_layout": "us", "sys_lang": "en_US.UTF-8", "sys_enc": "other"}),
        ):
            with self.subTest(field=field):
                original = self.ctx.user_configuration[field]
                self.ctx.user_configuration[field] = value
                with self.assertRaisesRegex(ValueError, field):
                    self.server.validate(self.ctx)
                self.ctx.user_configuration[field] = original
        self.ctx.omarchy_install["boot"]["esp_mount"] = "/foreign"
        with self.assertRaisesRegex(ValueError, "setting: boot"):
            self.server.validate(self.ctx)
        self.assertFalse(self.ctx.state_dir.exists())

    def test_groups_are_checked_and_preserved(self):
        self.ctx.user_credentials["users"][0]["groups"] = ["absent"]
        with self.assertRaisesRegex(ValueError, "groups must exist"):
            self.server.validate(self.ctx)
        self.assertEqual(self.inventory, [])
        self.ctx.user_credentials["users"][0]["groups"] = ["users"]
        self.server.validate(self.ctx)
        self.assertEqual(json.loads(self.ctx.creds_path.read_text())["users"][0]["groups"], ["users"])

    def test_unsupported_key_options_block_installation(self):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.directory / "key")], check=True)
        key = (self.directory / "key.pub").read_text()
        keys = self.directory / "authorized_keys"
        self.ctx.authorized_keys_path = keys
        for option in ('typo-option', 'restrict="yes"', 'from="192.0.2.0/99"', 'restrict,', 'from="unclosed'):
            with self.subTest(option=option):
                keys.write_text(option + " " + key)
                with self.assertRaisesRegex(ValueError, "entry 1"):
                    self.server.validate(self.ctx)
                self.assertFalse(self.ctx.state_dir.exists())

    def test_validated_documents_are_frozen_and_private(self):
        old_umask = os.umask(0o022)
        try:
            self.server.validate(self.ctx)
        finally:
            os.umask(old_umask)
        self.assertEqual(self.inventory, ["/dev/test-only-A"])
        for name in ("creds_path", "arch_config_path"):
            self.assertEqual(getattr(self.ctx, name).stat().st_mode & 0o777, 0o600)
        frozen = json.loads(self.ctx.arch_config_path.read_text())
        self.assertNotIn("omarchy_install", frozen)
        self.assertEqual(frozen["disk_config"], self.ctx.user_configuration["disk_config"])
        self.ctx.user_configuration["disk_config"]["device_modifications"][0]["device"] = "/dev/changed-B"
        self.ctx.user_credentials["disk_config"] = {}
        self.assertEqual(json.loads(self.ctx.arch_config_path.read_text()), frozen)
        self.assertNotIn("disk_config", json.loads(self.ctx.creds_path.read_text()))

    def test_real_keys_and_options_are_accepted_and_cached(self):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.directory / "key")], check=True)
        key = (self.directory / "key.pub").read_text().strip()
        keys = self.directory / "authorized_keys"
        keys.write_text('# comment\n' + key + '\nfrom="192.0.2.*",no-agent-forwarding ' + key + '\n')
        self.ctx.authorized_keys_path = keys
        self.server.validate(self.ctx)
        self.assertEqual(len(self.ctx.state["server_authorized_keys"]), 2)
        keys.write_text("changed after preflight")
        self.assertEqual(self.ctx.state["server_authorized_keys"][0], key)

    def test_invalid_key_blocks_staging(self):
        keys = self.directory / "authorized_keys"
        self.ctx.authorized_keys_path = keys
        for body in ("garbage\n", "# only a comment\n", "-----BEGIN OPENSSH PRIVATE KEY-----\n"):
            keys.write_text(body)
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.server.validate(self.ctx)
            self.assertFalse(self.ctx.state_dir.exists())

    def test_one_valid_key_cannot_mask_a_malformed_entry(self):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.directory / "key")], check=True)
        keys = self.directory / "authorized_keys"
        keys.write_text((self.directory / "key.pub").read_text() + "invalid second entry\n")
        with self.assertRaisesRegex(ValueError, "entry 2"):
            config.authorized_keys(keys)

    def test_alias_normalization_for_archinstall_and_cleanup(self):
        alias = "/dev/disk/by-path/pci-0000:00:1f.2-ata-1"
        self.answers["disk"] = alias
        conf, creds = config.make_config(self.answers, 16 * 1024**3)
        self.ctx.user_configuration = conf
        self.ctx.omarchy_install = conf["omarchy_install"]
        self.ctx.user_credentials = creds
        self.server.validate(self.ctx)
        self.assertEqual(self.ctx.state["canonical_install_disk"], "/dev/sda")
        self.assertEqual(self.ctx.user_configuration["disk_config"]["device_modifications"][0]["device"], "/dev/sda")
        frozen = json.loads(self.ctx.arch_config_path.read_text())
        self.assertEqual(frozen["disk_config"]["device_modifications"][0]["device"], "/dev/sda")

    def test_lsblk_inventory_requests_tree_and_expected_columns(self):
        config.lsblk_inventory()
        last_call = self.lsblk_calls[-1]
        self.assertIn("--tree", last_call)
        self.assertEqual(last_call[0:3], ["lsblk", "-Jbo", "PATH,TYPE,SIZE,MOUNTPOINTS,RO,MODEL,DISK-SEQ"])

    def test_require_candidate_disk_distinguishes_partitioned_and_mounted_children(self):
        unmounted_partitioned = [
            {
                "path": "/dev/sda",
                "type": "disk",
                "ro": False,
                "size": 20 * 1024**3,
                "mountpoints": [None],
                "model": "DISK_A",
                "children": [
                    {"path": "/dev/sda1", "type": "part", "ro": False, "size": 512 * 1024**2, "mountpoints": [None]},
                    {"path": "/dev/sda2", "type": "part", "ro": False, "size": 19 * 1024**3, "mountpoints": []},
                ],
            }
        ]
        blank_disk = [
            {
                "path": "/dev/sdb",
                "type": "disk",
                "ro": False,
                "size": 16 * 1024**3,
                "mountpoints": [],
                "model": "DISK_B",
                "children": [],
            }
        ]
        mounted_child = [
            {
                "path": "/dev/sdc",
                "type": "disk",
                "ro": False,
                "size": 32 * 1024**3,
                "mountpoints": [None],
                "model": "DISK_C",
                "children": [
                    {"path": "/dev/sdc1", "type": "part", "ro": False, "size": 32 * 1024**3, "mountpoints": ["/run/archiso/bootmnt"]},
                ],
            }
        ]
        with patch.object(config, "lsblk_inventory", return_value=unmounted_partitioned):
            selected = config.require_candidate_disk("/dev/sda")
            self.assertEqual(selected["path"], "/dev/sda")
        with patch.object(config, "lsblk_inventory", return_value=blank_disk):
            selected = config.require_candidate_disk("/dev/sdb")
            self.assertEqual(selected["path"], "/dev/sdb")
        with patch.object(config, "lsblk_inventory", return_value=mounted_child):
            with self.assertRaisesRegex(ValueError, "Installation needs an unmounted writable whole disk"):
                config.require_candidate_disk("/dev/sdc")

    def test_release_disk_rejects_invalid_parsed_plans_without_cleanup(self):
        self.server.validate(self.ctx)
        canonical = self.ctx.state["canonical_install_disk"]
        def make_entry(path, wipe=True):
            return types.SimpleNamespace(wipe=wipe, device=types.SimpleNamespace(device_info=types.SimpleNamespace(path=Path(path) if path is not None else None)))
        bad_plans = [
            None,
            types.SimpleNamespace(config=None),
            types.SimpleNamespace(config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[]))),
            types.SimpleNamespace(config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[
                make_entry(canonical),
                make_entry("/dev/sdb"),
            ]))),
            types.SimpleNamespace(config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[
                make_entry("/dev/sdb", wipe=True),
            ]))),
            types.SimpleNamespace(config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[
                make_entry(canonical, wipe=False),
            ]))),
            types.SimpleNamespace(config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[
                make_entry(canonical, wipe="true"),
            ]))),
        ]
        for plan in bad_plans:
            with self.subTest(plan=plan):
                self.ctx.state["arch_config_handler"] = plan
                with self.assertRaisesRegex(ValueError, "Parsed disk plan must contain exactly one wiping modification"):
                    self.server.release_disk(self.ctx)
                self.assertEqual(self.cleanup_calls, [])

    def test_release_disk_rejects_divergent_or_absent_cleanup_device(self):
        self.server.validate(self.ctx)
        canonical = self.ctx.state["canonical_install_disk"]
        entry = types.SimpleNamespace(wipe=True, device=types.SimpleNamespace(device_info=types.SimpleNamespace(path=Path(canonical))))
        self.ctx.state["arch_config_handler"] = types.SimpleNamespace(
            config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[entry]))
        )
        for bad_cleanup in ["/dev/sdb", None]:
            with self.subTest(cleanup=bad_cleanup):
                with patch.object(self.server.shared, "_install_disk", return_value=bad_cleanup):
                    with self.assertRaisesRegex(ValueError, "Parsed disk plan must contain exactly one wiping modification"):
                        self.server.release_disk(self.ctx)
                    self.assertEqual(self.cleanup_calls, [])
        self.ctx.state["canonical_install_disk"] = None
        with self.assertRaisesRegex(ValueError, "Parsed disk plan must contain exactly one wiping modification"):
            self.server.release_disk(self.ctx)
        self.assertEqual(self.cleanup_calls, [])

    def prepare_disk_release(self):
        self.server.validate(self.ctx)
        canonical = self.ctx.state["canonical_install_disk"]
        good_entry = types.SimpleNamespace(wipe=True, device=types.SimpleNamespace(device_info=types.SimpleNamespace(path=Path(canonical))))
        self.ctx.state["arch_config_handler"] = types.SimpleNamespace(
            config=types.SimpleNamespace(disk_config=types.SimpleNamespace(device_modifications=[good_entry]))
        )

    def test_release_disk_success_with_matching_parsed_plan(self):
        self.prepare_disk_release()
        self.server.release_disk(self.ctx)
        self.assertEqual(self.cleanup_calls, [self.ctx])
        self.assertEqual(self.cleanup_disk_seqs, [self.disk_seq])

    def test_disk_release_refuses_replacement_at_same_path(self):
        self.prepare_disk_release()
        self.disk_seq += 1
        with self.assertRaisesRegex(ValueError, "sequence changed"):
            self.server.release_disk(self.ctx)
        self.assertEqual(self.cleanup_calls, [])

    def test_disk_release_refuses_late_mount(self):
        self.prepare_disk_release()
        self.disk_children = [{"mountpoints": ["/srv/late-mount"]}]
        with self.assertRaisesRegex(ValueError, "unmounted writable whole disk"):
            self.server.release_disk(self.ctx)
        self.assertEqual(self.cleanup_calls, [])

    def test_partition_guard_rechecks_identity_after_release(self):
        self.prepare_disk_release()
        self.server.release_disk(self.ctx)
        self.disk_seq += 1
        with self.assertRaisesRegex(ValueError, "sequence changed"):
            self.ctx.state["pre_partition_check"]()

    def test_install_rejects_missing_disk_identity(self):
        self.disk_seq = None
        with self.assertRaisesRegex(ValueError, "kernel disk sequence"):
            self.server.validate(self.ctx)
        self.assertEqual(self.cleanup_calls, [])

if __name__ == "__main__":
    unittest.main()
