#!/usr/bin/env python3
"""Exercise migration durability, classification and immutable applied IDs."""

import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    path = ROOT / "builder/server/runtime/bin" / name
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


migration = load("omarchy-server-migrate")
repository = load("omarchy-server-repository")


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / "scripts"
        self.base.mkdir()
        self.state = self.root / "state"
        self.proof = self.root / "proof"
        self.entries = []

    def script(self, name="0001-example", scope="server", content=None):
        path = self.base / (name + ".sh")
        path.write_text(content or "printf x >> " + shlex.quote(str(self.proof)) + "\n")
        entry = {
            "id": name,
            "scope": scope,
            "file": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "idempotent": True,
        }
        self.entries.append(entry)
        self.save()
        return entry

    def save(self):
        (self.base / "manifest.json").write_text(json.dumps(self.entries))

    def test_runs_once_and_records_hash(self):
        entry = self.script()
        migration.migrate(self.base, self.state)
        migration.migrate(self.base, self.state)
        self.assertEqual(self.proof.read_text(), "x")
        self.assertEqual(
            json.loads((self.state / "0001-example.json").read_text())["sha256"],
            entry["sha256"],
        )
        self.assertFalse((self.state / "pending.json").exists())

    def test_desktop_never_executes_shared_and_server_do(self):
        self.script("0001-desktop", "desktop")
        self.script("0002-shared", "shared")
        self.script("0003-server", "server")
        migration.migrate(self.base, self.state)
        self.assertEqual(self.proof.read_text(), "xx")
        self.assertFalse((self.state / "0001-desktop.json").exists())

    def test_validate_all_hashes_before_first_execution(self):
        self.script()
        self.script("0002-invalid")["sha256"] = "0" * 64
        self.save()
        with self.assertRaises(ValueError):
            migration.migrate(self.base, self.state)
        self.assertFalse(self.proof.exists())

    def test_applied_migration_change_is_refused(self):
        entry = self.script()
        migration.migrate(self.base, self.state)
        path = self.base / entry["file"]
        path.write_text("exit 0\n")
        entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.save()
        with self.assertRaisesRegex(ValueError, "Applied migration was changed"):
            migration.migrate(self.base, self.state)
        self.assertEqual(self.proof.read_text(), "x")

    def test_failure_retains_journal_and_idempotent_retry(self):
        flag = shlex.quote(str(self.root / "first-attempt"))
        self.script(
            content="if ! test -f " + flag + "; then touch " + flag + "; exit 1; fi\n"
        )
        with self.assertRaises(subprocess.CalledProcessError):
            migration.migrate(self.base, self.state)
        self.assertTrue((self.state / "pending.json").exists())
        self.assertFalse((self.state / "0001-example.json").exists())
        migration.migrate(self.base, self.state)
        self.assertTrue((self.state / "0001-example.json").exists())
        self.assertFalse((self.state / "pending.json").exists())

    def test_pending_does_not_apply_or_write_state(self):
        self.script()
        migration.migrate(self.base, self.state, pending=True)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.proof.exists())

    def test_outside_path_refused(self):
        entry = self.script()
        outside = self.root / "outside"
        outside.write_text("exit 0\n")
        (self.base / entry["file"]).unlink()
        (self.base / entry["file"]).symlink_to(outside)
        self.save()
        with self.assertRaises(ValueError):
            migration.migrate(self.base, self.state)

    def test_duplicate_out_of_order_unknown_scope_and_unreviewed(self):
        for kind in ("duplicate", "order", "scope", "idempotence"):
            with self.subTest(kind=kind):
                self.entries = []
                self.script()
                if kind == "duplicate":
                    self.entries.append(dict(self.entries[0]))
                elif kind == "order":
                    self.script("0000-before")
                elif kind == "scope":
                    self.entries[0]["scope"] = "automatic"
                else:
                    self.entries[0]["idempotent"] = False
                self.save()
                with self.assertRaises(ValueError):
                    migration.migrate(self.base, self.state)

    def test_cli_refuses_desktop_before_execution(self):
        with patch.object(migration.os, "geteuid", return_value=0), patch.object(
            migration, "EDITION", self.root / "edition"
        ), patch.object(migration.sys, "argv", ["omarchy-server-migrate"]):
            (self.root / "edition").write_text("desktop\n")
            with self.assertRaisesRegex(ValueError, "server edition"):
                migration.main()


class RepositoryPolicyTests(unittest.TestCase):
    def test_config_requires_both_signatures_and_trusted_keys(self):
        text = repository.repository_config("https://example.invalid/repo/$arch")
        self.assertIn("PackageRequired DatabaseRequired TrustedOnly", text)

    def test_reject_url_injection_or_unencrypted_transport(self):
        for url in (
            "http://example.invalid",
            "https://user:secret@example.invalid",
            "https://example.invalid/\nSigLevel=Never",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                repository.repository_config(url)

    def test_reject_weaker_effective_pacman_policy(self):
        for output in (
            "PackageRequired PackageTrustedOnly DatabaseOptional DatabaseTrustedOnly",
            "PackageOptional PackageTrustAll DatabaseOptional DatabaseTrustAll",
        ):
            with patch.object(
                repository.subprocess, "check_output", return_value=output
            ):
                with self.assertRaises(ValueError):
                    repository.check()


if __name__ == "__main__":
    unittest.main()
