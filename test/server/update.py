#!/usr/bin/env python3
"""Exercise signed fixture upgrades and snapshot retention on a NEW overlay."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import os
import shutil
import tempfile
import subprocess
from qemu import VM, ROOT, run, working_copy


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fixture_signing_key(fingerprint):
    try:
        key = subprocess.check_output(
            ["git", "-C", str(ROOT), "config", "user.signingkey"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as error:
        raise ValueError("git user.signingkey is not configured") from error
    if not key:
        raise ValueError("git user.signingkey is not configured")
    if key != fingerprint:
        raise ValueError("Fixture signer must match the installed maintainer trust anchor")
    # The guest must trust the exact signing subkey that GPG selects now.
    # Otherwise the signed-update acceptance fails only after building the ISO.
    with tempfile.TemporaryDirectory(prefix="server-signer-preflight-") as name:
        home = Path(name)
        payload, signature = home / "payload", home / "payload.sig"
        payload.write_bytes(b"omarchy-server-signing-preflight\n")
        try:
            for command in (
                ["gpg", "--batch", "--local-user", fingerprint, "--detach-sign",
                 "--output", str(signature), str(payload)],
                ["gpg", "--batch", "--homedir", str(home), "--import",
                 str(ROOT / "builder/server/keyring/omarchy-server.gpg")],
                ["gpg", "--batch", "--homedir", str(home), "--verify",
                 str(signature), str(payload)],
            ):
                subprocess.run(command, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, check=True)
        except subprocess.CalledProcessError as error:
            detail = error.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Maintainer signing key is incompatible with the shipped "
                "server keyring: " + detail
            ) from error
        except FileNotFoundError as error:
            raise RuntimeError("gpg is not installed or not in PATH") from error
    return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("iso", type=Path)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    base = opts.directory.resolve()
    receipt = json.loads((base / "acceptance-pass.json").read_text())
    if receipt["case"] != ("base-bios" if opts.bios else "base"):
        raise ValueError("Use a default accepted installation with matching firmware")
    if receipt["iso_sha256"] != sha(opts.iso):
        raise ValueError("Use the original accepted installation ISO")
    repository = opts.repository.resolve()
    spec = importlib.util.spec_from_file_location("server_repository", ROOT / "builder/server/package-repository.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify(repository)
    key = fixture_signing_key(verifier.FINGERPRINT)
    original_hash = sha(base / "system.qcow2")
    directory = working_copy(base, "signed-update")
    fixture = directory / "update-fixture"
    fixture.mkdir()
    archives = []
    for version in (1, 2):
        build = fixture / f"v{version}"
        build.mkdir()
        (build / "PKGBUILD").write_text(f'''pkgname=omarchy-server-update-fixture
pkgver={version}
pkgrel=1
arch=('any')
license=('MIT')
options=('!debug' '!strip')
package() {{
  install -d "$pkgdir/usr/share/omarchy-server-update-fixture"
  printf '%s\\n' "$pkgver" > "$pkgdir/usr/share/omarchy-server-update-fixture/version"
}}
''')
        run("makepkg", "--nodeps", "--noconfirm", cwd=build)
        archive = next(build.glob("*.pkg.tar.zst"))
        run("gpg", "--batch", "--local-user", key, "--detach-sign", str(archive))
        archives.append(archive)
    tree = fixture / "media"
    tree.mkdir()
    for source in (archives[0], Path(str(archives[0]) + ".sig"), archives[1], Path(str(archives[1]) + ".sig")):
        shutil.copy2(source, tree / source.name)
    run("repo-add", "--sign", "--key", key, "--include-sigs", str(tree / "server-test.db.tar.gz"), str(tree / archives[1].name))
    media = fixture / "repo.iso"
    # Package archives are binary; create their media with the same isolated environment as seed().
    environment = dict(os.environ)
    environment.pop("SOURCE_DATE_EPOCH", None)
    run("xorriso", "-as", "mkisofs", "-quiet", "-V", "server-update-test", "-o", str(media), str(tree), env=environment)
    disk = directory / "system.qcow2"
    vm = VM(directory, disk, opts.port, [opts.iso.resolve(), media], bios=opts.bios, share=repository)
    try:
        vm.wait_ssh()
        log = vm.sudo('''
mkdir -p /run/server-iso /run/server-live /run/server-update-test /run/server-channel
mount -o ro /dev/sr0 /run/server-iso
mount -o loop,ro /run/server-iso/arch/x86_64/airootfs.sfs /run/server-live
mount -o ro /dev/sr1 /run/server-update-test
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-channel
pacman-key --verify /run/server-channel/omarchy-server.db.sig /run/server-channel/omarchy-server.db
cp /etc/pacman.conf /run/server-online-pacman.conf
trap 'cp /run/server-online-pacman.conf /etc/pacman.conf; umount /run/server-channel /run/server-update-test /run/server-live /run/server-iso' EXIT
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture=auto
CheckSpace
SigLevel=Required DatabaseOptional TrustedOnly
LocalFileSigLevel=Required
[omarchy-server]
SigLevel=PackageRequired DatabaseRequired TrustedOnly
Server=file:///run/server-channel/
[server-test]
SigLevel=PackageRequired DatabaseRequired TrustedOnly
Server=file:///run/server-update-test/
[offline]
SigLevel=PackageRequired DatabaseOptional TrustedOnly
Server=file:///run/server-live/var/cache/omarchy/mirror/offline/
CONF
omarchy-server-repository --check
pacman-key --verify /run/server-update-test/server-test.db.sig /run/server-update-test/server-test.db
pacman -U --noconfirm /run/server-update-test/omarchy-server-update-fixture-1-1-any.pkg.tar.zst
test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 1
mkdir -p /run/server-update-fault
printf '#!/bin/sh\\nexit 1\\n' > /run/server-update-fault/limine-update
chmod 755 /run/server-update-fault/limine-update
original=
for attempt in 1 2 3 4 5 6; do
  if PATH="/run/server-update-fault:$PATH" omarchy-update -y > /run/server-update-fault/result 2>&1; then
    echo "Expected the boot-artifact failure" >&2
    exit 1
  fi
  cat /run/server-update-fault/result
  grep -Fq 'Update failed during boot-artifacts' /run/server-update-fault/result
  snapshot=$(cat /var/lib/omarchy-server/update-snapshot)
  if [[ -z $original ]]; then original=$snapshot; fi
  test "$snapshot" = "$original"
  # Exercise actual number cleanup while the one healthy checkpoint is pinned.
  omarchy-snapshot create
  test "$(cat "/.snapshots/$snapshot/snapshot/usr/share/omarchy-server-update-fixture/version")" = 1
done
omarchy-update -y
test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 2
test ! -e /var/lib/omarchy-server/update-state
test ! -e /var/lib/omarchy-server/update-snapshot
for i in 1 2 3 4 5 6 7; do omarchy-snapshot create; done
snapper --no-dbus -c root list
count=$(grep -l '<cleanup>number</cleanup>' /.snapshots/*/info.xml | wc -l)
test "$count" = 5
systemctl start snapper-cleanup.service
systemctl is-active snapper-cleanup.timer
''')
        (directory / "update.txt").write_text(log)
    finally:
        vm.stop()
    vm = VM(directory, disk, opts.port, [], bios=opts.bios)
    try:
        vm.wait_ssh()
        vm.sudo('test "$(cat /usr/share/omarchy-server-update-fixture/version)" = 2; omarchy-server-repository --check; omarchy-server-boot-check; systemctl is-active systemd-networkd sshd')
    finally:
        vm.stop()
    if sha(base / "system.qcow2") != original_hash:
        raise ValueError("Accepted source baseline changed")
    (directory / "update-pass.json").write_text(json.dumps(dict(passed=True, source=str(base), source_disk_sha256=original_hash, iso_sha256=receipt["iso_sha256"], signed_package_upgrade=True, signed_database=True, protected_snapshot_retries=6, retained_snapshots=5), indent=2) + "\n")
    print("SIGNED_PACKAGE_UPGRADE_SNAPSHOT_RETENTION_REBOOT_PASS", directory, flush=True)

if __name__ == "__main__":
    main()
