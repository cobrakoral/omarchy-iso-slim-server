#!/usr/bin/env python3
"""Fault injection and recovery on fresh overlays of an accepted server guest."""

import argparse
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time
from qemu import PASSWORD, ROOT, VM, run

CASES = ("post-hook", "pre-hook", "full-root", "full-esp", "download", "power-cut")


def space_guard():
    # Fill only guest filesystems, using sparse/discard-aware QEMU. Keep the
    # host safely away from ENOSPC; never retire existing disks automatically.
    if shutil.disk_usage(ROOT).free < 8 * 1024**3:
        raise RuntimeError("Host has less than 8 GiB free; stop and retain evidence")


class PackageHandler(SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        with (self.server.evidence / "http.log").open("a") as log:
            log.write((fmt % args) + "\n")

    def do_GET(self):
        if not self.path.endswith("omarchy-server-fault-fixture-2-1-any.pkg.tar.zst"):
            return super().do_GET()
        path = Path(self.translate_path(self.path))
        data = path.read_bytes()
        start, end = 0, len(data) - 1
        byte_range = self.headers.get("Range")
        if byte_range:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
            if not match:
                self.send_error(416)
                return
            start = int(match[1])
            end = min(int(match[2]), end) if match[2] else end
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{len(data)}")
                self.end_headers()
                return
        self.send_response(206 if byte_range else 200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if byte_range:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        if self.server.abort_download:
            # Advertise the full remaining length, then interrupt the socket.
            # The next request can genuinely resume using HTTP byte ranges.
            end = min(end, start + 128 * 1024 - 1)
            self.close_connection = True
            self.server.aborted += 1
        try:
            self.wfile.write(data[start : end + 1])
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass


def configure(vm, manifest):
    return vm.sudo(
        """
mkdir -p /run/fault-packages
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/fault-packages
pacman-key --add /run/fault-packages/MAINTAINER.asc
pacman-key --lsign-key """
        + manifest["signing_key"]
        + """
cp /etc/pacman.conf /var/log/fault-original-pacman.conf
cat > /etc/pacman.conf <<'CONF'
[options]
Architecture=auto
CheckSpace
SigLevel=Required DatabaseOptional
LocalFileSigLevel=Required
[fault]
Server=file:///run/fault-packages/stable/
CONF
pacman -U --noconfirm /run/fault-packages/"""
        + manifest["package"]
        + """ /run/fault-packages/old/*.pkg.tar.zst
test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = 1
printf baseline > /etc/rollback-proof
"""
    )


def hook(vm, when, command, abort=False):
    vm.sudo(
        """mkdir -p /etc/pacman.d/hooks
cat > /etc/pacman.d/hooks/99-fault.hook <<'CONF'
[Trigger]
Operation=Upgrade
Type=Package
Target=omarchy-server-fault-fixture
[Action]
Description=Deliberate fault injection
When="""
        + when
        + """
Exec=/usr/bin/bash -c '"""
        + command
        + """'
"""
        + ("AbortOnFail\n" if abort else "")
        + "CONF\n"
    )


def attempt(vm, expected, needle):
    text = vm.sudo(
        """
set +e
result=$(omarchy-update -y 2>&1)
status=$?
set -e
printf '%s\\n' "$result"
test "$status" """
        + ("= 0" if expected == 0 else "!= 0")
        + """
"""
        + (
            "grep -q 'Update complete' <<< \"$result\"\n"
            if expected == 0
            else "! grep -q 'Update complete' <<< \"$result\"\n"
        )
        + "grep -F "
        + __import__("shlex").quote(needle)
        + ' <<< "$result"\n'
    )
    return text


def latest_snapshot(vm):
    return vm.sudo("""
find /.snapshots -mindepth 1 -maxdepth 1 -type d -printf '%f\\n' |
  grep -E '^[0-9]+$' | sort -n | tail -n1
""").strip()


def healthy(vm):
    return vm.sudo("""
omarchy-server-boot-check
systemctl is-active systemd-networkd systemd-resolved sshd
systemctl is-active snapper-cleanup.timer limine-snapper-sync.service
test "$(findmnt -no FSTYPE /)" = btrfs
! grep -q '/.snapshots/' /proc/cmdline
pacman -Dk
pacman -Qkk omarchy-server-fault-fixture
""")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base", type=Path)
    parser.add_argument("packages", type=Path)
    parser.add_argument("--case", choices=("all",) + CASES, default="all")
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    base, packages = opts.base.resolve(), opts.packages.resolve()
    if (
        not base.is_relative_to(ROOT / "test-runs/server")
        or not (base / "acceptance-pass.json").is_file()
    ):
        raise ValueError("Use a stopped, accepted disposable guest")
    if not packages.is_relative_to(ROOT / ".server-work"):
        raise ValueError("Use the isolated package staging directory")
    manifest = json.loads((packages / "maintenance.json").read_text())
    if (
        hashlib.sha256((packages / manifest["package"]).read_bytes()).hexdigest()
        != manifest["sha256"]
    ):
        raise ValueError("Maintenance archive does not match the staging manifest")
    cases = CASES if opts.case == "all" else (opts.case,)
    for case in cases:
        space_guard()
        suffix = "-bios" if opts.bios else "-luks" if opts.encrypt else ""
        directory = (
            ROOT
            / "test-runs/server"
            / (time.strftime("%Y%m%d-%H%M%S") + "-failure-" + case + suffix)
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
        print("CASE_START", case, directory, flush=True)

        def boot():
            space_guard()
            vm = VM(
                directory,
                disk,
                opts.port,
                [],
                bios=opts.bios,
                share=packages,
                sparse_zeros=True,
            )
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
        server = tunnel = None
        try:
            (directory / "setup.txt").write_text(configure(vm, manifest))
            before = latest_snapshot(vm)
            if case in ("post-hook", "pre-hook"):
                hook(
                    vm,
                    "PostTransaction" if case == "post-hook" else "PreTransaction",
                    "exit 1",
                    abort=case == "pre-hook",
                )
                text = attempt(vm, 1, "Update failed during pacman")
                version = 2 if case == "post-hook" else 1
                vm.sudo(
                    'test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = '
                    + str(version)
                )
                number = latest_snapshot(vm)
                assert number != before, "Missing pre-transaction snapshot"
                vm.sudo(
                    'test "$(cat /.snapshots/'
                    + number
                    + '/snapshot/usr/share/omarchy-server-fault-fixture/version)" = 1'
                )
                if case == "post-hook" and not (opts.bios or opts.encrypt):
                    vm.sudo(
                        "printf changed > /etc/rollback-proof; printf '%s\\n' "
                        + number
                        + " > /var/log/server-test-snapshot; limine-snapper-sync"
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
                    vm.sudo("""
mkdir -p /run/fault-packages
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/fault-packages
test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = 1
""")
                vm.sudo("rm /etc/pacman.d/hooks/99-fault.hook")
            elif case in ("full-root", "full-esp"):
                if case == "full-root":
                    vm.sudo("""
available=$(df -B1 --output=avail / | tail -n1)
fallocate -l "$(( available - 32 * 1024 * 1024 ))" /fault-space-reservation
# Exhaust actual extent reservations without writing gigabytes on the host.
size=$(stat -c %s /fault-space-reservation)
while fallocate -l "$((size + 4 * 1024 * 1024))" /fault-space-reservation 2>/run/fault-enospc; do
  size=$((size + 4 * 1024 * 1024))
done
grep -q 'No space left on device' /run/fault-enospc
cat /run/fault-enospc
test "$(df -B1 --output=avail / | tail -n1)" -lt 536870912
""")
                    filler = "/fault-space-reservation"
                else:
                    vm.sudo("""
available=$(df -B1 --output=avail /boot | tail -n1)
if dd if=/dev/zero of=/boot/fault-space-reservation bs=1M status=none 2>/run/fault-enospc; then
  echo 'ERROR: ESP fill did not reach ENOSPC'; exit 1
fi
grep -q 'No space left on device' /run/fault-enospc
cat /run/fault-enospc
sync
test "$(df -B1 --output=avail /boot | tail -n1)" -lt 134217728
""")
                    filler = "/boot/fault-space-reservation"
                text = attempt(vm, 1, "Insufficient free space")
                assert (
                    latest_snapshot(vm) == before
                ), "Low-space refusal created a snapshot"
                vm.sudo(
                    'test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = 1; rm '
                    + filler
                    + "; sync"
                )
            elif case == "download":
                server = ThreadingHTTPServer(
                    ("127.0.0.1", 0),
                    lambda *a, **kw: PackageHandler(*a, directory=str(packages), **kw),
                )
                server.abort_download, server.aborted = True, 0
                server.evidence = directory
                threading.Thread(target=server.serve_forever, daemon=True).start()
                tunnel = subprocess.Popen(
                    [
                        "ssh",
                        "-N",
                        "-i",
                        str(directory / "key"),
                        "-o",
                        "StrictHostKeyChecking=no",
                        "-o",
                        "UserKnownHostsFile=/dev/null",
                        "-o",
                        "ExitOnForwardFailure=yes",
                        "-p",
                        str(opts.port),
                        "-R",
                        "127.0.0.1:18080:127.0.0.1:" + str(server.server_port),
                        "admin@127.0.0.1",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=(directory / "tunnel.log").open("w"),
                )
                for _ in range(50):
                    if tunnel.poll() is not None:
                        raise RuntimeError("SSH reverse tunnel failed")
                    if (
                        vm.ssh(
                            "curl -fsS http://127.0.0.1:18080/stable/fault.db >/dev/null",
                            check=False,
                        ).returncode
                        == 0
                    ):
                        break
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Local package HTTP endpoint unavailable")
                vm.sudo(
                    "sed -i 's#file:///run/fault-packages/stable/#http://127.0.0.1:18080/stable/#' /etc/pacman.conf"
                )
                text = attempt(vm, 1, "Update failed during pacman")
                assert server.aborted > 0, "Package download was not interrupted"
                vm.sudo(
                    'test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = 1'
                )
                server.abort_download = False
            elif case == "power-cut":
                hook(
                    vm,
                    "PostTransaction",
                    "touch /var/log/fault-paused; sync; sleep 300",
                )
                vm.sudo(
                    """nohup bash -c 'omarchy-update -y > /var/log/fault-attempt.log 2>&1' </dev/null >/var/log/fault-launch.log 2>&1 &"""
                )
                for _ in range(120):
                    if (
                        vm.ssh("test -f /var/log/fault-paused", check=False).returncode
                        == 0
                    ):
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("Update never reached the fault-injection hook")
                text = vm.sudo(
                    "cat /var/log/fault-attempt.log; cat /var/lib/omarchy-server/update-state"
                )
                # SIGKILL deliberately bypasses guest ACPI and QEMU flushing.
                os.kill(vm.child.pid, signal.SIGKILL)
                vm.child.expect(__import__("pexpect").EOF, timeout=20)
                vm.stop()
                vm = boot()
                vm.sudo("""
test -f /var/lib/omarchy-server/update-state
test -f /var/lib/pacman/db.lck
! pgrep -x pacman
""")
                text += attempt(vm, 1, "Pacman database is locked")
                vm.sudo("""
! pgrep -x pacman
rm /var/lib/pacman/db.lck /etc/pacman.d/hooks/99-fault.hook
mkdir -p /run/fault-packages
mount -t 9p -o trans=virtio,version=9p2000.L,ro packages /run/fault-packages
""")
            if case == "post-hook" and (opts.bios or opts.encrypt):
                text += attempt(vm, 1, "acknowledge the repair")
                vm.sudo(
                    "pacman -S --noconfirm omarchy-server-fault-fixture; rm /var/lib/omarchy-server/update-state"
                )
            if case == "power-cut":
                vm.sudo("pacman -S --noconfirm omarchy-server-fault-fixture")
            (directory / "failure-observed.txt").write_text(text)
            repaired = attempt(vm, 0, "Update complete")
            vm.sudo("""
test "$(cat /usr/share/omarchy-server-fault-fixture/version)" = 2
test ! -e /var/lib/omarchy-server/update-state
""")
            (directory / "retry-pass.txt").write_text(repaired + healthy(vm))
        finally:
            if tunnel:
                tunnel.terminate()
                tunnel.wait(timeout=10)
            if server:
                server.shutdown()
                server.server_close()
            if vm:
                vm.stop()
        vm = boot()
        try:
            (directory / "cold-boot-pass.txt").write_text(healthy(vm))
        finally:
            vm.stop()
        receipt = {
            "case": case,
            "bios": opts.bios,
            "encrypted": opts.encrypt,
            "package": manifest,
            "passed": True,
            "host_free_bytes": shutil.disk_usage(ROOT).free,
            "source_revision": subprocess.check_output(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_sha256": {
                str(path.relative_to(ROOT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in (
                    ROOT / "builder/server/runtime/bin/omarchy-update",
                    Path(__file__),
                    ROOT / "test/server/qemu.py",
                )
            },
        }
        (directory / "failure-pass.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        print("FAILURE_RECOVERY_PASS", case, suffix, flush=True)
        space_guard()


if __name__ == "__main__":
    main()
