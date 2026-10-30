# Omarchy Slim Server — measured validation

Validated on 2026-10-29. This is an **unofficial community build**, not an
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
