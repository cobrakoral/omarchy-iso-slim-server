#!/usr/bin/env python3
"""Drive the delivered Gum wizard, then prove console and password SSH login."""

import argparse
from pathlib import Path
import time
import pexpect
from qemu import VM, ROOT, PASSWORD, run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("iso", type=Path)
    opts = parser.parse_args()
    directory = ROOT / "test-runs/server" / f'{time.strftime("%Y%m%d-%H%M%S")}-console'
    directory.mkdir(mode=0o700, parents=True)
    disk = directory / "system.qcow2"
    run("qemu-img", "create", "-f", "qcow2", str(disk), "16G")
    vm = VM(directory, disk, 2422, [opts.iso.resolve()], installer=True)
    try:
        # The primary VT owns the wizard. Move that same delivered installer
        # to its supported serial console so pexpect can enter actual answers.
        vm.child.expect(r"\[root@archiso[^#]*#", timeout=180)
        vm.child.sendline(
            "systemctl stop getty@tty1.service; stty cols 120 rows 40; TERM=xterm-256color /root/.automated_script.sh"
        )

        def answer(prompt, value="", clear=False):
            vm.child.expect(prompt, timeout=60)
            if clear:
                vm.child.sendcontrol("u")
            vm.child.send(value + "\r")

        answer("System language / locale")
        answer("Console keyboard layout")
        answer("Hostname", "slim-test", True)
        answer("Administrator username")
        answer("Administrator password", PASSWORD)
        answer("Confirm password", PASSWORD)
        answer("Timezone")
        answer("Networking")
        answer("Optional path to SSH public-key file")
        answer("Encrypt the disk with LUKS")
        answer("Install cloud-init")
        answer("Installation disk", "\x1b[B")
        vm.child.expect("Erase /dev/vda", timeout=60)
        vm.child.send("y")
        vm.child.expect(r"Installation complete\.", timeout=1800)
        print("INTERACTIVE_GUM_INSTALL_PASS", flush=True)
    finally:
        vm.stop()
    vm = VM(directory, disk, 2422, [])
    try:
        vm.child.expect("slim-test login:", timeout=180)
        vm.child.sendline("admin")
        vm.child.expect("Password:")
        vm.child.sendline(PASSWORD)
        vm.child.expect("admin@slim-test")
        with (directory / "password-ssh.log").open("w") as log:
            child = pexpect.spawn(
                "ssh",
                [
                    "-tt",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-o",
                    "PubkeyAuthentication=no",
                    "-p",
                    "2422",
                    "admin@127.0.0.1",
                    "id -un; sudo sshd -T | grep -i '^passwordauthentication yes$'",
                ],
                encoding="utf-8",
                timeout=120,
            )
            child.logfile = log
            child.expect("password:")
            child.sendline(PASSWORD)
            child.expect("password for admin:")
            child.sendline(PASSWORD)
            child.expect("(?i)passwordauthentication yes")
            child.expect(pexpect.EOF)
            child.close()
            if child.exitstatus:
                raise RuntimeError("Password SSH check failed")
        (directory / "console-pass.txt").write_text(
            "Gum wizard, unencrypted install, default no cloud-init, console password login, password SSH fallback: PASS\n"
        )
        print("CONSOLE_AND_PASSWORD_SSH_PASS", directory, flush=True)
    finally:
        vm.stop()


if __name__ == "__main__":
    main()
