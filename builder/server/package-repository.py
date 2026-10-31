#!/usr/bin/env python3
"""Build and sign the small unofficial server repository outside the ISO container."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
FINGERPRINT = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"
ARCHIVE = "limine-snapper-sync-1.32.1-2-x86_64.pkg.tar.zst"
ARCHIVE_SHA = "95e239bd4c3297cc66c67f64ff66ef5df72ab5105995a2b11caf0f4a896e8465"


def run(*args, **kw):
    return subprocess.run(args, check=True, **kw)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_vendor():
    base = ROOT / "builder/server/runtime/upstream"
    lock = json.loads((ROOT / "builder/server/runtime-upstream.lock.json").read_text())
    actual = {str(p.relative_to(base)): sha(p) for p in base.rglob("*") if p.is_file()}
    if (
        actual != lock["files"]
        or (base / "REVISION").read_text().strip() != lock["revision"]
    ):
        raise ValueError(
            "Vendored official runtime differs from the reviewed revision/hash lock"
        )
    return lock


def source_files():
    paths = [ROOT / "builder/server/runtime-upstream.lock.json"]
    for name in ("runtime", "keyring", "limine-snapper-sync"):
        paths += [
            p
            for p in (ROOT / "builder/server" / name).rglob("*")
            if p.is_file()
            and not any(x in p.parts for x in ("pkg", "src", "__pycache__"))
            and ".pkg.tar." not in p.name
        ]
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(paths)}


def pinned_signature(path, signature=None):
    signature = signature or Path(str(path) + ".sig")
    result = subprocess.run(
        ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature), str(path)],
        text=True,
        capture_output=True,
        check=True,
    )
    signatures = [
        line.split()
        for line in result.stdout.splitlines()
        if line.startswith("[GNUPG:] VALIDSIG ")
    ]
    if not any(
        parts[2] == FINGERPRINT or parts[-1] == FINGERPRINT for parts in signatures
    ):
        raise ValueError("Signature is not from the pinned server maintainer")


def verify(repo):
    pinned_signature(repo / "repository.json")
    record = json.loads((repo / "repository.json").read_text())
    if record["maintainer_fingerprint"] != FINGERPRINT or record["source_dirty"]:
        raise ValueError(
            "Repository must identify the pinned key and clean committed sources"
        )
    if record["source_files"] != source_files():
        raise ValueError("Signed repository package sources differ from this checkout")
    allowed = set(record["files"]) | {"repository.json", "repository.json.sig"}
    extras = {p.name for p in repo.iterdir()} - allowed
    for name in extras:
        if (
            ".pkg.tar.zst" not in name
            or not (repo / name.removesuffix(".sig")).is_file()
        ):
            raise ValueError("Unlisted repository file: " + name)
    for name, expected in record["files"].items():
        if Path(name).name != name or sha(repo / name) != expected:
            raise ValueError("Repository file checksum mismatch: " + name)
    for path in (
        repo / "omarchy-server.db",
        repo / "omarchy-server.files",
        *repo.glob("*.pkg.tar.zst"),
    ):
        pinned_signature(path)
    print("SIGNED_REPOSITORY_VERIFIED", repo, flush=True)


def main():
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--verify", type=Path)
    parser.add_argument("--upstream-archive", type=Path)
    parser.add_argument(
        "--reuse",
        type=Path,
        help="Reuse unchanged immutable packages from a prior signed repository",
    )
    opts = parser.parse_args()
    if opts.verify:
        verify(opts.verify.resolve())
        return
    if os.geteuid() == 0:
        raise ValueError("Run as the signing/build user; makepkg must not run as root")
    out = opts.output.resolve()
    if out.exists():
        raise ValueError("Use a new immutable repository output directory")
    vendor = check_vendor()
    key = subprocess.check_output(
        ["git", "-C", str(ROOT), "config", "user.signingkey"], text=True
    ).strip()
    if key != FINGERPRINT:
        raise ValueError(
            "The configured signing key must match the pinned repository trust anchor"
        )
    previous = None
    current_sources = source_files()
    if opts.reuse:
        opts.reuse = opts.reuse.resolve()
        pinned_signature(opts.reuse / "repository.json")
        previous = json.loads((opts.reuse / "repository.json").read_text())
        if (
            previous["source_dirty"]
            or previous["maintainer_fingerprint"] != FINGERPRINT
        ):
            raise ValueError(
                "Only a clean signed repository may supply immutable packages"
            )
    scratch = ROOT / ".server-work"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="repository-build-", dir=scratch) as work:
        work = Path(work)
        upstream = opts.upstream_archive
        if upstream is None:
            upstream = work / ARCHIVE
            url = "https://pkgs.omarchy.org/stable/x86_64/" + ARCHIVE
            urllib.request.urlretrieve(url, upstream)
            signature = upstream.with_name(upstream.name + ".sig")
            urllib.request.urlretrieve(url + ".sig", signature)
            run("sudo", "-n", "pacman-key", "--verify", str(signature), str(upstream))
        if sha(upstream) != ARCHIVE_SHA:
            raise ValueError(
                "Official snapshot package does not match the reviewed pin"
            )
        repo = out / "stable/x86_64"
        repo.mkdir(parents=True)
        archives = []
        for recipe in ("keyring", "limine-snapper-sync", "runtime"):
            source = ROOT / "builder/server" / recipe
            build = work / recipe
            shutil.copytree(
                source,
                build,
                ignore=shutil.ignore_patterns(
                    "pkg", "src", "*.pkg.tar.*", "__pycache__"
                ),
            )
            if recipe == "limine-snapper-sync":
                shutil.copy2(upstream, build / ARCHIVE)
            outputs = subprocess.check_output(
                ["makepkg", "--packagelist"], cwd=build, text=True
            ).splitlines()
            if len(outputs) != 1:
                raise ValueError("Expected exactly one package from each server recipe")
            archive = Path(outputs[0])
            if previous and (opts.reuse / archive.name).is_file():
                prefix = "builder/server/" + recipe + "/"
                before = {
                    k: v
                    for k, v in previous["source_files"].items()
                    if k.startswith(prefix)
                }
                after = {
                    k: v for k, v in current_sources.items() if k.startswith(prefix)
                }
                if before != after:
                    raise ValueError(
                        "Recipe changed without a package-version bump: " + recipe
                    )
                old = opts.reuse / archive.name
                for path in (old, Path(str(old) + ".sig")):
                    if sha(path) != previous["files"].get(path.name):
                        raise ValueError(
                            "Immutable package differs from its signed manifest"
                        )
                pinned_signature(old)
                for path in (old, Path(str(old) + ".sig")):
                    shutil.copy2(path, repo / path.name)
                archives.append(repo / old.name)
                print("REUSED_IMMUTABLE_PACKAGE", old.name, flush=True)
                continue
            run("makepkg", "--nodeps", "--noconfirm", cwd=build)
            expected = {
                "keyring": "omarchy-server-keyring",
                "runtime": "omarchy-server",
                "limine-snapper-sync": "limine-snapper-sync-server",
            }[recipe]
            actual = subprocess.check_output(
                ["pacman", "-Qp", str(archive)], text=True
            ).split()[0]
            if actual != expected:
                raise ValueError("Unexpected package output: " + actual)
            run("gpg", "--batch", "--yes", "-u", key, "--detach-sign", str(archive))
            run("gpg", "--verify", str(archive) + ".sig", str(archive))
            for path in (archive, archive.with_name(archive.name + ".sig")):
                shutil.copy2(path, repo / path.name)
            archives.append(repo / archive.name)
        run(
            "repo-add",
            "--sign",
            "--key",
            key,
            "--include-sigs",
            str(repo / "omarchy-server.db.tar.gz"),
            *map(str, archives)
        )
        # Raw Git hosting serves symlinks as text. Publish actual database bytes.
        for alias in (
            "omarchy-server.db",
            "omarchy-server.db.sig",
            "omarchy-server.files",
            "omarchy-server.files.sig",
        ):
            path = repo / alias
            data = path.read_bytes()
            path.unlink()
            path.write_bytes(data)
        shutil.copy2(ROOT / "builder/server/keyring/omarchy-server.asc", repo)
        shutil.copy2(
            ROOT / "builder/server/runtime/bin/omarchy-server-repository",
            repo / "enable-repository",
        )
        (repo / "enable-repository").chmod(0o755)
        record = {
            "unofficial": True,
            "source_revision": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_dirty": bool(
                subprocess.check_output(
                    ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
                )
            ),
            "maintainer_fingerprint": FINGERPRINT,
            "source_files": source_files(),
            "official_snapshot_package": {"file": ARCHIVE, "sha256": ARCHIVE_SHA},
            "official_runtime": vendor,
            "files": {p.name: sha(p) for p in sorted(repo.iterdir()) if p.is_file()},
        }
        (repo / "repository.json").write_text(json.dumps(record, indent=2) + "\n")
        run(
            "gpg",
            "--batch",
            "--yes",
            "-u",
            key,
            "--detach-sign",
            str(repo / "repository.json"),
        )
        for path in (repo / "omarchy-server.db", repo / "omarchy-server.files"):
            run("gpg", "--verify", str(path) + ".sig", str(path))
        print("SIGNED_SERVER_REPOSITORY_READY", repo, flush=True)


if __name__ == "__main__":
    main()
