#!/usr/bin/env python3
"""Regressions for preserving immutable outputs and truthful cold-cache inputs."""

import errno
import importlib.util
import json
from pathlib import Path
import tempfile
import stat
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "build_inputs", ROOT / "builder/server/build-inputs.py"
)
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)

pkg_repo_spec = importlib.util.spec_from_file_location(
    "package_repository", ROOT / "builder/server/package-repository.py"
)
pkg_repo = importlib.util.module_from_spec(pkg_repo_spec)
pkg_repo_spec.loader.exec_module(pkg_repo)
IMAGE = "ghcr.io/archlinux/archlinux@sha256:" + "a" * 64


class BuildInputsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "source"
        self.root.mkdir()
        self.out = Path(self.temp.name) / "output"
        self.cache = Path(self.temp.name) / "cache"

        def git(root, *args):
            return "" if args == ("status", "--porcelain") else "a" * 40

        self.addCleanup(patch.stopall)
        patch.object(inputs, "git", side_effect=git).start()
        patch.object(inputs, "files", return_value={}).start()
        patch.object(
            inputs.shutil,
            "disk_usage",
            return_value=type("Space", (), {"free": 20 * 1024**3})(),
        ).start()

    def prepare(self, **kwargs):
        args = dict(
            root=self.root,
            output=self.out,
            cache=self.cache,
            image=IMAGE,
            epoch="100",
            empty_cache=True,
        )
        args.update(kwargs)
        return inputs.prepare(**args)

    def test_existing_release_is_never_modified(self):
        self.out.mkdir()
        original = self.out / "release.iso"
        original.write_bytes(b"original")
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(original.read_bytes(), b"original")
        self.assertFalse(self.cache.exists())

    def test_occupied_cache_is_refused_even_when_not_cold(self):
        self.cache.mkdir()
        (self.cache / "old").write_text("keep")
        with self.assertRaises(ValueError):
            self.prepare(empty_cache=False)
        self.assertEqual((self.cache / "old").read_text(), "keep")
        self.assertFalse(self.out.exists())

    def test_output_and_cache_cannot_overlap(self):
        with self.assertRaises(ValueError):
            self.prepare(cache=self.out / "cache")
        self.assertFalse(self.out.exists())

    def test_symlink_output_is_refused(self):
        target = self.out.with_name("target")
        target.mkdir()
        self.out.symlink_to(target)
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(list(target.iterdir()))

    def test_empty_cache_never_seeds_legacy_archives(self):
        legacy = self.root / ".server-work/cache/mirror"
        legacy.mkdir(parents=True)
        (legacy / "a.pkg").write_bytes(b"archive")
        record = self.prepare()
        self.assertTrue(record["empty_cache_requested"])
        self.assertFalse(record["legacy_cache_seeded"])
        self.assertEqual(
            list(self.cache.iterdir()),
            [self.cache / ".server-build-reservation"],
        )

    def test_explicit_warm_generation_preserves_old_archives(self):
        legacy = self.root / ".server-work/cache/mirror"
        legacy.mkdir(parents=True)
        old = legacy / "a.pkg"
        old.write_bytes(b"archive")
        record = self.prepare(empty_cache=False)
        self.assertTrue(record["legacy_cache_seeded"])
        self.assertEqual(old.stat().st_ino, (self.cache / "mirror/a.pkg").stat().st_ino)

    def test_warm_generation_copies_across_filesystems(self):
        legacy = self.root / ".server-work/cache/mirror"
        legacy.mkdir(parents=True)
        (legacy / "a.pkg").write_bytes(b"archive")
        with patch.object(inputs.os, "link", side_effect=OSError(errno.EXDEV, "cross-device")):
            record = self.prepare(empty_cache=False)
        self.assertTrue(record["legacy_cache_seeded"])
        self.assertEqual((self.cache / "mirror/a.pkg").read_bytes(), b"archive")

    def test_checks_destination_filesystems_before_reserving_paths(self):
        self.out.mkdir()
        self.cache.mkdir()
        real_stat = Path.stat

        def distinct_filesystems(path, **kwargs):
            result = real_stat(path, **kwargs)
            if path == self.cache:
                fields = list(result)
                fields[2] += 1
                return inputs.os.stat_result(fields)
            return result

        def space(path):
            self.assertIn(path, (self.out, self.cache))
            return type("Space", (), {"free": 4 * 1024**3})()

        with (
            patch.object(Path, "stat", distinct_filesystems),
            patch.object(inputs.shutil, "disk_usage", side_effect=space),
            self.assertRaisesRegex(ValueError, f"12 GiB free on {self.cache}"),
        ):
            self.prepare(output=self.out / "nested", cache=self.cache / "nested")
        self.assertFalse(any(self.out.iterdir()))
        self.assertFalse(any(self.cache.iterdir()))

    def test_dirty_sources_are_rejected_before_output_creation(self):
        inputs.git.side_effect = lambda root, *args: (
            " M source" if args == ("status", "--porcelain") else "a" * 40
        )
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.out.exists())

    def test_mutable_container_tag_is_rejected(self):
        with self.assertRaises(ValueError):
            self.prepare(image="archlinux:latest")
        self.assertFalse(self.out.exists())

    def test_changed_sources_cannot_qualify_a_finished_build(self):
        self.prepare()
        inputs.files.return_value = {"changed": {"sha256": "b" * 64}}
        with self.assertRaises(ValueError):
            inputs.verify(self.root, self.out)

    def _run_concurrent_prepare_contention(self, shared_path, target_key, distinct_paths):
        import threading
        barrier = threading.Barrier(2)
        original_unused = inputs.unused

        def gated_unused(path):
            original_unused(path)
            if path == shared_path:
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass

        results = []
        errors = []

        def worker(target_path):
            try:
                res = self.prepare(**{target_key: target_path})
                results.append((target_path, res))
            except Exception as e:
                errors.append((target_path, e))

        with patch.object(inputs, "unused", side_effect=gated_unused):
            t1 = threading.Thread(target=worker, args=(distinct_paths[0],))
            t2 = threading.Thread(target=worker, args=(distinct_paths[1],))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn("claimed", str(errors[0][1]))
        winning = results[0][0]
        losing = distinct_paths[0] if winning == distinct_paths[1] else distinct_paths[1]
        marker_name = ".server-build-reservation"
        self.assertFalse((losing / marker_name).exists())
        self.assertTrue((shared_path / marker_name).exists())
        self.assertTrue((winning / marker_name).exists())

    def test_concurrent_prepare_shared_output_different_caches_only_one_wins(self):
        cache2 = Path(self.temp.name) / "cache2"
        self._run_concurrent_prepare_contention(
            shared_path=self.out,
            target_key="cache",
            distinct_paths=(self.cache, cache2),
        )

    def test_concurrent_prepare_shared_cache_different_outputs_only_one_wins(self):
        out2 = Path(self.temp.name) / "output2"
        self._run_concurrent_prepare_contention(
            shared_path=self.cache,
            target_key="output",
            distinct_paths=(self.out, out2),
        )


class SourceArchiveTests(unittest.TestCase):
    def test_only_the_recorded_git_trees_are_staged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            cache = Path(directory) / "cache"
            cache.mkdir()
            for path in ("builder/build.sh", "configs/overlay", "archiso/configs/releng/profile"):
                source = root / path
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(path)
            for repo, paths in ((root, ("builder", "configs")), (root / "archiso", ("configs/releng",))):
                inputs.git(repo, "init", "-q")
                inputs.git(repo, "add", *paths)
            record = {
                "source_revision": inputs.git(root, "write-tree"),
                "archiso_revision": inputs.git(root / "archiso", "write-tree"),
                "source_files": inputs.files(root),
            }
            (root / "configs/ignored.pyc").touch()
            (root / "archiso/configs/releng/ignored.pyc").touch()
            inputs.stage_sources(root, cache, record)
            self.assertTrue((cache / "source/configs/overlay").is_file())
            self.assertTrue((cache / "source/archiso/configs/releng/profile").is_file())
            self.assertEqual(list((cache / "source").rglob("*.pyc")), [])

    def test_resume_requires_complete_production_manifest_and_archiso_pin(self):
        record = {"source_files": {"builder/original": {"sha256": "fixture"}},
                  "archiso_revision": "pinned"}
        root = Path("/fixture")
        working = dict(record["source_files"], **{"test/new.py": {}})
        with patch.object(inputs, "files", return_value=working), \
             patch.object(inputs, "git", side_effect=lambda root, *args: "" if args[0] == "status" else "pinned") as git:
            inputs.verify_production(root, record)
            working["configs/new"] = {}
            with self.assertRaisesRegex(ValueError, "exact production inputs"):
                inputs.verify_production(root, record)
            del working["configs/new"]
            git.side_effect = lambda root, *args: "changed"
            with self.assertRaisesRegex(ValueError, "archiso revision"):
                inputs.verify_production(root, record)

    def test_owned_case_path_requires_exact_run_scope(self):
        from release import owned_case_path

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = root / "test-runs"
            runs.mkdir()
            prefix = "release-test-base-"
            planned = runs / (prefix + "expected")
            decoy = runs / "standalone"
            decoy.mkdir()
            cases = {"base": {"directory": str(planned)}}
            self.assertIsNone(owned_case_path(cases, "cloud", prefix, runs))
            self.assertEqual(owned_case_path(cases, "base", prefix, runs), planned)
            planned.symlink_to(decoy, target_is_directory=True)
            for candidate in (planned, decoy, root / (prefix + "outside")):
                with self.subTest(candidate=candidate):
                    cases["base"]["directory"] = str(candidate)
                    with self.assertRaisesRegex(ValueError, "Invalid run-owned install directory"):
                        owned_case_path(cases, "base", prefix, runs)


class RunRecordProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.work = self.dir / "work"
        self.work.mkdir()
        self.output = self.dir / "output"
        self.output.mkdir()
        self.cache = self.dir / "cache"
        self.cache.mkdir()
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        self.iso = self.output / "server.iso"
        self.iso.write_bytes(b"iso-content")
        import release

        self.release = release
        self.source = "a" * 40
        self.iso_hash = release.sha(self.iso)

        # Create dummy logs and metadata files
        self.test_logs = {}
        for name in release.REQUIRED_TEST_LOGS:
            log_file = self.work / name
            log_file.write_text("log: " + name)
            self.test_logs[name] = release.sha(log_file)

        (self.repo / "repository.json").write_text("repo-json")
        self.repo_hash = release.sha(self.repo / "repository.json")
        (self.output / "server-repository-provenance.sha256").write_text(
            f"{self.repo_hash}  repository.json\n"
        )

        self.meta_hashes = {}
        for name in release.REQUIRED_METADATA_FILES:
            meta_file = self.output / name
            if not meta_file.exists():
                meta_file.write_text("meta: " + name)
            self.meta_hashes[name] = release.sha(meta_file)

        self.valid_record = {
            "version": 1,
            "work": str(self.work.resolve()),
            "output": str(self.output.resolve()),
            "cache": str(self.cache.resolve()),
            "source_revision": self.source,
            "test_logs": self.test_logs,
            "iso": str(self.iso.resolve()),
            "iso_sha256": self.iso_hash,
            "metadata_hashes": self.meta_hashes,
            "repository_json_sha256": self.repo_hash,
            "cases": {
                "base": {"directory": str(self.work / "install-base")}
            },
        }

    def test_load_and_save_run_record(self):
        self.release.save_run_record(self.work, self.valid_record)
        loaded = self.release.load_run_record(self.work)
        self.assertEqual(loaded, self.valid_record)

    def test_unbound_legacy_checkpoint_rejected(self):
        legacy = self.work / "release-checkpoint.json"
        legacy.write_text(json.dumps({"base": {"directory": "old"}}))
        with self.assertRaisesRegex(ValueError, "Unbound legacy release record is not resumable"):
            self.release.load_run_record(self.work)

    def test_missing_binding_keys_rejected_as_unbound_legacy_record(self):
        incomplete = dict(self.valid_record)
        del incomplete["source_revision"]
        (self.work / "release-run.json").write_text(json.dumps(incomplete))
        with self.assertRaisesRegex(ValueError, "Unbound legacy release record is not resumable"):
            self.release.load_run_record(self.work)

    def test_changed_test_source_rejected_on_resume(self):
        self.valid_record["source_revision"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "source revision changed before resume"):
            self.release.verify_resume_record(
                self.valid_record,
                self.work,
                self.output,
                self.cache,
                self.source,
                self.repo,
                self.iso,
                self.iso_hash,
            )

    def test_mismatched_test_log_rejected_on_resume(self):
        (self.work / "upstream-tests.log").write_text("tampered")
        with self.assertRaisesRegex(ValueError, "Required completed test log or build metadata missing or changed"):
            self.release.verify_resume_record(
                self.valid_record,
                self.work,
                self.output,
                self.cache,
                self.source,
                self.repo,
                self.iso,
                self.iso_hash,
            )

    def test_incomplete_hash_sets_are_rejected_on_resume(self):
        for field in ("test_logs", "metadata_hashes"):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "missing required evidence"):
                self.release.verify_resume_record(
                    self.valid_record | {field: {}},
                    self.work,
                    self.output,
                    self.cache,
                    self.source,
                    self.repo,
                    self.iso,
                    self.iso_hash,
                )

    def test_mismatched_repo_provenance_rejected_on_resume(self):
        (self.output / "server-repository-provenance.sha256").write_text("different repository.json\n")
        with self.assertRaisesRegex(ValueError, "does not match builder repository provenance"):
            self.release.verify_resume_record(
                self.valid_record,
                self.work,
                self.output,
                self.cache,
                self.source,
                self.repo,
                self.iso,
                self.iso_hash,
            )

    def test_mismatched_work_output_cache_rejected_on_resume(self):
        other = self.dir / "other"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "Resume work directory differs"):
            self.release.verify_resume_record(
                self.valid_record,
                other,
                self.output,
                self.cache,
                self.source,
                self.repo,
                self.iso,
                self.iso_hash,
            )

    def test_lock_work_prevents_concurrent_resume(self):
        fd1 = self.release.lock_work(self.work)
        try:
            with self.assertRaisesRegex(RuntimeError, "Private work directory is locked"):
                self.release.lock_work(self.work)
        finally:
            import os
            os.close(fd1)


class ReceiptBindingTests(unittest.TestCase):
    def setUp(self):
        import copy
        import hashlib
        import release

        self.release = release
        self.receipt = {
            "iso_sha256": "a" * 64,
            "source_dirty": False,
            "packages": {"systemd": "262.1-1"},
            "source_revision": "b" * 40,
            "harness_sha256": hashlib.sha256(b"committed harness").hexdigest(),
        }
        self.patcher = patch.object(
            release.subprocess, "check_output", return_value=b"committed harness"
        )
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def check(self):
        self.release.validate_receipt(self.receipt, "a" * 64, {"systemd": "262.1-1"})

    def test_committed_receipt_can_be_reused_by_a_later_qualifier(self):
        self.check()

    def test_substituted_iso_is_refused(self):
        self.receipt["iso_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            self.check()

    def test_dirty_test_source_is_refused(self):
        self.receipt["source_dirty"] = True
        with self.assertRaises(ValueError):
            self.check()

    def test_different_package_versions_are_refused(self):
        self.receipt["packages"] = {"systemd": "262"}
        with self.assertRaises(ValueError):
            self.check()

    def test_harness_hash_must_match_the_recorded_commit(self):
        self.receipt["harness_sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            self.check()


class PackageRepositorySignatureTests(unittest.TestCase):
    def test_validsig_requires_good_status_in_same_signature(self):
        pin = pkg_repo.FINGERPRINT
        good = "[GNUPG:] GOODSIG 1234 Maintainer"
        valid = f"[GNUPG:] VALIDSIG {'A' * 40} 2026-11-08 1 0 4 0 22 8 00 {pin}"
        start = "[GNUPG:] NEWSIG"
        for statuses in (
            (start, "[GNUPG:] REVKEYSIG 1234 Maintainer", valid),
            (start, "[GNUPG:] EXPKEYSIG 1234 Maintainer", valid),
            (start, good, start, valid),
        ):
            result = subprocess.CompletedProcess([], 0, "\n".join(statuses))
            with (
                self.subTest(statuses=statuses),
                patch.object(pkg_repo.subprocess, "run", return_value=result),
                self.assertRaisesRegex(ValueError, "No good signature"),
            ):
                pkg_repo.pinned_signature(Path("test"))
        result = subprocess.CompletedProcess([], 0, "\n".join((start, good, valid)))
        with patch.object(pkg_repo.subprocess, "run", return_value=result):
            pkg_repo.pinned_signature(Path("test"))


class PackageRepositoryStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / "source_repo"
        self.source.mkdir()
        self.dest = Path(self.temp.name) / "staged_repo"

    def test_stage_verifies_staged_copy_independently(self):
        (self.source / "repository.json").write_text("content")
        verified_path = None

        def fake_verify(repo):
            nonlocal verified_path
            verified_path = repo
            return []

        with patch.object(pkg_repo, "verify", side_effect=fake_verify):
            staged = pkg_repo.stage(self.source, self.dest)

        self.assertEqual(staged, self.dest.resolve())
        self.assertEqual(verified_path, self.dest.resolve())
        self.assertEqual((self.dest / "repository.json").read_text(), "content")
        # Modify source, verify staged copy remains independent
        (self.source / "repository.json").write_text("tampered")
        self.assertEqual((self.dest / "repository.json").read_text(), "content")

    def test_stage_rejects_source_symlinks_before_copying(self):
        target = self.source / "target_file"
        target.write_text("data")
        (self.source / "link").symlink_to(target)

        with self.assertRaises(ValueError) as ctx:
            pkg_repo.stage(self.source, self.dest)
        self.assertIn("Expected regular repository file to stage: link", str(ctx.exception))
        self.assertFalse(self.dest.exists())

    def test_stage_refuses_existing_or_symlinked_destination(self):
        self.dest.mkdir()
        with self.assertRaises(ValueError):
            pkg_repo.stage(self.source, self.dest)

        dangling = Path(self.temp.name) / "dangling_link"
        dangling.symlink_to(Path(self.temp.name) / "nonexistent")
        with self.assertRaises(ValueError):
            pkg_repo.stage(self.source, dangling)

    def test_stage_refuses_destination_inside_source(self):
        inside = self.source / "nested_stage"
        with self.assertRaises(ValueError):
            pkg_repo.stage(self.source, inside)

    def test_stage_sets_private_mode(self):
        (self.source / "repository.json").write_text("content")
        with patch.object(pkg_repo, "verify", return_value=[]):
            staged = pkg_repo.stage(self.source, self.dest)
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)


class CompactionJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.disk = self.dir / "system.qcow2"
        self.target = self.dir / "system.compacted.qcow2"
        self.journal = self.dir / "compaction-journal.json"
        self.receipt = self.dir / "compaction.json"

    def test_interrupted_atomic_receipt_can_be_retried(self):
        import release

        final = self.dir / "server-release-acceptance.json"
        with (
            patch.object(Path, "replace", side_effect=OSError("interrupted")),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            release._write_atomic(final, '{"partial": true}\n')
        self.assertFalse(final.exists())
        release._write_atomic(final, '{"passed": true}\n')
        self.assertEqual(json.loads(final.read_text()), {"passed": True})

    def test_stale_acceptance_digest_blocks_compaction(self):
        import release

        self.disk.write_text("original")
        expected = release.sha(self.disk)
        self.disk.write_text("modified since acceptance")
        with (
            patch.object(release.subprocess, "check_output", return_value="{}"),
            patch.object(release.subprocess, "run") as qemu_img,
            self.assertRaisesRegex(ValueError, "changed after testing"),
        ):
            release.compact(self.dir, expected_sha256=expected)
        self.assertNotIn("convert", [call.args[0][1] for call in qemu_img.call_args_list])
        self.assertFalse(self.journal.exists())
        self.assertFalse(self.target.exists())

    def test_prepared_compaction_recovers_with_tested_digest(self):
        import release

        self.disk.write_text("original")
        self.target.write_text("compacted")
        expected = release.sha(self.disk)
        prepared = {
            "original_sha256": expected,
            "compacted_sha256": release.sha(self.target),
            "backing_file": None,
        }
        self.journal.write_text(json.dumps({"original_sha256": expected, "receipt": prepared}))
        with self.assertRaisesRegex(ValueError, "differs from the tested disk digest"):
            release.compact(self.dir, expected_sha256="0" * 64)
        self.assertEqual(self.disk.read_text(), "original")
        self.assertEqual(release.compact(self.dir, expected_sha256=expected), prepared)
        self.assertEqual(self.disk.read_text(), "compacted")
        self.assertEqual(release.compact(self.dir, expected_sha256=expected), prepared)

    def test_conversion_journal_recovers_partial_target(self):
        import release

        self.disk.write_text("original")
        attempts = iter(("partial", "compacted"))

        def convert(command, **kwargs):
            if command[1] == "convert":
                self.assertFalse(self.target.exists())
                contents = next(attempts)
                self.target.write_text(contents)
                if contents == "partial":
                    raise subprocess.CalledProcessError(1, command)

        with (
            patch.object(release.subprocess, "check_output", return_value="{}"),
            patch.object(release.subprocess, "run", side_effect=convert),
            patch.object(release.os, "sync"),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                release.compact(self.dir)
            self.assertEqual(self.disk.read_text(), "original")
            self.assertEqual(self.target.read_text(), "partial")
            self.assertEqual(json.loads(self.journal.read_text())["stage"], "converting")
            release.compact(self.dir)

        self.assertEqual(self.disk.read_text(), "compacted")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.journal.exists())

    def test_conversion_journal_keeps_unowned_target(self):
        import release

        self.disk.write_text("original")
        self.target.write_text("unowned")
        with self.assertRaisesRegex(ValueError, "target already exists"):
            release.compact(self.dir)
        self.assertEqual(self.disk.read_text(), "original")
        self.assertEqual(self.target.read_text(), "unowned")
        self.assertFalse(self.journal.exists())

    def test_conversion_journal_requires_matching_original_and_backing(self):
        import release

        self.disk.write_text("original")
        self.target.write_text("partial")
        valid = {
            "stage": "converting",
            "original_sha256": release.sha(self.disk),
            "backing_file": None,
        }
        for changed in ({"original_sha256": "different"}, {"backing_file": "other"},
                        {"stage": "unknown"}):
            with self.subTest(changed=changed):
                self.journal.write_text(json.dumps(valid | changed))
                with self.assertRaisesRegex(ValueError, "conversion journal mismatch"):
                    release.compact(self.dir)
                self.assertEqual(self.target.read_text(), "partial")

    def test_reconcile_interrupted_before_disk_replacement(self):
        import release
        self.disk.write_text("original_disk_bytes")
        self.target.write_text("prepared_compacted_bytes")
        orig_sha = release.sha(self.disk)
        prep_sha = release.sha(self.target)
        record = {
            "virtual_bytes_compared_equal": True,
            "allocated_before": 1024,
            "allocated_after": 512,
            "compacted_sha256": prep_sha,
            "backing_file": None,
        }
        journal_data = {
            "original_sha256": orig_sha,
            "receipt": record,
        }
        self.journal.write_text(json.dumps(journal_data))

        res = release.compact(self.dir, backing=None)
        self.assertEqual(res, record)
        self.assertEqual(self.disk.read_text(), "prepared_compacted_bytes")
        self.assertFalse(self.target.exists())
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.receipt.is_file())
        self.assertEqual(json.loads(self.receipt.read_text()), record)

    def test_reconcile_interrupted_after_disk_replacement_before_receipt(self):
        import release
        self.disk.write_text("prepared_compacted_bytes")
        prep_sha = release.sha(self.disk)
        record = {
            "virtual_bytes_compared_equal": True,
            "allocated_before": 1024,
            "allocated_after": 512,
            "compacted_sha256": prep_sha,
            "backing_file": "/path/to/base.qcow2",
        }
        journal_data = {
            "original_sha256": "some_prior_hash",
            "receipt": record,
        }
        self.journal.write_text(json.dumps(journal_data))

        res = release.compact(self.dir, backing="/path/to/base.qcow2")
        self.assertEqual(res, record)
        self.assertEqual(self.disk.read_text(), "prepared_compacted_bytes")
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.receipt.is_file())
        self.assertEqual(json.loads(self.receipt.read_text()), record)

    def test_reconcile_refuses_corrupted_disk_bytes_and_preserves_evidence(self):
        import release
        self.disk.write_text("corrupted_disk_bytes")
        self.target.write_text("prepared_compacted_bytes")
        prep_sha = release.sha(self.target)
        journal_data = {
            "original_sha256": "expected_original_hash",
            "receipt": {
                "virtual_bytes_compared_equal": True,
                "allocated_before": 1024,
                "allocated_after": 512,
                "compacted_sha256": prep_sha,
                "backing_file": None,
            },
        }
        self.journal.write_text(json.dumps(journal_data))

        with self.assertRaises(ValueError) as ctx:
            release.compact(self.dir, backing=None)
        self.assertIn("preserving evidence", str(ctx.exception))
        self.assertTrue(self.journal.is_file())
        self.assertTrue(self.disk.is_file())
        self.assertTrue(self.target.is_file())
        self.assertFalse(self.receipt.exists())

    def test_reconcile_preserves_unexpected_target_when_disk_already_swapped(self):
        import release
        self.disk.write_text("prepared_compacted_bytes")
        self.target.write_text("unexpected_leftover_target")
        prep_sha = release.sha(self.disk)
        journal_data = {
            "original_sha256": "expected_original_hash",
            "receipt": {
                "virtual_bytes_compared_equal": True,
                "allocated_before": 1024,
                "allocated_after": 512,
                "compacted_sha256": prep_sha,
                "backing_file": None,
            },
        }
        self.journal.write_text(json.dumps(journal_data))

        with self.assertRaises(ValueError) as ctx:
            release.compact(self.dir, backing=None)
        self.assertIn("preserving evidence", str(ctx.exception))
        self.assertTrue(self.journal.is_file())
        self.assertTrue(self.disk.is_file())
        self.assertTrue(self.target.is_file())
        self.assertFalse(self.receipt.exists())

    def test_reconcile_refuses_missing_target_with_unswapped_disk(self):
        import release
        self.disk.write_text("original_unswapped_bytes")
        orig_sha = release.sha(self.disk)
        journal_data = {
            "original_sha256": orig_sha,
            "receipt": {
                "virtual_bytes_compared_equal": True,
                "allocated_before": 1024,
                "allocated_after": 512,
                "compacted_sha256": "different_compacted_hash",
                "backing_file": None,
            },
        }
        self.journal.write_text(json.dumps(journal_data))

        with self.assertRaises(ValueError) as ctx:
            release.compact(self.dir, backing=None)
        self.assertIn("preserving evidence", str(ctx.exception))
        self.assertTrue(self.journal.is_file())
        self.assertTrue(self.disk.is_file())
        self.assertFalse(self.receipt.exists())


if __name__ == "__main__":
    unittest.main()
