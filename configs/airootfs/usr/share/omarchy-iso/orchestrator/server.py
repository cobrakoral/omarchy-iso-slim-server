"""Minimal server phases around the shared, official disk/image/boot pipeline."""

from pathlib import Path
import json
import subprocess
from . import phases_impl as shared


def chroot(ctx, *args):
    subprocess.run(["arch-chroot", str(ctx.target), *args], check=True)


def write(ctx, name, text, mode=0o644):
    p = ctx.target / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    p.chmod(mode)


def validate(ctx):
    # Run before even releasing disk holders. Unattended inputs are untrusted data.
    from server_config import validate as validate_answers

    conf = ctx.user_configuration
    spec = ctx.omarchy_install
    if spec.get("edition") != "server" or ctx.defer_provisioning or ctx.is_protected:
        raise ValueError(
            "This server ISO needs a server full-disk configuration with an administrator"
        )
    users = ctx.user_credentials.get("users", [])
    if len(users) != 1 or not users[0].get("sudo"):
        raise ValueError("Exactly one sudo administrator is required")
    server = spec.get("server", {})
    answers = dict(
        server,
        hostname=conf["hostname"],
        username=ctx.username,
        password_hash=users[0].get("enc_password", ""),
        timezone=conf["timezone"],
        keyboard=conf["locale_config"]["kb_layout"],
        locale=conf["locale_config"]["sys_lang"],
        encrypt=ctx.encrypt,
        encryption_password=ctx.user_credentials.get("encryption_password", ""),
    )
    validate_answers(answers)
    if (
        conf.get("kernels") != ["linux-omarchy"]
        or conf.get("app_config")
        or conf.get("audio_config")
        or conf.get("packages")
        or conf.get("network_config")
        or conf.get("services")
    ):
        raise ValueError(
            "Server input must not add desktop applications, services or package selections"
        )
    if bool(conf.get("disk_config", {}).get("disk_encryption")) != ctx.encrypt:
        raise ValueError("Encryption marker and disk configuration disagree")
    if ctx.target != Path("/mnt"):
        raise ValueError("Server installs use /mnt")
    disk = shared._install_disk(ctx)
    disks = json.loads(
        subprocess.check_output(
            ["lsblk", "-Jbo", "PATH,TYPE,SIZE,MOUNTPOINTS,RO", disk], text=True
        )
    )["blockdevices"]

    def busy(b):
        return any(b.get("mountpoints") or []) or any(
            busy(c) for c in b.get("children", [])
        )

    if (
        len(disks) != 1
        or disks[0]["type"] != "disk"
        or disks[0]["ro"]
        or busy(disks[0])
        or int(disks[0]["size"]) < 12 * 1024**3
    ):
        raise ValueError(
            "Installation needs an unmounted writable whole disk of at least 12 GiB"
        )
    # Refuse malformed/custom layouts before any disk-holder cleanup. This
    # edition's text form and seed generator both produce the official layout.
    from server_config import make_config

    answers["disk"] = disk
    expected, _ = make_config(answers, int(disks[0]["size"]))
    if conf["disk_config"] != expected["disk_config"]:
        raise ValueError("Server disk layout must match the generated full-disk layout")
    if ctx.authorized_keys_path is not None:
        shared._authorized_keys(ctx.authorized_keys_path)


def configure(ctx):
    from server_config import network_config

    write(ctx, "etc/omarchy-edition", "server\n")
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
        "sshd",
        "systemd-timesyncd",
    )
    chroot(ctx, "systemctl", "set-default", "multi-user.target")
    chroot(ctx, "usermod", "-L", "root")
    # Each installation generates its own host keys, never baked into the image.
    chroot(ctx, "ssh-keygen", "-A")
    has_keys = ctx.authorized_keys_path is not None
    if has_keys:
        keys = shared._authorized_keys(ctx.authorized_keys_path)
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
            ctx, "run/omarchy-server-offline.conf", Path("/etc/pacman.conf").read_text()
        )
        chroot(
            ctx,
            "pacman",
            "--config",
            "/run/omarchy-server-offline.conf",
            "-Sy",
            "--needed",
            "--noconfirm",
            "cloud-init",
        )
    finally:
        subprocess.run(["umount", str(cache)], check=True)
        (ctx.target / "run/omarchy-server-offline.conf").unlink(missing_ok=True)
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
        "sshd",
        "snapper-cleanup.timer",
        "limine-snapper-sync",
    ):
        chroot(ctx, "systemctl", "is-enabled", unit)
    if (ctx.target / "etc/NetworkManager").exists():
        raise ValueError("NetworkManager must not be configured")


def build_phases(ctx):
    return [
        ("Validating server inputs", validate),
        ("Preparing live environment", shared.prepare_live),
        ("Verifying offline image and mirror", shared.prepare_install_target),
        ("Installing server root image", shared.arch_install_system),
        ("Configuring network, SSH and snapshots", configure),
        ("Installing optional offline cloud-init", cloud_init),
        ("Finalizing Limine boot", shared.finalize_limine_boot),
        ("Validating server target", validate_target),
        ("Creating factory snapshot", shared.create_factory_snapshot),
    ]
