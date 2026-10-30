#!/usr/bin/env python3
"""Real signed system-package upgrade, cold boot, snapshot restore and cold boot.

The historical userspace is constructed from signed Arch archive packages.
The kernel upgrade uses Omarchy edge explicitly; this is not a stable-channel
release-to-release certification. Guest package access is read-only local 9p.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from qemu import ROOT, VM, run

PACKAGES = (
    "linux-omarchy",
    "systemd",
    "systemd-libs",
    "systemd-sysvcompat",
    "systemd-ukify",
    "limine",
    "limine-mkinitcpio-hook",
    "limine-snapper-sync-server",
    "omarchy-server",
)
OLD = {
    "linux-omarchy": "7.2.8-9",
    "systemd": "262-1",
    "limine": "12.9.3-1",
    **{p: "262-1" for p in ("systemd-libs", "systemd-sysvcompat", "systemd-ukify")},
}
NEW = {
    "linux-omarchy": "7.2.8-10",
    "systemd": "262.1-1",
    "limine": "12.9.4-1",
    **{p: "262.1-1" for p in ("systemd-libs", "systemd-sysvcompat", "systemd-ukify")},
}


def observe(vm, directory, phase, expected):
    kernel = vm.sudo("uname -r").strip()
    versions = dict(
        line.split() for line in vm.sudo("pacman -Q " + " ".join(PACKAGES)).splitlines()
    )
    for package, version in expected.items():
        if versions[package] != version:
            raise AssertionError(
                f"{phase}: {package} is {versions[package]}, expected {version}"
            )
    if kernel != expected["linux-omarchy"] + "-omarchy":
        raise AssertionError(f"{phase}: running kernel mismatch: {kernel}")
    names = vm.sudo("pacman -Qq").split()
    spec = importlib.util.spec_from_file_location(
        "server_packages", ROOT / "builder/server/check-packages.py"
    )
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    checker.check(names)
    proof = vm.sudo("""
test -d "/usr/lib/modules/$(uname -r)"
modinfo -k "$(uname -r)" btrfs
systemctl is-active systemd-networkd systemd-resolved sshd
systemctl is-active snapper-cleanup.timer limine-snapper-sync.service
omarchy-server-boot-check
test "$(findmnt -no FSTYPE /)" = btrfs
! grep -q "/.snapshots/" /proc/cmdline
cat /proc/cmdline
""")
    record = {
        "phase": phase,
        "kernel": kernel,
        "packages": versions,
        "package_count": len(names),
        "checks": proof,
    }
    (directory / f"{phase}.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"{phase.upper()}_BOOT_PASS {versions}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path, help="Stopped disposable UEFI base run")
    parser.add_argument(
        "packages", type=Path, help="Output of prepare_system_upgrade.py"
    )
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    base, packages = opts.base.resolve(), opts.packages.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use a previously accepted disposable installation")
    if not packages.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use the isolated package staging workspace")
    for required in ("MAINTAINER.asc", "current/server-upgrade.db", "provenance.json"):
        if not (packages / required).is_file():
            raise ValueError(
                f"Incomplete package staging: {required}; run prepare_system_upgrade.py first"
            )
    directory = (
        opts.directory
        or ROOT
        / "test-runs/server"
        / (time.strftime("%Y%m%d-%H%M%S") + "-system-upgrade")
    ).resolve()
    if not directory.is_relative_to(ROOT / "test-runs/server"):
        raise ValueError("Disposable test directory required")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copy2(base / "key", directory / "key")
    disk = directory / "system.qcow2"
    run(
        "qemu-img",
        "create",
        "-f",
        "qcow2",
        "-F",
        "qcow2",
        "-b",
        str(base / "system.qcow2"),
        str(disk),
    )
    print(f"Evidence: {directory}", flush=True)

    def boot():
        vm = VM(directory, disk, opts.port, [], share=packages)
        try:
            vm.wait_ssh()
        except BaseException:
            vm.stop()
            raise
        return vm

    vm = boot()
    try:
        log = vm.sudo("""
mkdir -p /run/server-upgrade
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-upgrade
cp /etc/pacman.conf /var/log/server-upgrade-online-pacman.conf
pacman-key --add /run/server-upgrade/MAINTAINER.asc
pacman-key --lsign-key 7C5C6FEB40A5B493007FAA3650B1EB533F640140
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture=auto
SigLevel=Required DatabaseOptional
LocalFileSigLevel=Required
[server-upgrade]
Server=file:///run/server-upgrade/current/
CONF
pacman -U --noconfirm /run/server-upgrade/current/omarchy-server-*.pkg.tar.zst
if pacman -Q omarchy-server-update-fixture >/dev/null 2>&1; then
  pacman -R --noconfirm omarchy-server-update-fixture
fi
pacman -U --noconfirm /run/server-upgrade/old/*.pkg.tar.zst
limine-update
omarchy-server-boot-check
sync
""")
        (directory / "historical-setup.txt").write_text(log)
    finally:
        vm.stop()

    vm = boot()
    try:
        observe(vm, directory, "before", OLD)
        log = vm.sudo("""
mkdir -p /run/server-upgrade
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-upgrade
printf baseline > /etc/rollback-proof
omarchy-update -y
# This is the updater's actual pre-transaction root snapshot, not a new
# post-update snapshot made just to satisfy the recovery harness.
find /.snapshots -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | grep -E '^[0-9]+$' | sort -n | tail -n1 > /var/log/server-test-snapshot
number=$(cat /var/log/server-test-snapshot)
test "$(cat "/.snapshots/$number/snapshot/etc/rollback-proof")" = baseline
grep -A1 '^%VERSION%$' "/.snapshots/$number/snapshot/var/lib/pacman/local/linux-omarchy-7.2.8-9/desc" | grep -Fx 7.2.8-9
printf changed > /etc/rollback-proof
cp /var/log/server-upgrade-online-pacman.conf /etc/pacman.conf
sync
""")
        (directory / "upgrade.txt").write_text(log)
    finally:
        vm.stop()

    vm = boot()
    try:
        observe(vm, directory, "after", NEW)
    finally:
        vm.stop()

    # Existing harness selects the real generated snapshot boot menu entry,
    # runs the upstream restore utility in a terminal, and cold-boots normal @.
    run(
        sys.executable,
        str(ROOT / "test/server/recovery.py"),
        str(directory),
        "--port",
        str(opts.port),
    )
    vm = boot()
    try:
        restored = observe(vm, directory, "restored", OLD)
        vm.sudo(
            'cp /var/log/server-upgrade-online-pacman.conf /etc/pacman.conf; test "$(cat /etc/rollback-proof)" = baseline'
        )
    finally:
        vm.stop()
    receipt = {
        "source_revision": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "source_files_sha256": {
            str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in [
                ROOT / "builder/server/runtime/PKGBUILD",
                *sorted((ROOT / "builder/server/runtime/bin").glob("omarchy-*")),
                Path(__file__).resolve(),
                ROOT / "test/server/prepare_system_upgrade.py",
                ROOT / "test/server/recovery.py",
                ROOT / "test/server/qemu.py",
            ]
            if path.is_file()
        },
        "base": str(base),
        "kernel_channel": "edge",
        "historical_userspace": "constructed from signed Arch archive packages",
        "guest_external_network": False,
        "old": OLD,
        "new": NEW,
        "restored": restored["packages"],
        "unchanged_versions": ["limine-mkinitcpio-hook", "limine-snapper-sync-server"],
    }
    (directory / "system-upgrade-pass.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    print("REAL_SYSTEM_UPGRADE_SNAPSHOT_RESTORE_REBOOT_PASS", flush=True)


if __name__ == "__main__":
    main()
