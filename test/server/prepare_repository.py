#!/usr/bin/env python3
"""Create isolated signed/invalid repositories and a migration fault fixture."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from qemu import ROOT, run

KEY = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"


def replace(path, body):
    path.unlink(missing_ok=True)  # Break hard links before changing test data.
    path.write_bytes(body)


def index(repo):
    for path in repo.glob("omarchy-server.db*"):
        path.unlink()
    for path in repo.glob("omarchy-server.files*"):
        path.unlink()
    run(
        "repo-add",
        "--sign",
        "--key",
        KEY,
        "--include-sigs",
        str(repo / "omarchy-server.db.tar.gz"),
        *map(str, sorted(repo.glob("*.pkg.tar.zst")))
    )
    for name in (
        "omarchy-server.db",
        "omarchy-server.db.sig",
        "omarchy-server.files",
        "omarchy-server.files.sig",
    ):
        path = repo / name
        body = path.read_bytes()
        replace(path, body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source, output = args.repository.resolve(), args.output.resolve()
    if not output.is_relative_to(ROOT / ".server-work") or output.exists():
        raise ValueError("Use a new isolated test-media directory")
    output.mkdir(parents=True)
    cases = (
        "good",
        "bad-database",
        "missing-database-signature",
        "unsigned-package",
        "wrong-key",
        "untrusted-database",
        "migration",
    )
    for name in cases:
        shutil.copytree(source, output / name, copy_function=os.link)
    repo = output / "bad-database"
    body = bytearray((repo / "omarchy-server.db").read_bytes())
    body[len(body) // 2] ^= 1
    replace(repo / "omarchy-server.db", bytes(body))
    (output / "missing-database-signature/omarchy-server.db.sig").unlink()
    repo = output / "unsigned-package"
    for path in repo.glob("omarchy-server-0.2.0-*.sig"):
        path.unlink()
    index(repo)  # Signed metadata without an embedded signature for this package.
    replace(
        output / "wrong-key/omarchy-server.asc",
        (ROOT / "builder/omarchy.gpg").read_bytes(),
    )
    # A signed database is still rejected when its issuer is not trusted.
    # The disposable private key never enters the guest export or a release.
    with tempfile.TemporaryDirectory(
        prefix="repo-test-key-", dir=ROOT / ".server-work"
    ) as directory:
        home = Path(directory)
        run(
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "Disposable repository test <test@example.invalid>",
            "ed25519",
            "sign",
            "1d",
        )
        repo = output / "untrusted-database"
        signature = repo / "omarchy-server.db.sig"
        signature.unlink()
        run(
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--output",
            str(signature),
            "--detach-sign",
            str(repo / "omarchy-server.db"),
        )
        run("gpgconf", "--homedir", str(home), "--kill", "all")
    build = output / "migration-fixture"
    shutil.copytree(
        ROOT / "builder/server/runtime",
        build,
        ignore=shutil.ignore_patterns("pkg", "src", "*.pkg.tar.*", "__pycache__"),
    )
    path = build / "PKGBUILD"
    path.write_text(path.read_text().replace("pkgver=0.2.0", "pkgver=0.2.1"))
    migrations = build / "migrations"
    entries = json.loads((migrations / "manifest.json").read_text())
    fixture_scripts = [
        ("0002-desktop-fixture", "desktop", "touch /etc/desktop-migration-was-run\n"),
        (
            "0003-shared-fixture",
            "shared",
            "test -f /etc/shared-migration-proof || printf x > /etc/shared-migration-proof\nprintf '%s\\n' applied >> /var/log/shared-migration-invocations\n",
        ),
        ("0004-failure-fixture", "server", "test -f /etc/allow-fixture-migration\n"),
    ]
    for name, scope, text in fixture_scripts:
        path = migrations / (name + ".sh")
        path.write_text(text)
        entries.append(
            {
                "id": name,
                "scope": scope,
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "idempotent": True,
                "review": "Disposable acceptance fixture only",
            }
        )
    (migrations / "manifest.json").write_text(json.dumps(entries, indent=2) + "\n")
    run("makepkg", "--nodeps", "--noconfirm", cwd=build)
    archives = subprocess.check_output(
        ["makepkg", "--packagelist"], cwd=build, text=True
    ).splitlines()
    assert len(archives) == 1
    package = Path(archives[0])
    run("gpg", "--batch", "--yes", "-u", KEY, "--detach-sign", str(package))
    repo = output / "migration"
    for path in repo.glob("omarchy-server-0.2.0-*"):
        path.unlink()
    for path in (package, package.with_name(package.name + ".sig")):
        shutil.copy2(path, repo / path.name)
    index(repo)
    upstream = output / "upstream"
    upstream.mkdir()
    for package in (ROOT / ".server-work/cache/mirror").glob("*.pkg.tar.zst"):
        signature = package.with_name(package.name + ".sig")
        if signature.is_file():
            for path in (package, signature):
                os.link(path, upstream / path.name)
    run(
        "repo-add",
        "--prevent-downgrade",
        "--include-sigs",
        str(upstream / "upstream.db.tar.gz"),
        *map(str, sorted(upstream.glob("*.pkg.tar.zst")))
    )
    # Migration fixture is explicitly separate and must never be published.
    (output / "TEST-MEDIA-ONLY").write_text(
        "Contains invalid signatures and a failing 0.2.1 fixture. NEVER PUBLISH.\n"
    )
    print("REPOSITORY_TEST_MEDIA_READY", output, flush=True)


if __name__ == "__main__":
    main()
