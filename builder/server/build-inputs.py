#!/usr/bin/env python3
"""Reserve immutable release paths and record a clean server build's inputs."""

import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def fingerprint(path, mode):
    link = path.is_symlink()
    content = os.readlink(path).encode() if link else path.read_bytes()
    return {
        "mode": mode,
        "symlink": link,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def files(root):
    index = subprocess.check_output([
        "git", "-C", str(root), "ls-files", "-s", "-z",
        "builder", "configs", "bin", "test", ".gitmodules",
    ])
    result = {}
    for entry in index.split(b"\0"):
        if not entry:
            continue
        header, name = entry.decode().split("\t", 1)
        mode, _, stage = header.split()
        if stage != "0":
            raise ValueError(f"Unmerged build input: {name}")
        result[name] = fingerprint(root / name, mode)
    return result


def production_files(manifest):
    return {
        name: entry for name, entry in manifest.items()
        if name == ".gitmodules" or name.startswith(("builder/", "configs/", "bin/"))
    }


def verify_production(root, record):
    if (
        production_files(files(root)) != production_files(record["source_files"])
        or git(root, "rev-parse", "HEAD:archiso") != record["archiso_revision"]
        or git(root / "archiso", "rev-parse", "HEAD") != record["archiso_revision"]
        or git(root / "archiso", "status", "--porcelain")
    ):
        raise ValueError("Qualification requires the exact production inputs and archiso revision")


def stage_sources(root, cache, record):
    destination = cache / "source"
    destination.mkdir(mode=0o700)
    archiso = destination / "archiso"
    archiso.mkdir()
    for repo, revision, target, paths in (
        (root, record["source_revision"], destination, ("builder", "configs")),
        (root / "archiso", record["archiso_revision"], archiso, ("configs/releng",)),
    ):
        archive = subprocess.check_output(["git", "-C", str(repo), "archive", revision, *paths])
        subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)
    for name, entry in record["source_files"].items():
        if name.startswith(("builder/", "configs/")):
            staged = destination / name
            if fingerprint(staged, entry["mode"]) != entry:
                raise ValueError(f"Archived build input differs from the manifest: {name}")


def unused(path):
    if path.is_symlink() or (
        path.exists() and (not path.is_dir() or any(path.iterdir()))
    ):
        raise ValueError(
            "Build paths must be absent or empty directories: " + str(path)
        )


def check_build_space(output, cache):
    def existing_parent(path):
        while not path.exists():
            path = path.parent
        if not path.is_dir():
            raise ValueError(f"Build path has a non-directory parent: {path}")
        return path

    output_parent, cache_parent = map(existing_parent, (output, cache))
    if output_parent.stat().st_dev == cache_parent.stat().st_dev:
        reserves = ((output_parent, 12),)
    else:
        reserves = ((output_parent, 3), (cache_parent, 12))
    for path, gib in reserves:
        if shutil.disk_usage(path).free < gib * 1024**3:
            raise ValueError(f"A full server build requires at least {gib} GiB free on {path}")


def link_or_copy(source, target):
    try:
        return os.link(source, target)
    except OSError as error:
        if error.errno != errno.EXDEV:
            raise
        return shutil.copy2(source, target)


def prepare(root, output, cache, image, epoch, empty_cache):
    if output.is_symlink() or cache.is_symlink():
        raise ValueError("Build paths must not be symlinks")
    output, cache = output.resolve(), cache.resolve()
    if output == cache or output.is_relative_to(cache) or cache.is_relative_to(output):
        raise ValueError("Build output and cache must not overlap")
    for path in (output, cache):
        if root == path or root.is_relative_to(path):
            raise ValueError("Build paths must not contain the source checkout")
        unused(path)
    if git(root, "status", "--porcelain"):
        raise ValueError("Server releases require a clean committed checkout")
    if not re.fullmatch(r"[A-Za-z0-9./:_-]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("Pin the build container by SHA256 digest")
    if not re.fullmatch(r"[0-9]+", str(epoch)) or int(epoch) <= 0:
        raise ValueError("SOURCE_DATE_EPOCH must be a positive integer")
    expected = git(root, "rev-parse", "HEAD:archiso")
    actual = git(root / "archiso", "rev-parse", "HEAD")
    if actual != expected or git(root / "archiso", "status", "--porcelain"):
        raise ValueError("Initialize the clean pinned archiso submodule first")
    check_build_space(output, cache)
    claimed = []
    try:
        for path in sorted([output, cache], key=lambda p: str(p)):
            path.mkdir(parents=True, exist_ok=True)
            marker = path / ".server-build-reservation"
            try:
                marker.touch(mode=0o600, exist_ok=False)
            except FileExistsError:
                raise ValueError(f"Build path is already claimed: {path}")
            claimed.append(marker)
            entries = [e for e in path.iterdir() if e.name != ".server-build-reservation"]
            if entries:
                raise ValueError(f"Build paths must be absent or empty directories: {path}")
    except Exception:
        for marker in claimed:
            marker.unlink(missing_ok=True)
        raise
    seeded = False
    legacy = root / ".server-work/cache/mirror"
    if not empty_cache and legacy.is_dir():
        shutil.copytree(legacy, cache / "mirror", copy_function=link_or_copy, symlinks=True)
        seeded = True
    record = {
        "source_revision": git(root, "rev-parse", "HEAD"),
        "source_dirty": False,
        "archiso_revision": actual,
        "container": image,
        "source_date_epoch": int(epoch),
        "empty_cache_requested": empty_cache,
        "cache_initially_empty": True,
        "legacy_cache_seeded": seeded,
        "source_files": files(root),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (output / "server-build-inputs.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    return record


def verify(root, output):
    record = json.loads((output / "server-build-inputs.json").read_text())
    if (
        git(root, "status", "--porcelain")
        or git(root, "rev-parse", "HEAD") != record["source_revision"]
        or git(root / "archiso", "rev-parse", "HEAD") != record["archiso_revision"]
        or git(root / "archiso", "status", "--porcelain")
        or files(root) != record["source_files"]
    ):
        raise ValueError("Source changed during the build; release is not qualified")
    print("BUILD_INPUTS_UNCHANGED", record["source_revision"], flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--image")
    parser.add_argument("--epoch")
    parser.add_argument("--empty-cache", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        verify(ROOT, args.output.resolve())
    else:
        if args.cache is None or args.image is None or args.epoch is None:
            parser.error("--cache, --image and --epoch required for preparation")
        record = prepare(ROOT, args.output, args.cache, args.image, args.epoch, args.empty_cache)
        stage_sources(ROOT, args.cache.resolve(), record)


if __name__ == "__main__":
    main()
