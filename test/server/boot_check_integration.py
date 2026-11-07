#!/usr/bin/env python3
"""Install the maintenance package and reject bad boot artifacts in QEMU."""

import argparse
import json
from pathlib import Path
import shutil
import time
from qemu import PASSWORD, ROOT, VM, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("packages", type=Path)
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    base, packages = opts.base.resolve(), opts.packages.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("An accepted disposable installation is required")
    if not packages.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use the isolated package staging workspace")
    case = "bios" if opts.bios else "luks" if opts.encrypt else "uefi"
    directory = (
        ROOT
        / "test-runs/server"
        / (time.strftime("%Y%m%d-%H%M%S") + "-boot-check-" + case)
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
    print(f"Evidence: {directory}", flush=True)
    vm = VM(directory, disk, opts.port, [], bios=opts.bios, share=packages)
    try:
        if opts.encrypt:
            vm.child.expect("(?i)passphrase", timeout=180)
            vm.child.sendline(PASSWORD)
        vm.wait_ssh()
        log = vm.sudo("""
mkdir -p /run/server-upgrade
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-upgrade
pacman-key --add /run/server-upgrade/MAINTAINER.asc
pacman-key --lsign-key 7C5C6FEB40A5B493007FAA3650B1EB533F640140
cp /etc/pacman.conf /run/boot-check-pacman.conf
sed -i 's/^LocalFileSigLevel.*/LocalFileSigLevel = Required/' /run/boot-check-pacman.conf
pacman --config /run/boot-check-pacman.conf -U --noconfirm /run/server-upgrade/current/omarchy-server-*.pkg.tar.zst
omarchy-server-boot-check
if test -d /sys/firmware/efi; then
  artifact=/boot/EFI/Linux/omarchy_linux-omarchy.efi
else
  artifact=/boot/$(cat /etc/machine-id)/linux-omarchy/vmlinuz
fi
cp "$artifact" /run/boot-artifact.backup
trap 'cp /run/boot-artifact.backup "$artifact"; sync' EXIT
if test -d /sys/firmware/efi; then
  python - "$artifact" <<'PY'
import pefile,sys
from pathlib import Path
path=Path(sys.argv[1])
data=bytearray(path.read_bytes())
with pefile.PE(data=data,fast_load=True) as image:
    section=next(s for s in image.sections if s.Name.rstrip(b'\\0') == b'.uname')
    data[section.PointerToRawData] ^= 1
path.write_bytes(data)
PY
else
  printf corrupt-kernel > "$artifact"
fi
if omarchy-server-boot-check > /run/rejected-artifact.txt 2>&1; then
  echo "ERROR: invalid boot artifact accepted"; exit 1
fi
cat /run/rejected-artifact.txt
cp /run/boot-artifact.backup "$artifact"
trap - EXIT
omarchy-server-boot-check
before=$(snapper --csvout -c root list --columns number)
umount /boot
trap 'mountpoint -q /boot || mount /boot' EXIT
if omarchy-update -y > /run/rejected-update.txt 2>&1; then
  echo "ERROR: update accepted unmounted /boot"; exit 1
fi
grep -F '/boot is not mounted' /run/rejected-update.txt
test "$before" = "$(snapper --csvout -c root list --columns number)"
mount /boot
trap - EXIT
omarchy-server-boot-check
systemctl is-active sshd systemd-networkd systemd-resolved
sync
""")
        (directory / "boot-check.txt").write_text(log)
    finally:
        vm.stop()
    # Prove the rejected corruption tests left a bootable guest.
    vm = VM(directory, disk, opts.port, [], bios=opts.bios)
    try:
        if opts.encrypt:
            vm.child.expect("(?i)passphrase", timeout=180)
            vm.child.sendline(PASSWORD)
        vm.wait_ssh()
        log = vm.sudo(
            "uname -r; omarchy-server-boot-check; systemctl is-active sshd systemd-networkd systemd-resolved"
        )
        (directory / "reboot.txt").write_text(log)
    finally:
        vm.stop()
    (directory / "boot-check-pass.json").write_text(
        json.dumps(
            {
                "case": case,
                "checks": [
                    "valid artifacts",
                    "reject stale UKI or corrupt BIOS kernel",
                    "reject unmounted ESP before snapshot or transaction",
                    "cold reboot",
                ],
            },
            indent=2,
        )
        + "\n"
    )
    print(f"BOOT_CHECK_{case.upper()}_REJECTION_REBOOT_PASS", flush=True)


if __name__ == "__main__":
    main()
