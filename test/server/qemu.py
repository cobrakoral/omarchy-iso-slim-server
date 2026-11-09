#!/usr/bin/env python3
"""Real offline installation acceptance. Artifacts remain in test-runs/server/."""

import argparse
from contextlib import ExitStack
import hashlib
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess as sp
import time
import tempfile
import pexpect

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "Disposable-Server-Test-2026"
GUEST_ASSERTIONS = r"""
expect_failure() {
    if "$@"; then
        printf 'Unexpected success: %s\n' "$1" >&2
        return 1
    fi
}
"""


def run(*args, **kw):
    return sp.run(args, check=True, text=True, **kw)


def output(*args):
    return sp.check_output(args, text=True)


def seed(path, label, files, avoid=None):
    source = path.with_suffix("")
    source.mkdir()
    for name, content in files.items():
        (source / name).write_text(content)
    environment = dict(os.environ)
    environment.pop("SOURCE_DATE_EPOCH", None)
    run(
        "xorriso", "-as", "mkisofs", "-quiet", "-V", label, "-o", str(path), str(source),
        env=environment,
    )
    if avoid is not None and media_uuid(path) == media_uuid(avoid):
        raise ValueError("Installer and seed media must have distinct filesystem UUIDs")


def stage_server_channel(source, destination):
    spec = importlib.util.spec_from_file_location("server_repository", ROOT / "builder/server/package-repository.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify(source)
    if destination.exists():
        verifier.verify(destination)
        if (destination / "repository.json").read_bytes() != (source / "repository.json").read_bytes():
            raise ValueError("Use a new fixture directory for a different signed channel")
    else:
        shutil.copytree(source, destination)


def media_uuid(path):
    return output("blkid", "-p", "-s", "UUID", "-o", "value", str(path)).strip()


def working_copy(base, label):
    """A manual recheck must never write its accepted source/backing disk."""
    base = base.resolve()
    if not base.is_relative_to(ROOT / "test-runs/server") or not (base / "key").is_file():
        raise ValueError("Use a stopped disposable installation with its test SSH key")
    directory = ROOT / "test-runs/server" / f"{time.time_ns()}-{label}"
    directory.mkdir(mode=0o700)
    for name in ("key", "OVMF_VARS.fd", "installer.iso", "instance.iso"):
        if (base / name).is_file():
            shutil.copy2(base / name, directory / name)
    run("qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b",
        str(base / "system.qcow2"), str(directory / "system.qcow2"))
    return directory


def require_owned_leaf(directory):
    """Internal in-place recovery is restricted to unaccepted leaf work disks."""
    directory = directory.resolve()
    if not directory.is_relative_to(ROOT / "test-runs/server"):
        raise ValueError("Disposable work directory required")
    if any((directory / name).exists() for name in ("acceptance-pass.json", "compaction.json")):
        raise ValueError("Accepted or compacted installations require a new overlay")
    disk = directory / "system.qcow2"
    info = disk.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("In-place recovery requires a regular disk without aliases")
    for candidate in ROOT.rglob("*.qcow2"):
        if candidate.samefile(disk):
            continue
        info = json.loads(output("qemu-img", "info", "--output=json", str(candidate)))
        backing = info.get("full-backing-filename") or info.get("backing-filename")
        if backing and (candidate.parent / backing).resolve() == disk:
            raise ValueError("Recovery work disk is a backing file; use a new overlay")


def qmp(path, command, arguments=None, *, timeout=10):
    deadline = time.monotonic() + timeout
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        buffer = b""

        def set_timeout():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"QMP {command} timed out")
            sock.settimeout(remaining)

        def read_message():
            nonlocal buffer
            while True:
                set_timeout()
                line, separator, rest = buffer.partition(b"\n")
                if separator:
                    buffer = rest
                    return json.loads(line)
                chunk = sock.recv(4096)
                if not chunk:
                    raise ConnectionError(f"QMP closed while waiting for {command}")
                buffer += chunk

        set_timeout()
        sock.connect(str(path))
        if "QMP" not in read_message():
            raise ValueError("QMP greeting missing")
        for cmd in [
            {"execute": "qmp_capabilities"},
            {"execute": command, "arguments": arguments or {}},
        ]:
            set_timeout()
            sock.sendall((json.dumps(cmd) + "\n").encode())
            while True:
                reply = read_message()
                if "return" in reply or "error" in reply:
                    break
            if "error" in reply:
                return reply
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
        networks=None,
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
        # Once the exclusive SSH-port lock is acquired, every subsequent
        # initializer step needs the same exception-safe cleanup as spawn().
        # An exception traceback can keep this partially built VM alive.
        self.control = None
        self.log = None
        self.child = None
        try:
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
                "-qmp",
                f"unix:{self.qmp},server=on,wait=off",
                "-no-reboot",
            ]
            if networks is None:
                # Existing offline acceptance keeps its egress-blocked single NIC.
                args += [
                    "-netdev",
                    f"user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:{port}-:22",
                    "-device",
                    "virtio-net-pci,netdev=net0",
                ]
            else:
                if not networks:
                    raise ValueError("A guest needs a management NIC")
                for index, network in enumerate(networks):
                    args += network.arguments(index, port if index == 0 else None)
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
        except BaseException:
            with ExitStack() as cleanup:
                cleanup.callback(self.port_guard.close)
                if self.control is not None:
                    cleanup.callback(self.control.cleanup)
                if self.log is not None:
                    cleanup.callback(self.log.close)
                if self.child is not None:
                    cleanup.callback(self.child.close, force=True)
            raise

    def stop(self, *, require_clean=False):
        with ExitStack() as cleanup:
            cleanup.callback(self.port_guard.close)
            cleanup.callback(self.control.cleanup)
            cleanup.callback(self.log.close)
            cleanup.callback(self.child.close, force=True)
            if self.child.isalive():
                reply = qmp(self.qmp, "system_powerdown")
                if "error" in reply:
                    raise RuntimeError(f"QMP powerdown failed: {reply['error']}")
                try:
                    self.child.expect(pexpect.EOF, timeout=60)
                except pexpect.TIMEOUT as error:
                    qmp(self.qmp, "quit")
                    self.child.expect(pexpect.EOF, timeout=10)
                    if require_clean:
                        raise RuntimeError("VM did not shut down cleanly") from error
            else:
                self.child.expect(pexpect.EOF, timeout=5)
        if require_clean and self.child.exitstatus != 0:
            raise RuntimeError("QEMU exited unsuccessfully")

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
            + __import__("shlex").quote(GUEST_ASSERTIONS + command)
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
    parser.add_argument(
        "--run-directory", type=Path, help="Specific disposable directory for a fresh installation"
    )
    parser.add_argument("--bios", action="store_true")
    parser.add_argument("--static", action="store_true")
    parser.add_argument("--cloud-init", action="store_true")
    parser.add_argument("--encrypt", action="store_true")
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    if opts.run_directory and opts.reuse:
        raise ValueError("--run-directory and --reuse are mutually exclusive")
    with opts.iso.open("rb") as image:
        iso_hash = hashlib.file_digest(image, "sha256").hexdigest()
    source_revision = output("git", "-C", str(ROOT), "rev-parse", "HEAD").strip()
    source_dirty = bool(output("git", "-C", str(ROOT), "status", "--porcelain"))
    harness_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    case = ("cloud" if opts.cloud_init else "base") + ("-luks" if opts.encrypt else "")
    case += ("-bios" if opts.bios else "") + ("-static" if opts.static else "")
    if opts.reuse:
        directory = opts.reuse.resolve()
        if not directory.is_relative_to(ROOT / "test-runs/server"):
            raise ValueError("Only this project's disposable test disks may be reused")
        previous = json.loads((directory / "acceptance-pass.json").read_text())
        if (
            Path(previous["iso"]).resolve() != opts.iso.resolve()
            or previous.get("iso_sha256", iso_hash) != iso_hash
        ):
            raise ValueError("Reuse must identify the original installation ISO")
        if previous["case"] != case:
            raise ValueError("Recheck options must match the original installation case")
        directory = working_copy(directory, case + "-recheck")
        disk = directory / "system.qcow2"
        cidata = directory / "installer.iso"
    else:
        server_runs = ROOT / "test-runs/server"
        if opts.run_directory:
            directory = opts.run_directory.absolute()
            if directory.parent != server_runs.resolve() or directory.is_symlink():
                raise ValueError("Run directory must be a direct child of ROOT/test-runs/server")
        else:
            server_runs.mkdir(parents=True, exist_ok=True)
            directory = server_runs / f'{time.strftime("%Y%m%d-%H%M%S")}-{case}'
        directory.mkdir(mode=0o700)
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
            avoid=opts.iso,
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
# nftables.service is Type=oneshot without RemainAfterExit. Assert enablement
# and that the kernel actually holds the rules, not "systemctl is-active".
systemctl is-enabled nftables
test -f /etc/nftables.conf
nft -c -f /etc/nftables.conf
nft list table inet omarchy_server_filter | grep -F 'tcp dport 22 accept'
nft list table inet omarchy_server_filter | grep -F 'udp sport 67 udp dport 68 accept'
nft list table inet omarchy_server_filter | grep -F 'udp sport 547 udp dport 546 accept'
nft list table inet omarchy_server_filter | grep -F 'icmpv6 type'
grep -Fx 'LLMNR=no' /etc/systemd/resolved.conf.d/90-omarchy-server-no-multicast.conf
grep -Fx 'MulticastDNS=no' /etc/systemd/resolved.conf.d/90-omarchy-server-no-multicast.conf
grep -Fx 'LLMNR=no' /etc/systemd/network/20-omarchy-server.network
grep -Fx 'MulticastDNS=no' /etc/systemd/network/20-omarchy-server.network
resolvectl llmnr | grep -Fx 'Global: no'
resolvectl mdns | grep -Fx 'Global: no'
! ss -H -lun | awk '{print $5}' | grep -Eq ':(5353|5355)$'
test -f /etc/systemd/logind.conf.d/90-omarchy-server-power-button.conf
grep -Fx 'HandlePowerKey=poweroff' /etc/systemd/logind.conf.d/90-omarchy-server-power-button.conf
busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager HandlePowerKey | grep -Fx 's "poweroff"'
systemctl is-enabled snapper-cleanup.timer limine-snapper-sync
systemctl is-active snapper-cleanup.timer limine-snapper-sync
test "$(systemctl get-default)" = multi-user.target
expect_failure systemctl is-enabled snapper-timeline.timer
for package in networkmanager hyprland quickshell sddm pipewire docker podman cockpit; do expect_failure pacman -Q "$package"; done
expect_failure systemctl is-active dhcpcd NetworkManager
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
expect_failure timeout 4 bash -c '</dev/tcp/1.1.1.1/443'
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
                "expect_failure pacman -Q cloud-init; test ! -e /etc/cloud/cloud.cfg.d/90-omarchy-server.cfg"
            )
        print("SERVICES_NETWORK_AUTH_PACKAGES_SNAPSHOTS_PASS", flush=True)
        installed_packages = dict(
            line.split(maxsplit=1) for line in vm.sudo("pacman -Q").splitlines()
        )
        with opts.iso.open("rb") as image:
            if hashlib.file_digest(image, "sha256").hexdigest() != iso_hash:
                raise ValueError("ISO changed during acceptance")
        if (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != harness_hash
            or output("git", "-C", str(ROOT), "rev-parse", "HEAD").strip()
            != source_revision
        ):
            raise ValueError("Acceptance source changed during the run")
        receipt_data = {
            "case": case,
            "run_kind": "recheck" if opts.reuse else "install",
            "iso_sha256": iso_hash,
            "source_revision": source_revision,
            "source_dirty": source_dirty,
            "harness_sha256": harness_hash,
            "packages": installed_packages,
            "disk": str(disk),
            "iso": str(opts.iso.resolve()),
            "checks": "boot, console password, key SSH, networkd, resolved, disabled LLMNR/mDNS, active dual-stack nftables policy, no egress, package exclusions, snapshot creation and boot entries, cloud-init selection",
        }
    finally:
        vm.stop(require_clean=True)
    with disk.open("rb") as stream:
        receipt_data["disk_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    receipt_path = directory / "acceptance-pass.json"
    temporary = receipt_path.with_suffix(".json.tmp")
    with temporary.open("x") as stream:
        json.dump(receipt_data, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(receipt_path)
    print(directory, flush=True)


if __name__ == "__main__":
    main()
