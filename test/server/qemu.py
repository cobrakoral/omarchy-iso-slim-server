#!/usr/bin/env python3
"""Real offline installation acceptance. Artifacts remain in test-runs/server/."""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess as sp
import time
import tempfile
import pexpect

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Disposable-Server-Test-2026"


def run(*args, **kw):
    return sp.run(args, check=True, text=True, **kw)


def output(*args):
    return sp.check_output(args, text=True)


def seed(path, label, files):
    source = path.with_suffix("")
    source.mkdir()
    for name, content in files.items():
        (source / name).write_text(content)
    run(
        "xorriso", "-as", "mkisofs", "-quiet", "-V", label, "-o", str(path), str(source)
    )


def qmp(path, command, arguments=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.connect(str(path))
        f = sock.makefile("rwb", buffering=0)
        f.readline()
        for cmd in [
            {"execute": "qmp_capabilities"},
            {"execute": command, "arguments": arguments or {}},
        ]:
            f.write((json.dumps(cmd) + "\n").encode())
            while True:
                reply = json.loads(f.readline())
                if "return" in reply or "error" in reply:
                    break
        return reply


class VM:
    def __init__(
        self,
        directory,
        disk,
        port,
        media,
        installer=False,
        bios=False,
        share=None,
        sparse_zeros=False,
    ):
        self.path = directory
        self.port = port
        ports = Path(tempfile.gettempdir()) / f"omarchy-qemu-ports-{os.getuid()}"
        ports.mkdir(mode=0o700, exist_ok=True)
        self.port_guard = (ports / str(port)).open("a")
        try:
            fcntl.flock(self.port_guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        except OSError as error:
            self.port_guard.close()
            raise ValueError(f"Refusing busy guest SSH port {port}") from error
        vars = directory / "OVMF_VARS.fd"
        if not vars.exists():
            shutil.copy("/usr/share/edk2/x64/OVMF_VARS.4m.fd", vars)
        # UNIX socket paths are limited to 107 bytes. Evidence directories can
        # be longer (especially BIOS/encrypted fault cases), so keep the socket
        # in a short private directory without shortening the evidence path.
        self.control = tempfile.TemporaryDirectory(prefix="omarchy-qmp-")
        self.qmp = Path(self.control.name) / "qmp.sock"
        args = [
            "-enable-kvm",
            "-cpu",
            "host",
            "-m",
            "1536",
            "-smp",
            "2",
            "-display",
            "none",
            "-serial",
            "stdio",
            "-monitor",
            "none",
            "-drive",
            "if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd",
            "-drive",
            f"if=pflash,format=raw,file={vars}",
            "-drive",
            f"if=virtio,format=qcow2,file={disk}"
            + (",discard=unmap,detect-zeroes=unmap" if sparse_zeros else ""),
            "-netdev",
            f"user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22",
            "-device",
            "virtio-net-pci,netdev=net0",
            "-qmp",
            f"unix:{self.qmp},server=on,wait=off",
            "-no-reboot",
        ]
        if bios:
            index = (
                args.index(
                    "if=pflash,format=raw,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd"
                )
                - 1
            )
            del args[index : index + 4]
        for i, cd in enumerate(media):
            args += ["-drive", f"file={cd},media=cdrom,readonly=on,index={i+2}"]
        if share is not None:
            args += [
                "-fsdev",
                f"local,id=packages,path={share},security_model=none,readonly=on",
                "-device",
                "virtio-9p-pci,fsdev=packages,mount_tag=packages,addr=0x9",
            ]
        if installer:
            args += ["-boot", "order=d"]
        self.log = (directory / f"serial-{time.time_ns()}.log").open("w")
        self.child = pexpect.spawn(
            "qemu-system-x86_64",
            args,
            encoding="utf-8",
            codec_errors="replace",
            timeout=900,
        )
        self.child.logfile = self.log

    def stop(self):
        if self.child.isalive():
            qmp(self.qmp, "system_powerdown")
            try:
                self.child.expect(pexpect.EOF, timeout=60)
            except pexpect.TIMEOUT:
                qmp(self.qmp, "quit")
                self.child.expect(pexpect.EOF, timeout=10)
        else:
            self.child.expect(pexpect.EOF, timeout=5)
        self.child.close()
        self.log.close()
        self.control.cleanup()
        self.port_guard.close()

    def ssh(self, command, check=True):
        result = sp.run(
            [
                "ssh",
                "-i",
                str(self.path / "key"),
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "ConnectTimeout=3",
                "-o",
                "BatchMode=yes",
                "-p",
                str(self.port),
                "admin@127.0.0.1",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        with (self.path / "ssh.log").open("a") as log:
            log.write(f"$ {command}\n{result.stdout}{result.stderr}\n")
        if check and result.returncode:
            raise RuntimeError(result.stderr + result.stdout)
        return result

    def sudo(self, command):
        return self.ssh(
            "printf '%s\\n' '"
            + PASSWORD
            + "' | sudo -S -- bash -euc "
            + __import__("shlex").quote(command)
        ).stdout

    def wait_ssh(self):
        for _ in range(180):
            if not self.child.isalive():
                self.child.expect(pexpect.EOF, timeout=5)
                raise RuntimeError(
                    "Guest exited before SSH: " + self.child.before[-2000:]
                )
            if self.ssh("true", check=False).returncode == 0 and self.child.isalive():
                return
            if not self.child.isalive():
                self.child.expect(pexpect.EOF, timeout=5)
                raise RuntimeError(
                    "Guest exited before SSH: " + self.child.before[-2000:]
                )
            time.sleep(2)
        raise RuntimeError("SSH did not become available")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("iso", type=Path)
    parser.add_argument(
        "--reuse", type=Path, help="Recheck an existing disposable installation"
    )
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--cloud-init", action="store_true")
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    case = ("cloud" if opts.cloud_init else "base") + ("-luks" if opts.encrypt else "")
    case += ("-bios" if opts.bios else "") + ("-static" if opts.static else "")
    if opts.reuse:
        directory = opts.reuse.resolve()
        if not directory.is_relative_to(ROOT / "test-runs/server"):
            raise ValueError("Only this project's disposable test disks may be reused")
        disk = directory / "system.qcow2"
        cidata = directory / "installer.iso"
    else:
        directory = (
            ROOT / "test-runs/server" / f'{time.strftime("%Y%m%d-%H%M%S")}-{case}'
        )
        directory.mkdir(parents=True)
        os.chmod(directory, 0o700)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(directory / "key"))
        disk = directory / "system.qcow2"
        run("qemu-img", "create", "-f", "qcow2", str(disk), "16G")
        spec = importlib.util.spec_from_file_location(
            "server_config", ROOT / "builder/server/config.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        password_hash = sp.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=PASSWORD,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        answers = dict(
            disk="/dev/vda",
            hostname="slim-test",
            username="admin",
            password_hash=password_hash,
            timezone="UTC",
            keyboard="us",
            locale="en_US.UTF-8",
            cloud_init=opts.cloud_init,
            encrypt=opts.encrypt,
            encryption_password=PASSWORD if opts.encrypt else "",
        )
        if opts.static:
            answers.update(
                network="static",
                mac="52:54:00:12:34:56",
                address="10.0.2.15/24",
                gateway="10.0.2.2",
                dns="10.0.2.3",
            )
        config, creds = module.make_config(answers, 16 * 1024**3)
        cidata = directory / "installer.iso"
        seed(
            cidata,
            "cidata",
            {
                "user_configuration.json": json.dumps(config),
                "user_credentials.json": json.dumps(creds),
                "user_encrypt_installation.txt": str(opts.encrypt).lower(),
                "authorized_keys": (directory / "key.pub").read_text(),
                "user-data": "#cloud-config\nwrite_files:\n  - path: /etc/installer-cidata-was-reused\n    content: WRONG\n",
                "meta-data": "instance-id: must-never-run\n",
            },
        )
        print(f"Installing {case}: {directory}", flush=True)
        vm = VM(
            directory,
            disk,
            opts.port,
            [opts.iso.resolve(), cidata],
            installer=True,
            bios=opts.bios,
        )
        try:
            initial = vm.child.expect(
                [
                    r"\[root@archiso[^#]*#",
                    r"Installation complete\.",
                    "Installation halted.",
                ],
                timeout=1800,
            )
            if initial == 0:
                vm.child.sendline("tail -n +1 -f /var/log/omarchy-install.log")
            if initial == 2:
                raise RuntimeError("Installer failed before log attachment")
            index = vm.child.expect(
                [
                    r"Installation complete\.",
                    "Installation halted.",
                    "Configuration error:",
                    pexpect.EOF,
                ],
                timeout=1800,
            )
            if index:
                raise RuntimeError("Offline installation failed; inspect serial log")
        finally:
            vm.stop()
    media = [cidata]
    if opts.cloud_init:
        instance = directory / "instance.iso"
        if not instance.exists():
            seed(
                instance,
                "omarchy-instance",
                {
                    "meta-data": "instance-id: slim-acceptance\nlocal-hostname: slim-test\n",
                    "user-data": "#cloud-config\nwrite_files:\n  - path: /etc/cloud-proof\n    content: separate-instance-seed\n",
                },
            )
        media.append(instance)
    vm = VM(directory, disk, opts.port, media, bios=opts.bios)
    try:
        if opts.encrypt:
            vm.child.expect("(?i)passphrase", timeout=180)
            vm.child.sendline(PASSWORD)
        vm.wait_ssh()
        vm.sudo(
            "cryptsetup isLuks /dev/vda2"
            if opts.encrypt
            else "! cryptsetup isLuks /dev/vda2"
        )
        identity = vm.sudo(
            "cat /etc/machine-id; sha256sum /etc/ssh/ssh_host_ed25519_key.pub"
        )
        (directory / "identity.txt").write_text(identity)
        print("BOOT_AND_SSH_PASS", flush=True)
        # Password authentication is checked at the console; SSH is key-only.
        vm.child.expect("slim-test login:", timeout=120)
        vm.child.sendline("admin")
        vm.child.expect("Password:", timeout=10)
        vm.child.sendline(PASSWORD)
        vm.child.expect("admin@slim-test", timeout=30)
        evidence = vm.sudo("""
set -e
systemctl is-active systemd-networkd systemd-resolved sshd
systemctl is-enabled snapper-cleanup.timer limine-snapper-sync
systemctl is-active snapper-cleanup.timer limine-snapper-sync
test "$(systemctl get-default)" = multi-user.target
! systemctl is-enabled snapper-timeline.timer
for package in networkmanager hyprland quickshell sddm pipewire docker podman cockpit; do ! pacman -Q "$package"; done
! systemctl is-active dhcpcd NetworkManager
readlink /etc/resolv.conf | grep -Fx /run/systemd/resolve/stub-resolv.conf
networkctl --no-pager status
ip -4 address | grep 10.0.2.15
ip -6 route | grep default
sshd -T | grep -Fxi 'passwordauthentication no'
findmnt -no FSTYPE / | grep -Fx btrfs
findmnt -no OPTIONS / | grep 'subvol=/@'
btrfs subvolume list /
snapper --no-dbus -c root list
cmp /etc/snapper/configs/root /usr/share/omarchy/default/snapper/root
test ! -e /etc/installer-cidata-was-reused
! timeout 4 bash -c '</dev/tcp/1.1.1.1/443'
pacman -Q | tee /var/log/server-acceptance-packages.log
printf 'PACKAGE_COUNT='; pacman -Qq | wc -l
printf baseline > /etc/rollback-proof
snapper --no-dbus -c root create -c number -d acceptance --print-number > /var/log/server-test-snapshot
printf changed > /etc/rollback-proof
limine-snapper-sync
find /boot -maxdepth 3 -type f
cat /boot/limine.conf
""")
        if opts.static:
            evidence += vm.sudo("ip -4 route | grep 'default via 10.0.2.2'")
        (directory / "acceptance.txt").write_text(evidence)
        if opts.cloud_init:
            vm.sudo(
                'cloud-init status --wait --long; test "$(cat /etc/cloud-proof)" = separate-instance-seed; test ! -e /etc/installer-cidata-was-reused'
            )
        else:
            vm.sudo(
                "! pacman -Q cloud-init; test ! -e /etc/cloud/cloud.cfg.d/90-omarchy-server.cfg"
            )
        print("SERVICES_NETWORK_AUTH_PACKAGES_SNAPSHOTS_PASS", flush=True)
        (directory / "acceptance-pass.json").write_text(
            json.dumps(
                {
                    "case": case,
                    "disk": str(disk),
                    "iso": str(opts.iso.resolve()),
                    "checks": "boot, console password, key SSH, networkd, resolved, no egress, package exclusions, snapshot creation and boot entries, cloud-init selection",
                },
                indent=2,
            )
        )
    finally:
        vm.stop()
    print(directory, flush=True)


if __name__ == "__main__":
    main()
