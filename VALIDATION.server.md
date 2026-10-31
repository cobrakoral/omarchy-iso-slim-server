# Omarchy Slim Server — measured validation

Latest validation: **2026-10-31, operational workload and fresh-machine application recovery**.
See the final section for current evidence. The original baseline was validated on 2026-10-29. This is an **unofficial community build**, not an
Omarchy release or endorsement.

## Artifact

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
