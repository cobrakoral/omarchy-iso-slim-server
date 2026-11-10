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


# A Linux interface name is at most 15 bytes. Reject metacharacters so it
# can safely appear in both [Match] and a deterministic .network filename.
INTERFACE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}\Z")
MAC_ADDRESS = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\Z")
NETWORK_FIELDS = {"ipv4", "ipv6", "address4", "gateway4", "dns4", "address6", "gateway6", "dns6"}


def ethernet_interfaces(links=None, sysfs=Path("/sys/class/net")):
    """List physical/VM Ethernet links, excluding Wi-Fi and virtual bridges."""
    if links is None:
        links = json.loads(subprocess.check_output(["ip", "-j", "link", "show"], text=True))
    found = {}
    for link in links:
        name = link.get("ifname")
        if (
            link.get("link_type") != "ether"
            or not isinstance(name, str)
            or not INTERFACE_NAME.fullmatch(name)
            or not (sysfs / name / "device").exists()
            or (sysfs / name / "wireless").exists()
        ):
            continue
        if name in found:
            raise ValueError("Duplicate detected Ethernet interface: " + name)
        found[name] = {"name": name, "mac": str(link.get("address", "")).lower()}
    return [found[name] for name in sorted(found)]


def _dns_addresses(value, family):
    if isinstance(value, str):
        items = value.replace(",", " ").split()
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        items = value
    else:
        raise ValueError("DNS servers must be addresses separated by spaces or a list")
    parser = ipaddress.IPv4Address if family == 4 else ipaddress.IPv6Address
    return [str(parser(item)) for item in items]


def network_config(name, settings=None):
    """Generate one explicit, independent IPv4/IPv6 networkd profile."""
    if not isinstance(name, str) or not INTERFACE_NAME.fullmatch(name):
        raise ValueError("Invalid Ethernet interface name")
    if settings is None:
        settings = {}
    if not isinstance(settings, dict) or set(settings) - NETWORK_FIELDS:
        raise ValueError("Unsupported interface network settings")
    ipv4, ipv6 = settings.get("ipv4", "dhcp"), settings.get("ipv6", "auto")
    if ipv4 not in ("dhcp", "static", "disabled") or ipv6 not in ("auto", "static", "disabled"):
        raise ValueError("Unsupported IPv4 or IPv6 configuration mode")
    lines = [
        "[Match]", f"Name={name}", "", "[Network]",
        f"DHCP={'ipv4' if ipv4 == 'dhcp' else 'no'}",
        f"IPv6AcceptRA={'yes' if ipv6 == 'auto' else 'no'}",
        f"LinkLocalAddressing={'ipv6' if ipv6 != 'disabled' else 'no'}",
        "LLMNR=no", "MulticastDNS=no",
    ]
    for family, mode in ((4, ipv4), (6, ipv6)):
        address = settings.get(f"address{family}", "")
        gateway = settings.get(f"gateway{family}", "")
        dns = settings.get(f"dns{family}", "")
        if not isinstance(address, str) or not isinstance(gateway, str):
            raise ValueError(f"IPv{family} address and gateway must be strings")
        if mode == "static":
            if not isinstance(address, str) or "/" not in address:
                raise ValueError(f"Static IPv{family} requires an address with prefix")
            parser = ipaddress.IPv4Interface if family == 4 else ipaddress.IPv6Interface
            interface = parser(address)
            lines.append(f"Address={interface}")
            if gateway:
                address_parser = ipaddress.IPv4Address if family == 4 else ipaddress.IPv6Address
                route = address_parser(gateway)
                if route not in interface.network and not (family == 6 and route.is_link_local):
                    raise ValueError(f"IPv{family} gateway is outside the connected subnet")
                lines.append(f"Gateway={route}")
        elif address or gateway:
            raise ValueError(f"IPv{family} address and gateway require static mode")
        if mode == "disabled" and dns:
            raise ValueError(f"Disabled IPv{family} cannot specify DNS servers")
        lines.extend(f"DNS={server}" for server in _dns_addresses(dns, family))
    # Both DHCP clients must leave the installer's static hostname intact.
    lines += ["", "[DHCPv4]", "UseHostname=no", "", "[DHCPv6]", "UseHostname=no"]
    return "\n".join(lines) + "\n"


def _legacy_network_settings(answers):
    """Keep previously generated unattended DHCP/static IPv4 answers valid."""
    mode = answers.get("network", "dhcp")
    if mode not in ("dhcp", "static"):
        raise ValueError("Network must be dhcp or static")
    if mode == "dhcp":
        return {}
    mac = answers.get("mac", "")
    if not isinstance(mac, str) or not MAC_ADDRESS.fullmatch(mac):
        raise ValueError("Static networking requires an interface MAC address")
    dns4, dns6 = [], []
    for address in str(answers.get("dns", "")).replace(",", " ").split():
        parsed = ipaddress.ip_address(address)
        (dns4 if parsed.version == 4 else dns6).append(str(parsed))
    settings = {
        "ipv4": "static", "address4": answers.get("address", ""),
        "gateway4": answers.get("gateway", ""), "dns4": dns4, "dns6": dns6,
    }
    network_config("test0", settings)
    return settings


def network_profiles(interfaces, answers):
    """Pair detected Ethernet links with overrides, defaulting every link."""
    overrides = answers.get("network_interfaces", {})
    if not isinstance(overrides, dict):
        raise ValueError("Network interface overrides must be a JSON object")
    for name, settings in overrides.items():
        network_config(name, settings)
    names = {link["name"] for link in interfaces}
    if len(names) != len(interfaces):
        raise ValueError("Duplicate Ethernet interface names")
    if set(overrides) - names:
        raise ValueError("Network configuration names an undetected Ethernet interface")
    legacy = _legacy_network_settings(answers)
    if legacy and overrides:
        raise ValueError("Cannot combine legacy static network and per-interface settings")
    if legacy:
        matched = [link["name"] for link in interfaces if link["mac"] == answers["mac"].lower()]
        if len(matched) != 1:
            raise ValueError("Legacy static network MAC does not match a detected Ethernet interface")
        overrides = {matched[0]: legacy}
    return {f"10-{name}.network": network_config(name, overrides.get(name, {})) for name in sorted(names)}


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
    _legacy_network_settings(answers)
    overrides = answers.get("network_interfaces", {})
    if not isinstance(overrides, dict):
        raise ValueError("Network interface overrides must be a JSON object")
    for name, settings in overrides.items():
        network_config(name, settings)


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
            if k in ("network", "address", "gateway", "dns", "mac", "network_interfaces", "cloud_init", "disk_seq")
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
    if sys.argv[1:] == ["--list-interfaces"]:
        print(json.dumps(ethernet_interfaces()))
        sys.exit(0)
    if len(sys.argv) == 3 and sys.argv[1] == "--render-interface":
        try:
            print(network_config(sys.argv[2], json.load(sys.stdin)), end="")
        except (ValueError, KeyError, TypeError) as error:
            sys.exit(str(error))
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
