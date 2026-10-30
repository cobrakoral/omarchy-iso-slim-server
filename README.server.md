# Omarchy Slim Server ISO (unofficial)

This is an **unofficial Omarchy Server edition**, maintained in
[cobrakoral/omarchy-iso-slim-server](https://github.com/cobrakoral/omarchy-iso-slim-server).
It is not an official release or endorsement by Omarchy. It follows the
edition/package-set direction in DHH's server proposal, without its BBS,
Docker, desktop, or optional administration applications.

## Build

Use an x86_64 Arch/Omarchy development host with Docker, Git, sudo and KVM.
The build container installs archiso, arch-install-scripts, btrfs-progs, the
GNU toolchain, grub and jq. The host needs working loop devices and Btrfs
mount support. Allow at least 12 GiB **free** workspace, plus space for test
VMs; sparse guest disks still consume their written data. 4 GiB RAM plus
swap works with the two-worker live compression limit.

```sh
git clone --recurse-submodules git@github.com:cobrakoral/omarchy-iso-slim-server.git
cd omarchy-iso-slim-server
git checkout feature/slim-server
OMARCHY_EDITION=server bin/omarchy-iso-make --no-boot-offer
sha256sum --check release/SHA256SUMS
```

The selector dispatches before the desktop builder handles caches. Server
work belongs exclusively to `.server-work/`; no host package cache is
purged or shared. Desktop builds retain their existing default behavior.
The server downloads the manifest closure directly: it never builds and
strips a desktop image. The target root is the official packed Btrfs image;
archiso provides the live ISO's BIOS Syslinux and x86_64 UEFI GRUB routes.
The installed system uses the official Limine deployment/UKI code.

`builder/server/target.packages` and `live.packages` are the explicit roots.
Pacman resolves dependencies, checks upstream package signatures, and checks
archive hashes. The local server packages are built through makepkg and
installed normally through pacman. The offline database is built with the
shared indexer. It advertises the complete transaction, while the shipped
archives contain only cloud-init's delta over the root image. Both are
SHA-256 checked before the installer formats a disk. This retains upstream's
ISO-integrity trust model (`SigLevel=Never` for its internal offline repo).
Verify the unofficial ISO's checksum/signature before writing installation
media. The release directory includes `SHA256SUMS`, an ISO `.sha256`, a binary
ISO `.sig`, `SHA256SUMS.asc`, and `UNOFFICIAL-SIGNING-KEY.asc`. The signing
identity is the fork maintainer, not Omarchy. Its primary fingerprint is
`7C5C6FEB40A5B493007FAA3650B1EB533F640140`.

```sh
cd release
sha256sum --check SHA256SUMS
gpg --import UNOFFICIAL-SIGNING-KEY.asc
gpg --verify omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso.sig \
  omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso
```
 The installed online repositories require package signatures.

## Install and access

Write the ISO to removable media or attach it to a VM. Boot the normal
console installer. It asks for UTF-8 locale, console keyboard, hostname,
administrator, password, timezone, networking, optional SSH public-key file,
encryption, cloud-init, and the destination disk. The entire selected disk
is erased only after confirmation and input/media validation. The minimum
disk size is 12 GiB; the standard ESP remains 2 GiB.

LUKS and cloud-init both default to **No**. The administrator always has a
password for local console login and sudo. With valid public keys, SSH uses
key-only authentication; without keys, SSH password access remains enabled.
Root SSH login and keyboard-interactive authentication are disabled. Every
installation initializes a unique machine ID, pacman keyring, and SSH host
keys. No account, password, private key or fixed machine identity is baked
into the delivered target image.

```sh
ssh admin@server-address
sudo systemctl status systemd-networkd systemd-resolved sshd
```

The installed system boots to the normal multi-user console. It does not
contain NetworkManager, Hyprland, Quickshell, a display manager, X11/Wayland,
audio applications, container engines, themes, or dashboards. A closure
check rejects graphical libraries brought in transitively as well.

The unattended input remains the official installer-only `cidata` format:
`user_configuration.json`, `user_credentials.json`, optional
`user_encrypt_installation.txt` and `authorized_keys`. Generate the same
contract using `builder/server/config.py`; see `test/server/qemu.py` for a
complete disposable example. Desktop/deferred/protected input is refused
by this edition before disk cleanup. Store credential media securely.

## Networking

The installed server enables **systemd-networkd** and **systemd-resolved**.
`/etc/resolv.conf` points at `/run/systemd/resolve/stub-resolv.conf`. The
installer writes `/etc/systemd/network/20-omarchy-server.network`. DHCP is
the default, including IPv6 DHCP and router advertisements. Static IPv4
selects an interface by its persistent MAC address and requires an
address/prefix, an on-link gateway and DNS addresses; IPv6
router advertisements remain enabled. No second DHCP daemon is enabled.

## Snapshots, updates and recovery

The shared installer preserves upstream's `@`, `@home`, `@log`, and `@pkg`
subvolumes and a read-only `@factory`. The upstream retention template is
copied verbatim: **five numbered pre-update root snapshots, no timeline**.
`snapper-cleanup.timer` is enabled, and `limine-snapper-sync.service` plus
Snapper's plugin keep snapshot boot entries synchronized. The small
`inotify-tools` dependency keeps the standard watcher active. There is no
invented hourly/daily retention policy.

```sh
sudo omarchy-snapshot create
sudo snapper -c root list
sudo systemctl list-timers snapper-cleanup.timer
omarchy-update
```

The server update command takes a lock, creates a snapshot, performs a full
pacman system upgrade, runs Limine deployment, and verifies the deployed kernel and initramfs match the installed modules.
It refuses an unmounted /boot before snapshotting or upgrading and stops on failed snapshots/upgrades/boot deployment. The boot check validates the UEFI UKI release and embedded kernel hash, or the BIOS kernel hash, and checks the initramfs module version. It deliberately
runs only the applicable package/boot workflow: desktop migrations, GUI
restart helpers, AUR builds, and mise provisioning do not run here.
The custom server packages currently update by rebuilding/installing this
fork's packages; they are not published in the official Omarchy repository.

To recover, choose a snapshot from Limine at boot. Its initramfs uses the
official Btrfs overlay hook. Log in and run `sudo omarchy-snapshot restore`,
then reboot to the restored writable root. `/home`, logs and package cache
are separate subvolumes and do not roll back with the root.

The headless `limine-snapper-sync-server` package preserves the upstream
sync/restore core, hooks, configuration and service. It omits notification
launchers, desktop files and icons, and removes the otherwise unnecessary
`libnotify` dependency. It provides the upstream package capability without
altering the desktop recipe. Its source package is Omarchy
`limine-snapper-sync 1.32.1-2`, pinned by SHA-256 after signature verification;
the corresponding GPL source is
[Zesko/limine-snapper-sync tag 1.32.1](https://gitlab.com/Zesko/limine-snapper-sync/-/tree/1.32.1).

## Optional cloud-init

All cloud-init dependencies are bundled in the ISO, but packages used only
by cloud-init are absent from the default target. Selecting it installs the
delta using normal pacman against the local offline repository and enables
the standard cloud-init services.

First-boot NoCloud instance media must use the label **`omarchy-instance`**,
with ordinary `user-data` and `meta-data` files. Installer-time `cidata`
media uses a different label and is not processed as instance configuration.
NoCloud and the safe None fallback are the configured datasources.
`ds-identify` is enabled explicitly because its stock detection recognizes
`cidata`, not this custom label. A new instance needs a unique `instance-id`.

```yaml
# meta-data
instance-id: server-unique-001
local-hostname: server
```

```yaml
# user-data
#cloud-config
write_files:
  - path: /etc/instance-note
    content: Provisioned from separate instance media
```

The installer owns initial networking and the administrator account.
Cloud-init network generation is disabled so it cannot replace the working
networkd configuration. Its default user list is empty; explicit instance
user-data can create additional accounts. Installed unique SSH host keys
are preserved. Root login stays disabled. Additional network datasources
are not enabled by default.

## Test

Host dependencies:

```sh
sudo pacman -S --needed qemu-system-x86 qemu-img edk2-ovmf libisoburn python-pexpect base-devel
python test/server/unit.py
python -m unittest test.unit.test_install_base_delta test.unit.test_root_image test.unit.test_place_root_image
python test/server/qemu.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso
python test/server/qemu.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso --cloud-init
python test/server/qemu.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso --encrypt
python test/server/qemu.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso --static
python test/server/qemu.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso --bios
python test/server/console.py release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso
python test/server/recovery.py test-runs/server/<successful-base-run>
python test/server/recovery.py test-runs/server/<successful-encrypted-run> --encrypt
python test/server/update.py test-runs/server/<successful-base-run> release/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso
```

Tests use disposable sparse 16 GiB disks and QEMU user networking with
`restrict=on`, allowing host SSH forwarding and local DHCP while blocking
internet access. Logs, media, credentials and VM disks stay in
`test-runs/server/` with private permissions. Never use their public test
password or SSH keys outside these disposable VMs. No production disk is
touched. Acceptance includes login, SSH, service state, networking, package
exclusions, snapshot creation and cloud-init label separation. The recovery
test selects an actual generated Limine snapshot entry, restores through the
interactive recovery utility, and checks the normal writable root after a
further reboot. The update test upgrades a real fixture package from version
1 to 2 using local media, checks its pre-update snapshot, exercises numbered
retention and cleanup, and reboots the updated installation.

## Real system upgrade and recovery acceptance

The fixture update test remains useful for package-transaction regression coverage.
The additional real-package test stages signature-checked Arch archive packages
for systemd 262 and Limine 12.9.3, boots that constructed historical userspace,
then uses the actual server updater to install systemd 262.1, Limine 12.9.4 and
Omarchy kernel 7.2.8-10. The kernel is explicitly from **edge**; this is a
controlled compatibility test, not stable release-to-release certification.

Run this after building the baseline ISO (keep its isolated package cache) and
completing a default UEFI acceptance installation. Host dependencies additionally
include makepkg/repo-add and access to the configured maintainer signing key.
Preparation downloads archives and never installs packages on the host. The
QEMU guest has no external network access; a read-only 9p share supplies signed
packages and pacman uses Required package signature policy.

```sh
python test/server/boot_check.py
python test/server/prepare_system_upgrade.py
python test/server/system_upgrade.py test-runs/server/<successful-base-run> \
  .server-work/system-upgrade/packages
python test/server/boot_check_integration.py test-runs/server/<successful-bios-run> \
  .server-work/system-upgrade/packages --bios
python test/server/boot_check_integration.py test-runs/server/<successful-encrypted-run> \
  .server-work/system-upgrade/packages --encrypt
```

Each run creates a new qcow2 overlay and leaves the backing acceptance disk
unchanged. Keep backing disks until dependent overlays are retired. Do not run
a backing guest while its overlay is being tested. Real-upgrade acceptance checks
the running kernel, installed modules, deployed boot payload, SSH, networkd,
resolved and snapshot services before and after upgrading. It then uses the real
Limine snapshot menu and restore command, cold-boots the restored root and
checks the earlier package versions and kernel again. Receipts, source hashes,
archive provenance and serial/SSH logs remain in the recorded test directories.
Boot-check integration also rejects a stale UKI or corrupted BIOS kernel and
refuses an update with /boot unmounted, then proves another cold boot works.

The baseline ISO contains maintenance package 0.1.0. The stronger boot checker
is delivered in package **0.1.1** and will be included by subsequent ISO builds;
installing that signed package in disposable guests is part of these tests.
The existing ISO is not silently replaced. The signed package and its checksum
are under `release/omarchy-server-0.1.1-1-any.pkg.tar.zst` and
`release/SERVER-MAINTENANCE-SHA256SUMS`. After verifying the maintainer fingerprint
listed above, an existing server can install the package normally:

```sh
sha256sum --check SERVER-MAINTENANCE-SHA256SUMS
gpg --verify omarchy-server-0.1.1-1-any.pkg.tar.zst.sig \
  omarchy-server-0.1.1-1-any.pkg.tar.zst
sudo pacman-key --add UNOFFICIAL-SIGNING-KEY.asc
sudo pacman-key --lsign-key 7C5C6FEB40A5B493007FAA3650B1EB533F640140
sudo pacman -U ./omarchy-server-0.1.1-1-any.pkg.tar.zst
sudo omarchy-server-boot-check
```

New-version transitions for
limine-mkinitcpio-hook and the headless snapshot core remain to be tested when
suitable additional versions are staged.

## Source pins and upstream tracking

The branch tracks upstream `quattro` for fetch/merge, with pushes directed
only to `origin`. No upstream PR is opened automatically. Base ISO revision:
`4df07ef` (the archiso submodule is pinned separately in Git). Copied runtime
snapshot templates/scripts come from Omarchy
`84782475aeb885c8d05f9e85b6ad0f1a0add60e5`; see the vendored `REVISION` and
license. No modified runtime/package repository must exist outside this
fork for the ISO to build.

The wrapper pins the Arch container digest. `release/server-container.json`
and `server-packages.lock` record the container and package transaction.
`server-source-revision.txt` and `server-source-files.sha256` identify the
exact source. Check out that revision, initialize its pinned submodule,
and use the recorded container digest to reproduce the source build.
Keep the downloaded `.server-work/cache/mirror` when reproducing a build:
Arch and Omarchy mirrors are moving repositories, not immutable snapshots.
A package lock documents versions; it cannot make a removed mirror archive
reappear. A byte-identical ISO is not promised (Btrfs UUIDs and build-time
metadata vary). Merge upstream changes explicitly and rerun acceptance,
especially when Archinstall or the Limine package changes.

See [the measured build and acceptance record](VALIDATION.server.md).

## Validation limits

Acceptance uses disposable x86_64 QEMU/KVM guests. Physical hardware and
Secure Boot are outside this validation. The system locale is selectable;
the installer prompts remain English. The installed disk is a configured
server, not a sealed VM cloning template. Preparing a template requires the
usual cloud-init cleanup and machine-identity/SSH-key sealing before cloning.
