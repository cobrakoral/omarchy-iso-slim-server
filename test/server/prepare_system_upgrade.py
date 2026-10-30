#!/usr/bin/env python3
"""Stage signed real package transitions; never change the host package database."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
from qemu import ROOT, run

URLS = {
    "old": [
        f"https://archive.archlinux.org/packages/s/{name}/{name}-262-1-x86_64.pkg.tar.zst"
        for name in ("systemd", "systemd-libs", "systemd-sysvcompat", "systemd-ukify")
    ]
    + [
        "https://archive.archlinux.org/packages/l/limine/limine-12.9.3-1-x86_64.pkg.tar.zst"
    ],
    "current": [
        "https://pkgs.omarchy.org/edge/x86_64/linux-omarchy-7.2.8-10-x86_64.pkg.tar.zst"
    ],
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory", type=Path, default=ROOT / ".server-work/system-upgrade"
    )
    opts = parser.parse_args()
    directory = opts.directory.resolve()
    if not directory.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use this project's isolated build workspace")
    package_root = directory / "packages"
    provenance = []
    for side, urls in URLS.items():
        destination = package_root / side
        destination.mkdir(parents=True, exist_ok=True)
        for url in urls:
            for suffix in ("", ".sig"):
                path = destination / (url.rsplit("/", 1)[1] + suffix)
                if not path.exists():
                    print(f"Downloading {url}{suffix}", flush=True)
                    temporary = path.with_name(path.name + ".part")
                    with urllib.request.urlopen(
                        url + suffix, timeout=120
                    ) as source, temporary.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    temporary.rename(path)
                provenance.append(
                    {
                        "url": url + suffix,
                        "file": str(path.relative_to(package_root)),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
    current = package_root / "current"
    for source in (ROOT / ".server-work/cache/mirror").glob("*.pkg.tar.zst"):
        # Upstream packages must retain their detached signatures. Custom packages
        # are added separately and signed by the configured maintainer identity.
        if source.with_name(source.name + ".sig").exists():
            for item in (source, source.with_name(source.name + ".sig")):
                target = current / item.name
                if not target.exists():
                    os.link(item, target)
    runtime = directory / "runtime"
    runtime.mkdir(exist_ok=True)
    shutil.copytree(
        ROOT / "builder/server/runtime",
        runtime,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    run("makepkg", "--nodeps", "--noconfirm", "--force", cwd=runtime)
    archives = list(runtime.glob("omarchy-server-*.pkg.tar.zst"))
    if len(archives) != 1:
        raise ValueError("Expected one newly built server maintenance package")
    signing_key = subprocess.check_output(
        ["git", "-C", str(ROOT), "config", "user.signingkey"], text=True
    ).strip()
    archive = archives[0]
    run(
        "gpg",
        "--batch",
        "--yes",
        "--local-user",
        signing_key,
        "--detach-sign",
        str(archive),
    )
    for source in (archive, archive.with_name(archive.name + ".sig")):
        shutil.copy2(source, current / source.name)
    with (package_root / "MAINTAINER.asc").open("wb") as public_key:
        subprocess.run(
            ["gpg", "--armor", "--export", signing_key], stdout=public_key, check=True
        )
    # Keep the highest package version even when lexical filename order differs.
    packages = sorted(current.glob("*.pkg.tar.zst"))
    run(
        "repo-add",
        "--prevent-downgrade",
        "--include-sigs",
        str(current / "server-upgrade.db.tar.gz"),
        *map(str, packages),
    )
    expected = {"linux-omarchy": "7.2.8-10", "systemd": "262.1-1", "limine": "12.9.4-1"}
    actual = {}
    with tarfile.open(current / "server-upgrade.db.tar.gz") as database:
        for entry in database:
            if entry.name.endswith("/desc"):
                fields = dict(
                    part.split("\n", 1)
                    for part in database.extractfile(entry)
                    .read()
                    .decode()
                    .strip()
                    .split("\n\n")
                )
                actual[fields["%NAME%"]] = fields["%VERSION%"]
    for name, version in expected.items():
        if actual.get(name) != version:
            raise ValueError(f"Staged {name} is {actual.get(name)}, expected {version}")
    (package_root / "provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    print(package_root, flush=True)


if __name__ == "__main__":
    main()
