#!/usr/bin/env python3
"""Real signed repository bootstrap, negative trust tests and migration rollback."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
from qemu import PASSWORD, ROOT, VM, run

CASES = (
    "upgrade",
    "bad-database",
    "missing-database-signature",
    "unsigned-package",
    "wrong-key",
    "untrusted-database",
    "migration",
)


def mount(vm):
    vm.sudo(
        "mkdir -p /run/server-repo; mountpoint -q /run/server-repo || mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/server-repo"
    )


def enable(vm, name):
    return vm.sudo(
        "python3 /run/server-repo/"
        + name
        + "/enable-repository --enable --url file:///run/server-repo/"
        + name
    )


def attempt(vm, command, success, needle):
    import shlex

    return vm.sudo(
        """
set +e
text=$("""
        + command
        + """ 2>&1)
status=$?
set -e
printf '%s\\n' "$text"
test "$status" """
        + ("= 0" if success else "!= 0")
        + """
grep -iF """
        + shlex.quote(needle)
        + """ <<< "$text"
"""
    )


def health(vm, allow_cloud=False):
    names = vm.sudo("pacman -Qq").split()
    spec = importlib.util.spec_from_file_location(
        "server_packages", ROOT / "builder/server/check-packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.check([name for name in names if not (allow_cloud and name == "cloud-init")])
    text = vm.sudo("""
omarchy-server-repository --check
omarchy-server-migrate
omarchy-server-boot-check
systemctl is-active sshd systemd-networkd systemd-resolved snapper-cleanup.timer limine-snapper-sync.service
pacman -Dk
pacman -Q omarchy-server omarchy-server-keyring limine-snapper-sync-server
test "$(findmnt -no FSTYPE /)" = btrfs
! grep -q '/.snapshots/' /proc/cmdline
""")
    return {"packages": len(names), "checks": text}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("media", type=Path)
    parser.add_argument("--case", choices=("all",) + CASES, default="all")
    parser.add_argument("--port", type=int, default=2422)
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--encrypt", action="store_true")
    opts = parser.parse_args()
    base, media = opts.base.resolve(), opts.media.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use a stopped, accepted disposable baseline")
    if (
        not media.is_relative_to(ROOT / ".server-work")
        or not (media / "TEST-MEDIA-ONLY").is_file()
    ):
        raise ValueError("Use isolated repository test media")
    cases = CASES if opts.case == "all" else (opts.case,)
    for case in cases:
        if shutil.disk_usage(ROOT).free < 8 * 1024**3:
            raise RuntimeError("Host below 8 GiB free; preserve evidence and stop")
        label = "-bios" if opts.bios else "-luks" if opts.encrypt else ""
        directory = (
            ROOT
            / "test-runs/server"
            / (time.strftime("%Y%m%d-%H%M%S") + "-repo-" + case + label)
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
        print("REPOSITORY_CASE_START", case, directory, flush=True)

        def boot():
            vm = VM(directory, disk, opts.port, [], bios=opts.bios, share=media)
            try:
                if opts.encrypt:
                    vm.child.expect("(?i)passphrase", timeout=180)
                    vm.child.sendline(PASSWORD)
                vm.wait_ssh()
            except BaseException:
                vm.stop()
                raise
            return vm

        vm = boot()
        try:
            mount(vm)
            vm.sudo("""
cp /etc/pacman.conf /var/log/repository-original-pacman.conf
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture=auto
SigLevel=Required DatabaseOptional
LocalFileSigLevel=Required
[upstream]
Server=file:///run/server-repo/upstream/
CONF
cp /etc/pacman.conf /run/pacman.before-bootstrap
""")
            rejected = ""
            if case == "wrong-key":
                rejected = attempt(
                    vm,
                    "python3 /run/server-repo/wrong-key/enable-repository --enable --url file:///run/server-repo/wrong-key",
                    False,
                    "pinned maintainer fingerprint",
                )
                vm.sudo("cmp /etc/pacman.conf /run/pacman.before-bootstrap")
            elif case in (
                "bad-database",
                "missing-database-signature",
                "unsigned-package",
                "untrusted-database",
            ):
                if case == "untrusted-database":
                    vm.sudo("pacman-key --add /run/server-repo/untrusted-test-key.asc")
                enable(vm, case)
                rejected = attempt(
                    vm,
                    "pacman -Syu --noconfirm omarchy-server",
                    False,
                    ".pkg.tar.zst.sig" if case == "unsigned-package" else "signature",
                )
                vm.sudo("""
pacman -Q omarchy-server | grep -F '0.1.0-1'
! pacman -Q omarchy-server-keyring
pacman -Q limine-snapper-sync-server | grep -F '1.32.1-2'
""")
            (directory / "rejection.txt").write_text(rejected)
            (directory / "bootstrap.txt").write_text(enable(vm, "good"))
            # Remove only this rejected test repository's cached database so a
            # newer invalid fixture mtime cannot suppress the corrected fetch.
            vm.sudo(
                "rm -f /var/lib/pacman/sync/omarchy-server.db /var/lib/pacman/sync/omarchy-server.db.sig"
            )
            log = attempt(vm, "omarchy-update -y", True, "Update complete")
            vm.sudo("""
pacman -Q omarchy-server | grep -F '0.2.0-1'
pacman -Q limine-snapper-sync-server | grep -F '1.32.1-3'
test -f /var/lib/omarchy-server/migrations/0001-package-repository.json
cp /var/lib/omarchy-server/migrations/0001-package-repository.json /run/migration-first.json
omarchy-server-migrate
cmp /run/migration-first.json /var/lib/omarchy-server/migrations/0001-package-repository.json
! test -e /var/lib/omarchy-server/migrations/pending.json
""")
            # Weakened repository policy must stop the updater before it makes a snapshot.
            vm.sudo(
                "cp /etc/pacman.d/omarchy-server.conf /run/repo.good; sed -i 's/PackageRequired DatabaseRequired TrustedOnly/Never/' /etc/pacman.d/omarchy-server.conf"
            )
            weakened = attempt(
                vm,
                "omarchy-update -y",
                False,
                "requires trusted package AND database signatures",
            )
            vm.sudo("cp /run/repo.good /etc/pacman.d/omarchy-server.conf")
            (directory / "upgrade.txt").write_text(log + weakened)
            if case == "migration":
                enable(vm, "migration")
                vm.sudo("printf baseline > /etc/rollback-proof")
                failed = attempt(vm, "omarchy-update -y", False, "Update failed during")
                vm.sudo("""
test ! -e /etc/desktop-migration-was-run
test "$(cat /etc/shared-migration-proof)" = x
test "$(grep -o applied /var/log/shared-migration-invocations | wc -l)" = 1
test -f /var/lib/omarchy-server/migrations/pending.json
test ! -e /var/lib/omarchy-server/migrations/0004-failure-fixture.json
find /.snapshots -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' | grep -E '^[0-9]+$' | sort -n | tail -n1 > /var/log/server-test-snapshot
printf changed > /etc/rollback-proof
""")
                failed += attempt(
                    vm, "omarchy-update -y", False, "acknowledge the repair"
                )
                vm.sudo("""
touch /etc/allow-fixture-migration
omarchy-server-migrate
test "$(grep -o applied /var/log/shared-migration-invocations | wc -l)" = 1
test ! -e /var/lib/omarchy-server/migrations/pending.json
rm /var/lib/omarchy-server/update-state
cp /usr/share/omarchy/server-migrations/manifest.json /run/manifest.original
cp /usr/share/omarchy/server-migrations/0003-shared-fixture.sh /run/shared.original
python3 - <<'PY'
from pathlib import Path
import json,hashlib
p=Path('/usr/share/omarchy/server-migrations/0003-shared-fixture.sh')
p.write_text('touch /etc/changed-applied-migration-ran' + chr(10))
m=Path('/usr/share/omarchy/server-migrations/manifest.json')
rows=json.loads(m.read_text())
next(r for r in rows if r['id']=='0003-shared-fixture')['sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
m.write_text(json.dumps(rows))
PY
""")
                failed += attempt(
                    vm, "omarchy-server-migrate", False, "Applied migration was changed"
                )
                vm.sudo("""
test ! -e /etc/changed-applied-migration-ran
cp /run/manifest.original /usr/share/omarchy/server-migrations/manifest.json
cp /run/shared.original /usr/share/omarchy/server-migrations/0003-shared-fixture.sh
cp /etc/omarchy-edition /run/edition.original
printf desktop > /etc/omarchy-edition
""")
                failed += attempt(vm, "omarchy-server-migrate", False, "server edition")
                vm.sudo("cp /run/edition.original /etc/omarchy-edition")
                repaired = attempt(vm, "omarchy-update -y", True, "Update complete")
                (directory / "migration-recovery.txt").write_text(failed + repaired)
                (directory / "before-restore.json").write_text(
                    json.dumps(health(vm), indent=2)
                )
                vm.stop()
                vm = None
                run(
                    sys.executable,
                    str(ROOT / "test/server/recovery.py"),
                    str(directory),
                    "--port",
                    str(opts.port),
                )
                vm = boot()
                mount(vm)
                vm.sudo("""
pacman -Q omarchy-server | grep -F '0.2.0-1'
test ! -e /etc/shared-migration-proof
test ! -e /var/lib/omarchy-server/migrations/0003-shared-fixture.json
test ! -e /etc/desktop-migration-was-run
touch /etc/allow-fixture-migration
""")
                (directory / "migration-reapply.txt").write_text(
                    attempt(vm, "omarchy-update -y", True, "Update complete")
                )
                vm.sudo("""
test "$(cat /etc/shared-migration-proof)" = x
test "$(grep -o applied /var/log/shared-migration-invocations | wc -l)" = 2
test ! -e /etc/desktop-migration-was-run
""")
            first = health(vm)
            assert first["packages"] == 176, first
            (directory / "health.json").write_text(json.dumps(first, indent=2))
        finally:
            if vm:
                vm.stop()
        vm = boot()
        try:
            second = health(vm)
            assert second["packages"] == 176, second
            (directory / "cold-boot.json").write_text(json.dumps(second, indent=2))
        finally:
            vm.stop()
        record = {
            "case": case,
            "bios": opts.bios,
            "encrypted": opts.encrypt,
            "passed": True,
            "package_count": 176,
            "headless_package": "1.32.1-3",
            "source_revision": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (
                    Path(__file__),
                    ROOT / "test/server/qemu.py",
                    ROOT / "builder/server/runtime/bin/omarchy-server-migrate",
                    ROOT / "builder/server/runtime/bin/omarchy-server-repository",
                )
            },
        }
        (directory / "repository-pass.json").write_text(
            json.dumps(record, indent=2) + "\n"
        )
        print("SIGNED_REPOSITORY_MIGRATION_PASS", case, label, flush=True)


if __name__ == "__main__":
    main()
