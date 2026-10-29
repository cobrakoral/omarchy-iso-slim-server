"""Server answers -> the official archinstall/orchestrator contract (data only)."""

import ipaddress
import json
import re
import subprocess
import sys
from pathlib import Path


def network_config(answers):
    mode = answers.get("network", "dhcp")
    if mode not in ("dhcp", "static"):
        raise ValueError("Network must be dhcp or static")
    # Match hardware Ethernet/Wi-Fi links, never bridges/tunnels or loopback.
    lines = ["[Match]", "Type=ether wlan"]
    if mode == "static":
        mac = answers.get("mac", "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", mac):
            raise ValueError("Static networking requires an interface MAC address")
        lines += [f"MACAddress={mac.lower()}"]
    else:
        lines += ["Path=*"]
    lines += ["", "[Network]", "IPv6AcceptRA=yes", "LinkLocalAddressing=ipv6"]
    if mode == "dhcp":
        lines += ["DHCP=yes"]
    else:
        address = ipaddress.IPv4Interface(answers["address"])
        gateway = ipaddress.IPv4Address(answers["gateway"])
        if gateway not in address.network:
            raise ValueError("IPv4 gateway is outside the configured subnet")
        lines += ["DHCP=ipv6", f"Address={address}", f"Gateway={gateway}"]
        dns = answers.get("dns", "").replace(",", " ").split()
        if not dns:
            raise ValueError("Static networking needs at least one DNS address")
        lines += [f"DNS={ipaddress.ip_address(d)}" for d in dns]
    return "\n".join(lines) + "\n"


def validate(answers):
    for name, pattern in [
        ("hostname", r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"),
        ("username", r"[a-z_][a-z0-9_-]{0,30}"),
    ]:
        if not re.fullmatch(pattern, answers.get(name, "")):
            raise ValueError(f"Invalid {name}")
    if answers["username"] in ("root", "nobody"):
        raise ValueError("Choose a regular administrator account")
    if not re.fullmatch(
        r"\$6\$(?:rounds=[0-9]+\$)?[./0-9A-Za-z]{1,16}\$[./0-9A-Za-z]{86}",
        answers.get("password_hash", ""),
    ):
        raise ValueError(
            "A SHA-512 password hash is required for console and sudo access"
        )
    zone = answers.get("timezone", "UTC")
    if (
        ".." in zone
        or zone.startswith("/")
        or not (Path("/usr/share/zoneinfo") / zone).is_file()
    ):
        raise ValueError("Unknown timezone")
    keymap = answers.get("keyboard", "us")
    known = subprocess.check_output(
        ["localectl", "list-keymaps"], text=True
    ).splitlines()
    if keymap not in known:
        raise ValueError("Unknown console keyboard layout")
    locale = answers.get("locale", "en_US.UTF-8")
    if not re.fullmatch(r"[A-Za-z_]+\.UTF-8(?:@[a-z]+)?", locale):
        raise ValueError("Invalid UTF-8 locale")
    supported = Path("/etc/locale.gen").read_text()
    if not any(x.lstrip("#").split()[:1] == [locale] for x in supported.splitlines()):
        raise ValueError("Unsupported locale")
    for option in ("cloud_init", "encrypt"):
        if not isinstance(answers.get(option, False), bool):
            raise ValueError(f"{option} must be boolean")
    if answers.get("encrypt") and not answers.get("encryption_password"):
        raise ValueError("Encryption needs a passphrase")
    network_config(answers)


def make_config(answers, size):
    validate(answers)
    if size < 12 * 1024**3:
        raise ValueError("Target disk must be at least 12 GiB")
    disk = answers["disk"]
    mib = 1024**2
    boot = 2 * 1024**3
    root_id = "8c2c2b92-1070-455d-b76a-56263bab24aa"

    def part(start, size, fs, ident, mount, flags, subvols):
        return dict(
            btrfs=subvols,
            dev_path=None,
            flags=flags,
            fs_type=fs,
            mount_options=["compress=zstd"] if fs == "btrfs" else [],
            mountpoint=mount,
            obj_id=ident,
            size={
                "sector_size": {"unit": "B", "value": 512},
                "unit": "B",
                "value": size,
            },
            start={
                "sector_size": {"unit": "B", "value": 512},
                "unit": "B",
                "value": start,
            },
            status="create",
            type="primary",
        )

    disk_config = {
        "config_type": "default_layout",
        "device_modifications": [
            {
                "device": disk,
                "wipe": True,
                "partitions": [
                    part(
                        mib,
                        boot,
                        "fat32",
                        "ea21d3f2-82bb-49cc-ab5d-6f81ae94e18d",
                        "/boot",
                        ["boot", "esp"],
                        [],
                    ),
                    part(
                        boot + mib,
                        (size // mib) * mib - boot - 2 * mib,
                        "btrfs",
                        root_id,
                        None,
                        [],
                        [
                            {"mountpoint": m, "name": n}
                            for m, n in [
                                ("/", "@"),
                                ("/home", "@home"),
                                ("/var/log", "@log"),
                                ("/var/cache/pacman/pkg", "@pkg"),
                            ]
                        ],
                    ),
                ],
            }
        ],
    }
    if answers.get("encrypt"):
        disk_config["disk_encryption"] = {
            "encryption_type": "luks",
            "lvm_volumes": [],
            "iter_time": 2000,
            "partitions": [root_id],
            "encryption_password": answers["encryption_password"],
        }
    config = dict(
        app_config=None,
        auth_config={},
        bootloader_config={"bootloader": "Limine", "uki": False, "removable": False},
        disk_config=disk_config,
        hostname=answers["hostname"],
        kernels=["linux-omarchy"],
        network_config=None,
        ntp=True,
        services=[],
        swap=False,
        timezone=answers.get("timezone", "UTC"),
        locale_config={
            "kb_layout": answers.get("keyboard", "us"),
            "sys_enc": "UTF-8",
            "sys_lang": answers.get("locale", "en_US.UTF-8"),
        },
        mirror_config={
            "custom_repositories": [],
            "custom_servers": [{"url": "file:///var/cache/omarchy/mirror/offline/"}],
            "mirror_regions": {},
            "optional_repositories": [],
        },
        packages=[],
        version="3.0.9",
    )
    config["omarchy_install"] = {
        "edition": "server",
        "mode": "full_disk",
        "target_mount": "/mnt",
        "language": "en",
        "input_method": "none",
        "storage": {"kernel": "linux-omarchy"},
        "boot": {
            "esp_mount": "/boot",
            "esp_path": "/EFI/limine",
            "efi_binary": "limine_x64.efi",
            "enable_fallback": True,
        },
        "server": {
            k: v
            for k, v in answers.items()
            if k in ("network", "address", "gateway", "dns", "mac", "cloud_init")
        },
    }
    creds = {
        "users": [
            {
                "username": answers["username"],
                "enc_password": answers["password_hash"],
                "groups": [],
                "sudo": True,
            }
        ]
    }
    if answers.get("encrypt"):
        creds["encryption_password"] = answers["encryption_password"]
    return config, creds


if __name__ == "__main__":
    answers = json.loads(Path(sys.argv[1]).read_text())
    disk = answers["disk"]
    # Whole disks only, and never the mounted live medium or a mounted host disk.
    blocks = json.loads(
        subprocess.check_output(
            ["lsblk", "-Jbo", "PATH,TYPE,SIZE,MOUNTPOINTS,RO", disk], text=True
        )
    )["blockdevices"]

    def busy(b):
        return any(b.get("mountpoints") or []) or any(
            busy(c) for c in b.get("children", [])
        )

    if (
        len(blocks) != 1
        or blocks[0]["type"] != "disk"
        or blocks[0]["ro"]
        or busy(blocks[0])
    ):
        raise ValueError("Target must be an unmounted writable whole disk")
    config, creds = make_config(answers, int(blocks[0]["size"]))
    for name, obj in [
        ("user_configuration.json", config),
        ("user_credentials.json", creds),
    ]:
        p = Path("/root") / name
        p.touch(mode=0o600)
        p.chmod(0o600)
        p.write_text(json.dumps(obj, indent=2) + "\n")
    Path("/root/user_encrypt_installation.txt").write_text(
        str(answers.get("encrypt", False)).lower() + "\n"
    )
