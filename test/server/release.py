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
    os.sync()  # Make frees from the completed compaction visible before checking.
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host below 8 GiB reserve; retain evidence and stop")


def compact(directory, backing=None):
    # Only this invocation's newly installed, stopped disk, before overlays exist.
    disk = directory / "system.qcow2"
    target = directory / "system.compacted.qcow2"
    if target.exists():
        raise ValueError("Compaction target already exists")
    old_record = directory / "compaction.json"
    if old_record.is_file():
        previous = json.loads(old_record.read_text())
        if sha(disk) != previous["compacted_sha256"]:
            raise ValueError("Completed test disk changed before resume")
        if previous.get("backing_file") == (str(backing) if backing else None):
            return previous
        shutil.copy2(old_record, directory / "standalone-compaction.json")
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
    conversion = [
        "qemu-img",
        "convert",
        "-c",
        "-O",
        "qcow2",
        "-o",
        "compression_type=zstd",
    ]
    if backing is not None:
        # Safe rebase of an empty overlay stores only bytes differing from the
        # accepted base. Never use -u: QEMU must preserve the virtual contents.
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(disk),
                str(target),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            ["qemu-img", "rebase", "-b", str(backing), "-F", "qcow2", str(target)],
            check=True,
        )
    else:
        subprocess.run([*conversion, str(disk), str(target)], check=True)
    subprocess.run(["qemu-img", "compare", "-q", str(disk), str(target)], check=True)
    after = target.stat().st_blocks * 512
    target.replace(disk)
    record = {
        "virtual_bytes_compared_equal": True,
        "allocated_before": before,
        "allocated_after": after,
        "compacted_sha256": sha(disk),
        "backing_file": str(backing) if backing else None,
    }
    (directory / "compaction.json").write_text(json.dumps(record, indent=2) + "\n")
    os.sync()
    return record


def validate_receipt(receipt, iso_hash, packages):
    if (
        receipt.get("iso_sha256") != iso_hash
        or receipt.get("source_dirty") is not False
        or receipt.get("packages") != packages
    ):
        raise ValueError("Receipt ISO, clean-source or package-version binding differs")
    code = subprocess.check_output(
        ["git", "show", receipt["source_revision"] + ":test/server/qemu.py"], cwd=ROOT
    )
    if hashlib.sha256(code).hexdigest() != receipt.get("harness_sha256"):
        raise ValueError("Receipt does not match its recorded committed harness")


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
    parser.add_argument(
        "--resume-work",
        type=Path,
        help="Resume a verified completed build without replacing earlier artifacts",
    )
    args = parser.parse_args()
    output, cache = args.output.resolve(), args.cache.resolve()
    if args.resume_work and (output / "server-release-acceptance.json").exists():
        raise ValueError(
            "Completed qualified release is immutable; use a new generation"
        )
    for path in (output, cache):
        if path.exists() and args.resume_work is None:
            raise ValueError("Use NEW output/cache paths: " + str(path))
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Use a clean committed checkout")
    if args.resume_work:
        work = args.resume_work.resolve()
        if not work.is_relative_to(ROOT / ".server-work") or not work.is_dir():
            raise ValueError(
                "Resume only this checkout's recorded private work directory"
            )
    else:
        work = ROOT / ".server-work" / ("release-" + time.strftime("%Y%m%d-%H%M%S"))
        work.mkdir(parents=True, mode=0o700)
    (ROOT / "test-runs/server").mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if not args.resume_work:
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
        for script in (
            "unit.py",
            "migrations.py",
            "boot_check.py",
            "release_inputs.py",
        ):
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
    else:
        repo = work / "channel/stable/x86_64"
        env = dict(os.environ, OMARCHY_PATH=str(work / "upstream-runtime"))
        run(
            [
                "python3",
                ROOT / "builder/server/package-repository.py",
                "--verify",
                repo,
            ],
            work / "resume-channel.log",
            env,
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
        and not inputs["source_dirty"]
    )
    for name, entry in inputs["source_files"].items():
        if name.startswith(("builder/", "configs/", "bin/")) or name == ".gitmodules":
            current = ROOT / name
            data = (
                os.readlink(current).encode()
                if entry["symlink"]
                else current.read_bytes()
            )
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(
                    "Resume requires unchanged production build inputs: " + name
                )
    checksums = dict(
        (line.split()[1], line.split()[0])
        for line in (output / "SHA256SUMS").read_text().splitlines()
    )
    if checksums[iso.name] != iso_hash:
        raise ValueError("Completed build ISO checksum differs")
    expected = json.loads((output / "server-image-packages.json").read_text())
    # Generated profile only: checked completed ISO and source provenance above.
    # Retain downloaded archives, metadata and every existing generation.
    profile = cache / "profile"
    if profile.exists() and not args.resume_work:
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
        wanted = {
            "base": "base",
            "cloud": "cloud",
            "bios": "base-bios",
            "luks": "base-luks",
        }[name]
        matches = []
        if args.resume_work:
            for path in (ROOT / "test-runs/server").glob("*/acceptance-pass.json"):
                old = json.loads(path.read_text())
                if (
                    old.get("case") == wanted
                    and old.get("iso_sha256") == iso_hash
                    and Path(old["iso"]).resolve() == iso
                ):
                    matches.append(path.parent)
        if len(matches) > 1:
            raise ValueError("Ambiguous completed installation receipts")
        if matches:
            directory = matches[0]
            print("REUSING_VERIFIED_INSTALL", wanted, directory, flush=True)
        else:
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
        validate_receipt(receipt, iso_hash, packages)
        compaction = compact(
            directory, bases["base"] / "system.qcow2" if name != "base" else None
        )
        cases[name] = {
            "directory": str(directory),
            "receipt": receipt,
            "compaction": compaction,
        }
        bases[name] = directory
        (work / "release-checkpoint.json").write_text(
            json.dumps(cases, indent=2) + "\n"
        )
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
        "source_revision": inputs["source_revision"],
        "acceptance_source_revision": source,
        "resumed": bool(args.resume_work),
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
