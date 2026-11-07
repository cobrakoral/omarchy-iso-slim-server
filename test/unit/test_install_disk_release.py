#!/usr/bin/python

"""Releasing the install disk before archinstall partitions it.

omarchy-iso-cleanup-disk refuses when something still holds the disk, and its
last line says what. prepare_live has to put that line on the failure screen
rather than the "returned non-zero exit status 1" a bare check=True gives, and
has to stop before archinstall is ever loaded. These tests put stand-in
cleanup scripts on PATH, so the real command capture runs.
"""

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "configs/airootfs/usr/share/omarchy-iso"))

# phases_impl imports the archinstall adapter at module scope, which pulls in
# the archinstall library that only exists on the live ISO.
sys.modules.setdefault(
    "orchestrator.archinstall_adapter",
    types.ModuleType("orchestrator.archinstall_adapter"),
)

from orchestrator import phases_impl, server  # noqa: E402

FULL_DISK = {"disk_config": {"device_modifications": [{"device": "/dev/vda", "wipe": True}]}}


class InstallDiskReleaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_dir = Path(self.tmp.name)
        path = mock.patch.dict(os.environ, {"PATH": f"{self.bin_dir}:{os.environ['PATH']}"})
        path.start()
        self.addCleanup(path.stop)
        self.arch = mock.Mock()
        patches = [
            mock.patch.object(phases_impl, "arch", self.arch, create=True),
            mock.patch.object(phases_impl, "_stage_qualcomm_firmware"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def cleanup_script(self, body: str) -> None:
        script = self.bin_dir / "omarchy-iso-cleanup-disk"
        script.write_text(f"#!/bin/sh\n{body}\n")
        script.chmod(0o755)

    def ctx(self):
        return types.SimpleNamespace(
            is_protected=False,
            user_configuration=FULL_DISK,
            arch_config_path=Path("/root/user_configuration.json"),
            creds_path=Path("/root/user_credentials.json"),
            state={},
        )

    def test_a_held_disk_stops_with_what_holds_it(self):
        self.cleanup_script(
            'echo "Cleaning up existing holders on install disk: $1"\n'
            'echo "/dev/vda is still in use and cannot be partitioned (vda2 is held by md127)."\n'
            "exit 1"
        )

        with self.assertRaises(RuntimeError) as raised:
            phases_impl.prepare_live(self.ctx())

        self.assertEqual(
            str(raised.exception),
            "/dev/vda is still in use and cannot be partitioned (vda2 is held by md127).",
        )
        self.arch.load_arch_config.assert_not_called()

    def test_a_silent_failure_still_names_the_disk(self):
        self.cleanup_script("exit 1")

        with self.assertRaises(RuntimeError) as raised:
            phases_impl.prepare_live(self.ctx())

        self.assertEqual(str(raised.exception), "Could not release /dev/vda")

    def test_a_released_disk_goes_on_to_archinstall(self):
        self.cleanup_script('[ "$1" = /dev/vda ] || exit 1\necho "Released $1"')

        phases_impl.prepare_live(self.ctx())

        self.arch.load_arch_config.assert_called_once()


    def test_server_checks_media_before_disk_release(self):
        ctx = self.ctx()
        ctx.target = self.bin_dir
        with mock.patch.object(server, "validate"), \
             mock.patch.object(phases_impl, "verify_root_image_layout"), \
             mock.patch.object(phases_impl, "verify_install_medium", side_effect=RuntimeError("bad media")), \
             mock.patch.object(phases_impl, "capture") as disk_command:
            with self.assertRaisesRegex(RuntimeError, "bad media"):
                for _, phase in server.build_phases(ctx)[:4]:
                    phase(ctx)
        self.arch.load_arch_config.assert_called_once()
        disk_command.assert_not_called()

    def test_server_parse_error_precedes_disk_release(self):
        ctx = self.ctx()
        ctx.target = self.bin_dir
        self.arch.load_arch_config.side_effect = ValueError("bad config")
        with mock.patch.object(server, "validate"), mock.patch.object(phases_impl, "capture") as disk_command:
            with self.assertRaisesRegex(ValueError, "bad config"):
                for _, phase in server.build_phases(ctx)[:4]:
                    phase(ctx)
        disk_command.assert_not_called()

    def test_rejected_target_is_not_cleaned_up(self):
        ctx = self.ctx()
        ctx.target = self.bin_dir
        ctx.is_protected = True
        hooks = ctx.target / "etc/pacman.d/hooks"
        hooks.mkdir(parents=True)
        hook = hooks / phases_impl.TARGET_DEFERRED_BOOT_HOOKS[0]
        hook.symlink_to("/dev/null")
        with mock.patch.object(phases_impl.subprocess, "run") as command:
            phases_impl.cleanup_hook_masks(ctx)
            phases_impl.cleanup_protected_state(ctx)
        self.assertEqual(hook.readlink(), Path("/dev/null"))
        command.assert_not_called()

    def test_cleanup_restores_only_masks_created_in_this_run(self):
        ctx = self.ctx()
        hooks = self.bin_dir / "etc/pacman.d/hooks"
        hooks.mkdir(parents=True)
        existing = hooks / "existing.hook"
        existing.symlink_to("/dev/null")
        original = hooks / "original.hook"
        original.write_text("original hook")
        phases_impl._mask_mkinitcpio_pacman_hooks(
            ctx, self.bin_dir, (existing.name, original.name, "new.hook")
        )
        self.assertEqual(original.readlink(), Path("/dev/null"))
        phases_impl.cleanup_hook_masks(ctx)
        phases_impl.cleanup_hook_masks(ctx)
        self.assertEqual(existing.readlink(), Path("/dev/null"))
        self.assertEqual(original.read_text(), "original hook")
        self.assertFalse((hooks / "new.hook").is_symlink())
        self.assertFalse(original.with_suffix(".hook.omarchy-backup").exists())


if __name__ == "__main__":
    unittest.main()
