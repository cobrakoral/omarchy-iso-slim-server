#!/usr/bin/env python3
"""Build from an empty project cache and qualify a fresh server release.

Run from a fresh clean checkout, passing NEW output/cache paths. The signed
channel is downloaded independently; no prior ISO or project cache is seeded.
All credential-bearing logs and disposable disks stay outside the release.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

from qemu import ROOT, VM
from repository import health

FINGERPRINT = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"
CHANNEL = "https://raw.githubusercontent.com/cobrakoral/omarchy-iso-slim-server/server-packages/stable/x86_64/"
RUNTIME = "84782475aeb885c8d05f9e85b6ad0f1a0add60e5"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, log, env=None):
    print("RELEASE_STEP", log.name, flush=True)
    with log.open("w") as transcript:
        subprocess.run(
            [str(x) for x in args],
            cwd=ROOT,
            env=env,
            stdout=transcript,
            stderr=subprocess.STDOUT,
            check=True,
        )


def reserve():
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host below 8 GiB reserve; retain evidence and stop")


def compact(directory):
    # Only this invocation's newly installed, stopped disk, before overlays exist.
    disk = directory / "system.qcow2"
    target = directory / "system.compacted.qcow2"
    if target.exists():
        raise ValueError("Compaction target already exists")
    info = json.loads(
        subprocess.check_output(
            ["qemu-img", "info", "--output=json", str(disk)], text=True
        )
    )
    if info.get("backing-filename"):
        raise ValueError("Only standalone fresh acceptance disks may be compacted")
    before = disk.stat().st_blocks * 512
    subprocess.run(
        ["qemu-img", "check", str(disk)], check=True, stdout=subprocess.DEVNULL
    )
    subprocess.run(
        [
            "qemu-img",
            "convert",
            "-c",
            "-O",
            "qcow2",
            "-o",
            "compression_type=zstd",
            str(disk),
            str(target),
        ],
        check=True,
    )
    subprocess.run(["qemu-img", "compare", "-q", str(disk), str(target)], check=True)
    after = target.stat().st_blocks * 512
    target.replace(disk)
    record = {
        "virtual_bytes_compared_equal": True,
        "allocated_before": before,
        "allocated_after": after,
        "compacted_sha256": sha(disk),
    }
    (directory / "compaction.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def produced(command, log, marker, env=None):
    before = set((ROOT / "test-runs/server").glob("*"))
    run(command, log, env)
    matches = [
        p
        for p in set((ROOT / "test-runs/server").glob("*")) - before
        if (p / marker).is_file()
    ]
    if len(matches) != 1:
        raise ValueError("Expected exactly one NEW passing receipt: " + marker)
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2452)
    args = parser.parse_args()
    output, cache = args.output.resolve(), args.cache.resolve()
    for path in (output, cache):
        if path.exists():
            raise ValueError("Use NEW output/cache paths: " + str(path))
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Use a clean committed checkout")
    work = ROOT / ".server-work" / ("release-" + time.strftime("%Y%m%d-%H%M%S"))
    work.mkdir(parents=True, mode=0o700)
    (ROOT / "test-runs/server").mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    repo = work / "channel/stable/x86_64"
    repo.mkdir(parents=True)

    def fetch(name):
        with urllib.request.urlopen(
            CHANNEL + urllib.parse.quote(name), timeout=45
        ) as response:
            (repo / name).write_bytes(response.read())

    for name in ("repository.json", "repository.json.sig"):
        fetch(name)
    spec = importlib.util.spec_from_file_location(
        "package_repository", ROOT / "builder/server/package-repository.py"
    )
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    package.pinned_signature(repo / "repository.json")
    metadata = json.loads((repo / "repository.json").read_text())
    for name in metadata["files"]:
        if Path(name).name != name:
            raise ValueError("Invalid signed channel path")
        fetch(name)
    package.verify(repo)
    # The full upstream tests must use their matching runtime, not an older host install.
    runtime = work / "upstream-runtime"
    for cmd in [
        ["git", "init", str(runtime)],
        [
            "git",
            "-C",
            str(runtime),
            "remote",
            "add",
            "origin",
            "https://github.com/omacom/omarchy.git",
        ],
        [
            "git",
            "-C",
            str(runtime),
            "fetch",
            "--depth=1",
            "--filter=blob:none",
            "origin",
            RUNTIME,
        ],
        [
            "git",
            "-C",
            str(runtime),
            "sparse-checkout",
            "set",
            "default/i18n",
            "install/provisioning",
        ],
        ["git", "-C", str(runtime), "checkout", "--detach", "FETCH_HEAD"],
    ]:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    env = dict(os.environ, OMARCHY_PATH=str(runtime))
    run(["bash", "test/all"], work / "upstream-tests.log", env)
    for script in ("unit.py", "migrations.py", "boot_check.py", "release_inputs.py"):
        run(
            ["python3", ROOT / "test/server" / script],
            work / ("regression-" + script + ".log"),
            env,
        )
    build_env = dict(
        env,
        OMARCHY_SERVER_RELEASE_DIR=str(output),
        OMARCHY_SERVER_CACHE_DIR=str(cache),
        OMARCHY_SERVER_REPOSITORY_DIR=str(repo),
    )
    run(
        ["bash", ROOT / "bin/omarchy-iso-server-make", "--empty-cache"],
        work / "build.log",
        build_env,
    )
    isos = list(output.glob("*.iso"))
    if len(isos) != 1:
        raise ValueError("Expected exactly one fresh ISO")
    iso = isos[0]
    iso_hash = sha(iso)
    inputs = json.loads((output / "server-build-inputs.json").read_text())
    assert (
        inputs["empty_cache_requested"]
        and not inputs["legacy_cache_seeded"]
        and inputs["source_revision"] == source
    )
    expected = json.loads((output / "server-image-packages.json").read_text())
    # Generated profile only: checked completed ISO and source provenance above.
    # Retain downloaded archives, metadata and every existing generation.
    profile = cache / "profile"
    mounts = subprocess.check_output(
        ["findmnt", "-rn", "-o", "TARGET"], text=True
    ).splitlines()
    if profile.is_symlink() or any(
        m == str(profile) or m.startswith(str(profile) + "/") for m in mounts
    ):
        raise ValueError("Generated profile unexpectedly mounted or linked")
    run(
        ["sudo", "-n", "rm", "-rf", "--one-file-system", "--", profile],
        work / "discard-generated-profile.log",
    )
    cases = {}
    bases = {}
    for name, options in [
        ("base", []),
        ("cloud", ["--cloud-init"]),
        ("bios", ["--bios"]),
        ("luks", ["--encrypt"]),
    ]:
        reserve()
        directory = produced(
            [
                "python3",
                ROOT / "test/server/qemu.py",
                iso,
                "--port",
                str(args.port),
                *options,
            ],
            work / ("install-" + name + ".log"),
            "acceptance-pass.json",
            env,
        )
        receipt = json.loads((directory / "acceptance-pass.json").read_text())
        packages = dict(expected["base"])
        if name == "cloud":
            packages.update(expected["cloud_delta"])
        assert (
            receipt["iso_sha256"] == iso_hash
            and receipt["source_revision"] == source
            and not receipt["source_dirty"]
        )
        assert (
            receipt["packages"] == packages
        ), "Installed package versions differ from build closure"
        compaction = compact(directory)
        cases[name] = {
            "directory": str(directory),
            "receipt": receipt,
            "compaction": compaction,
        }
        bases[name] = directory
    trust = {}
    for name in ("base", "cloud"):
        reserve()
        directory = produced(
            [
                "python3",
                ROOT / "test/server/installed_repository.py",
                bases[name],
                repo,
                "--port",
                str(args.port),
            ],
            work / ("trust-" + name + ".log"),
            "installed-repository-pass.json",
            env,
        )
        trust[name] = {
            "directory": str(directory),
            "receipt": json.loads(
                (directory / "installed-repository-pass.json").read_text()
            ),
        }
    recovery = Path(trust["base"]["directory"])
    reserve()
    run(
        [
            "python3",
            ROOT / "test/server/recovery.py",
            recovery,
            "--port",
            str(args.port),
        ],
        work / "recovery.log",
        env,
    )
    assert (recovery / "rollback-pass.txt").is_file()
    vm = VM(recovery, recovery / "system.qcow2", args.port, [], share=repo)
    try:
        vm.wait_ssh()
        restored = health(vm)
        restored["trust"] = vm.sudo(
            "mkdir -p /run/release-channel; mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/release-channel; pacman-key --verify /run/release-channel/omarchy-server.db.sig /run/release-channel/omarchy-server.db"
        )
    finally:
        vm.stop()
    assert sha(iso) == iso_hash
    assert not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)
    record = {
        "passed": True,
        "source_revision": source,
        "iso": str(iso),
        "iso_sha256": iso_hash,
        "build_inputs": inputs,
        "package_closures": expected,
        "installs": cases,
        "installed_trust": trust,
        "restored_health": restored,
        "runtime_tests_revision": RUNTIME,
        "work": str(work),
        "limits": [
            "Fresh build and functional reproduction, not byte-identical ISO reproduction",
            "No shared production code changed; desktop ISO build not repeated",
            "Physical hardware and Secure Boot remain unverified",
        ],
    }
    (output / "server-release-acceptance.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print("SERVER_FRESH_RELEASE_ACCEPTANCE_PASS", output, flush=True)


if __name__ == "__main__":
    main()
