#!/usr/bin/env python3
"""Boot an actual Limine snapshot entry, restore it, then verify persistence."""

import argparse
import json
from pathlib import Path
import re
import shlex
import pexpect
import time
from qemu import PASSWORD
from qemu import VM, ROOT, qmp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=2422)
    opts = parser.parse_args()
    directory = opts.directory.resolve()
    if not directory.is_relative_to(ROOT / "test-runs/server"):
        raise ValueError("Only disposable acceptance disks are supported")
    disk = directory / "system.qcow2"
    vm = VM(directory, disk, opts.port, [])
    try:
        vm.wait_ssh()
        config = vm.sudo("cat /boot/limine.conf")
        number = vm.sudo("cat /var/log/server-test-snapshot").strip()
        vm.sudo(
            f'test "$(cat /etc/rollback-proof)" = changed; test "$(cat /.snapshots/{number}/snapshot/etc/rollback-proof)" = baseline'
        )
        entries = list(re.finditer(r"(?m)^[ \t]*/+[^/\n].*$", config))
        selected_number = None
        for index, entry in enumerate(entries):
            body = config[
                entry.end() : (
                    entries[index + 1].start()
                    if index + 1 < len(entries)
                    else len(config)
                )
            ]
            if f"/.snapshots/{number}/snapshot" in body:
                selected_number = index + 1
                break
        if selected_number is None:
            raise RuntimeError("Snapshot boot entry missing")
        # Change only the default selection, preserving the complete generated
        # hierarchy, original root entry, hashes, paths and machine-id metadata.
        selected = re.sub(r"(?m)^default_entry:.*$", "default_entry: 1", config)
        selected = re.sub(r"(?m)^timeout:.*$", "timeout: 0", selected)
        vm.sudo(
            "cp /boot/limine.conf /boot/limine.before-acceptance.conf; printf %s "
            + shlex.quote(selected)
            + " > /boot/limine.conf; sync"
        )
    finally:
        vm.stop()
    vm = VM(directory, disk, opts.port, [])
    try:
        # Exercise the real menu: root -> Snapshots -> the requested ID -> kernel.
        snapshot_headers = re.findall(r"(?m)^[ \t]*/{3}\+?([0-9]+)\b", config)
        ordinal = snapshot_headers.index(number)
        vm.child.expect("BdsDxe: starting", timeout=60)
        time.sleep(2)
        keys = (
            ["down", "down", "ret"] + ["down"] * (ordinal + 1) + ["ret", "down", "ret"]
        )
        for key in keys:
            qmp(vm.qmp, "human-monitor-command", {"command-line": "sendkey " + key})
            time.sleep(0.2)
        vm.wait_ssh()
        proof = vm.sudo(
            'cat /proc/cmdline; findmnt -no FSTYPE /; test "$(cat /etc/rollback-proof)" = baseline; touch /etc/snapshot-session-write; test -w /etc/snapshot-session-write; grep -E "RESTORE|ROOT_SUB" /etc/limine-snapper-sync.conf || true'
        )
        if "/.snapshots/" not in proof or "overlay" not in proof:
            raise RuntimeError("Guest did not boot the snapshot overlay")
        (directory / "snapshot-boot.txt").write_text(proof)
        # The upstream recovery utility owns a real terminal. Feed answers
        # only after their prompts; piping ahead can be read by an earlier prompt.
        with (directory / "restore.txt").open("w") as log:
            child = pexpect.spawn(
                "ssh",
                [
                    "-tt",
                    "-i",
                    str(directory / "key"),
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "UserKnownHostsFile=/dev/null",
                    "-p",
                    str(opts.port),
                    "admin@127.0.0.1",
                    "sudo omarchy-snapshot restore",
                ],
                encoding="utf-8",
                codec_errors="replace",
                timeout=180,
            )
            child.logfile = log
            child.expect("password for admin:")
            child.sendline(PASSWORD)
            child.expect(r"Choice \[r/l/c\]:")
            child.sendline("r")
            child.expect("Description for backup subvolume @:")
            child.sendline("acceptance backup")
            child.expect(r"Reboot now\? \[Y/n\]:")
            child.sendline("n")
            child.expect(pexpect.EOF)
            child.close()
            if child.exitstatus != 0:
                raise RuntimeError("Snapshot restore failed; inspect restore.txt")
        # Simulate selecting the ordinary root on the next boot. Production's
        # one-time interactive menu selection never changes default_entry.
        restored_config = vm.sudo("cat /boot/limine.conf")
        restored_entries = list(re.finditer(r"(?m)^[ \t]*/+[^/\n].*$", restored_config))
        normal_index = None
        for index, entry in enumerate(restored_entries):
            body = restored_config[
                entry.end() : (
                    restored_entries[index + 1].start()
                    if index + 1 < len(restored_entries)
                    else len(restored_config)
                )
            ]
            if "rootflags=subvol=@ " in body and "/.snapshots/" not in body:
                normal_index = index + 1
                break
        if normal_index is None:
            raise RuntimeError("Restored normal boot entry is missing")
        vm.sudo(
            "sed -i 's/^default_entry:.*/default_entry: "
            + str(normal_index)
            + "/' /boot/limine.conf; sync"
        )
    finally:
        vm.stop()
    vm = VM(directory, disk, opts.port, [])
    try:
        vm.wait_ssh()
        proof = vm.sudo(
            'test "$(cat /etc/rollback-proof)" = baseline; test ! -e /etc/snapshot-session-write; findmnt -no FSTYPE / | grep -Fx btrfs; findmnt -no OPTIONS / | grep "subvol=/@"; cat /proc/cmdline; systemctl is-active systemd-networkd sshd'
        )
        if "/.snapshots/" in proof:
            raise RuntimeError("Restored machine still boots a snapshot")
        (directory / "rollback-pass.txt").write_text(proof)
        print("SNAPSHOT_BOOT_RESTORE_REBOOT_PASS", flush=True)
    finally:
        vm.stop()


if __name__ == "__main__":
    main()
