#!/usr/bin/env python3
"""Exercise a real package upgrade and pre-update snapshot without internet."""

import argparse
from pathlib import Path
import shutil
from qemu import VM, ROOT, run, seed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--bios", action="store_true")
    opts = parser.parse_args()
    directory = opts.directory.resolve()
    if not directory.is_relative_to(ROOT / "test-runs/server"):
        raise ValueError("Disposable test directory required")
    fixture = directory / "update-fixture"
    fixture.mkdir(exist_ok=True)
    archives = []
    for version in (1, 2):
        build = fixture / f"v{version}"
        build.mkdir(exist_ok=True)
        (build / "PKGBUILD").write_text(f"""pkgname=omarchy-server-update-fixture
pkgver={version}
pkgrel=1
arch=('any')
license=('MIT')
options=('!debug' '!strip')
package() {{
  install -d "$pkgdir/usr/share/omarchy-server-update-fixture"
  printf '%s\\n' "$pkgver" > "$pkgdir/usr/share/omarchy-server-update-fixture/version"
}}
""")
        run("makepkg", "--nodeps", "--noconfirm", "--force", cwd=build)
        archives.append(next(build.glob("*.pkg.tar.zst")))
    tree = fixture / "media"
    tree.mkdir(exist_ok=True)
    shutil.copy(archives[0], tree / "version1.pkg.tar.zst")
    shutil.copy(archives[1], tree / archives[1].name)
    run("repo-add", str(tree / "server-test.db.tar.gz"), str(tree / archives[1].name))
    media = fixture / "repo.iso"
    run(
        "xorriso",
        "-as",
        "mkisofs",
        "-quiet",
        "-V",
        "server-update-test",
        "-o",
        str(media),
        str(tree),
    )
    disk = directory / "system.qcow2"
    vm = VM(directory, disk, 2422, [opts.iso.resolve(), media], bios=opts.bios)
    try:
        vm.wait_ssh()
        log = vm.sudo(r"""
mkdir -p /run/server-iso /run/server-live /run/server-update-test
mount -o ro /dev/sr0 /run/server-iso
mount -o loop,ro /run/server-iso/arch/x86_64/airootfs.sfs /run/server-live
mount -o ro /dev/sr1 /run/server-update-test
cp /etc/pacman.conf /run/server-online-pacman.conf
trap 'cp /run/server-online-pacman.conf /etc/pacman.conf; umount /run/server-update-test /run/server-live /run/server-iso' EXIT
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture=auto
SigLevel=Never
LocalFileSigLevel=Never
[server-test]
Server=file:///run/server-update-test/
[offline]
Server=file:///run/server-live/var/cache/omarchy/mirror/offline/
CONF
# Only this test's generated cache entries: a repeated fixture build can
# produce different bytes at the same deliberately simple version number.
rm -f /var/cache/pacman/pkg/omarchy-server-update-fixture-*.pkg.tar.*
pacman -U --noconfirm /run/server-update-test/version1.pkg.tar.zst
test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 1
omarchy-update -y
test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 2
pacman -Q omarchy-server-update-fixture
find /.snapshots -path '*/snapshot/usr/share/omarchy-server-update-fixture/version' -exec grep -l '^1$' {} + | grep .
# Repeated actual snapshot commands must retain exactly five numbered roots.
for i in 1 2 3 4 5 6 7; do omarchy-snapshot create; done
snapper --no-dbus -c root list
count=$(grep -l '<cleanup>number</cleanup>' /.snapshots/*/info.xml | wc -l)
test "$count" = 5
systemctl start snapper-cleanup.service
systemctl is-active snapper-cleanup.timer
""")
        (directory / "update-pass.txt").write_text(log)
    finally:
        vm.stop()
    vm = VM(directory, disk, 2422, [], bios=opts.bios)
    try:
        vm.wait_ssh()
        vm.sudo(
            'test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 2; systemctl is-active systemd-networkd sshd'
        )
        print("OFFLINE_PACKAGE_UPGRADE_SNAPSHOT_RETENTION_REBOOT_PASS", flush=True)
    finally:
        vm.stop()


if __name__ == "__main__":
    main()
