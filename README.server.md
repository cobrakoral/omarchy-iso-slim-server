# Omarchy Slim Server ISO (unofficial)

This is an **unofficial Omarchy Server edition**, maintained in
[cobrakoral/omarchy-iso-slim-server](https://github.com/cobrakoral/omarchy-iso-slim-server).
It is not an official release or endorsement by Omarchy. It follows the
edition/package-set direction in DHH's server proposal, without its BBS,
Docker, desktop, or optional administration applications.

## Build

Use an x86_64 Arch/Omarchy development host with Docker, Git, sudo, Python, GnuPG, makepkg/repo-add and KVM.
The build container installs archiso, arch-install-scripts, btrfs-progs, the
GNU toolchain, grub and jq. The host needs working loop devices and Btrfs
mount support. Allow at least 12 GiB **free** workspace, plus space for test
VMs; sparse guest disks still consume their written data. 4 GiB RAM plus
swap works with the two-worker live compression limit.

```sh
git clone --recurse-submodules git@github.com:cobrakoral/omarchy-iso-slim-server.git
cd omarchy-iso-slim-server
git checkout feature/slim-server
# Reuse the verified published packages; no maintainer private key is needed.
git clone --depth=1 --single-branch --branch server-packages \
  https://github.com/cobrakoral/omarchy-iso-slim-server.git .server-work/signed-channel
# Compare the public-key fingerprint below before importing it.
gpg --import builder/server/keyring/omarchy-server.asc
export OMARCHY_SERVER_REPOSITORY_DIR="$PWD/.server-work/signed-channel/stable/x86_64"
OMARCHY_EDITION=server bin/omarchy-iso-make --no-boot-offer
# Enter the release/server-<timestamp> directory printed by the builder.
# sha256sum --check SHA256SUMS
```

The selector dispatches before the desktop builder handles caches. Server
work belongs exclusively to `.server-work/`; no host system package cache is
purged. Each build gets a new cache/output generation; earlier project package
archives seed it through hard links and remain preserved. Use `--empty-cache`
to disable that seeding. Output and cache paths must be absent or empty;
a populated generation is refused before Docker starts. Builds require clean
committed sources, the pinned clean archiso submodule, and a container digest.
The wrapper records inputs before building and rejects a source change during
the build. Override `OMARCHY_SERVER_CACHE_DIR` or `OMARCHY_SERVER_RELEASE_DIR`
only deliberately. Desktop builds retain their existing default behavior.
The server downloads the manifest closure directly: it never builds and
strips a desktop image. The target root is the official packed Btrfs image;
archiso provides the live ISO's BIOS Syslinux and x86_64 UEFI GRUB routes.
The installed system uses the official Limine deployment/UKI code.

`builder/server/target.packages` and `live.packages` are the explicit roots.
Pacman resolves dependencies, checks upstream package signatures, and checks
archive hashes. Custom packages and their repository database are signed outside the ISO
container and verified before use. No private signing key enters the container.
Packages are installed normally through pacman. The offline database is built with the
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
cd release/server-<build-generation>
sha256sum --check SHA256SUMS
gpg --import UNOFFICIAL-SIGNING-KEY.asc
gpg --verify omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso.sig \
  omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso
```
The installed server channel requires trusted package **and database** signatures.

## Fresh checkout and automated release qualification

From a fresh checkout of the desired committed revision, initialize its pinned
submodule and import the verified maintainer public key, then run:

```sh
python3 test/server/release.py \
  --output "$PWD/release/server-fresh-$(date +%Y%m%d-%H%M%S)" \
  --cache "$PWD/.server-work/cold-$(date +%Y%m%d-%H%M%S)"
```

Both paths must be new. The qualifier downloads and verifies the published
signed server packages independently, uses an empty project build cache, and
never seeds archives from an earlier project generation. It fetches a sparse
checkout of the pinned official runtime for the full upstream tests, avoiding
an accidental comparison with an older host installation. The original
container digest and clean source/submodule revisions are recorded. A cold
build needs working upstream mirrors and network access; the subsequent
installation tests run offline.

The automated sequence runs the upstream VM-free shell/Python suite and the
server tests, builds the ISO, checks clean source provenance, and installs
fresh default UEFI, cloud-init UEFI, BIOS and encrypted UEFI guests. It compares
each installed package/version map against the build's base/cloud closures.
Acceptance receipts bind the ISO SHA-256, source revision, source cleanliness
and harness hash. The qualifier then tests installed channel trust without
manual key import and boots/restores an actual snapshot, followed by normal
root boot and another installed-trust check. Any failed command prevents the
final `server-release-acceptance.json` success record.

To limit space, it removes only that completed generation's generated
`cache/profile` after build verification. Downloaded archives and provenance
remain. Newly created test disks are compacted only after QEMU stops and
`qemu-img compare` verifies identical virtual contents; no pre-existing test
disk is compacted or removed. After each fresh installation, safe QEMU rebase
can retain only the bytes differing from the first accepted disk. That base
and all dependencies remain preserved. This storage step happens after the
full fresh installation; it does not substitute a cloned system for a test. New guest cases require an 8 GiB free-space
reserve, and a full build requires at least 12 GiB before it starts.

If qualification stops, resume that completed build with the same output/cache
paths and `--resume-work <private-work-directory>` printed in the step logs.
The runner verifies the ISO checksum, unchanged production build inputs,
exact package/version maps, clean-source receipts and the committed harness
hash before reusing completed cases. It also checks the compacted disk hash.
It does not rebuild into or overwrite the earlier output. Image-source and
acceptance-source revisions are recorded separately when the runner advances.
A failure never emits the final success record; earlier logs and cases remain.

The output includes `server-build-inputs.json`, exact build/source/package
metadata, the ISO and the aggregate acceptance record. Credential media,
private keys, raw SSH and serial/password logs remain in private test/work
directories. The qualifier does not publish artifacts or open a PR. The
maintainer reviews the result and signs the ISO and final release checksums;
raw build checksums alone are not a maintainer signature.

This demonstrates a fresh functional rebuild and automated qualification.
It does **not** promise byte-identical ISOs: Btrfs identity and build metadata
vary, and Arch/Omarchy repositories move. Recorded package versions/hashes
show the exact resolved transaction; preserving its archives is necessary
for replay after mirrors remove a version. Pinning the initial container
digest also does not freeze later package downloads.

## Current release and signed updates

The refreshed 2026.10.31 ISO includes **omarchy-server 0.2.0-1**,
**omarchy-server-keyring 20261031-1**, and **limine-snapper-sync-server 1.32.1-3**.
The snapshot core still comes from the pinned official 1.32.1-2 archive;
`-3` is the headless packaging revision, not a new upstream snapshot-core version.
Default installs contain **176 packages**; selecting cloud-init produces **203**.
The earlier 2026.10.29 ISO and maintenance archives remain historical artifacts.

The live unofficial package channel is:

```ini
[omarchy-server]
SigLevel = PackageRequired DatabaseRequired TrustedOnly
Server = https://raw.githubusercontent.com/cobrakoral/omarchy-iso-slim-server/server-packages/stable/$arch
```

Fresh installations configure this automatically and populate their own
per-machine Pacman keyring from the packaged public trust anchor. The repository
configuration under `/etc/pacman.d/omarchy-server.conf` is administrator-owned;
package updates preserve a custom mirror URL. `omarchy-server-repository --check`
checks effective signature policy; the updater refuses weakened policy before
making a snapshot or changing packages. Normal maintenance remains:

```sh
sudo omarchy-update -y
sudo omarchy-server-migrate --pending
```

### Bootstrap an older installation

A pre-0.2 installation needs the pinned public key and channel enabled once.
Download the four files `omarchy-server.asc`, `repository.json`,
`repository.json.sig` and `enable-repository` from the channel's `stable/x86_64/`
directory. Verify the key fingerprint against the value in this document or
another already-trusted record, import that public key into your user GPG
keyring, and authenticate the manifest and helper before running it:

```sh
gpg --show-keys --fingerprint omarchy-server.asc
gpg --import omarchy-server.asc
gpg --verify repository.json.sig repository.json
python3 - <<'PYVERIFY'
import hashlib, json
from pathlib import Path
manifest = json.loads(Path('repository.json').read_text())
assert hashlib.sha256(Path('enable-repository').read_bytes()).hexdigest() == manifest['files']['enable-repository']
PYVERIFY
vi enable-repository
sudo python3 ./enable-repository --enable
sudo omarchy-update -y
```

The helper also checks the downloaded key's primary fingerprint before adding
trust, and refuses desktop editions. It never disables signature checks. The
actual upgrade uses the normal full Pacman transaction and a pre-update snapshot.
The keyring package supports subsequent authenticated public-key updates. Keep
its trusted/revoked records and public certifications current before key expiry;
a different primary fingerprint requires explicit trust-bootstrap review.

### Migration policy

Only the packaged `server-migrations/manifest.json` is eligible. Every entry
has a stable ordered ID, scope, exact file hash and explicit idempotence review.
Server and shared entries run as root; desktop entries never execute. Shared
means deliberately reviewed for this edition, not automatic import of desktop
migrations. The normal desktop migration runner and its user-state files are
not used on the server. The shared disk/image/desktop keyring code is unchanged;
server-specific trust population and agent cleanup live only in the server phase.

The ALPM post-transaction hook supports upgrades from older updater versions;
the server updater also runs migrations before boot verification. A separate
lock prevents concurrent migration runs. Private durable receipts and the
pending journal live in `/var/lib/omarchy-server/migrations/` on root. Root
rollback restores both configuration and the ledger, so later upgrades reapply
what the restored root has not received. An applied migration may not change.
An unfinished migration may not disappear or change hash/scope without explicit
repair review; a committed receipt safely reconciles a leftover journal.
Failures retain evidence and stop the updater rather than announcing success.

To repair a failure, inspect the retained transcript and pending journal, fix
the underlying condition, rerun `sudo omarchy-server-migrate`, then acknowledge
the repaired package-hook failure as described in the failure-recovery section.
Use a new migration ID to change already-applied behavior. Never mark a failed
migration applied manually or clear its journal without reviewing the repair.

### Maintain and publish the channel

`builder/server/runtime-upstream.lock.json` checks the small vendored official
runtime selection against its source revision and hashes. The headless snapshot
recipe separately pins the official archive hash; a concrete dependency prevents
a graphical snapshot package from satisfying the server runtime by accident.
The curated upstream files remain vendored: this round does not claim an
upstream runtime split or a new snapshot-core release.

The maintainer builds and signs packages on the host with the configured pinned
key. Reuse the prior signed generation for unchanged packages; changing a recipe
requires a version/release bump. Build, verify and test before publication:

```sh
python3 builder/server/package-repository.py --output .server-work/packages-next \
  --reuse .server-work/signed-channel/stable/x86_64 \
  --upstream-archive /path/to/verified/limine-snapper-sync-1.32.1-2-x86_64.pkg.tar.zst
python3 builder/server/package-repository.py --verify .server-work/packages-next/stable/x86_64
python3 test/server/prepare_repository.py .server-work/packages-next/stable/x86_64 .server-work/channel-tests
python3 test/server/repository.py test-runs/server/<accepted-base-run> .server-work/channel-tests
python3 builder/server/publish-repository.py .server-work/packages-next/stable/x86_64 --publish
```

The publisher is restricted to the authorized fork and the `server-packages`
branch, makes a signed atomic Git commit, never force-pushes, and refuses to
replace an existing versioned archive/signature with different bytes. Older
versioned archives remain available for clients with cached databases. Database
aliases are real files because raw Git hosting serves symlinks as text. Private
signing keys, test repositories and credential media are never published.
Unsigned, tampered, wrong-key and failing-migration fixtures in test media must
never be used as a production channel. Fresh ISO builds can consume the exact
verified published generation, retaining source and package provenance. Byte-
identical future ISOs are not promised; filesystem/build metadata can vary.

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

### Multiple NICs and persistent policy

The generated DHCP profile manages each matching hardware link. It does not
infer which uplink should win when several links advertise a default route.
Use MAC-matched files with names sorting before `20-omarchy-server.network`
for an explicit site policy. For example, a backup/management link can use:

```ini
# /etc/systemd/network/10-management.network
[Match]
MACAddress=aa:bb:cc:dd:ee:01
[Network]
DHCP=ipv4
IPv6AcceptRA=no
DNSDefaultRoute=yes
[DHCPv4]
RouteMetric=2000
UseDNS=yes
```

The preferred uplink can use:

```ini
# /etc/systemd/network/11-uplink.network
[Match]
MACAddress=aa:bb:cc:dd:ee:02
[Network]
DHCP=yes
IPv6AcceptRA=yes
DNSDefaultRoute=yes
Domains=~.
[DHCPv4]
RouteMetric=100
UseDNS=yes
[IPv6AcceptRA]
RouteMetric=100
```

The lower route metric selects the uplink. Its `~.` DNS routing domain sends
ordinary queries to that link while it is available; the management link's
DHCP DNS becomes available when the uplink loses carrier. This policy is
explicit configuration, not an installer guess. It was tested through cable
loss, recovery, DHCP renewal and cold boot. Networkd removes the failed link's
routes and DNS configuration; this does not detect an upstream outage that
leaves Ethernet carrier up.

Apply network changes from the console or an independent management link,
then use `networkctl reload` and `networkctl reconfigure <interface>`.
Inspect `ip route`, `ip -6 route` and `resolvectl status`. Static IPv4 remains
MAC-matched and persists across reboot. The text installer currently offers
DHCP or static IPv4; IPv6-only static deployments need an administrator-owned
networkd profile (`DHCP=no`, IPv6 `Address=`, `Gateway=` and `DNS=`). The
IPv6-only acceptance uses such a persistent profile with all non-loopback
IPv4 addresses removed. Public IPv6 routing still requires a suitable uplink.

### Network acceptance matrix

```sh
python3 test/server/networking.py test-runs/server/<accepted-default-UEFI-run> \
  --model all --external
```

This opt-in suite permits guest egress for real DNS and HTTPS, creates new
copy-on-write disks, and leaves the accepted backing disk unchanged. It tests
virtio, e1000 and RTL8139 data NIC models alongside a management NIC. A third
isolated Ethernet socket peer records DHCP REQUEST/ACK renewal exchanges;
it binds only to host loopback and never serves the host LAN. The original
offline suites retain `restrict=on` networking.

The virtio case additionally fetches and verifies the live signed channel,
and runs a detached IPv6-only phase with both IPv4-capable NICs disconnected.
Its IPv6 HTTP endpoint and DNS endpoint are host-loopback fixtures. The DNS
endpoint forwards real AAAA queries through the host's IPv4 resolver because
the validation host has no upstream IPv6 route. This proves guest IPv6-only
transport, not public IPv6 Internet reachability. Receipts record ISO/source
hashes, actual kernel NIC drivers, routes, resolver selection, renewal packets,
SSH authentication refusal, listening ports and cold-boot service health.
Physical NICs, storage controllers and Secure Boot require separate validation.

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
was introduced in 0.1.1; failure recovery followed in 0.1.2. Both are included
in the current **0.2.0** channel package. Subsequent ISO builds include the current
recipe; the existing ISO is not silently replaced. The latest signed package
and checksums are `release/omarchy-server-0.1.2-1-any.pkg.tar.zst` and
`release/SERVER-FAILURE-RECOVERY-SHA256SUMS`. After verifying the maintainer
fingerprint listed above, the historical 0.1.2 maintenance round can be
reproduced with the commands below. Current installations should use the signed
channel described above:

```sh
sha256sum --check SERVER-FAILURE-RECOVERY-SHA256SUMS
gpg --verify omarchy-server-0.1.2-1-any.pkg.tar.zst.sig \
  omarchy-server-0.1.2-1-any.pkg.tar.zst
sudo pacman-key --add UNOFFICIAL-SIGNING-KEY.asc
sudo pacman-key --lsign-key 7C5C6FEB40A5B493007FAA3650B1EB533F640140
sudo pacman -U ./omarchy-server-0.1.2-1-any.pkg.tar.zst
sudo omarchy-server-boot-check
```

New-version transitions for
limine-mkinitcpio-hook and the headless snapshot core remain to be tested when
suitable additional versions are staged.

## Update failure recovery (introduced in 0.1.2, retained in 0.2.0)

The original ISO still contains 0.1.0. Package 0.1.2 includes the 0.1.1
boot-integrity checks and adds failure detection; rebuilding the ISO includes
it automatically. The signed archive is
`release/omarchy-server-0.1.2-1-any.pkg.tar.zst`, with checksums in
`SERVER-FAILURE-RECOVERY-SHA256SUMS` and its detached signature. Verify the
maintainer fingerprint above, then use normal `pacman -U` to install it.

The updater requires a mounted `/boot` and minimum free reserves of **512 MiB
on root** and **128 MiB on `/boot`** before taking a snapshot. These reserves do
not guarantee room for an arbitrary upgrade. Pacman's configured `CheckSpace`
checks remain necessary. A low-space refusal happens before the snapshot or
package transaction, so free space and retry.

Each transaction retains a private transcript under `/var/log/omarchy-server/`.
The atomic `/var/lib/omarchy-server/update-state` marker records the active
phase and transcript. Logs live on the separate `@log` subvolume and survive
root restoration. Success removes the marker only after the package upgrade,
boot verification and Limine snapshot synchronization all succeed.

Pacman can exit zero despite a failing post-transaction hook or scriptlet.
The updater checks the uncoloured C-locale output for `error:` and refuses to
print success in that case. It preserves a `pacman-hooks` marker and blocks
another update until the administrator repairs and reruns the failed action
(or reinstalls the affected packages), then explicitly removes the marker to
acknowledge the repair. Simply retrying `pacman -Syu` may not rerun a hook when
its package is already upgraded. Inspect the transcript before choosing the
repair. Alternatively, boot the known-good snapshot through Limine and use
`omarchy-snapshot restore` before retrying.

An interrupted download can be retried normally once its source is available.
After abrupt shutdown, a stale Pacman lock is reported and never removed
automatically. Confirm no package manager is running before removing
`/var/lib/pacman/db.lck`; inspect the transcript and package database, repair
or reinstall affected packages, then retry. Do not reboot a system with failed
boot verification until its boot artifacts are repaired or a known-good
snapshot has been restored.

Reproduce the fault matrix using a stopped accepted guest and its retained
package mirror. Preparation needs the configured GPG signing key and stages
signed fixture packages without installing anything on the host:

```sh
python test/server/prepare_failures.py
python test/server/failures.py test-runs/server/<accepted-base-run> \
  .server-work/failure-tests/packages
python test/server/failures.py test-runs/server/<accepted-bios-run> \
  .server-work/failure-tests/packages --bios --case post-hook
python test/server/failures.py test-runs/server/<accepted-encrypted-run> \
  .server-work/failure-tests/packages --encrypt --case power-cut
```

Cases cover pre/post hook failure, ENOSPC on root and ESP, interrupted/resumed
HTTP downloads, stale-lock refusal, and a QEMU SIGKILL during a deliberately paused post-transaction
hook. Package downloads use a host-loopback HTTP server through an SSH reverse
tunnel; external guest networking stays blocked. Tests retain the failed
attempt, repair, cold-boot checks and source/package hashes. The post-hook UEFI
case also boots the actual pre-update snapshot and restores it. These tests
exercise specific failure points, not every possible power-loss write window.

The harness creates fresh copy-on-write guest disks, uses zero-discard support
for the ESP fill, reserves Btrfs extents for root ENOSPC, and refuses new runs
with less than **8 GiB free on the host**. Host snapshot settings are independent
of the guest image and are never changed by this suite. Existing acceptance
disks and release artifacts must be preserved.

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
