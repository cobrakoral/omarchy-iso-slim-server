#!/usr/bin/env python3
"""Measured short workload and encrypted external-to-guest backup recovery.

All disks are disposable. The recovery disk is a new standalone installation;
its source guest is stopped, and its backing graph cannot reference that guest.
The host repository is not an independent offsite failure domain.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import time

from qemu import ROOT, VM, run
from repository import health
from release import compact, reserve, produced

FINGERPRINT = "7C5C6FEB40A5B493007FAA3650B1EB533F640140"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def logged(args, path):
    with path.open("w") as output:
        subprocess.run(
            [str(x) for x in args],
            cwd=ROOT,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )


def ssh_args(directory, port):
    return [
        "-i",
        str(directory / "key"),
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-P",
        str(port),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--port", type=int, default=2462)
    args = parser.parse_args()
    base, iso = args.base.resolve(), args.iso.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use this project's stopped accepted source baseline")
    if args.seconds < 10:
        raise ValueError("At least 10 seconds of workload required")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Commit the acceptance code first")
    reserve()
    iso_hash = sha(iso)
    base_hash = sha(base / "system.qcow2")
    source_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = ROOT / "test-runs/server" / (stamp + "-operational-source")
    directory.mkdir(mode=0o700)
    shutil.copy2(base / "key", directory / "key")
    run(
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base / "system.qcow2"),
        str(directory / "system.qcow2"),
    )
    work = ROOT / ".server-work/round6" / stamp
    share = work / "source-share"
    share.mkdir(parents=True, mode=0o700)
    for name in ("operational_guest.py", "operational_workload.py"):
        shutil.copy2(ROOT / "test/server" / name, share / name)
    keydir = work / "keys"
    keydir.mkdir(mode=0o700)
    key = keydir / "backup.passphrase"
    key.write_text(secrets.token_urlsafe(48) + "\n")
    key.chmod(0o600)
    backups = work / "external-backup"
    backups.mkdir(mode=0o700)
    results = {}
    vm = None

    def boot(disk_directory, media):
        reserve()
        guest = VM(
            disk_directory, disk_directory / "system.qcow2", args.port, [], share=media
        )
        try:
            guest.wait_ssh()
            guest.sudo(
                "mkdir -p /run/operational; mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/operational"
            )
        except BaseException:
            guest.stop()
            raise
        return guest

    def phase(name, extra=""):
        print("OPERATIONAL_PHASE", name, flush=True)
        value = json.loads(
            vm.sudo(
                "python3 /run/operational/operational_guest.py " + name + " " + extra
            )
        )
        assert value["passed"]
        (work / (name + ".json")).write_text(json.dumps(value, indent=2) + "\n")
        results[name] = value
        return value

    try:
        vm = boot(directory, share)
        original_identity = vm.sudo(
            "cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub"
        )
        before = health(vm)
        assert before["packages"] == 176
        phase("install")
        phase("soak", "--seconds " + str(args.seconds))
        soaked = results["soak"]["database"]
        vm.stop()
        vm = boot(directory, share)
        assert phase("check")["database"] == soaked
        backup_started = time.monotonic()
        backup = phase("backup")
        plain = backups / "application.tar"
        logged(
            [
                "scp",
                *ssh_args(directory, args.port),
                "admin@127.0.0.1:" + backup["backup"],
                plain,
            ],
            work / "backup-transfer.log",
        )
        plain.chmod(0o600)
        cipher = backups / "application.tar.gpg"
        logged(
            [
                "gpg",
                "--batch",
                "--yes",
                "--pinentry-mode",
                "loopback",
                "--passphrase-file",
                key,
                "--symmetric",
                "--cipher-algo",
                "AES256",
                "--output",
                cipher,
                plain,
            ],
            work / "backup-encryption.log",
        )
        logged(
            [
                "gpg",
                "--batch",
                "--yes",
                "--local-user",
                FINGERPRINT,
                "--detach-sign",
                cipher,
            ],
            work / "backup-signing.log",
        )
        cipher_hash = sha(cipher)
        plain.unlink()
        backup_completed = time.monotonic()
        results["backup_host"] = {
            "sha256": cipher_hash,
            "bytes": cipher.stat().st_size,
            "seconds": backup_completed - backup_started,
            "outside_guest": True,
            "independent_offsite_storage": False,
        }
        phase("snapshot")
        high_water = results["snapshot"]["database"]["records"]
        vm.stop()
        vm = None
        logged(
            [
                "python3",
                ROOT / "test/server/recovery.py",
                directory,
                "--port",
                str(args.port),
            ],
            work / "source-boundary-recovery.log",
        )
        vm = boot(directory, share)
        phase("boundaries")
        assert (
            results["boundaries"]["database"]["records"]
            == backup["source_after_backup"]["records"]
        )
        source_health = health(vm)
        vm.stop()
        vm = None
        unavailable = time.monotonic()
        # No source VM is started or read after this point. Only the encrypted backup,
        # its separate key, test code and fresh installation media are used to recover.
        spec = importlib.util.spec_from_file_location(
            "package_repository", ROOT / "builder/server/package-repository.py"
        )
        package = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(package)
        package.pinned_signature(cipher)
        bad = backups / "tampered.tar.gpg"
        blob = bytearray(cipher.read_bytes())
        blob[len(blob) // 2] ^= 1
        bad.write_bytes(blob)
        bad_output = backups / "rejected-plaintext.tar"
        corrupted = subprocess.run(
            [
                "gpg",
                "--batch",
                "--yes",
                "--pinentry-mode",
                "loopback",
                "--passphrase-file",
                str(key),
                "--output",
                str(bad_output),
                "--decrypt",
                str(bad),
            ],
            capture_output=True,
        )
        assert corrupted.returncode != 0
        if bad_output.exists():
            bad_output.unlink()
        try:
            package.pinned_signature(bad, Path(str(cipher) + ".sig"))
        except subprocess.CalledProcessError:
            signature_rejected = True
        else:
            raise AssertionError("Tampered ciphertext signature accepted")
        missing = subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase-file",
                str(keydir / "absent-key"),
                "--output",
                str(bad_output),
                "--decrypt",
                str(cipher),
            ],
            capture_output=True,
        )
        assert missing.returncode != 0 and not bad_output.exists()
        results["negative_backup"] = {
            "tampered_signature_rejected": signature_rejected,
            "gpg_authentication_rejected_corruption": True,
            "missing_key_rejected": True,
            "no_unverified_archive_delivered": True,
        }
        destination = produced(
            ["python3", ROOT / "test/server/qemu.py", iso, "--port", str(args.port)],
            work / "fresh-destination-install.log",
            "acceptance-pass.json",
        )
        receipt = json.loads((destination / "acceptance-pass.json").read_text())
        assert receipt["iso_sha256"] == iso_hash and not receipt["source_dirty"]
        compact(
            destination
        )  # Still standalone; never rebase recovery onto a source disk.
        chain = json.loads(
            subprocess.check_output(
                [
                    "qemu-img",
                    "info",
                    "--backing-chain",
                    "--output=json",
                    str(destination / "system.qcow2"),
                ],
                text=True,
            )
        )
        assert len(chain) == 1
        delivery = work / "recovery-delivery"
        delivery.mkdir(mode=0o700)
        for name in ("operational_guest.py", "operational_workload.py"):
            shutil.copy2(share / name, delivery / name)
        restore_started = time.monotonic()
        package.pinned_signature(cipher)
        assert sha(cipher) == cipher_hash
        tar = delivery / "application.tar"
        assert not tar.exists()
        logged(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase-file",
                key,
                "--output",
                tar,
                "--decrypt",
                cipher,
            ],
            work / "backup-decryption.log",
        )
        tar.chmod(0o600)
        vm = boot(destination, delivery)
        fresh_identity = vm.sudo(
            "cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub"
        )
        assert fresh_identity != original_identity
        clean = vm.sudo(
            "test ! -e /opt/server-acceptance; test ! -e /var/lib/server-acceptance; test ! -e /var/log/server-acceptance-marker; test ! -e /var/cache/pacman/pkg/server-acceptance-marker; printf FRESH_TARGET_EMPTY"
        )
        restored = phase("restore", "--archive /run/operational/application.tar")
        assert restored["database"] == backup["database"]
        restored_at = time.monotonic()
        assert (
            vm.sudo("cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub")
            == fresh_identity
        )
        post = phase("post_restore")
        assert post["database"]["records"] == backup["database"]["records"] + 1
        restored_health = health(vm)
        assert restored_health["packages"] == 176
        vm.stop()
        vm = boot(destination, delivery)
        assert phase("check")["database"] == post["database"]
        cold_health = health(vm)
        assert (
            vm.sudo("cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub")
            == fresh_identity
        )
        assert sha(base / "system.qcow2") == base_hash and sha(iso) == iso_hash
        assert (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip()
            == source_revision
        )
        record = {
            "passed": True,
            "source_revision": source_revision,
            "iso": str(iso),
            "iso_sha256": iso_hash,
            "source_base": str(base),
            "source_base_sha256": base_hash,
            "source": str(directory),
            "destination": str(destination),
            "destination_standalone": True,
            "fresh_identity_preserved": True,
            "work": str(work),
            "phases": results,
            "fresh_target_empty": clean,
            "source_health": source_health,
            "restored_health": restored_health,
            "cold_health": cold_health,
            "rto_seconds_including_fresh_install": restored_at - unavailable,
            "restore_seconds": restored_at - restore_started,
            "backup_age_at_source_stop_seconds": unavailable - backup_completed,
            "acknowledged_records_after_backup_not_recovered": high_water
            - backup["database"]["records"],
            "backup_ciphertext_sha256": cipher_hash,
            "backup_ciphertext": str(cipher),
            "key_escrow": str(key),
            "limits": [
                "Short synthetic workload, not multiweek validation",
                "Backup external to guest on same VM host; offsite/host-loss recovery not established",
                "Application/config/user-data restore onto fresh OS, not a whole-disk image restore",
                "Physical hardware, Secure Boot and public IPv6 remain unverified",
            ],
        }
        (work / "operational-pass.json").write_text(json.dumps(record, indent=2) + "\n")
        print("OPERATIONAL_BACKUP_RESTORE_PASS", work, flush=True)
    finally:
        if vm is not None:
            vm.stop()


if __name__ == "__main__":
    main()
