#!/usr/bin/env python3
"""Build from an empty project cache and qualify a fresh server release.

Run from a fresh clean checkout, passing NEW output/cache paths. The signed
channel is downloaded independently; no prior ISO or project cache is seeded.
All credential-bearing logs and disposable disks stay outside the release.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

from qemu import ROOT, VM
from repository import health
from update import fixture_signing_key

FINGERPRINT = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"
CHANNEL = "https://raw.githubusercontent.com/cobrakoral/omarchy-iso-slim-server/server-packages/stable/x86_64/"
RUNTIME = "84782475aeb885c8d05f9e85b6ad0f1a0add60e5"

SERVER_TEST_SCRIPTS = (
    "unit.py",
    "input_validation.py",
    "network_menu.py",
    "harness_guards.py",
    "operational_unit.py",
    "migrations.py",
    "boot_check.py",
    "release_inputs.py",
)
REQUIRED_TEST_LOGS = (
    "upstream-tests.log",
    *(f"regression-{script}.log" for script in SERVER_TEST_SCRIPTS),
    "build.log",
)

REQUIRED_METADATA_FILES = (
    "server-build-inputs.json",
    "server-image-packages.json",
    "server-packages.lock",
    "server-repository-provenance.sha256",
    "SHA256SUMS",
)


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args, log, env=None, *, pass_fds=()):
    print("RELEASE_STEP", log.name, flush=True)
    with log.open("w") as transcript:
        subprocess.run(
            [str(x) for x in args],
            cwd=ROOT,
            env=env,
            stdout=transcript,
            stderr=subprocess.STDOUT,
            check=True,
            pass_fds=pass_fds,
        )


def reserve():
    os.sync()  # Make frees from the completed compaction visible before checking.
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host below 8 GiB reserve; retain evidence and stop")


def _sync_dir(directory):
    dfd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _write_atomic(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
    _sync_dir(path.parent)


def _sync_file(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _finalize_compaction(directory, target, disk, receipt_path, journal_path, receipt):
    if target.exists():
        target.replace(disk)
        _sync_dir(directory)
    _write_atomic(receipt_path, json.dumps(receipt, indent=2) + "\n")
    journal_path.unlink()
    _sync_dir(directory)
    return receipt


def compact(directory, backing=None, *, expected_sha256=None):
    lock_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError(f"Compaction already in progress for {directory}") from error
        return _compact_locked(directory, backing, lock_fd, expected_sha256)
    finally:
        os.close(lock_fd)


def _compact_locked(directory, backing, lock_fd, expected_sha256):
    # Only this invocation's newly installed, stopped disk, before overlays exist.
    disk = directory / "system.qcow2"
    target = directory / "system.compacted.qcow2"
    journal_path = directory / "compaction-journal.json"
    receipt_path = directory / "compaction.json"
    backing_str = str(backing) if backing else None

    if disk.is_symlink() or not disk.is_file():
        raise ValueError("System disk is not a regular file; preserving evidence")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError("Compaction target path is not a regular file; preserving evidence")
    if journal_path.is_symlink() or receipt_path.is_symlink():
        raise ValueError("Compaction evidence is a symlink; preserving evidence")

    if journal_path.is_file():
        journal = json.loads(journal_path.read_text())
        if expected_sha256 is not None and journal.get("original_sha256") != expected_sha256:
            raise ValueError("Compaction journal differs from the tested disk digest; preserving evidence")
        disk_sha = sha(disk)
        if "receipt" in journal:
            receipt_payload = journal["receipt"]
            if receipt_payload["backing_file"] != backing_str:
                raise ValueError("Compaction journal backing file mismatch; preserving evidence")
            orig_sha = journal["original_sha256"]
            if expected_sha256 is not None and receipt_payload.get("original_sha256") != orig_sha:
                raise ValueError("Prepared compaction receipt differs from the tested disk digest; preserving evidence")
            prep_sha = receipt_payload["compacted_sha256"]
            if target.exists():
                if disk_sha != orig_sha or sha(target) != prep_sha:
                    raise ValueError("Compaction hash mismatch with prepared target present; preserving evidence")
            elif disk_sha != prep_sha:
                raise ValueError("Disk hash does not match prepared compaction digest; preserving evidence")
            return _finalize_compaction(directory, target, disk, receipt_path, journal_path, receipt_payload)

        if (
            journal.get("stage") != "converting"
            or journal.get("original_sha256") != disk_sha
            or "backing_file" not in journal
            or journal["backing_file"] != backing_str
        ):
            raise ValueError("Compaction conversion journal mismatch; preserving evidence")
        if target.exists():
            target.unlink()
            _sync_dir(directory)

    if target.exists():
        raise ValueError("Compaction target already exists")
    old_record = receipt_path
    if old_record.is_file():
        previous = json.loads(old_record.read_text())
        if sha(disk) != previous["compacted_sha256"]:
            raise ValueError("Completed test disk changed before resume")
        if expected_sha256 is not None and (
            previous.get("original_sha256") != expected_sha256
            or previous.get("backing_file") != backing_str
        ):
            raise ValueError("Completed compaction differs from the tested disk or backing file")
        if previous.get("backing_file") == backing_str:
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
    orig_sha = sha(disk)
    if expected_sha256 is not None and orig_sha != expected_sha256:
        raise ValueError("Acceptance disk changed after testing; preserving evidence")
    converting = {
        "stage": "converting",
        "original_sha256": orig_sha,
        "backing_file": backing_str,
    }
    _write_atomic(journal_path, json.dumps(converting, indent=2) + "\n")
    # The writer must retain the lock if this process dies during conversion.
    inherited_lock = (lock_fd,)
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
            pass_fds=inherited_lock,
        )
        subprocess.run(
            ["qemu-img", "rebase", "-b", str(backing), "-F", "qcow2", str(target)],
            check=True,
            pass_fds=inherited_lock,
        )
    else:
        subprocess.run(
            [*conversion, str(disk), str(target)], check=True, pass_fds=inherited_lock
        )
    subprocess.run(
        ["qemu-img", "compare", "-q", str(disk), str(target)],
        check=True,
        pass_fds=inherited_lock,
    )
    after = target.stat().st_blocks * 512
    if sha(disk) != orig_sha:
        raise ValueError("Original disk modified during compaction; preserving evidence")
    prep_sha = sha(target)
    record = {
        "virtual_bytes_compared_equal": True,
        "allocated_before": before,
        "allocated_after": after,
        "compacted_sha256": prep_sha,
        "original_sha256": orig_sha,
        "backing_file": backing_str,
    }
    journal_data = {
        "original_sha256": orig_sha,
        "receipt": record,
    }
    _sync_file(target)
    _write_atomic(journal_path, json.dumps(journal_data, indent=2) + "\n")
    _finalize_compaction(directory, target, disk, receipt_path, journal_path, record)
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


def verify_extended_receipt(receipt, *, kind, iso_hash, source, base):
    """Bind the selected regression evidence to this ISO, source and base VM."""
    if kind == "network-virtio":
        valid_case = receipt.get("model") == "virtio"
    elif kind == "failure-pre-hook":
        valid_case = (
            receipt.get("case") == "pre-hook"
            and receipt.get("bios") is False
            and receipt.get("encrypted") is False
        )
    else:
        raise ValueError("Extended acceptance has an unknown case")
    if (
        not valid_case
        or receipt.get("passed") is not True
        or receipt.get("iso_sha256") != iso_hash
        or receipt.get("source_revision") != source
        or receipt.get("base") != str(base)
    ):
        raise ValueError("Extended acceptance receipt does not match its qualified ISO and source")


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


def lock_work(work):
    fd = os.open(str(work), os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(fd)
        raise RuntimeError(f"Private work directory is locked: {work}") from error
    except Exception:
        os.close(fd)
        raise
    return fd


def save_run_record(work, record):
    _write_atomic(work / "release-run.json", json.dumps(record, indent=2) + "\n")


def load_run_record(work):
    record_file = work / "release-run.json"
    if record_file.is_symlink() or not record_file.is_file():
        raise ValueError("Unbound legacy release record is not resumable")
    record = json.loads(record_file.read_text())
    required_keys = {
        "work", "output", "cache", "source_revision", "test_logs",
        "iso", "iso_sha256", "metadata_hashes", "repository_json_sha256", "cases",
    }
    if (
        not isinstance(record, dict)
        or record.get("version") != 1
        or not required_keys.issubset(record)
        or not isinstance(record["cases"], dict)
    ):
        raise ValueError("Unbound legacy release record is not resumable")
    return record


def verify_run_identity(record, work, output, cache, source):
    for name, path in (("work", work), ("output", output), ("cache", cache)):
        if record[name] != str(path):
            raise ValueError(f"Resume {name} directory differs from recorded run")
    if record["source_revision"] != source:
        raise ValueError("Acceptance test source revision changed before resume")


def require_cold_release_inputs(inputs):
    if (
        inputs.get("empty_cache_requested") is not True
        or inputs.get("legacy_cache_seeded") is not False
        or inputs.get("source_dirty") is not False
    ):
        raise ValueError("Fresh release requires clean source and an unseeded empty build cache")


def require_recovery_receipt(directory):
    path = directory / "rollback-pass.txt"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Snapshot recovery did not produce a regular passing receipt")
    return path


def repository_provenance(output, repo):
    digest = sha(repo / "repository.json")
    if (output / "server-repository-provenance.sha256").read_text().split() != [
        digest, "repository.json"
    ]:
        raise ValueError("Staged repository.json digest does not match builder repository provenance")
    return digest


def verify_resume_record(record, work, output, cache, source, repo, iso, iso_hash):
    verify_run_identity(record, work, output, cache, source)
    if record["iso_sha256"] != iso_hash or record["iso"] != str(iso):
        raise ValueError("Resume ISO differs from recorded run")
    if repository_provenance(output, repo) != record["repository_json_sha256"]:
        raise ValueError("Staged repository.json digest differs from recorded run")
    for directory, hashes, required in (
        (work, record["test_logs"], REQUIRED_TEST_LOGS),
        (output, record["metadata_hashes"], REQUIRED_METADATA_FILES),
    ):
        if not isinstance(hashes, dict) or set(hashes) != set(required):
            raise ValueError("Run record is missing required evidence hashes")
        for name in required:
            path = directory / name
            if not path.is_file() or sha(path) != hashes[name]:
                raise ValueError(f"Required completed test log or build metadata missing or changed: {name}")


def owned_case_path(cases, name, prefix, runs):
    if name not in cases:
        return None
    directory = Path(cases[name]["directory"])
    if (
        directory.parent != runs
        or not directory.name.startswith(prefix)
        or directory.is_symlink()
        or (directory.exists() and not directory.is_dir())
    ):
        raise ValueError(f"Invalid run-owned install directory: {directory}")
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--port", type=int, default=2452)
    parser.add_argument("--repository", type=Path, help="Local signed server channel to qualify")
    parser.add_argument(
        "--resume-work",
        type=Path,
        help="Resume a verified completed build without replacing earlier artifacts",
    )
    args = parser.parse_args()
    if args.resume_work and args.repository:
        parser.error("Resume uses the repository already staged in the work directory")
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
    fixture_signing_key(FINGERPRINT)
    if args.resume_work:
        work = args.resume_work.resolve()
        if not work.is_relative_to(ROOT / ".server-work") or not work.is_dir():
            raise ValueError(
                "Resume only this checkout's recorded private work directory"
            )
    else:
        work = ROOT / ".server-work" / ("release-" + time.strftime("%Y%m%d-%H%M%S"))
        work.mkdir(parents=True, mode=0o700)
    work_lock = lock_work(work)
    try:
        _main_locked(args, output, cache, work, work_lock)
    finally:
        os.close(work_lock)


def _main_locked(args, output, cache, work, work_lock):
    server_runs = (ROOT / "test-runs/server").resolve()
    server_runs.mkdir(parents=True, exist_ok=True)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    if not args.resume_work:
        repo = work / "channel/stable/x86_64"
        spec = importlib.util.spec_from_file_location(
            "package_repository", ROOT / "builder/server/package-repository.py"
        )
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        if args.repository:
            shutil.copytree(args.repository.resolve(), repo, symlinks=True)
        else:
            repo.mkdir(parents=True)

            def fetch(name):
                with urllib.request.urlopen(
                    CHANNEL + urllib.parse.quote(name), timeout=45
                ) as response:
                    (repo / name).write_bytes(response.read())

            for name in ("repository.json", "repository.json.sig"):
                fetch(name)
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
        for script in SERVER_TEST_SCRIPTS:
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
        if (output / "server-release-acceptance.json").exists():
            raise ValueError("Completed qualified release is immutable; use a new generation")
        run_record = load_run_record(work)
        verify_run_identity(run_record, work, output, cache, source)
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
    require_cold_release_inputs(inputs)
    spec = importlib.util.spec_from_file_location(
        "build_inputs", ROOT / "builder/server/build-inputs.py"
    )
    build_inputs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build_inputs)
    build_inputs.verify_production(ROOT, inputs)
    checksums = dict(
        (line.split()[1], line.split()[0])
        for line in (output / "SHA256SUMS").read_text().splitlines()
    )
    if checksums[iso.name] != iso_hash:
        raise ValueError("Completed build ISO checksum differs")
    expected = json.loads((output / "server-image-packages.json").read_text())

    if args.resume_work:
        verify_resume_record(run_record, work, output, cache, source, repo, iso, iso_hash)
    else:
        run_record = {
            "version": 1,
            "work": str(work),
            "output": str(output),
            "cache": str(cache),
            "source_revision": source,
            "test_logs": {name: sha(work / name) for name in REQUIRED_TEST_LOGS},
            "iso": str(iso),
            "iso_sha256": iso_hash,
            "metadata_hashes": {name: sha(output / name) for name in REQUIRED_METADATA_FILES},
            "repository_json_sha256": repository_provenance(output, repo),
            "cases": {},
        }
        save_run_record(work, run_record)

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
    # Build timestamps must not become seed filesystem identifiers.
    env.pop("SOURCE_DATE_EPOCH", None)
    cases = run_record["cases"]
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

        prefix = f"{work.name}-{wanted}-"
        directory = owned_case_path(cases, name, prefix, server_runs)
        receipt_path = directory / "acceptance-pass.json" if directory else None
        reusing = receipt_path is not None and (receipt_path.exists() or receipt_path.is_symlink())
        if not reusing:
            if "compaction" in cases.get(name, {}):
                raise ValueError(f"Completed acceptance receipt is missing: {receipt_path}")
            directory = server_runs / (prefix + uuid.uuid4().hex)
            receipt_path = directory / "acceptance-pass.json"
            cases[name] = {"directory": str(directory)}
            save_run_record(work, run_record)
            run(
                [
                    "python3",
                    ROOT / "test/server/qemu.py",
                    iso,
                    "--port",
                    str(args.port),
                    "--run-directory",
                    directory,
                    *options,
                ],
                work / (directory.name + ".log"),
                env,
                pass_fds=(work_lock,),
            )
        if receipt_path.is_symlink() or not receipt_path.is_file():
            raise ValueError(f"Expected acceptance pass receipt at {receipt_path}")
        receipt = json.loads(receipt_path.read_text())
        if (
            receipt.get("run_kind") != "install"
            or receipt.get("case") != wanted
            or receipt.get("source_revision") != source
            or receipt.get("iso") != str(iso.resolve())
            or receipt.get("disk") != str(directory / "system.qcow2")
        ):
            raise ValueError(f"Acceptance receipt differs from the run-owned install: {directory}")
        packages = dict(expected["base"])
        if name == "cloud":
            packages.update(expected["cloud_delta"])
        validate_receipt(receipt, iso_hash, packages)
        disk_sha = receipt.get("disk_sha256")
        if not isinstance(disk_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", disk_sha):
            raise ValueError(f"Acceptance receipt is missing the tested disk digest: {directory}")
        backing = bases["base"] / "system.qcow2" if name != "base" else None
        recorded = cases[name]
        if "compaction" in recorded:
            compaction = recorded["compaction"]
            disk = directory / "system.qcow2"
            sidecar = directory / "compaction.json"
            if (
                recorded["receipt"] != receipt
                or disk.is_symlink()
                or not disk.is_file()
                or sidecar.is_symlink()
                or not sidecar.is_file()
                or json.loads(sidecar.read_text()) != compaction
                or compaction["original_sha256"] != disk_sha
                or compaction["backing_file"] != (str(backing) if backing else None)
                or sha(disk) != compaction["compacted_sha256"]
            ):
                raise ValueError(f"Completed acceptance evidence changed: {directory}")
        else:
            compaction = compact(directory, backing, expected_sha256=disk_sha)
        if reusing:
            print("REUSING_VERIFIED_INSTALL", wanted, directory, flush=True)
        cases[name] = {
            "directory": str(directory),
            "receipt": receipt,
            "compaction": compaction,
        }
        bases[name] = directory
        save_run_record(work, run_record)
    additional = {}
    for name, command, marker in (
        ("interactive", ["python3", ROOT / "test/server/console.py", iso, "--port", str(args.port)], "console-pass.json"),
        ("acpi", ["python3", ROOT / "test/server/acpi.py", bases["base"], "--iso", iso, "--port", str(args.port)], "acpi-pass.json"),
        ("signed-update", ["python3", ROOT / "test/server/update.py", bases["base"], iso, "--repository", repo, "--port", str(args.port)], "update-pass.json"),
    ):
        reserve()
        directory = produced(command, work / (name + ".log"), marker, env)
        receipt = json.loads((directory / marker).read_text())
        if not receipt["passed"] or receipt["iso_sha256"] != iso_hash:
            raise ValueError("Additional acceptance does not identify this ISO")
        if name == "interactive":
            compact(directory, bases["base"] / "system.qcow2")
        additional[name] = {"directory": str(directory), "receipt": receipt}
    # Keep the mandatory extension small: one dual-stack NIC and one
    # pre-transaction failure followed by recovery on disposable overlays.
    reserve()
    network = produced(
        ["python3", ROOT / "test/server/networking.py", bases["base"],
         "--model", "virtio", "--port", str(args.port)],
        work / "network-virtio.log", "network-pass.json", env,
    )
    network_receipt = json.loads((network / "network-pass.json").read_text())
    verify_extended_receipt(network_receipt, kind="network-virtio", iso_hash=iso_hash,
                            source=source, base=bases["base"])
    additional["network-virtio"] = {"directory": str(network), "receipt": network_receipt}

    reserve()
    fault_staging = work / "failure-fixtures"
    run(
        ["python3", ROOT / "test/server/prepare_failures.py",
         "--repository", repo, "--mirror", cache / "mirror",
         "--directory", fault_staging],
        work / "failure-fixtures.log", env,
    )
    failure = produced(
        ["python3", ROOT / "test/server/failures.py", bases["base"],
         fault_staging / "packages", "--case", "pre-hook", "--port", str(args.port)],
        work / "failure-pre-hook.log", "failure-pass.json", env,
    )
    failure_receipt = json.loads((failure / "failure-pass.json").read_text())
    verify_extended_receipt(failure_receipt, kind="failure-pre-hook", iso_hash=iso_hash,
                            source=source, base=bases["base"])
    additional["failure-pre-hook"] = {"directory": str(failure), "receipt": failure_receipt}
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
            "--in-place",
            recovery,
            "--port",
            str(args.port),
        ],
        work / "recovery.log",
        env,
    )
    require_recovery_receipt(recovery)
    vm = VM(recovery, recovery / "system.qcow2", args.port, [], share=repo)
    try:
        vm.wait_ssh()
        restored = health(vm)
        restored["trust"] = vm.sudo(
            "mkdir -p /run/release-channel; mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/release-channel; pacman-key --verify /run/release-channel/omarchy-server.db.sig /run/release-channel/omarchy-server.db"
        )
    finally:
        vm.stop()
    current_source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    verify_resume_record(run_record, work, output, cache, current_source, repo, iso, sha(iso))
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Acceptance source changed during qualification")
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
        "additional_acceptance": additional,
        "restored_health": restored,
        "runtime_tests_revision": RUNTIME,
        "work": str(work),
        "limits": [
            "Fresh build and functional reproduction, not byte-identical ISO reproduction",
            "Shared installer tests ran; desktop ISO build was not repeated",
            "Physical hardware and Secure Boot remain unverified",
        ],
    }
    _write_atomic(
        output / "server-release-acceptance.json", json.dumps(record, indent=2) + "\n"
    )
    print("SERVER_FRESH_RELEASE_ACCEPTANCE_PASS", output, flush=True)


if __name__ == "__main__":
    main()
