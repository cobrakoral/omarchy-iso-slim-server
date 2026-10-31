#!/usr/bin/env python3
"""Publish verified artifacts atomically to the authorized fork's package branch."""

import argparse
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]


def git(*args, cwd=ROOT):
    return subprocess.check_output(["git", "-C", str(cwd), *args], text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("repository", type=Path)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Push the signed artifact commit to origin/server-packages",
    )
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location(
        "server_repository", ROOT / "builder/server/package-repository.py"
    )
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    repo = args.repository.resolve()
    verifier.verify(repo)
    origin = git("remote", "get-url", "--push", "origin")
    if not re.search(r"[:/]cobrakoral/omarchy-iso-slim-server(?:\.git)?$", origin):
        raise ValueError("Publication is restricted to the authorized writable fork")
    branch = "server-packages"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    work = ROOT / ".server-work" / ("publish-" + stamp)
    remote = git("ls-remote", "--heads", "origin", "refs/heads/" + branch)
    if remote:
        git("fetch", "origin", branch)
        git(
            "worktree",
            "add",
            "-b",
            "package-publication-" + stamp,
            str(work),
            "FETCH_HEAD",
        )
    else:
        git("worktree", "add", "--detach", str(work), "HEAD")
        git("switch", "--orphan", "package-publication-" + stamp, cwd=work)
    destination = work / "stable/x86_64"
    destination.mkdir(parents=True, exist_ok=True)
    for source in repo.iterdir():
        if not source.is_file() or source.is_symlink():
            raise ValueError("Publish regular verified repository files only")
        target = destination / source.name
        if (
            ".pkg.tar." in source.name
            and target.exists()
            and target.read_bytes() != source.read_bytes()
        ):
            raise ValueError(
                "Published package bytes are immutable; reuse the archive or bump its version: "
                + source.name
            )
        shutil.copy2(source, target)
    # Retain older versioned archives for clients whose database is cached.
    (work / "README.md").write_text(
        "# Unofficial Omarchy Slim Server packages\n\n"
        "Signed community package channel for x86_64. Not an official Omarchy release.\n\n"
        "Source and installation instructions: "
        "https://github.com/cobrakoral/omarchy-iso-slim-server/blob/feature/slim-server/README.server.md\n\n"
        "Pinned maintainer fingerprint: " + verifier.FINGERPRINT + "\n\n"
        "Packages and repository databases require trusted signatures. Versioned archives "
        "are never replaced; old versions remain available for cached database clients.\n"
    )
    git("add", "stable/x86_64", "README.md", cwd=work)
    git(
        "commit",
        "-S",
        "-m",
        "Publish signed unofficial server package channel",
        cwd=work,
    )
    revision = git("rev-parse", "HEAD", cwd=work)
    print("PREPARED_PACKAGE_BRANCH", revision, work, flush=True)
    if args.publish:
        git("push", "origin", "HEAD:refs/heads/" + branch, cwd=work)
        print("PUBLISHED_SIGNED_SERVER_REPOSITORY", revision, flush=True)


if __name__ == "__main__":
    main()
