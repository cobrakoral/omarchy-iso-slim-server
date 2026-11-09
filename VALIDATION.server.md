# Omarchy Slim Server — measured validation

Latest validation covers installer and update hardening on **2026-11-07**.
Earlier sections describe their own historical artifacts. The ISO is an
unofficial community build.

## Installer and update hardening — 2026-11-07

- ISO `release/server-hardened-20261107-084838/omarchy-slim-server-unofficial-2026.11.07-x86_64.iso`, **1,255,964,672 bytes**.
- SHA-256 `2a6c66fbeb22d4344d127c8a8b6374d6d8bf3e64ad6729dd023c3c6c85fecf71`.
- Image and qualification source `4ae12d878fff955e090463eac3c640d63b4c11a4`.
- Pinned archiso `424e78130db2af6c1ceb55b442d7914b1109ff2b`.
- Target packages **176 base / 203 cloud-init**. Kernel `linux-omarchy 7.2.15-1`
  and signed runtime `omarchy-server 0.2.0-2`.

| Area | Measured result |
|---|---|
| Disk release | The installer halted before disk release with either the corrupt ISO checksum or the unsigned-mirror seed. `qemu-img compare` matched each test disk with its original marker disk. |
| Installer inputs | Regressions covered canonical install values, the built account and group namespaces, SSH key option parsing, protected mount ownership and restoration of installer-owned hook masks. |
| Source and package integrity | A cold build staged the recorded Git trees. Inspection of the actual SquashFS found zero installer bytecode files, the reserved `wheel` group and the recorded source revisions. The repository verifier admitted a signed historical pair and refused an unsigned backup. |
| Install and access | Fresh default UEFI, cloud-init UEFI, BIOS and encrypted UEFI matched their exact package maps. The Gum installer and console login passed, with password SSH verified on forwarded port 2452. |
| ACPI | Live ISO 2.10s, fresh installed 0.82s and cold boot 0.78s. The ignore override kept the guest running. Removing it restored shutdown in 0.89s. |
| Update retention | A signed upgrade kept the same healthy pre-update root across six boot-artifact failures and numbered cleanup. A successful retry restored five-snapshot retention and cold-booted. |
| Failure recovery | Signed post-hook failure, stale lock and interrupted update passed on disposable overlays. Post-hook recovery exercised Limine snapshot boot, restore, retry and cold boot. |
| Installed trust and restore | Base and cloud guests verified the signed channel. The recovered base booted the writable root and verified repository trust again. |
| Harness guards | Regressions covered QCOW2 links and identity, fresh versus recheck receipts, and fail-fast negative guest assertions. |

The shared shell suite and **383 Python tests (three skipped)** ran
against the pinned runtime. All **89 server regressions** passed.
The release directory holds the signed ISO and checksums, build and package
manifests, aggregate guest acceptance, supplementary acceptance, packed-image
inspection and artifact preservation record. Prior signed ISO hashes and the
previous accepted base disk hash were verified again.

The build used the maintainer-signed local `0.2.0-2` channel candidate. The
published `server-packages` channel serves `0.2.0-1`. Building this source
requires the matching signed candidate. QEMU/KVM qualification covers the
server image and the shared installer has VM-free test coverage. Public IPv6
deployment testing is pending. Secure Boot is outside this scope.

## Original artifact — 2026-10-29

- File: `omarchy-slim-server-unofficial-2026.10.29-x86_64.iso`
- Size: **1,255,964,672 bytes** (1.170 GiB).
- SHA-256: `6279a0b938d45a300d44da8254f790eeabdf2cfc92c139630882145984c7a53c`
- Built source: `238ced46686289f33d0db7943b248b1ef63e9d26`.
- Acceptance harness: `fe5ab79e47c6364534e17eda3fc2a0110b0e5198`.
- Target package count: **175** by default, **202** with cloud-init.
- Optional offline delta: **27** archives beyond the root image.
- Boot structure: BIOS Syslinux and x86_64 UEFI GRUB; installed boot uses Limine.
- Volume ID: `OMARCHY_SRV_202610`.

The ISO and SHA256SUMS have verified detached signatures from the fork
maintainer. Primary fingerprint:
`7C5C6FEB40A5B493007FAA3650B1EB533F640140`.
Signing subkey: `793FECA4D9623FB11D3ED6F2B2784E57B706E711`.
These are unofficial signatures.

## Environment and package revisions

Build host: Omarchy 4.0.6, x86_64, 3.8 GiB RAM and 11 GiB swap. Guests:
QEMU/KVM, two vCPUs, 1536 MiB RAM and disposable sparse 16 GiB disks.
`restrict=on` blocks external guest networking; SSH orchestration uses a
loopback-only host forward. Installation packages come from the ISO.

| Component | Version |
|---|---|
| linux-omarchy | 7.2.8-9 |
| archinstall | 4.5-1 |
| systemd | 262.1-1 |
| openssh | 10.6p1-2 |
| snapper | 0.13.2-1 |
| limine | 12.9.4-1 |
| limine-mkinitcpio-hook | 1.40.0-2 |
| limine-snapper-sync-server | 1.32.1-2 |
| cloud-init | 26.2-1 |
| omarchy-server | 0.1.0-1 |

## Acceptance results

All rows passed against the ISO above.

| Check | Evidence |
|---|---|
| Interactive Bash/Gum wizard | Completed real answers through locale, keyboard, account, timezone, disk and optional choices; installed and rebooted |
| Password access fallback | Console login and password SSH work when no public key was supplied |
| Default offline installation | Boots to ordinary login; 175 packages; cloud-init absent |
| Optional offline cloud-init | 202 packages; NoCloud instance wrote its requested file; installer cidata was not processed |
| Network and DNS | networkd/resolved active; DHCP IPv4 and IPv6; persistent static IPv4/gateway/DNS by MAC; resolved synthetic gateway query |
| SSH and identity | Key SSH and sudo work; root SSH disabled; machine IDs and host-key hashes distinct across five guests |
| Package exclusions | No NetworkManager, desktop, display manager, audio applications, Docker, Podman or Cockpit; closure rejects graphical dependencies |
| Snapshots | Official subvolumes, read-only factory snapshot, five numbered roots, active cleanup timer and watcher; timeline disabled as upstream specifies |
| Plain recovery | Selected generated snapshot through Limine's menu; writable overlay; actual restore and another reboot into writable @ with earlier file value |
| Encrypted installation | LUKS enabled only by choice; passphrase unlock, boot, console and SSH pass |
| Encrypted recovery | Snapshot boot, unlock, restore and normal-root reboot pass |
| UEFI package update | Real fixture upgrade 1 to 2, pre-update content retained, five numbered snapshots after cleanup, updated system reboots |
| BIOS installation and update | Full ISO installation, kernel/initramfs boot, SSH, fixture upgrade, snapshots and reboot pass |
| Package trust | Installed keyring verified the bundled cloud-init package signature |
| Integrity | ISO SHA-256, source-file manifest, boot records and unofficial detached signatures verified |

Unit/regression checks: **68 passed** (10 server checks plus 58 shared
image/installer checks). Shell syntax and error-level shellcheck checks pass.

## Evidence locations on the development VM

Artifacts and signatures are under:
`/home/omarchy/Projects/omarchy-iso-slim-server/release/`.

Final disposable test runs:

- default: `test-runs/server/20261029-102947-base/`
- cloud-init: `test-runs/server/20261029-102545-cloud/`
- encrypted: `test-runs/server/20261029-103228-base-luks/`
- static: `test-runs/server/20261029-103352-base-static/`
- BIOS: `test-runs/server/20261029-103504-base-bios/`
- interactive wizard: `test-runs/server/20261029-102831-console/`

Each batch run retains its serial transcript, SSH results, package list and
acceptance record. Recovery runs retain snapshot-boot, restore and normal-root
proof. Unit results and matrix summaries are included in the release evidence
bundle. Temporary test credentials are confined to the ignored test directory.

## Source relationships and limits

The ISO base is upstream `4df07ef`; archiso is pinned at
`424e78130db2af6c1ceb55b442d7914b1109ff2b`. Vendored Omarchy runtime templates
and snapshot scripts come from
`84782475aeb885c8d05f9e85b6ad0f1a0add60e5`.
The headless snapshot package preserves Omarchy's verified 1.32.1-2 core;
its GPL source tag resolves to
`b4c0da2d4532a993809219f9e5d51a1fa85fcb93`.

Physical hardware and Secure Boot were not validated. This is a configured
server installer, not a sealed cloud cloning template. The server maintenance
package runs applicable package/snapshot/boot updates and excludes desktop
migrations; updates to that custom package currently require rebuilding its
recipe. Repository channels are mutable; source/container pins and package
locks document this build, but byte-identical future output is not promised.

No upstream pull request was opened.

## Maintenance acceptance — 2026-10-30

The original ISO above remains unchanged. This round delivers signed
`omarchy-server 0.1.1-1` and build-source changes, not a refreshed ISO.
Tested implementation commit: `4d0f180` (runtime change: `07c555b`).
Receipts include SHA-256 hashes of the tested source files.

The real system upgrade passed with external guest networking blocked and
Required package signatures. Historical userspace was deliberately constructed
from signed Arch archive packages. The newer kernel comes from **Omarchy edge**;
this does not certify a stable-channel release-to-release upgrade.

| Component | Before | After upgrade | After snapshot restore |
|---|---|---|---|
| Running Omarchy kernel | 7.2.8-9-omarchy | 7.2.8-10-omarchy | 7.2.8-9-omarchy |
| systemd and matching libs/sysvcompat/ukify | 262-1 | 262.1-1 | 262-1 |
| Limine package | 12.9.3-1 | 12.9.4-1 | 12.9.3-1 |
| Installed package count | 175 | 175 | 175 |

All three cold-boot observations passed key SSH, networkd/resolved, snapshot
cleanup/watcher service checks, package exclusion checks, installed module
checks and deployed kernel/initramfs verification. Recovery selected an actual
Limine snapshot entry, booted its writable overlay, ran the upstream restore
utility and cold-booted the restored writable root. Restored package versions
and the running kernel matched the historical baseline.

Additional real QEMU tests passed on BIOS and encrypted UEFI installations:

- Reject a corrupted BIOS kernel or stale UKI release.
- Restore the correct artifact and verify it again.
- Refuse an update with /boot unmounted, before snapshot creation or a package transaction.
- Remount /boot and complete another cold boot (including LUKS unlock).

Regression results: **77 passed** (9 new boot-integrity checks, 10 server
checks, 58 shared checks); Bash syntax and error-level shellcheck pass.
The QEMU package share has a fixed PCI address so adding/removing test media
does not change disk device placement. Repository preparation prevents a
lexically later old kernel filename from downgrading its package index and
asserts the staged versions before a guest is started.

Final evidence directories under `test-runs/server/`:

- `20261030-181152-system-upgrade/`
- `20261030-181401-boot-check-bios/`
- `20261030-181007-boot-check-luks/`

The tests use new qcow2 overlays; the original 2026-10-29 acceptance backing
disks remain unchanged. Superseded overlays generated during this maintenance
round were retired while retaining their logs and receipts. No unrelated data
or original release artifact was removed.

Signed package: `release/omarchy-server-0.1.1-1-any.pkg.tar.zst`, **17,957 bytes**.
SHA-256: `83257e807999fe250a6046c792649277281d21f9c5fd0010a6af037d8c7fc3ad`.
Its detached signature and `SERVER-MAINTENANCE-SHA256SUMS.asc` verify with the
same unofficial maintainer identity. Selected evidence is bundled in
`release/server-maintenance-evidence.tar.gz`; private test keys, credential
media, SSH command logs and restore terminal input transcripts are excluded.

New-version transitions for limine-mkinitcpio-hook and the snapshot sync core
remain outstanding; those packages stayed at 1.40.0-2 and 1.32.1-2 respectively.
Physical hardware, Secure Boot and the remaining operational roadmap are still
outside this round. Only about 878 MiB free remained after acceptance; a fresh
ISO build requires additional workspace (documented baseline: 12 GiB free).


## Failure-recovery acceptance — 2026-10-31

This round delivers signed **omarchy-server 0.1.2-1** and build-source changes.
The original ISO and its signatures/checksums remain unchanged. Runtime commit:
`e95a47d`; fault-harness commits: `1826d4a`, `27ae243`, `01db234`. Per-run
receipts identify the exact source hashes and tested package SHA-256.

Package size: **19,196 bytes**. SHA-256:
`5c41cd8a519b5056e07ef2eb6e45cd34d766996629d87939c363310951f63dc8`.
Its packaged updater matches the source file byte-for-byte. The unofficial
maintainer detached signature verifies with the fingerprint listed above.

The updater now retains private transaction transcripts outside root snapshots,
uses atomic phase markers, checks minimum root/ESP working space, refuses a
stale Pacman lock, and detects Pacman error output even when its exit status is
zero. Failed hooks require manual repair and explicit acknowledgment before
another upgrade; a no-op retry cannot silently clear that failure.

Fault injections use fresh disposable overlays of accepted installations,
read-only signed package shares and blocked external guest networking. A fixture
package changes from version 1 to 2; it is not part of the ISO's package set.
Each passing case includes an actual corrected update and cold reboot, SSH,
networkd/resolved, snapshot services, kernel/boot verification and Pacman
database/fixture-file integrity checks.

| Fault | Required observed behavior and recovery |
|---|---|
| Post-transaction hook exits nonzero while Pacman exits zero | Updater returns failure, retains transcript, reports possible applied changes; default UEFI boots the actual pre-update snapshot, restores version 1, then upgrades successfully. BIOS and LUKS also verify refusal until explicit hook repair/reinstallation acknowledgment. |
| Pre-transaction hook aborts | Version 1 remains installed; updater fails without claiming success; removing the fault allows a successful upgrade/reboot. |
| Root ENOSPC | Btrfs extent reservations reach ENOSPC; updater refuses before snapshot/package changes; releasing the reservation allows upgrade/reboot. |
| ESP ENOSPC | Zero writes fill FAT32 until ENOSPC; updater refuses before snapshot/package changes; removing the filler allows upgrade/reboot. |
| Interrupted package download | HTTP closes after a partial payload; no package changes or success claim; subsequent HTTP byte-range resume, signature verification, upgrade and reboot succeed. |
| Stale Pacman lock | Updater refuses without creating a snapshot or changing packages; does not remove the lock automatically; confirmed manual lock removal allows upgrade/reboot. |
| Abrupt shutdown during a post-transaction hook | SIGKILL terminates QEMU after the fixture upgrade reaches a deliberately paused hook. The guest reboots, the incomplete phase marker and transcript survive, no success is claimed, and repair/reinstallation plus retry complete successfully. Repeated with LUKS unlock. |

The signed 0.1.2 package also passed the real system upgrade and restore test
again: kernel **7.2.8-9 -> 7.2.8-10 -> 7.2.8-9**, systemd
**262 -> 262.1 -> 262**, and Limine **12.9.3 -> 12.9.4 -> 12.9.3**.
All observations included cold boots and matched kernel/modules/boot payloads;
installed package count remained **175**. The newer kernel is from edge and the
historical userspace is constructed from signed archived packages; this retains
the earlier controlled-test limitation.

The power-cut test does not require Pacman's lock file to survive reboot: on
this baseline it was absent, while the atomic updater marker persisted. The
separate stale-lock case verifies the lock policy directly. This corrects a
test assumption rather than dropping the lock-refusal check. Other test-fixture
corrections added HTTP byte-range support and moved QMP control sockets into
short private temporary paths to avoid the UNIX socket path-length limit.
Earlier failed attempts and logs remain available for inspection.

The final matrix passed **10 fault/recovery scenarios**: seven default UEFI
cases, BIOS post-hook recovery, encrypted UEFI post-hook recovery, and encrypted
UEFI power-cut recovery. The real system upgrade/rollback passed separately.

Regression checks: **77 passed** (10 server, 9 boot integrity, 58 shared).
Shell syntax, warning-level shellcheck and Python compilation pass.

The selected receipt list and sanitized evidence bundle are
`release/server-failure-recovery-acceptance.json` and
`release/server-failure-recovery-evidence.tar.gz`. Their checksums, this
validation record and the signed package are covered by
`release/SERVER-FAILURE-RECOVERY-SHA256SUMS` and its detached signature.
Private SSH keys, credential media, SSH command logs and interactive restore
transcripts are excluded from the release bundle; full evidence remains private
on the development VM.

This round uses accepted replacement baseline disks created on 2026-10-30:
default `20261030-190040-base`, BIOS `20261030-190713-base-bios`, encrypted
`20261030-190404-base-luks`, all under `test-runs/server/`. Earlier directory
lists above describe historical runs; the acceptance JSON records the current
selected overlay paths. No baseline disk is modified by these overlay tests.

These are controlled fault points, not proof of recovery from every power-loss
write window, physical disk failure or corrupt filesystem. Physical hardware,
Secure Boot, new-version hook/snapshot-core transitions and a signed package
update repository remain outside this round. Host snapshots remain disabled by
administrator policy; server image and guest snapshot behavior remain enabled.
A refreshed ISO has not been built in this maintenance round.

Selected current fault-test directories under `test-runs/server/`:

- `20261031-014527-failure-post-hook/`
- `20261031-014718-failure-pre-hook/`
- `20261031-014813-failure-full-root/`
- `20261031-014852-failure-full-esp/`
- `20261031-014931-failure-download/`
- `20261031-015007-failure-stale-lock/`
- `20261031-015042-failure-power-cut/`
- `20261031-014222-failure-post-hook-bios/`
- `20261031-014300-failure-post-hook-luks/`
- `20261031-014723-failure-power-cut-luks/`

Real-package upgrade/restore: `20261031-014412-system-upgrade/`.


## Signed channel, migrations and refreshed ISO — 2026-10-31

This round delivers a real signed package-update channel and a refreshed
**unofficial** server ISO. The earlier flat release and its signatures remain
unchanged; superseded development candidates are not the release below.

### Current artifacts

- ISO: `release/server-0.2.0-2026.10.31-final/omarchy-slim-server-unofficial-2026.10.31-x86_64.iso`.
- Size: **1,255,964,672 bytes** (1.170 GiB).
- SHA-256: `8210964608eb3bae6297ceb16a199f93e1003bc1bccce5577d296a33c27cfe8a`.
- ISO build-source revision: `581ee97d916b16f0096d2084848bf7073fdfbbe2`.
- Signed package-source revision: `9ad55285912572d2f07f250a63ed8c9699a48b7a`.
- Public artifact-branch commit: `bf975501ec65667ade9f9d46f802aa88107e8f52`.
- Live repository: `https://raw.githubusercontent.com/cobrakoral/omarchy-iso-slim-server/server-packages/stable/x86_64/`.
- Signed repository-manifest SHA-256: `65fa2b0ce73c07c8e725f3434f2fc6378c8901b161a3c03e84127c423182a642`.

The live HTTPS check fetched all **16 authenticated files**, matched every hash
to the signed manifest and verified the database, file database and all package
signatures against the pinned maintainer. Repository aliases contain actual
bytes rather than symlinks, as required by raw Git hosting.

| Package | Version | Bytes | SHA-256 |
|---|---|---:|---|
| omarchy-server | 0.2.0-1 | 23,737 | `e3fe156b6a28b72bbdec340c15128144731422909209e0fa6dad8dea069d4fbf` |
| omarchy-server-keyring | 20261031-1 | 12,999 | `3973828ae1d2a4b6e517c810ace403ee3b9aed35d4886f7588af690cf823248e` |
| limine-snapper-sync-server | 1.32.1-3 | 9,441,389 | `d987f2a58fd8a54322087a4a8c1fc6200c85d874cc689383d849ad5fda5dab08` |

The snapshot package upgrade is **packaging revision -2 to -3**, with the same
pinned official 1.32.1-2 source archive. It does not claim a newer upstream core.
The concrete server-package dependency prevents graphical notification packaging
from being selected as a substitute. The small official runtime selection is
checked against its revision/hash lock; curated vendoring remains explicit.

### Passed acceptance

| Scenario | Evidence |
|---|---|
| Signed upgrade from the original installed server | Runtime 0.1.0 -> 0.2.0, headless package 1.32.1-2 -> 1.32.1-3, added packaged trust anchor; real updater, migration hook, reboot; 176 packages |
| Tampered database | Pacman rejected the database; installed runtime/core unchanged; corrected signed channel upgraded and rebooted |
| Missing database signature | Required signature prevented the transaction; corrected signed channel upgraded and rebooted |
| Unsigned package under signed metadata | Missing required package signature prevented installation; corrected signed channel upgraded and rebooted |
| Wrong bootstrap fingerprint | Helper rejected the supplied key before altering Pacman configuration |
| Known but untrusted database issuer | Public test key imported without local certification; Pacman rejected its database signature; corrected trusted channel upgraded and rebooted |
| Scoped migration failure and repair | Desktop fixture never executed; reviewed shared/server work applied; failed action retained a journal and stopped update; repair resumed without rerunning applied work |
| Immutable applied migrations and edition guard | Changed applied script/hash refused; desktop edition refused before execution |
| Migration ledger rollback | Actual Limine snapshot boot, restore, normal-root reboot and upgrade reapplication restored correct configuration/ledger state; audit showed each applicable execution |
| BIOS and encrypted UEFI upgrade | Same signed 0.2.0 channel, concrete headless package, migrations and cold boots; LUKS unlock passed |
| Fresh default ISO installation | Entirely offline; normal login/SSH/networkd/resolved, package exclusions and snapshots; 176 packages |
| Fresh optional cloud-init ISO installation | Entirely offline; separate instance provisioning and installer cidata separation; 203 packages |
| Installed channel trust without bootstrap | Both fresh installations verified signed metadata and reinstalled the keyring through Required/TrustedOnly Pacman policy without adding or locally signing the key manually |
| Exact delivered runtime identity | Installed updater, migration runner and channel helper hashes matched the signed package-source manifest on both installations |
| Fresh ISO snapshot restoration | New default installation booted its actual snapshot, restored it, rebooted to normal writable @; signed-channel trust and boot integrity checked again |
| Immutable package generation | Reuse retained archives/signatures byte-for-byte; recipe change without a version bump was refused |

Regression results: **94 passed** (12 server, 15 migration/policy, 58 shared,
9 boot integrity), plus an occupied-SSH-port refusal probe. The shared image,
disk, desktop and generic target-keyring paths are unchanged. Only the server
phase populates the additional trust anchor and stops its GPG agents. No full
desktop ISO build was required or claimed for these server-only changes.

The migration journal regression was reproduced before correction: unfinished
work could disappear/change and a leftover journal after an applied receipt
was not reconciled. The three added checks failed before the fix and passed
after it. A pending ID/hash/scope must remain consistent until explicit repair;
a durable completed receipt safely clears a stale journal without reexecution.

The QEMU harness now reserves each forwarding port and refuses an occupied
port before launch, preventing overlapping runs from attaching to another guest.
An invalid overlapping run has no passing receipt and is excluded. All selected
final cases used guarded independent ports. Earlier prototype failures and
superseded build candidates remain recorded, not reported as successful releases.

The channel publisher verifies pinned signatures, source hashes and origin,
commits atomically to the fork's artifact branch, refuses versioned-file
replacement and retains old archives for cached clients. No force push, upstream
change, PR, GitHub Actions build, or private signing-key publication occurred.
Fresh builds use isolated cache/output generations and preserve previous release
signatures and package archives. The live channel uses strict package/database
signature policy; the internal ISO mirror retains upstream's ISO-integrity model.

The signed release manifest covers the new ISO, package-channel bundle, selected
sanitized evidence, acceptance summary and this validation record. Private SSH
keys, credential media, SSH command logs, private GPG material and interactive
restore input transcripts are excluded from the evidence bundle. Test-only
repositories (including the intentionally failing 0.2.1 fixture) were never
published. The production runtime is 0.2.0.

Physical hardware, Secure Boot, actual new-version upstream snapshot/hook
transitions and byte-identical ISO reproduction remain unverified. The server
still deliberately omits desktop provisioning and BBS/Docker/admin applications.
Host snapshots remain disabled by administrator request; guest snapshot and
rollback behavior remain enabled and tested.

## Live networking and emulated NIC matrix — 2026-10-31

The delivered **0.2.0 ISO remains unchanged**, with SHA-256
`8210964608eb3bae6297ceb16a199f93e1003bc1bccce5577d296a33c27cfe8a`.
The test source is signed commit `0868bba` (NIC/peer support `2dedcb8`).
No runtime package, installer phase or shared desktop behavior changed in this
round. This extends the evidence for the existing release.

The matrix booted new copy-on-write overlays of the accepted fresh installation
`test-runs/server/20261031-024251-base`. Each backing-disk SHA-256 was checked
again after the case, and receipts bind results to the exact ISO and test-source
hashes. Existing releases, package archives and test disks were preserved.

| Data NIC model | Actual kernel driver | DHCP and selected route/DNS | Cable loss/fallback/recovery | Cold boot and persistent static IPv4 |
|---|---|---|---|---|
| virtio-net-pci | virtio_net | Passed | Passed | Passed |
| e1000 | e1000 | Passed | Passed | Passed |
| rtl8139 | 8139cp | Passed | Passed | Passed |

Each guest also had a management NIC and a separate isolated renewal NIC.
Explicit MAC-matched networkd profiles selected the preferred uplink by route
metric and the `~.` DNS routing domain. Disconnecting it through QMP removed
its routes/resolver selection, selected management DHCP/DNS and preserved
working HTTP and SSH. Reconnection restored the preferred route and DNS.
Cold boot preserved that policy; the actual installer-generated static IPv4
profile also retained its address, gateway and DNS on a further cold boot.
Carrier loss is covered; loss of an upstream route with carrier still up is not.

The third NIC connects to a loopback-only framed Ethernet DHCP/ARP fixture,
not to a host bridge or LAN DHCP socket. Successful renewal requires an observed
unicast REQUEST with a nonzero client address, an ACK with the expected lease,
and an increased networkd lease timestamp. The initial libslirp-backed renewal
attempt produced no ACK in the observed window and correctly failed. It is
retained as failed evidence and is not counted as a successful renewal. The
controlled peer provides explicit request/ACK evidence without weakening the
assertion or changing the installed client.

The virtio case resolved real external DNS, fetched the live fork channel over
IPv4 HTTPS from inside the guest, and verified the manifest and database with
the installed trusted keyring and pinned primary fingerprint. The database bytes
matched the signed manifest; manifest SHA-256 remained
`65fa2b0ce73c07c8e725f3434f2fc6378c8901b161a3c03e84127c423182a642`.
No manual key import, local signature, package upgrade or weaker trust policy
was used for that check.

A further cold boot used a persistent IPv6-only data profile. A detached guest
unit ran while **both IPv4-capable NIC cables were disconnected**. It verified
no non-loopback IPv4 addresses, a static IPv6 default route, IPv6 HTTP transport,
real AAAA answers over an IPv6 DNS endpoint, and a live IPv6 SSH listener.
Management SSH and all server health checks recovered after reconnecting.
The DNS endpoint forwarded the guest's IPv6 UDP requests through the host's
IPv4 resolver. It is an explicit test adapter: the host has no upstream IPv6
route, so public IPv6 Internet reachability remains unverified.

All three cases rejected SSH password/keyboard-interactive negotiation when
public keys were supplied, and recorded the complete IPv4/IPv6 listener table.
Observed stock listeners included SSH TCP/22, loopback resolved DNS/53,
resolved LLMNR TCP/UDP 5355 and multicast UDP 5353. These results do not claim
that SSH is the only listening service or that a firewall was configured.
The package set stayed at **176**, with boot integrity, package-database checks,
networkd, resolved, SSH and guest snapshot services healthy.

Final successful runs:

- `test-runs/server/20261031-104254-network-virtio`
- `test-runs/server/20261031-104414-network-e1000`
- `test-runs/server/20261031-104517-network-rtl8139`

All **94** existing regression checks passed again (12 server, 15 migrations,
58 shared installer/image, 9 boot integrity). A separate overlay also rechecked
the original default `restrict=on` networking and blocked external guest TCP.
The online harness is opt-in; existing offline suites keep their prior policy.

Signed acceptance, sanitized evidence, source hashes and checksums are under
`release/server-network-2026.10.31/`. Credential media, SSH/private keys,
serial/password transcripts, raw SSH command logs and VM disks are excluded
from the distributable bundle. Failed exploratory runs have no passing receipt
and are excluded from the acceptance aggregate.

This is x86_64 QEMU/KVM NIC coverage, not physical hardware certification.
Physical NICs/storage, Wi-Fi, public IPv6 Internet and Secure Boot remain
unverified. The next release-maintenance milestone is fresh-checkout/empty-cache
reproduction and automated release acceptance; physical deployment checks still
need the relevant hardware and network.

## Fresh checkout, cold-cache build and automated qualification — 2026-10-31

A new ISO was built from a fresh GitHub checkout and the clean pinned archiso
submodule. **No earlier project package cache was seeded**. The live signed
server channel was downloaded and authenticated independently; the upstream
transaction was downloaded afresh. The initial container remained digest-pinned.

- Artifact: `release/server-rebuild-2026.10.31/omarchy-slim-server-unofficial-2026.10.31-x86_64.iso`
- Size: **1,255,964,672 bytes** (1.170 GiB).
- SHA-256: `553c3424ec8cd835167525c4e47dcb20f170e42b52d24bc160f860cc35721036`.
- Image source: `8dc00f363aa9457b6709277adbe36be17909ea78`.
- Acceptance runner: `7eabba50665c1ae5482394a8e63e154b7ce9bcd2`.
- Archiso: `424e78130db2af6c1ceb55b442d7914b1109ff2b`.
- Base/cloud package closures: **176 / 203**, with the same versions and
  package transaction as the earlier accepted 0.2.0 image.

| Fresh ISO installation | Exact installed package versions | Boot, SSH, services and snapshots |
|---|---|---|
| Default UEFI | 176, matched build closure | Passed |
| Cloud-init UEFI | 203, matched base + 27-package delta | Passed |
| BIOS | 176, matched build closure | Passed |
| Encrypted UEFI | 176, matched build closure | Passed, including passphrase unlock |

All installations began with new standalone disposable disks and ran the real
offline installer. Receipts bind the ISO SHA-256, clean source revision,
committed harness hash and installed package/version map. Default and cloud
systems then verified installed signed-channel trust without manual key import.
The default trust overlay booted an actual Limine snapshot, restored it,
rebooted to writable normal root and verified boot/package/service health and
repository trust again. The source and ISO digests remained unchanged.

The full upstream VM-free shell suite and **378 Python tests** ran against a
sparse checkout of official runtime `84782475aeb885c8d05f9e85b6ad0f1a0add60e5`;
Python finished successfully with **3 skips**. An initial host-side attempt had
selected an older installed runtime and failed three desktop keyboard-label
comparisons; selecting the actual pinned runtime resolved those comparisons.
The qualifier fetches that runtime explicitly. **50 server checks** also passed:
12 edition/configuration, 15 migrations, 9 boot integrity and 14 release-input/
receipt checks. The completed-output refusal was additionally exercised against
the real qualified release, with all its existing metadata unchanged.

The first qualification attempt stopped at its 8 GiB reserve after the first
two installs. It emitted no final success record. The runner now synchronizes
completed compaction frees before checking capacity, and can resume matching
completed cases. Resume verifies the ISO, unchanged production build files,
package versions, committed harness and compacted disk hashes; earlier receipts
retain their original revisions. Build and acceptance revisions are distinct in
the final aggregate. A completed qualified generation refuses another resume.

Only the newly generated profile and unused Docker image were discarded.
After the cold build completed, 478 byte-identical archive files shared extents
with retained archives, preserving all paths and SHA-256 values. This storage
optimization happened **after** the independent download/build; it was not
input cache seeding. Newly installed disks were compacted or safely rebased to
the first accepted disk only after a complete virtual-byte comparison. Bases
and dependencies are retained; existing generations/disks remain untouched.
Encrypted guest data remains encrypted. Host snapshots remain disabled.

The new ISO and final checksums carry the maintainer signatures. Signed
`server-release-acceptance.json`, source/build/package metadata and sanitized
evidence are beside the ISO. Credential media, private keys, serial/password
transcripts, raw SSH command logs and VM disks are excluded from the bundle.
Detailed private test paths are retained in the aggregate for local replay.

This verifies **functional reproduction from a fresh checkout and empty build
cache**, not byte-identical ISO reproduction. Btrfs identities and embedded
source/build metadata differ. Upstream mirrors remain mutable; preserving
archives is necessary for a later replay of the exact package versions.
No shared production installer/image or desktop code changed, so a desktop ISO
was not rebuilt. Physical hardware, public IPv6 and Secure Boot remain unverified.

## Operational workload and fresh-machine application recovery — 2026-10-31

The final planned development round exercised the existing signed ISO
`553c3424ec8cd835167525c4e47dcb20f170e42b52d24bc160f860cc35721036`.
No ISO, runtime package or production installer change was needed. The synthetic
fixture uses Python/SQLite already present in the **176-package** default server.

| Measurement | Observed result |
|---|---|
| Workload duration | 300.19 seconds |
| HTTP requests completed | 10,527 |
| Durable writes acknowledged and verified | 2,634; no duplicate committed tokens |
| Forced service process crash recovery | 1.44 seconds |
| Transient failed attempts retried | 53 (all requests eventually completed) |
| Request latency p95 / p99 | 62.7 / 85.8 ms |
| Process descriptors before / after load | 4 / 4 |
| Sampled maximum descriptors | 11 |
| RSS before / after load | 24.1 / 29.2 MiB |
| Backup database cut | 2,638 records, integrity and digest matched |
| Restored filesystem entries | 30; modes/owners/ACLs/xattrs/symlink/hardlink checked |
| Recovery including fresh OS installation | 125.78 seconds |
| Restore portion | 21.62 seconds |
| Acknowledged records after backup cut, absent from recovery | 26 |

This is a small synthetic fixture under QEMU on the development host, not a
production performance benchmark, availability guarantee or multiweek stability
claim. One forced process crash was exercised; arbitrary host power-loss windows
were not tested in this round. The fixture listened only on loopback.

The source was a new copy-on-write operational guest on the accepted 0.2.0
baseline. Its five-minute measured workload, database digest and cold-boot
continuity were verified. A backup-helper correction was then applied: static
paths are absolute, live SQLite/WAL files are excluded from file copying, and
the database uses SQLite's online backup API. Resume checked the committed
workload records and unchanged deployed application hash before reusing that
completed load. The original workload receipt remains intact and hashed.
Static source metadata matched its staging copy; concurrent write acknowledgments
and a health probe continued while the database backup was taken.

The archive was encrypted with AES-256/GnuPG and signed with the pinned maintainer
key outside the guest. Tampered signature/ciphertext and a missing key were
rejected, and no unverified archive was delivered to the replacement machine.
Nine archive-admission regressions rejected traversal, unrelated identity files,
device nodes, duplicate entries and escaping links. The restored target also
refused a second application restore without changing its data or service.

Before simulating loss, the source booted and restored a real Limine snapshot.
The root marker/database reverted; `/home`, `/var/log` and package-cache markers
kept their later values. The source was then stopped. A fresh standalone disk
was installed from the signed ISO. Its backing-chain length was **one**, with
no dependency on the source VM or its backing files. Recovery used the signed
encrypted backup, its separately held key and fresh installation media/code.
The machine ID and SSH host key were individually distinct from the source and
remained unchanged by application restore. Global account/identity/network/boot
files were not copied from the source.

The replacement matched the backup database digest and 30 filesystem entries,
accepted a new durable write and cold-booted with the same resulting data.
Boot integrity, package database, channel trust and required server/snapshot
services remained healthy at 176 packages. The 26 later acknowledgments were
intentionally outside the backup cut; this is not a zero-RPO claim.

The full upstream VM-free shell suite and **378 Python tests** passed again
(**3 skips**), plus **59 server checks** (12 edition, 15 migration, 9 boot,
14 release guards and 9 archive-admission tests). Source acceptance revision:
`3c0eb21d41fab1661a5f833baf91241579e1590d`. The prior signed ISO and baseline hashes remained
unchanged. New disk compression was verified byte-for-byte virtually before
replacing only this round's stopped source representation. Existing disks and
release generations were preserved; host snapshots remained disabled.

Signed sanitized acceptance and evidence are under
`release/server-operations-2026.10.31/`. Actual backup ciphertext, its key,
credential media, raw SSH and serial/password transcripts remain private and
are excluded from the distributable evidence.

The backup was **external to the guest on the same VM host**. It does not
establish an independent offsite failure domain or host-loss recovery. This is
application/configuration/user-data recovery onto a fresh OS, not a whole-disk
image restoration. All six planned development milestones now have measured
functional evidence; multiweek workload use, independent offsite recovery,
physical hardware, public IPv6 and Secure Boot still need deployment evidence.

## Explicit virtual ACPI power-button policy — 2026-10-31

Server source now sets `HandlePowerKey=poweroff` explicitly for live media and
installed targets, from the shared server policy `builder/server/logind.conf`.
The target drop-in sorts after an inherited ignore setting. No desktop policy,
package recipe, inhibitor bypass, or host snapshot setting changed.

The server target helper generated the file applied to a new disposable overlay
of the accepted 0.2.0 default installation. With a conflicting earlier ignore
setting, a real QMP `system_powerdown` left the guest running and SSH available.
After installing/reloading the generated policy, the same ACPI request powered
off normally in **0.88 seconds**. After cold boot, logind still reported
`poweroff`, and another ACPI shutdown completed in **0.77 seconds**. Serial
kernel shutdown evidence and successful exit were retained. The backing guest
was preserved and the actual development VM was never powered off by this test.

Evidence: `test-runs/server/20261031-150945-acpi/acpi-pass.json` and serial logs;
`.server-work/acpi-policy-test.log`. The 13 server configuration tests and 58
shared installer/image regressions passed; shell syntax and signed existing
package-source verification passed. Future fresh-ISO acceptance now requires
the explicit installed drop-in and the live logind `poweroff` property.

This validates the production helper-generated policy on a running server.
The updated live-image copy path passed shell/source checks, but no new ISO
was built: available space is below the 12 GiB build minimum. Existing signed
ISO bytes are unchanged and do not acquire this source change retroactively.


## Review corrections and qualified ISO — 2026-11-07

A fresh empty-cache build incorporates the production corrections from the
November 7 review. Supplementary integration also corrected retained-archive
selection in the historical upgrade helper. Existing releases, archives,
accepted disks and their backing chains are preserved.

- ISO: `omarchy-slim-server-unofficial-2026.11.07-x86_64.iso`.
- Size: **1,255,964,672 bytes**.
- SHA-256: `6a0b336606730ab55b673a29d7bb2046efeb2df08ec93551ea245b083aae0f70`.
- Image and full automated qualification source: `54b90d11716c449f8896389204dfddf2a6847871`.
- Supplementary helper source: `0873ce1703a74383222d854b676a7e805591050b`. Only four test
  files changed between these revisions; production build inputs remain identical.
- Package closure: **176 base / 203 cloud-init**; `linux-omarchy 7.2.15-1`.
- Custom channel remains `omarchy-server 0.2.0-1`,
  `omarchy-server-keyring 20261031-1`, `limine-snapper-sync-server 1.32.1-3`.

| Finding | Correction | Verification |
|---|---|---|
| F1 | Reject credential configuration overrides; freeze validated documents before shared loading. | input_validation.py |
| F2 | Validate every SSH public-key entry before deriving key-only SSH policy. | input_validation.py |
| F3 | Require literal JSON boolean true for administrator sudo. | input_validation.py |
| F4 | Reserve account names from the actual built target namespace. | input_validation.py |
| F5 | Hash paths relative to recipes, independently of checkout ancestors. | harness_guards.py |
| F6 | Create child disks for manual recovery, update and recheck; guard protected in-place writes. | harness_guards.py and real helper checks |
| F7 | Keep signed server policy and fixtures; select one manifest-bound maintenance archive. | signed-update, historical upgrade, post-hook recovery and regressions |
| F8 | Exclude build timestamps from seed creation and refuse live/seed UUID collisions. | harness_guards.py and timestamp-exported release qualification |

Full release qualification passed fresh UEFI base, cloud-init, BIOS and encrypted
UEFI installs with exact package/version maps; the delivered interactive Gum
wizard with console/password SSH access; installed base/cloud repository trust;
and real Limine snapshot boot, writable restore and a healthy normal-root reboot.
Build-only SOURCE_DATE_EPOCH was intentionally exported during qualification.
No seed mounted in place of the live ISO.

Delivered ACPI poweroff passed on live media (2.22 seconds), the fresh
installed system (0.81 seconds), and a cold boot (0.87 seconds).
An explicit ignore override kept the guest running as the negative control;
removing it restored normal shutdown. The test measures delivered policy.

A signed fixture upgraded version 1 to 2 under trusted package and repository
signatures, retained version 1 in the pre-update snapshot, kept exactly five
numbered snapshots after cleanup and cold-booted successfully. Manual recovery
and recheck used new child disks. All four supplementary helpers preserved the
accepted base disk SHA-256 before and after execution.

Historical system upgrade and restore passed: kernel
**7.2.8-9 -> 7.2.8-10 -> 7.2.8-9**, systemd
**262-1 -> 262.1-1 -> 262-1**, Limine
**12.9.3-1 -> 12.9.4-1 -> 12.9.3-1**. This is a controlled signed-archive/edge
transition, not stable-channel release-to-release certification. A real
post-transaction hook failure passed refusal, snapshot restore, corrected
update and cold boot with current signed server policy.

**79 server regressions passed**, including three final archive-selection checks.
The main release runner passed the preceding 76; the full upstream shell suite
and **378 Python tests (3 skips)** passed against the pinned runtime. Image and
acceptance revisions remain distinct from later test/documentation commits.
Server-specific production behavior changed; a desktop ISO was not rebuilt.

The local release includes signed aggregate/supplementary acceptance, review
closure, build/source/package metadata, this validation record, ISO signature
and final signed checksums. Credential media, private keys, raw SSH and
serial/password transcripts, disks and actual backups remain private.
The November 5 and October 31 signed ISO hashes were rechecked unchanged.
Host snapshots remain disabled; guest snapshots remain enabled.

Public IPv6 deployment tests remain pending. Secure Boot is excluded by the user.
Physical hardware, multiweek production operation and independent offsite
host-loss recovery are not established by this qualification. Moving package
repositories and per-build filesystem identities prevent a byte-identical
future-build promise. Test repositories were not published to the live channel.

## Source and harness review — 2026-11-08 (not ISO-qualified)

At the signed `b7072ce` checkpoint, the checkout, upstream runtime pin
`84782475aeb885c8d05f9e85b6ad0f1a0add60e5` and matching signed
`omarchy-server 0.2.0-3` repository were verified. The hosted fork branch was
still at `aaebfdc`; these validation changes remain local.

A QEMU startup-failure regression demonstrated that retaining the exception
traceback from a failed `pexpect.spawn` could retain the guest SSH port lock.
The constructor now closes the child (if spawned), serial log, QMP control
directory and port guard on this failure. A test retains the traceback while
requiring a second handle to acquire the same lock.

After this fix, all **134 server tests passed** in the seven source suites;
the shared suite ran **388 tests (385 passed, 3 skipped)** against the pinned
runtime. The signed `0.2.0-3` package channel verified against the unchanged
package recipes. A live QEMU QMP version/status round trip passed. Disposable
QCOW2 testing passed interrupted-finalization journal recovery, idempotent
resume and refusal of changed disk bytes. `git diff --check` passed.

A fresh ISO build and release guest matrix were **not run**. The build-space
preflight refused a proposed new cache/output generation on the shared
filesystem: approximately **9.20 GiB** free versus a **12 GiB** minimum,
short by approximately **2.80 GiB** before build and guest disk growth.
Release guest execution separately requires at least 8 GiB free at each
case boundary. No prior ISO, signed repository, accepted disk, build cache or
release evidence was discarded. The historical November 7 ISO does not
qualify these later source changes, and no new ISO or final acceptance JSON
was produced. Secure Boot and public IPv6 deployment remain deferred.

## Fresh signed 0.2.0-3 ISO release qualification — 2026-11-08

Following removal of the disposable historical build, release, and test-run
workspaces, free VM disk space increased to about 56 GiB. The clean, signed
production/harness source was `bdfa23c58407b4c8f6bfa5e93e38044e7e262e0e`.
This documentation record is made **after** qualification; it is not an input
to the qualified ISO. The generated source and acceptance revisions are kept
explicitly distinct from any later documentation-only commit.

The prior published signed channel supplied unchanged immutable
`omarchy-server-keyring 20261031-1` and
`limine-snapper-sync-server 1.32.1-3` packages. The official snapshot archive
passed its pinned hash and upstream signature check. The runtime package was
rebuilt and signed as `omarchy-server 0.2.0-3`; the reconstructed local channel
passed source-file, signed repository/database, and package verification. It
was **not published**. Local candidate:
`.server-work/candidate-20261108/stable/x86_64`.

- Fresh output: `release/server-fresh-20261108-094128/`.
- ISO: `omarchy-slim-server-unofficial-2026.11.08-x86_64.iso`.
- Size: **1,255,964,672 bytes**.
- ISO SHA-256: `6b3828edeec6c9f6f45c53529d26fd2016c8facf0a1b0cb6ff6596a7fac93ef8`.
- Fresh empty-cache build and qualification source:
  `bdfa23c58407b4c8f6bfa5e93e38044e7e262e0e` (clean).
- Upstream regression source: `84782475aeb885c8d05f9e85b6ad0f1a0add60e5`.
- Package closure: **176 base / 203 with cloud-init** (27 cloud delta).
- Immutable container digest is recorded in `server-build-inputs.json`.

The automated qualifier finished successfully and wrote
`server-release-acceptance.json` with `passed: true`. Fresh offline
installations passed **base UEFI, cloud-init UEFI, BIOS, and encrypted UEFI**
with exact package-version maps. Each tested stopped disk retained its original
SHA-256 in the acceptance record and a verified, content-preserving compaction
receipt. Delivered interactive Gum installation and SSH/password-login checks
passed. Live ISO and installed ACPI poweroff, cold-boot poweroff, the conflicting
ignore-policy negative control and recovered normal shutdown all passed.

The signed disposable upgrade fixture passed installed signature checks,
snapshot retention and healthy reboot. Installed channel trust passed in both
base and cloud guests, followed by actual snapshot recovery, restored-system
health and a final installed-channel trust verification. The fresh qualification
ran the upstream regression suite **388 tests (385 passed, 3 skipped)** and
all **134 server tests** in seven suites. `SHA256SUMS` and the final ISO SHA-256
were independently rechecked. The completed acceptance JSON also passed
independent checks of its source identity, ISO digest and four compaction disk
digests.

After acceptance, the maintainer locally signed the ISO (`.iso.sig`),
`SHA256SUMS` (`SHA256SUMS.asc`) and the acceptance record
(`server-release-acceptance.json.asc`) with the pinned Ed25519 signing identity.
All three detached signatures verified, including the pinned primary fingerprint
`7C5C6FEB40A5B493007FAA3650B1EB533F640140`.
`UNOFFICIAL-SIGNING-KEY.asc` contains the public key. Private keys and raw
credential-bearing VM transcripts remain outside the release directory.

This is complete virtual release qualification, **not** a guarantee of
byte-identical rebuilds or hardware qualification. Public IPv6 deployment and
Secure Boot remain deferred. No fork package publication, PR or GitHub push
was performed.

## Disk-safety hardening and public-channel release qualification — 2026-11-08

The previous signed community test image (`slim-server-2026.11.08-test1`)
completed functional QEMU acceptance, but code review reproduced an installer
cleanup failure path: `wipefs` errors could be ignored if the later partition
reread succeeded. This **superseding release** fails closed on both partition
and whole-disk signature-wipe failures. It adds isolated failure-injection tests
without touching actual devices. The release harness now uses explicit integrity
exceptions instead of optimization-removable `assert` checks; QEMU's SSH-port
lock, control directory and log resources are released on all constructor
failure paths, including failures before QEMU process creation.

The three implementation commits are individually GPG signed:

- `fc98efd610c911628cb2e10db966a5d603800756` — disk signature failure gate;
- `f10fb31b07172c2cb8d115fc8eeef56df160d6ff` — qualification guard;
- `b50eec605aa75099f7f0f8d2a3b647cfc4860d97` — QEMU constructor cleanup.

The independent, signed public `server-packages` channel was advanced by
`6f84662d413bbbee38739c24c0cc3c08841f74df`, publishing
`omarchy-server 0.2.0-3` and retaining both the previous `0.2.0-1` package
and other immutable historical archives. The new qualifier obtained and
verified this **public GitHub channel**, not an injected local candidate.

The full qualification used a new empty-cache build, clean source commit
`b50eec605aa75099f7f0f8d2a3b647cfc4860d97`, and pinned upstream runtime
`84782475aeb885c8d05f9e85b6ad0f1a0add60e5`.
The upstream suite reported **388 tests, 3 skipped**; the seven server
regression suites reported **143 tests passed**, including new cleanup,
optimized-Python and constructor-failure cases.

Release output: `release/server-fixed-20261108-143658/`.

- ISO: `omarchy-slim-server-unofficial-2026.11.08-x86_64.iso`;
- Size: **1,255,964,672 bytes**;
- SHA-256: `3707183a3fec121ada38625fa9ca2fffc7fb1ff2510bec09349a498d00f2fa35`;
- Acceptance: `server-release-acceptance.json` (`passed: true`, `resumed: true`);
- Installation closures: 176 base packages, 203 with cloud-init;
- Fresh guest cases: UEFI base, UEFI cloud, BIOS and LUKS, each with checked
  original and compacted disk digests;
- Additional acceptance: interactive Gum wizard, ACPI with negative control,
  signed update and retained snapshot, installed base/cloud package trust,
  real snapshot boot/restore and healthy reboot.

The first cloud guest completed its cloud-init boot and initially accepted SSH,
but a subsequent SSH handshake timed out and failed the harness. The
failure remained in its private test-run evidence; it was **not** counted as a
pass. Evidence-bound `--resume-work` verified the unchanged ISO/build source,
reused the accepted base disk and reran the cloud installation in a **new**
disposable VM. That fresh cloud case and all remaining cases passed. The
complete release harness exited 0 with `SERVER_FRESH_RELEASE_ACCEPTANCE_PASS`.

The final ISO SHA-256, manifest, signed channel and four compacted disk digests
were independently reverified. New detached Ed25519 signatures cover the ISO,
`SHA256SUMS` and `server-release-acceptance.json`, using pinned primary
fingerprint `7C5C6FEB40A5B493007FAA3650B1EB533F640140`. Every detached
signature verified. Credentials, raw SSH/session transcripts, test-disks and
private signing material remain outside release assets. Documentation commits
after qualification are not represented as inputs to this ISO.

These are QEMU/KVM qualification results, not hardware/Proxmox qualification.
Secure Boot and public IPv6 deployment remain deferred.

## Successful installer-secret cleanup and fail-safe acceptance — 2026-11-08 test3

The hardened **test3** release supersedes test2. Production source was modified
only in the live server installer wrapper, not in the signed installed runtime
package. Review verified the following independent GPG-signed commits:

- `a2a3880818a6552bd810b40cae6ceef0232ea1f6` — remove live-only
  credential/validated JSON after successful orchestration; refuse reboot or
  completion on cleanup failure; keep failed inputs for explicit retry;
  prevent a successful live console from relaunching the installer; treat a
  declined reboot as a normal successful exit.
- `5c18b8dae363da29e191eb0e036dfd85f9135f56` — make all remaining
  ACPI, interactive, installed-channel and repository acceptance checks
  explicit runtime exceptions rather than Python optimization-sensitive
  assertions. No acceptance test depends on a side effect inside `assert`.
- `03acd37d08863ad774f1ce6ccc9ebc2c320277cf` — document independent
  checkout/build against the public `server-packages` channel, current
  `omarchy-server 0.2.0-3`, and installer credential lifecycle.

VM-free regressions: pinned upstream **388 tests (3 skipped)** and seven
server suites **150 tests**. The critical suite was also run under both
`PYTHONOPTIMIZE=1` and `PYTHONOPTIMIZE=2`. New isolated Bash fixtures exercised
successful cleanup and post-success idempotence; failure preserving inputs;
subsequent safe retry; automatic or confirmed reboot; declined reboot; and
cleanup failures refusing a completion marker/reboot. Fixtures used synthetic
paths and credentials; attached input media was not modified.

A new unseeded build and complete release qualifier ran against exact signed
source `03acd37d08863ad774f1ce6ccc9ebc2c320277cf`, the pinned clean
archiso submodule and the **public, signed GitHub package channel** (not a
local test candidate). The qualifier exited **0** with
`SERVER_FRESH_RELEASE_ACCEPTANCE_PASS`. Its evidence is under the retained
private working directory `.server-work/release-20261108-164158`; the public
release contains only sanitized accepted evidence, not raw credential or SSH
transcripts.

- Output: `release/server-hardened-20261108-164158`.
- ISO: `omarchy-slim-server-unofficial-2026.11.08-x86_64.iso`.
- Bytes: **1,255,964,672**.
- SHA-256: `32f34f2fa651e7c17c1d567d2998e6c250f40e5b1a571b1979edf91c70dd9015`.
- Four fresh install modes: base UEFI; UEFI cloud-init; BIOS; encrypted UEFI.
  All passed and were compacted; their stored hashes and receipts were
  independently rechecked against all four retained guest disks.
- Interactive Gum installer, password/SSH login, live/installed/cold-boot ACPI,
  negative control, signed update/snapshot/reboot, installed repository trust
  (176 base/203 cloud packages), and snapshot boot/restore/reboot all passed.
- Independent GPG verification passed for the ISO, `SHA256SUMS`, and the
  aggregate acceptance JSON. The pinned maintainer public key is included,
  with primary fingerprint
  `7C5C6FEB40A5B493007FAA3650B1EB533F640140`.
- Exact signed annotated tag: `slim-server-2026.11.08-test3`, pointing to
  the qualified `03acd37d08863ad774f1ce6ccc9ebc2c320277cf` source.
- Release assets: exactly seven public files (ISO, ISO detached signature,
  `SHA256SUMS`, manifest signature, public signing key, accepted JSON, its
  detached signature). Test2 was marked superseded, retaining its original
  historical assets and signed tag.

The documentation-only checkpoint following release is **not** part of the
qualified ISO source. Physical hardware, Proxmox, Secure Boot and public IPv6
routing remain unverified; this is an unofficial community test image.

## Local-only network hardening work: 2026-11-09 (no new ISO)

The source branch now configures a baseline `nftables` firewall for the live
installer and installed server and disables multicast name resolution
(`LLMNR=no`, `MulticastDNS=no`) in global `systemd-resolved` and per-link
`systemd-networkd` settings. The policy uses `inet` to cover IPv4 and IPv6;
allows loopback, established traffic, SSH TCP/22, DHCPv4, DHCPv6 and necessary
ICMP/ICMPv6; drops uninvited input and forwarding; and permits outbound
traffic. The named `inet omarchy_server_filter` table does not clear unrelated
rulesets. The server installer enables `nftables.service`, validates the
configuration without loading it in the live host namespace, and checks the
unit is enabled in the target. The build profile enables nftables early in the
live boot. The package closure adds `nftables` as an explicit root dependency,
which was already present as a transitive dependency in the prior target.

Verification on OmarchyVM **only**: `nft -c` parsing, disposable `unshare`
network and mount namespaces (never the host network namespace), synthetic
IPv4+IPv6 listeners on TCP/22 and TCP/8080, dual-stack echo-request, permitted
DHCPv4 (67->68), permitted DHCPv6 (547->546), and blocking unsolicited 8080 on
both families after ruleset activation. The baseline phase verifies that both
ports are reachable **before** the firewall is loaded, avoiding false-positive
blocking results. Source unit tests also cover DHCP/static resolver options,
target configuration, live staging and both-family firewall policy. The
existing server regressions and Python/Bash syntax checks were rerun locally.

**Release boundary:** No new ISO was built, no release/tag was created, and no
Proxmox/physical-hardware boot was performed for this change. Published `test3`
remains unchanged and therefore does not contain this firewall until a future
explicitly authorized ISO build and qualification.
