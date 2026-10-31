#!/usr/bin/env python3
"""Reserve immutable release paths and record a clean server build's inputs."""

import argparse
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


def files(root):
    names = (
        subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "builder",
                "configs",
                "bin",
                "test",
                ".gitmodules",
            ]
        )
        .decode()
        .split("\0")
    )
    return {
        name: {
            "symlink": (root / name).is_symlink(),
            "sha256": hashlib.sha256(
                os.readlink(root / name).encode()
                if (root / name).is_symlink()
                else (root / name).read_bytes()
            ).hexdigest(),
        }
        for name in names
        if name
    }


def unused(path):
    if path.is_symlink() or (
        path.exists() and (not path.is_dir() or any(path.iterdir()))
    ):
        raise ValueError(
            "Build paths must be absent or empty directories: " + str(path)
        )


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
    if shutil.disk_usage(root).free < 12 * 1024**3:
        raise ValueError("A full server build requires at least 12 GiB free")
    output.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    seeded = False
    legacy = root / ".server-work/cache/mirror"
    if not empty_cache and legacy.is_dir():
        shutil.copytree(legacy, cache / "mirror", copy_function=os.link, symlinks=True)
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
        prepare(ROOT, args.output, args.cache, args.image, args.epoch, args.empty_cache)


if __name__ == "__main__":
    main()
