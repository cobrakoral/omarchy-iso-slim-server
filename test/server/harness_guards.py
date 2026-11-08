#!/usr/bin/env python3
"""Protect accepted disk graphs, source provenance and seed-media identity."""
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
import qemu
from system_upgrade import maintenance_package

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("package_repository", ROOT / "builder/server/package-repository.py")
repository = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repository)

class QmpShutdownTests(unittest.TestCase):
    def test_missing_greeting_times_out(self):
        with tempfile.TemporaryDirectory() as directory, socket.socket(socket.AF_UNIX) as listener:
            address = Path(directory) / "qmp"
            listener.bind(str(address))
            listener.listen()
            with self.assertRaises(TimeoutError):
                qemu.qmp(address, "query-status", timeout=0.05)

    def test_shutdown_failure_still_cleans_every_resource(self):
        vm = types.SimpleNamespace(
            child=Mock(), log=Mock(), control=Mock(), port_guard=Mock(), qmp=Path("qmp")
        )
        vm.child.isalive.return_value = True
        with patch.object(qemu, "qmp", side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                qemu.VM.stop(vm)
        vm.child.close.assert_called_once_with(force=True)
        vm.log.close.assert_called_once_with()
        vm.control.cleanup.assert_called_once_with()
        vm.port_guard.close.assert_called_once_with()

    def test_spawn_failure_releases_port_with_retained_exception(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            # Keep the original traceback alive: otherwise CPython's
            # refcounting can accidentally hide a leaked constructor lock.
            retained_error = None
            with patch.object(
                qemu.shutil, "copy", side_effect=lambda _src, dst: Path(dst).write_bytes(b"firmware")
            ):
                try:
                    with patch.object(
                        qemu.pexpect, "spawn", side_effect=OSError("injected spawn failure")
                    ):
                        qemu.VM(directory, directory / "unused.qcow2", port, [])
                except OSError as error:
                    retained_error = error
            self.assertIsNotNone(retained_error)
            guard = Path(tempfile.gettempdir()) / f"omarchy-qemu-ports-{os.getuid()}" / str(port)
            with guard.open("a") as second:
                fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(len(list(directory.glob("serial-*.log"))), 1)
            del retained_error


class SourceHashTests(unittest.TestCase):
    def test_parent_build_directory_names_do_not_hide_sources(self):
        with tempfile.TemporaryDirectory() as name:
            for parent in ("normal", "src", "pkg", "__pycache__"):
                with self.subTest(parent=parent):
                    root = Path(name) / parent / "checkout"
                    recipe = root / "builder/server/runtime"
                    (recipe / "bin").mkdir(parents=True)
                    lock = root / "builder/server/runtime-upstream.lock.json"
                    lock.write_text("{}")
                    code = recipe / "bin/omarchy-update"
                    code.write_text("original")
                    (recipe / "pkg").mkdir()
                    (recipe / "pkg/generated").write_text("not an input")
                    with patch.object(repository, "ROOT", root):
                        before = repository.source_files()
                        code.write_text("modified")
                        after = repository.source_files()
                    self.assertIn("builder/server/runtime/bin/omarchy-update", before)
                    self.assertNotIn("builder/server/runtime/pkg/generated", before)
                    self.assertNotEqual(before, after)

class FixtureSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.packages = Path(self.temp.name)
        (self.packages / "current").mkdir()
        self.name = "omarchy-server-0.2.0-1-any.pkg.tar.zst"
        self.archive = self.packages / "current" / self.name
        self.archive.write_bytes(b"selected package")
        self.archive.with_name(self.name + ".sig").write_bytes(b"signature")
        self.manifest = {
            "package": self.name,
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
        }
        self.save()

    def save(self):
        (self.packages / "maintenance.json").write_text(json.dumps(self.manifest))

    def test_retained_versions_do_not_change_selected_package(self):
        stale = self.packages / "current/omarchy-server-0.1.1-1-any.pkg.tar.zst"
        stale.write_bytes(b"retained old package")
        self.assertEqual(maintenance_package(self.packages), self.name)
        self.assertEqual(stale.read_bytes(), b"retained old package")

    def test_modified_or_unsigned_selected_package_is_refused(self):
        self.archive.write_bytes(b"changed package")
        with self.assertRaisesRegex(ValueError, "manifest"):
            maintenance_package(self.packages)
        self.archive.write_bytes(b"selected package")
        self.archive.with_name(self.name + ".sig").unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            maintenance_package(self.packages)

    def test_manifest_cannot_select_outside_current_directory(self):
        self.manifest["package"] = "../" + self.name
        self.save()
        with self.assertRaisesRegex(ValueError, "Invalid"):
            maintenance_package(self.packages)

class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runs = self.root / "test-runs/server"
        self.base = self.runs / "base"
        self.base.mkdir(parents=True)
        (self.base / "key").write_text("private-test-key")
        self.root_patcher = patch.object(qemu, "ROOT", self.root)
        self.root_patcher.start()
        self.addCleanup(self.root_patcher.stop)
        subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", str(self.base / "system.qcow2"), "16M"], check=True)

    def test_manual_copy_keeps_baseline_unchanged(self):
        disk = self.base / "system.qcow2"
        before = disk.read_bytes()
        work = qemu.working_copy(self.base, "check")
        info = json.loads(subprocess.check_output(["qemu-img", "info", "--output=json", str(work / "system.qcow2")], text=True))
        self.assertEqual(Path(info["full-backing-filename"]), disk)
        self.assertEqual(disk.read_bytes(), before)
        self.assertFalse((work / "acceptance-pass.json").exists())
        self.assertEqual(work.stat().st_mode & 0o777, 0o700)
        qemu.require_owned_leaf(work)

    def test_in_place_refuses_a_backing_disk(self):
        qemu.working_copy(self.base, "dependent")
        with self.assertRaisesRegex(ValueError, "backing file"):
            qemu.require_owned_leaf(self.base)

    def test_in_place_refuses_accepted_or_compacted_disks(self):
        for marker in ("acceptance-pass.json", "compaction.json"):
            (self.base / marker).write_text("{}")
            with self.assertRaisesRegex(ValueError, "new overlay"):
                qemu.require_owned_leaf(self.base)
            (self.base / marker).unlink()

    def test_in_place_refuses_aliases_to_accepted_disk(self):
        (self.base / "acceptance-pass.json").write_text("{}")
        disk = self.base / "system.qcow2"
        for name, link in (("symlink", Path.symlink_to), ("hardlink", Path.hardlink_to)):
            with self.subTest(name=name):
                alias = self.runs / name
                alias.mkdir()
                link(alias / "system.qcow2", disk)
                with self.assertRaisesRegex(ValueError, "without aliases"):
                    qemu.require_owned_leaf(alias)

    def test_negative_assertion_aborts_guest_shell(self):
        for command, expected in (("true", 1), ("false", 0)):
            with self.subTest(command=command):
                result = subprocess.run(
                    ["bash", "-euc", qemu.GUEST_ASSERTIONS + f"expect_failure {command}\nprintf REACHED"],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, expected)
                self.assertEqual(result.stdout, "" if expected else "REACHED")

    def test_media_does_not_inherit_build_epoch(self):
        old = self.root / "live.iso"
        tree = self.root / "live"
        tree.mkdir()
        (tree / "file").write_text("old epoch")
        env = dict(os.environ, SOURCE_DATE_EPOCH="1793917419")
        subprocess.run(["xorriso", "-as", "mkisofs", "-quiet", "-o", str(old), str(tree)], env=env, check=True, capture_output=True)
        with patch.dict(os.environ, {"SOURCE_DATE_EPOCH": "1793917419"}):
            qemu.seed(self.root / "seed.iso", "cidata", {"file": "new seed"}, avoid=old)
        self.assertNotEqual(qemu.media_uuid(old), qemu.media_uuid(self.root / "seed.iso"))

    def test_uuid_collision_is_refused_before_boot(self):
        with patch.object(qemu, "run"), patch.object(qemu, "media_uuid", return_value="collision"):
            with self.assertRaisesRegex(ValueError, "distinct filesystem UUIDs"):
                qemu.seed(self.root / "seed.iso", "cidata", {"file": "seed"}, avoid=self.root / "live.iso")


class RepositoryFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        listed = {}
        for name in ("omarchy-server.db", "omarchy-server.files"):
            for suffix in ("", ".sig"):
                path = self.repo / (name + suffix)
                path.write_bytes(b"signed fixture")
                listed[path.name] = repository.sha(path)
        (self.repo / "repository.json").write_text(json.dumps({
            "maintainer_fingerprint": repository.FINGERPRINT,
            "source_dirty": False,
            "source_files": {},
            "files": listed,
        }))
        (self.repo / "repository.json.sig").touch()
        self.signature = patch.object(repository, "pinned_signature").start()
        patch.object(repository, "source_files", return_value={}).start()
        self.addCleanup(patch.stopall)

    def test_backup_suffix_cannot_bypass_manifest(self):
        (self.repo / "old.pkg.tar.zst.backup").write_text("unsigned")
        with self.assertRaisesRegex(ValueError, "Unlisted repository file"):
            repository.verify(self.repo)

    def test_historical_archive_requires_and_verifies_signature(self):
        archive = self.repo / "old-1-1-any.pkg.tar.zst"
        archive.touch()
        with self.assertRaisesRegex(ValueError, "Unlisted repository file"):
            repository.verify(self.repo)
        signature = archive.with_name(archive.name + ".sig")
        signature.touch()
        approved = repository.verify(self.repo)
        self.signature.assert_any_call(archive)
        self.assertIn(archive, approved)
        self.assertIn(signature, approved)

if __name__ == "__main__":
    unittest.main()
