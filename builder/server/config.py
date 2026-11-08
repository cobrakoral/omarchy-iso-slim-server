"""Server answers -> the official archinstall/orchestrator contract (data only)."""

import ipaddress
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_DISK_BYTES = 12 * 1024**3
SYSTEM_USERS = Path("/usr/share/omarchy-iso/server-system-users")
SYSTEM_GROUPS = Path("/usr/share/omarchy-iso/server-system-groups")


def target_accounts():
    return set(SYSTEM_USERS.read_text().splitlines()), set(SYSTEM_GROUPS.read_text().splitlines())


def disk_busy(device):
    return any(device.get("mountpoints") or []) or any(
        disk_busy(c) for c in device.get("children", [])
    )


def lsblk_inventory(device=None):
    cmd = ["lsblk", "-Jbo", "PATH,TYPE,SIZE,MOUNTPOINTS,RO,MODEL,DISK-SEQ", "--tree"]
    if device is not None:
        cmd.extend(("--", device))
    return json.loads(subprocess.check_output(cmd, text=True))["blockdevices"]


def candidate_disks(devices):
    return [
        d
        for d in devices
        if d["type"] == "disk"
        and not d["ro"]
        and int(d["size"]) >= MIN_DISK_BYTES
        and not disk_busy(d)
    ]


def require_candidate_disk(device_path):
    blocks = lsblk_inventory(device_path)
    candidates = candidate_disks(blocks)
    if len(blocks) != 1 or len(candidates) != 1:
        raise ValueError(
            "Installation needs an unmounted writable whole disk of at least 12 GiB"
        )
    return candidates[0]


def supported_locales(text):
    locales = []
    for line in text.splitlines():
        fields = line.lstrip("# ").split()
        if (
            len(fields) == 2
            and fields[1] == "UTF-8"
            and re.fullmatch(r"[A-Za-z_]+\.UTF-8(?:@[a-z]+)?", fields[0])
        ):
            locales.append(fields[0])
    return locales


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


def validate_credentials(credentials):
    # Archinstall merges the credentials document over its configuration. Never
    # allow a second disk layout, root account, or legacy plaintext field here.
    if not isinstance(credentials, dict) or set(credentials) - {"users", "encryption_password"}:
        raise ValueError("Unsupported server credentials fields")
    users = credentials.get("users")
    if not isinstance(users, list) or len(users) != 1 or not isinstance(users[0], dict):
        raise ValueError("Exactly one sudo administrator is required")
    user = users[0]
    if set(user) - {"username", "enc_password", "sudo", "groups"}:
        raise ValueError("Unsupported administrator credentials fields")
    if user.get("sudo") is not True:
        raise ValueError("Administrator sudo must be the boolean true")
    for name in ("username", "enc_password"):
        if not isinstance(user.get(name), str):
            raise ValueError("Administrator " + name + " must be a string")
    groups = user.get("groups", [])
    if not isinstance(groups, list) or any(
        not isinstance(g, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,30}", g)
        for g in groups
    ):
        raise ValueError("Administrator groups must be group names")
    if not isinstance(credentials.get("encryption_password", ""), str):
        raise ValueError("Encryption password must be a string")
    return user


# Validate the option field separately. ssh-keygen fingerprints the key while
# ignoring the options that sshd will interpret during authorization.
KEY_OPTION = re.compile(r'([A-Za-z][A-Za-z0-9-]*)(?:="((?>\\"|[^"])*)")?')
KEY_FLAGS = {
    "restrict", "cert-authority", "no-touch-required", "verify-required",
} | {
    prefix + flag
    for prefix in ("", "no-")
    for flag in ("agent-forwarding", "port-forwarding", "pty", "user-rc", "x11-forwarding")
}


def key_payload(entry, key_types):
    if re.split(r"[ \t]+", entry, maxsplit=1)[0] not in key_types:
        position = 0
        valued_options = set()
        while True:
            match = KEY_OPTION.match(entry, position)
            if not match:
                raise ValueError("Invalid authorized_keys option")
            name, value = match.groups()
            name = name.lower()
            if name in {"command", "from", "principals"} and value is not None:
                if name in valued_options:
                    raise ValueError(f"Duplicate authorized_keys option: {name}")
                valued_options.add(name)
                if name == "from":
                    for pattern in value.split(","):
                        pattern = pattern.removeprefix("!")
                        if not re.fullmatch(r"[A-Za-z0-9*?._:/-]+", pattern):
                            raise ValueError("Invalid from pattern")
                        if "/" in pattern:
                            ipaddress.ip_network(pattern)
                elif name == "principals" and not all(value.split(",")):
                    raise ValueError("Empty certificate principal pattern")
            elif name not in KEY_FLAGS or value is not None:
                raise ValueError(f"Unsupported authorized_keys option: {name}")

            position = match.end()
            separator = entry[position : position + 1]
            if separator in (" ", "\t"):
                entry = entry[position:].lstrip(" \t")
                break
            if separator != ",":
                raise ValueError("Authorized key options must precede a public key")
            position += 1

    fields = re.split(r"[ \t]+", entry, maxsplit=2)
    if (
        len(fields) < 2
        or fields[0] not in key_types
        or not re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", fields[1])
    ):
        raise ValueError("Expected a public key type and key data")
    return " ".join(fields[:2])


def authorized_keys(path):
    keys = [line.strip(" \t") for line in path.read_text().splitlines()]
    keys = [line for line in keys if line and not line.startswith("#")]
    if not keys:
        raise ValueError("SSH public-key file contains no keys")
    key_types = set(subprocess.check_output(["ssh", "-Q", "key"], text=True).splitlines())
    with tempfile.NamedTemporaryFile(mode="w+", prefix="server-public-key-") as keyfile:
        for index, key in enumerate(keys, 1):
            try:
                if any(ord(char) < 32 and char != "\t" for char in key):
                    raise ValueError("Control character in authorized key")
                payload = key_payload(key, key_types)
            except ValueError as error:
                raise ValueError(f"Invalid SSH public key on entry {index}: {error}") from error
            keyfile.seek(0)
            keyfile.truncate()
            keyfile.write(payload + "\n")
            keyfile.flush()
            result = subprocess.run(
                ["ssh-keygen", "-l", "-f", keyfile.name],
                capture_output=True, text=True,
            )
            if result.returncode or not result.stdout.strip():
                raise ValueError(f"Invalid SSH public key on entry {index}")
    return keys


def validate(answers, system_users=()):
    if "disk_seq" in answers and (
        type(answers["disk_seq"]) is not int or answers["disk_seq"] <= 0
    ):
        raise ValueError("Installation disk requires a positive kernel disk sequence number")
    for name, pattern in [
        ("hostname", r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?"),
        ("username", r"[a-z_][a-z0-9_-]{0,30}"),
    ]:
        if not re.fullmatch(pattern, answers.get(name, "")):
            raise ValueError(f"Invalid {name}")
    if answers["username"] in {"root", "nobody", *system_users}:
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


def make_config(answers, size, system_users=()):
    validate(answers, system_users)
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
            if k in ("network", "address", "gateway", "dns", "mac", "cloud_init", "disk_seq")
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
    if sys.argv[1:] == ["--list-locales"]:
        locales = supported_locales(Path("/etc/locale.gen").read_text())
        if not locales:
            sys.exit("No supported UTF-8 locales found in /etc/locale.gen.")
        print("\n".join(locales))
        sys.exit(0)
    if sys.argv[1:] == ["--list-disks"]:
        devices = lsblk_inventory()
        candidates = candidate_disks(devices)
        candidates = [
            device for device in candidates
            if type(device.get("disk-seq")) is int and device["disk-seq"] > 0
        ]
        if not candidates:
            sys.exit("No unmounted writable disks of at least 12 GiB with a kernel disk sequence number found.")
        for device in candidates:
            model = " ".join((device.get("model") or "").split())
            print(
                f"{device['path']} {device['disk-seq']} "
                f"{int(device['size']) / 1024**3:.1f} GiB {model}"
            )
        sys.exit(0)
    answers = json.loads(Path(sys.argv[1]).read_text())
    disk = answers["disk"]
    # Whole disks only, and never the mounted live medium or a mounted host disk.
    selected = require_candidate_disk(disk)
    if "disk_seq" in answers and answers["disk_seq"] != selected.get("disk-seq"):
        sys.exit("Installation disk sequence changed since selection")
    users, groups = target_accounts()
    config, creds = make_config(answers, int(selected["size"]), users | groups)
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
