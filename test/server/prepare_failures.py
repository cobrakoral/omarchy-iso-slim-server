#!/usr/bin/env python3
"""Stage signed fault fixtures and the current maintenance package offline."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
from qemu import ROOT, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--directory", type=Path, default=ROOT / ".server-work/failure-tests"
    )
    opts = parser.parse_args()
    out = opts.directory.resolve()
    if not out.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use the isolated project package workspace")
    share = out / "packages"
    (share / "stable").mkdir(parents=True, exist_ok=True)
    (share / "old").mkdir(exist_ok=True)
    key = subprocess.check_output(
        ["git", "-C", str(ROOT), "config", "user.signingkey"], text=True
    ).strip()
    for version in (1, 2):
        build = out / f"fixture-{version}"
        build.mkdir(exist_ok=True)
        (build / "blob").write_bytes(random.Random(version).randbytes(2 * 1024**2))
        (build / "PKGBUILD").write_text(f"""pkgname=omarchy-server-fault-fixture
pkgver={version}
pkgrel=1
arch=('any')
license=('MIT')
options=('!debug' '!strip')
package() {{
  install -Dm644 "$startdir/blob" "$pkgdir/usr/share/omarchy-server-fault-fixture/blob"
  printf '%s\\n' "$pkgver" | install -Dm644 /dev/stdin "$pkgdir/usr/share/omarchy-server-fault-fixture/version"
}}
""")
        run("makepkg", "--nodeps", "--noconfirm", "--force", cwd=build)
        archive = next(build.glob("*.pkg.tar.zst"))
        run("gpg", "--batch", "--yes", "-u", key, "--detach-sign", str(archive))
        target = share / ("old" if version == 1 else "stable")
        for source in (archive, archive.with_name(archive.name + ".sig")):
            shutil.copy2(source, target / source.name)
    mirror = ROOT / ".server-work/cache/mirror"
    if not list(mirror.glob("linux-omarchy-*.pkg.tar.zst")):
        raise ValueError("Keep the baseline build's offline package mirror")
    for archive in mirror.glob("*.pkg.tar.zst"):
        signature = archive.with_name(archive.name + ".sig")
        if signature.is_file():
            for source in (archive, signature):
                target = share / "stable" / source.name
                if not target.exists():
                    os.link(source, target)
    build = out / "runtime"
    if build.exists():
        shutil.rmtree(build)
    shutil.copytree(
        ROOT / "builder/server/runtime",
        build,
        ignore=shutil.ignore_patterns("pkg", "src", "*.pkg.tar.*"),
    )
    run("makepkg", "--nodeps", "--noconfirm", "--force", cwd=build)
    archive = next(build.glob("omarchy-server-*.pkg.tar.zst"))
    run("gpg", "--batch", "--yes", "-u", key, "--detach-sign", str(archive))
    # The exact package under test is selected by a manifest, never a stale glob.
    for source in (archive, archive.with_name(archive.name + ".sig")):
        shutil.copy2(source, share / source.name)
    with (share / "MAINTAINER.asc").open("w") as target:
        run("gpg", "--armor", "--export", key, stdout=target)
    run(
        "repo-add",
        "--prevent-downgrade",
        "--include-sigs",
        str(share / "stable/fault.db.tar.gz"),
        *map(str, sorted((share / "stable").glob("*.pkg.tar.zst"))),
    )
    manifest = {
        "package": archive.name,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "signing_key": key,
    }
    (share / "maintenance.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("SIGNED_FAILURE_FIXTURES_READY", share, flush=True)


if __name__ == "__main__":
    main()
