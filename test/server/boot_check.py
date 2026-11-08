#!/usr/bin/env python3
"""Regression coverage for stale and inconsistent deployed boot artifacts."""

from importlib.machinery import SourceFileLoader
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
loader = SourceFileLoader(
    "boot_check", str(ROOT / "builder/server/runtime/bin/omarchy-server-boot-check")
)
spec = importlib.util.spec_from_loader(loader.name, loader)
check = importlib.util.module_from_spec(spec)
loader.exec_module(check)


class BootCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.release = "7.2.8-10-omarchy"
        self.module = self.root / "usr/lib/modules" / self.release
        self.module.mkdir(parents=True)
        (self.module / "pkgbase").write_text("linux-omarchy\n")
        (self.module / "vmlinuz").write_bytes(b"installed kernel")
        (self.root / "etc").mkdir()
        self.machine = "a" * 32
        (self.root / "etc/machine-id").write_text(self.machine)
        self.sections = {
            ".uname": self.release.encode(),
            ".linux": b"installed kernel",
            ".initrd": b"initramfs",
        }

    def test_boot_mount_must_match_configured_partition(self):
        device = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(254, 1))
        for mounted, matches in (
            ("254:1 /\n", True),
            ("254:2 /\n", False),
            ("254:1 /elsewhere\n", False),
            ("254:1 /\n254:2 /\n", False),
        ):
            replies = [
                subprocess.CompletedProcess([], 0, "/dev/disk/by-uuid/boot\n"),
                subprocess.CompletedProcess([], 0, mounted),
            ]
            with (
                self.subTest(mounted=mounted),
                patch.object(check.subprocess, "run", side_effect=replies),
                patch.object(Path, "stat", return_value=device),
            ):
                if matches:
                    check.check_mount()
                else:
                    with self.assertRaisesRegex(ValueError, "configured device"):
                        check.check_mount()

    def test_valid_uki(self):
        with patch.object(
            check, "uki_sections", return_value=self.sections
        ), patch.object(check, "initramfs_matches") as initrd:
            self.assertEqual(check.verify(self.root, True), self.release)
            initrd.assert_called_once()

    def test_stale_uki_release(self):
        self.sections[".uname"] = b"7.2.8-9-omarchy"
        with patch.object(check, "uki_sections", return_value=self.sections):
            with self.assertRaisesRegex(ValueError, "does not match"):
                check.verify(self.root, True)

    def test_same_release_wrong_payload(self):
        self.sections[".linux"] = b"other kernel with same release"
        with patch.object(check, "uki_sections", return_value=self.sections):
            with self.assertRaisesRegex(ValueError, "payload differs"):
                check.verify(self.root, True)

    def test_missing_uki_initramfs(self):
        self.sections[".initrd"] = b""
        with patch.object(check, "uki_sections", return_value=self.sections):
            with self.assertRaisesRegex(ValueError, "initramfs is missing"):
                check.verify(self.root, True)

    def test_initramfs_kernel_mismatch(self):
        listing = "usr/lib/modules/7.2.8-9-omarchy/kernel/fs/btrfs/btrfs.ko.zst\n"
        with patch.object(check.subprocess, "check_output", return_value=listing):
            with self.assertRaisesRegex(ValueError, "do not match"):
                check.initramfs_matches(Path("/test/initrd"), self.release)

    def test_mixed_initramfs_versions(self):
        listing = f"usr/lib/modules/{self.release}/kernel/a.ko\nusr/lib/modules/other/kernel/b.ko\n"
        with patch.object(check.subprocess, "check_output", return_value=listing):
            with self.assertRaises(ValueError):
                check.initramfs_matches(Path("/test/initrd"), self.release)

    def test_matching_initramfs(self):
        listing = f"usr/lib/modules/{self.release}/kernel/fs/btrfs/btrfs.ko.zst\n"
        with patch.object(check.subprocess, "check_output", return_value=listing):
            check.initramfs_matches(Path("/test/initrd"), self.release)

    def test_stale_bios_payload(self):
        boot = self.root / "boot" / self.machine / "linux-omarchy"
        boot.mkdir(parents=True)
        (boot / "vmlinuz").write_bytes(b"old kernel")
        with self.assertRaisesRegex(ValueError, "BIOS kernel payload differs"):
            check.verify(self.root, False)

    def test_missing_boot_mount_fails(self):
        device = SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(254, 1))
        replies = [
            subprocess.CompletedProcess([], 0, "/dev/disk/by-uuid/boot\n"),
            subprocess.CompletedProcess([], 1, ""),
        ]
        with (
            patch.object(check.subprocess, "run", side_effect=replies),
            patch.object(Path, "stat", return_value=device),
            patch.object(check, "verify") as verify,
        ):
            self.assertEqual(check.main(), 1)
            verify.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
