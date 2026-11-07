#!/usr/bin/env python3
"""Check the policy actually delivered by a server ISO and fresh installation."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import pexpect
from qemu import ROOT, VM, qmp, run, working_copy
from release import reserve

POLICY = "etc/systemd/logind.conf.d/90-omarchy-server-power-button.conf"
PROPERTY = "busctl get-property org.freedesktop.login1 /org/freedesktop/login1 org.freedesktop.login1.Manager HandlePowerKey"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", type=Path)
    parser.add_argument("--iso", type=Path, help="Also boot the original live ISO")
    parser.add_argument("--port", type=int, default=2472)
    args = parser.parse_args()
    base = args.base.resolve()
    receipt = json.loads((base / "acceptance-pass.json").read_text())
    if receipt["case"] != "base":
        raise ValueError("Use a stopped accepted default server installation")
    if args.iso and sha(args.iso) != receipt["iso_sha256"]:
        raise ValueError("Use the original accepted installation ISO")
    reserve()
    original = sha(base / "system.qcow2")
    directory = working_copy(base, "acpi")
    disk = directory / "system.qcow2"
    expected = sha(ROOT / "builder/server/logind.conf")
    records = []

    def poweroff(vm, case):
        began = time.monotonic()
        assert "return" in qmp(vm.qmp, "system_powerdown")
        vm.child.expect(pexpect.EOF, timeout=90)
        vm.log.flush()
        assert "reboot: Power down" in Path(vm.log.name).read_text(errors="replace")
        elapsed = time.monotonic() - began
        records.append(dict(case=case, orderly_acpi_poweroff=True, seconds=elapsed))
        print("ACPI_POWEROFF_PASS", case, round(elapsed, 2), flush=True)

    if args.iso:
        live = directory / "live"
        live.mkdir()
        run("qemu-img", "create", "-f", "qcow2", str(live / "blank.qcow2"), "12G")
        vm = VM(live, live / "blank.qcow2", args.port, [args.iso.resolve()], installer=True, bios=True)
        try:
            which = vm.child.expect([r"\[root@archiso[^#]*#", "Omarchy Slim Server"], timeout=300)
            if which == 1:
                vm.child.sendcontrol("c")
                vm.child.expect(r"\[root@archiso[^#]*#", timeout=45)
            vm.child.sendline("test \"$(sha256sum /" + POLICY + " | cut -d ' ' -f1)\" = " + expected +
                              " && " + PROPERTY + " | grep -Fx 's \"poweroff\"' && printf '\\n%s%s\\n' LIVE_ACPI_ READY")
            vm.child.expect(r"[\r\n]+LIVE_ACPI_READY[\r\n]+", timeout=45)
            poweroff(vm, "live-iso")
        finally:
            vm.stop()
    for boot in ("fresh-installed", "cold-boot", "negative-control-restored"):
        reserve()
        vm = VM(directory, disk, args.port, [])
        try:
            vm.wait_ssh()
            assert vm.sudo(PROPERTY).strip() == 's "poweroff"'
            assert vm.sudo("sha256sum /" + POLICY).split()[0] == expected
            if boot == "negative-control-restored":
                vm.sudo("printf '[Login]\\nHandlePowerKey=ignore\\n' > /etc/systemd/logind.conf.d/99-acpi-test-ignore.conf; systemctl reload systemd-logind")
                assert vm.sudo(PROPERTY).strip() == 's "ignore"'
                assert "return" in qmp(vm.qmp, "system_powerdown")
                time.sleep(2)
                assert vm.child.isalive() and vm.ssh("true").returncode == 0
                print("CONFLICTING_IGNORE_NEGATIVE_CONTROL_PASS", flush=True)
                vm.sudo("rm /etc/systemd/logind.conf.d/99-acpi-test-ignore.conf; systemctl reload systemd-logind")
                assert vm.sudo(PROPERTY).strip() == 's "poweroff"'
            poweroff(vm, boot)
        finally:
            vm.stop()
    assert sha(base / "system.qcow2") == original
    if args.iso:
        assert sha(args.iso) == receipt["iso_sha256"]
    record = dict(passed=True, base=str(base), base_disk_sha256=original,
                  iso_sha256=receipt["iso_sha256"], policy_sha256=expected,
                  negative_control="ignore kept guest running after ACPI event",
                  checks=records, scope="Delivered policy; no injected replacement configuration")
    (directory / "acpi-pass.json").write_text(json.dumps(record, indent=2) + "\n")
    print("SERVER_ACPI_ACCEPTANCE_PASS", directory, flush=True)

if __name__ == "__main__":
    main()
