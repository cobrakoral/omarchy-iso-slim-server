"""Minimal server phases around the shared, official disk/image/boot pipeline."""

from pathlib import Path
import json
import subprocess
import os
import tempfile
from . import phases_impl as shared


def chroot(ctx, *args):
    subprocess.run(["arch-chroot", str(ctx.target), *args], check=True)


def write(ctx, name, text, mode=0o644):
    p = ctx.target / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)


def validate(ctx):
    from server_config import (
        authorized_keys, make_config, require_candidate_disk,
        target_accounts, validate as validate_answers, validate_credentials,
    )

    conf = ctx.user_configuration
    spec = ctx.omarchy_install
    if spec.get("edition") != "server" or ctx.defer_provisioning or ctx.is_protected:
        raise ValueError(
            "This server ISO needs a server full-disk configuration with an administrator"
        )
    if ctx.target != Path("/mnt"):
        raise ValueError("Server installs use /mnt")
    if ctx.tailscale_authkey_path is not None:
        raise ValueError("Server installs do not accept a Tailscale auth key")

    user = validate_credentials(ctx.user_credentials)
    users, groups = target_accounts()
    if set(user.get("groups", ())) - groups:
        raise ValueError("Administrator groups must exist in the target image")
    reserved = users | groups
    answers = dict(
        spec.get("server", {}),
        hostname=conf["hostname"],
        username=user["username"],
        password_hash=user["enc_password"],
        timezone=conf["timezone"],
        keyboard=conf["locale_config"]["kb_layout"],
        locale=conf["locale_config"]["sys_lang"],
        encrypt=ctx.encrypt,
        encryption_password=ctx.user_credentials.get("encryption_password", ""),
    )
    validate_answers(answers, reserved)
    if bool(conf.get("disk_config", {}).get("disk_encryption")) != ctx.encrypt:
        raise ValueError("Encryption marker and disk configuration disagree")
    disk = shared._install_disk(ctx)
    if not isinstance(disk, str) or not disk.startswith("/dev/"):
        raise ValueError("An installation disk under /dev is required")
    selected_disk = require_candidate_disk(disk)
    canonical_disk = selected_disk.get("path")
    if not isinstance(canonical_disk, str) or not canonical_disk.startswith("/dev/"):
        raise ValueError("Canonical installation disk path under /dev is required")
    disk_seq = selected_disk.get("disk-seq")
    if type(disk_seq) is not int or disk_seq <= 0:
        raise ValueError("Installation disk requires a positive kernel disk sequence number")

    if "disk_seq" in answers and answers["disk_seq"] != disk_seq:
        raise ValueError("Installation disk sequence changed since selection")

    answers["disk"] = disk
    expected, credentials = make_config(answers, int(selected_disk["size"]), reserved)
    if set(conf) - set(expected):
        raise ValueError("Unsupported server configuration fields")
    if conf["disk_config"] != expected["disk_config"]:
        raise ValueError("Server disk layout must match the generated full-disk layout")
    for name, value in conf.items():
        if name not in {"omarchy_install", "disk_config"} and value != expected[name]:
            raise ValueError(f"Unsupported server configuration value for {name}")
    expected_spec = expected["omarchy_install"]
    for name, value in spec.items():
        if name == "defer_provisioning" and value is False:
            continue
        if name not in expected_spec or value != expected_spec[name]:
            raise ValueError(f"Unsupported server install setting: {name}")

    expected["disk_config"]["device_modifications"][0]["device"] = canonical_disk
    ctx.state["canonical_install_disk"] = canonical_disk
    ctx.state["canonical_install_disk_seq"] = disk_seq
    ctx.state["pre_partition_check"] = lambda: verify_candidate_disk(ctx)

    keys = authorized_keys(ctx.authorized_keys_path) if ctx.authorized_keys_path else []
    credentials["users"][0]["groups"] = list(user.get("groups", ()))
    # Both the shared phases and Archinstall consume the generated plan, with
    # package sources, bootloader and disk layout under the edition's control.
    ctx.user_configuration = expected
    ctx.omarchy_install = expected_spec
    ctx.user_credentials = credentials
    ctx.state["server_authorized_keys"] = keys
    ctx.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for attribute, data in (
        ("arch_config_path", {k: v for k, v in expected.items() if k != "omarchy_install"}),
        ("creds_path", credentials),
    ):
        fd, name = tempfile.mkstemp(prefix="server-validated-", suffix=".json", dir=ctx.state_dir)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
            stream.write("\n")
        setattr(ctx, attribute, Path(name))


def verify_candidate_disk(ctx):
    canonical_target = ctx.state.get("canonical_install_disk")
    expected_seq = ctx.state.get("canonical_install_disk_seq")
    if not canonical_target or not expected_seq:
        raise ValueError("Canonical installation disk identity is not initialized")
    from server_config import require_candidate_disk

    selected = require_candidate_disk(canonical_target)
    if selected.get("path") != canonical_target:
        raise ValueError("Installation disk path changed identity")
    if selected.get("disk-seq") != expected_seq:
        raise ValueError("Installation disk sequence changed identity")
    return expected_seq


def release_disk(ctx):
    handler = ctx.state.get("arch_config_handler")
    device_mods = getattr(getattr(handler, "config", None), "disk_config", None)
    device_mods = getattr(device_mods, "device_modifications", None)
    canonical_target = ctx.state.get("canonical_install_disk")
    if (
        not canonical_target
        or shared._install_disk(ctx) != canonical_target
        or not isinstance(device_mods, list)
        or len(device_mods) != 1
        or getattr(device_mods[0], "wipe", None) is not True
        or getattr(getattr(getattr(device_mods[0], "device", None), "device_info", None), "path", None) != Path(canonical_target)
    ):
        raise ValueError("Parsed disk plan must contain exactly one wiping modification for the canonical target disk")
    mounts = subprocess.check_output(["findmnt", "-rn", "-o", "TARGET"], text=True)
    if ctx.target.resolve() != ctx.target or any(
        Path(mount).is_relative_to(ctx.target) for mount in mounts.splitlines()
    ):
        raise ValueError("The installation mount tree must be unused")
    shared.release_install_disk(ctx, disk_seq=verify_candidate_disk(ctx))


def populate_server_keyring(ctx):
    # The shared target-keyring worker has finished. Populate only this edition's
    # extra trust anchor, then stop its agents so target unmount cannot be held.
    try:
        chroot(ctx, "pacman-key", "--populate", "omarchy-server")
    finally:
        subprocess.run(
            [
                "arch-chroot",
                str(ctx.target),
                "gpgconf",
                "--homedir",
                "/etc/pacman.d/gnupg",
                "--kill",
                "all",
            ],
            check=False,
        )


def configure_power_button(ctx):
    # Explicit policy for installed servers as well as the live ISO. Preserve
    # normal shutdown inhibitors; only the power-button action is selected.
    write(
        ctx,
        "etc/systemd/logind.conf.d/90-omarchy-server-power-button.conf",
        Path("/usr/share/omarchy-iso/server-logind.conf").read_text(),
    )


# Builder ships the same reviewed policy for the live installer and target.
SECURITY_CONFIG_DIR = Path("/usr/share/omarchy-iso")


def configure_network_security(ctx):
    # The live installer uses umask 077, but systemd-resolved runs as the
    # unprivileged systemd-resolve user. A 0700 drop-in directory causes it to
    # silently ignore LLMNR=no / MulticastDNS=no and bind ports 5353/5355.
    # Explicitly make this public configuration directory traversable.
    resolved_dropins = ctx.target / "etc/systemd/resolved.conf.d"
    resolved_dropins.mkdir(mode=0o755, parents=True, exist_ok=True)
    resolved_dropins.chmod(0o755)
    write(
        ctx,
        "etc/systemd/resolved.conf.d/90-omarchy-server-no-multicast.conf",
        (SECURITY_CONFIG_DIR / "server-resolved.conf").read_text(),
    )
    write(
        ctx,
        "etc/nftables.conf",
        (SECURITY_CONFIG_DIR / "server-nftables.conf").read_text(),
        0o644,
    )
    # The check validates nft syntax but cannot modify the live host ruleset.
    chroot(ctx, "nft", "-c", "-f", "/etc/nftables.conf")


def configure(ctx):
    from server_config import network_config

    write(ctx, "etc/omarchy-edition", "server\n")
    configure_power_button(ctx)
    configure_network_security(ctx)
    write(
        ctx,
        "etc/systemd/network/20-omarchy-server.network",
        network_config(ctx.omarchy_install.get("server", {})),
    )
    resolv = ctx.target / "etc/resolv.conf"
    resolv.unlink(missing_ok=True)
    resolv.symlink_to("/run/systemd/resolve/stub-resolv.conf")
    chroot(
        ctx,
        "systemctl",
        "enable",
        "systemd-networkd",
        "systemd-resolved",
        "nftables",
        "sshd",
        "systemd-timesyncd",
    )
    chroot(ctx, "systemctl", "set-default", "multi-user.target")
    chroot(ctx, "usermod", "-L", "root")
    # Each installation generates its own host keys, never baked into the image.
    chroot(ctx, "ssh-keygen", "-A")
    keys = ctx.state["server_authorized_keys"]
    has_keys = bool(keys)
    if has_keys:
        write(
            ctx,
            f"home/{ctx.username}/.ssh/authorized_keys",
            "\n".join(keys) + "\n",
            0o600,
        )
        (ctx.target / f"home/{ctx.username}/.ssh").chmod(0o700)
        chroot(
            ctx,
            "chown",
            "-R",
            f"{ctx.username}:{ctx.username}",
            f"/home/{ctx.username}/.ssh",
        )
    write(
        ctx,
        "etc/ssh/sshd_config.d/00-omarchy-server.conf",
        "PermitRootLogin no\nPubkeyAuthentication yes\nKbdInteractiveAuthentication no\n"
        + f'PasswordAuthentication {"no" if has_keys else "yes"}\n',
    )
    chroot(ctx, "sshd", "-t")
    # No live offline repo, credentials, graphical migrations or auto-login.
    online = Path("/usr/share/omarchy-iso/server-online.conf").read_text()
    write(ctx, "etc/pacman.conf", online)
    shared._join_target_keyring_init(ctx)
    populate_server_keyring(ctx)
    chroot(ctx, "omarchy-server-migrate")
    chroot(ctx, "omarchy-server-repository", "--check")
    chroot(
        ctx, "bash", "-euo", "pipefail", "/usr/share/omarchy/install/config/snapper.sh"
    )


def cloud_init(ctx):
    if not ctx.omarchy_install.get("server", {}).get("cloud_init", False):
        return
    # Standard pacman resolution and checksum verification, entirely from the ISO.
    cache = ctx.target / "var/cache/omarchy/mirror/offline"
    cache.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["mount", "--bind", str(shared.OFFLINE_MIRROR), str(cache)], check=True
    )
    try:
        write(
            ctx,
            "etc/pacman.d/omarchy-server-offline.conf",
            Path("/etc/pacman.conf").read_text(),
        )
        chroot(
            ctx,
            "pacman",
            "--config",
            "/etc/pacman.d/omarchy-server-offline.conf",
            "-Sy",
            "--needed",
            "--noconfirm",
            "cloud-init",
        )
    finally:
        subprocess.run(["umount", str(cache)], check=True)
        (ctx.target / "etc/pacman.d/omarchy-server-offline.conf").unlink(
            missing_ok=True
        )
    # NoCloud uses a DIFFERENT volume label. Installer cidata is never reused.
    write(
        ctx,
        "etc/cloud/cloud.cfg.d/90-omarchy-server.cfg",
        "datasource_list: [ NoCloud, None ]\n"
        "datasource:\n  NoCloud:\n    fs_label: omarchy-instance\n"
        "network:\n  config: disabled\n"
        "users: []\npreserve_hostname: true\nssh_deletekeys: false\ndisable_root: true\n"
        + f'ssh_pwauth: {"false" if ctx.authorized_keys_path else "true"}\n',
    )
    write(ctx, "etc/cloud/ds-identify.cfg", "policy: enabled\n")
    chroot(
        ctx,
        "systemctl",
        "enable",
        "cloud-init-main.service",
        "cloud-init-local.service",
        "cloud-init-network.service",
        "cloud-config.service",
        "cloud-final.service",
    )


def validate_target(ctx):
    shared.validate_boot(ctx)
    names = subprocess.check_output(
        ["arch-chroot", str(ctx.target), "pacman", "-Qq"], text=True
    ).split()
    import importlib.util

    mod = importlib.util.spec_from_file_location(
        "package_check", "/usr/share/omarchy-iso/check-server-packages.py"
    )
    checker = importlib.util.module_from_spec(mod)
    mod.loader.exec_module(checker)
    checker.check([n for n in names if n != "cloud-init"])
    if ("cloud-init" in names) != bool(
        ctx.omarchy_install.get("server", {}).get("cloud_init")
    ):
        raise ValueError("cloud-init selection was not applied")
    for unit in (
        "systemd-networkd",
        "systemd-resolved",
        "nftables",
        "sshd",
        "snapper-cleanup.timer",
        "limine-snapper-sync",
    ):
        chroot(ctx, "systemctl", "is-enabled", unit)
    chroot(ctx, "nft", "-c", "-f", "/etc/nftables.conf")
    resolved_dropins = ctx.target / "etc/systemd/resolved.conf.d"
    if (resolved_dropins.stat().st_mode & 0o777) != 0o755:
        raise ValueError("systemd-resolved drop-ins must be traversable by its service user")
    if ((resolved_dropins / "90-omarchy-server-no-multicast.conf").stat().st_mode & 0o777) != 0o644:
        raise ValueError("systemd-resolved drop-in must be readable by its service user")
    if (ctx.target / "etc/NetworkManager").exists():
        raise ValueError("NetworkManager must not be configured")


def build_phases(ctx):
    return [
        ("Validating server inputs", validate),
        ("Loading the approved install plan", shared.load_install_config),
        ("Verifying offline image and mirror", shared.prepare_install_target),
        ("Releasing the verified installation disk", release_disk),
        ("Installing server root image", shared.arch_install_system),
        ("Configuring network, SSH and snapshots", configure),
        ("Installing optional offline cloud-init", cloud_init),
        ("Finalizing Limine boot", shared.finalize_limine_boot),
        ("Validating server target", validate_target),
        ("Creating factory snapshot", shared.create_factory_snapshot),
    ]
