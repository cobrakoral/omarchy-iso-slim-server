#!/usr/bin/env python3
"""Archive admission must reject writes outside the defined application restore."""

import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from operational_guest import validate_tar


class RecoveryArchiveTests(unittest.TestCase):
    def check(self, entries, accepted=False):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "backup.tar"
            with tarfile.open(path, "w") as archive:
                manifest = tarfile.TarInfo("MANIFEST.json")
                manifest.size = 2
                archive.addfile(manifest, io.BytesIO(b"{}"))
                for entry in entries:
                    archive.addfile(
                        entry, io.BytesIO(bytes(entry.size)) if entry.isfile() else None
                    )
            if accepted:
                validate_tar(path)
            else:
                with self.assertRaises(ValueError):
                    validate_tar(path)

    def test_absolute_path(self):
        self.check([tarfile.TarInfo("/etc/shadow")])

    def test_parent_traversal(self):
        self.check([tarfile.TarInfo("opt/server-acceptance/../../etc/shadow")])

    def test_unrelated_identity_file(self):
        self.check([tarfile.TarInfo("etc/machine-id")])

    def test_file_scope_cannot_be_a_parent(self):
        self.check([tarfile.TarInfo("etc/server-acceptance.json/escape")])

    def test_escaping_symlink(self):
        entry = tarfile.TarInfo("opt/server-acceptance/escape")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "../../etc/shadow"
        self.check([entry])

    def test_escaping_hardlink(self):
        entry = tarfile.TarInfo("opt/server-acceptance/escape")
        entry.type = tarfile.LNKTYPE
        entry.linkname = "/etc/shadow"
        self.check([entry])

    def test_device_node(self):
        entry = tarfile.TarInfo("opt/server-acceptance/device")
        entry.type = tarfile.CHRTYPE
        self.check([entry])

    def test_duplicate_member(self):
        self.check([tarfile.TarInfo("etc/server-acceptance.json")] * 2)

    def test_relative_link_within_scope(self):
        entry = tarfile.TarInfo("var/lib/server-acceptance/documents/latest")
        entry.type = tarfile.SYMTYPE
        entry.linkname = "note.txt"
        self.check([entry], accepted=True)


if __name__ == "__main__":
    unittest.main()
