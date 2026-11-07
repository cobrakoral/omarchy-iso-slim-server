#!/usr/bin/env python3
"""Verify an ISO-installed server trusts the signed channel without bootstrap."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
from qemu import PASSWORD, ROOT, VM, run
from repository import health


def installed_packages(vm):
    packages = {}
    for line in vm.sudo("pacman -Q").splitlines():
        line = line.strip()
        if not line:
            continue
        name, version = line.split(maxsplit=1)
        packages[name] = version
    return packages


def verify_packages(actual, expected, context):
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        mismatched = sorted(
            f"{pkg} (expected {expected[pkg]}, got {actual[pkg]})"
            for pkg in (set(actual) & set(expected))
            if actual[pkg] != expected[pkg]
        )
        drift = []
        if missing:
            drift.append(f"missing: {missing}")
        if extra:
            drift.append(f"extra: {extra}")
        if mismatched:
            drift.append(f"version mismatch: {mismatched}")
        raise AssertionError(
            f"Installed package drift {context}: {'; '.join(drift)}"
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("repository", type=Path)
    parser.add_argument("--port", type=int, default=2423)
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--bios", action="store_true")
    args = parser.parse_args()
    base, repository = args.base.resolve(), args.repository.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use an accepted disposable ISO installation")
    if not repository.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use the verified isolated production repository")
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host below 8 GiB free")
    receipt = json.loads((base / "acceptance-pass.json").read_text())
    cloud = receipt["case"].startswith("cloud")
    expected_packages = receipt["packages"]
    provenance = json.loads((repository / "repository.json").read_text())
    label = "-luks" if args.encrypt else "-bios" if args.bios else ""
    directory = (
        ROOT
        / "test-runs/server"
        / (time.strftime("%Y%m%d-%H%M%S") + "-installed-repository" + label)
    )
    directory.mkdir(mode=0o700)
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

    def boot():
        vm = VM(directory, disk, args.port, [], bios=args.bios, share=repository)
        try:
            if args.encrypt:
                vm.child.expect("(?i)passphrase", timeout=180)
                vm.child.sendline(PASSWORD)
            vm.wait_ssh()
        except BaseException:
            vm.stop()
            raise
        return vm

    vm = boot()
    try:
        verify_packages(
            installed_packages(vm),
            expected_packages,
            "before channel transaction",
        )
        text = vm.sudo("""
omarchy-server-repository --check
grep -F 'server-packages/stable/$arch' /etc/pacman.d/omarchy-server.conf
mkdir -p /run/server-channel
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-channel
# No pacman-key --add or --lsign-key: the installer must already trust this key.
pacman-key --verify /run/server-channel/omarchy-server.db.sig /run/server-channel/omarchy-server.db
cat > /run/channel-test.conf <<'CONF'
[options]
Architecture=auto
SigLevel=PackageRequired DatabaseRequired TrustedOnly
[omarchy-server]
Server=file:///run/server-channel/
CONF
pacman --config /run/channel-test.conf -Syyu --noconfirm omarchy-server-keyring
omarchy-server-migrate
test -f /var/lib/omarchy-server/migrations/0001-package-repository.json
test ! -e /var/lib/omarchy-server/migrations/pending.json
""")
        hashes = {}
        for name in (
            "omarchy-update",
            "omarchy-server-migrate",
            "omarchy-server-repository",
        ):
            expected = provenance["source_files"]["builder/server/runtime/bin/" + name]
            actual = vm.sudo("sha256sum /usr/bin/" + name).split()[0]
            if actual != expected:
                raise AssertionError(
                    "Installed ISO runtime differs from signed source: " + name
                )
            hashes[name] = actual
        before = health(vm, allow_cloud=cloud)
        (directory / "channel-trust.txt").write_text(text)
    finally:
        vm.stop()
    vm = boot()
    try:
        after = health(vm, allow_cloud=cloud)
        verify_packages(
            installed_packages(vm),
            expected_packages,
            "after reboot",
        )
        assert before["packages"] == after["packages"] == len(expected_packages)
    finally:
        vm.stop()
    record = {
        "base": str(base),
        "passed": True,
        "package_count": after["packages"],
        "trusted_without_manual_bootstrap": True,
        "runtime_sha256": hashes,
        "repository_manifest_sha256": hashlib.sha256(
            (repository / "repository.json").read_bytes()
        ).hexdigest(),
        "health": after,
    }
    (directory / "installed-repository-pass.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print("ISO_INSTALLED_SIGNED_CHANNEL_PASS", directory, after["packages"], flush=True)


if __name__ == "__main__":
    main()
