#!/usr/bin/env python3
"""Sign an already accepted local server ISO, without publishing it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

FINGERPRINT = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def validated_release(directory):
    """Return the ISO only after proving this is an accepted release generation."""
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Specify a real server release directory")
    receipt = json.loads((directory / "server-release-acceptance.json").read_text())
    inputs = json.loads((directory / "server-build-inputs.json").read_text())
    if (
        receipt.get("passed") is not True
        or inputs.get("source_dirty") is not False
        or receipt.get("source_revision") != inputs.get("source_revision")
        or receipt.get("source_revision") != (directory / "server-source-revision.txt").read_text().strip()
        or (directory / "server-source-status.txt").read_text().strip()
        or not {"base", "bios", "cloud", "luks"}.issubset(receipt.get("installs", {}))
    ):
        raise ValueError("Only a clean, fully accepted server release can be signed")
    images = list(directory.glob("omarchy-slim-server-unofficial-*.iso"))
    if len(images) != 1 or images[0].is_symlink() or not images[0].is_file():
        raise ValueError("Expected exactly one regular server ISO")
    iso = images[0]
    digest = sha256(iso)
    checksum = f"{digest}  {iso.name}\n"
    if (
        Path(receipt.get("iso", "")).resolve() != iso.resolve()
        or receipt.get("iso_sha256") != digest
        or (directory / "SHA256SUMS").read_text() != checksum
    ):
        raise ValueError("ISO digest or accepted artifact path does not match the receipt")
    return iso, checksum


def run(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True)


def sign(directory):
    iso, checksum = validated_release(directory)
    artifacts = [
        iso.name + ".sha256",
        iso.name + ".sig",
        "SHA256SUMS.asc",
        "server-release-acceptance.json.asc",
        "UNOFFICIAL-SIGNING-KEY.asc",
    ]
    if any((directory / name).exists() or (directory / name).is_symlink() for name in artifacts):
        raise ValueError("Signing outputs already exist; do not replace an accepted generation")
    with tempfile.TemporaryDirectory(prefix=".server-sign-", dir=directory) as temporary:
        work = Path(temporary)
        (work / artifacts[0]).write_text(checksum)
        for source, target, armored in (
            (iso, work / artifacts[1], False),
            (directory / "SHA256SUMS", work / artifacts[2], True),
            (directory / "server-release-acceptance.json", work / artifacts[3], True),
        ):
            command = ["gpg", "--batch", "--local-user", FINGERPRINT]
            if armored:
                command.append("--armor")
            run(*command, "--detach-sign", "--output", str(target), str(source))
            result = run("gpg", "--batch", "--status-fd=1", "--verify", str(target), str(source))
            valid = any(
                line.startswith("[GNUPG:] VALIDSIG ")
                and FINGERPRINT in line.split()
                for line in result.stdout.splitlines()
            )
            if not valid:
                raise ValueError("Signature does not identify the pinned maintainer")
        (work / artifacts[4]).write_text(run("gpg", "--armor", "--export", FINGERPRINT).stdout)
        if not (work / artifacts[4]).stat().st_size:
            raise ValueError("Maintainer public key was not exported")
        for name in artifacts:
            os.replace(work / name, directory / name)
    print("SIGNED_ACCEPTED_SERVER_ISO", iso)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Fully qualified local server release directory")
    args = parser.parse_args()
    sign(args.directory.absolute())


if __name__ == "__main__":
    main()
