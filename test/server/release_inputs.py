#!/usr/bin/env python3
"""Regressions for preserving immutable outputs and truthful cold-cache inputs."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "build_inputs", ROOT / "builder/server/build-inputs.py"
)
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)
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
        self.assertEqual(list(self.cache.iterdir()), [])

    def test_explicit_warm_generation_preserves_old_archives(self):
        legacy = self.root / ".server-work/cache/mirror"
        legacy.mkdir(parents=True)
        old = legacy / "a.pkg"
        old.write_bytes(b"archive")
        record = self.prepare(empty_cache=False)
        self.assertTrue(record["legacy_cache_seeded"])
        self.assertEqual(old.stat().st_ino, (self.cache / "mirror/a.pkg").stat().st_ino)

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


if __name__ == "__main__":
    unittest.main()
