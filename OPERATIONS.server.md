# Omarchy Slim Server operational recovery

The operational acceptance uses a synthetic loopback HTTP/SQLite service on a
minimal server. It exercises durable writes, process crash/restart, online backup,
root rollback boundaries and application recovery onto a newly installed machine.
It is a short functional rehearsal, not multiweek stability or a production SLA.
The sample HTTP service is a test fixture, not a production application server.

## Root snapshots and external backups

| Location | Storage boundary | Root snapshot restore |
|---|---|---|
| `/etc`, `/opt`, `/var/lib` | root `@` | Reverts to the selected snapshot |
| `/home` | `@home` | Keeps later changes |
| `/var/log` | `@log` | Keeps later changes |
| `/var/cache/pacman/pkg` | `@pkg` | Keeps later changes |
| `/boot` | separate ESP | Outside the root snapshot |

The test writes markers on all four Btrfs boundaries, boots the actual Limine
snapshot and restores it. Only the root marker and root database revert. Home,
log and package-cache markers retain their later values. The update/boot tooling
must still keep boot payloads compatible; root snapshots are not backups of the
ESP. Snapshots on the same filesystem do not protect a lost disk or VM image.
Host snapshot policy is separate: the development host remains disabled while
installed guest snapshots stay enabled.

## Tested application backup contract

The backup includes the application code/configuration/systemd unit, a consistent
SQLite snapshot, application documents, and a selected user-data directory. It
preserves ownership, modes, ACLs, xattrs, symlinks and hardlinks. SQLite uses its
online backup API while writes and a health probe continue; copying only a live
main database file is not the tested procedure. Use each real application's
consistent-backup procedure rather than assuming this fixture covers it.

A separate archive manifest records contents and metadata. The archive is encrypted
with AES-256 using GnuPG and signed by the verified maintainer key for this test.
The encryption key stays in a separate private directory and is never mounted in
the guest. The public evidence contains measurements and digests, not the archive,
key, credential media, raw SSH logs or console/password transcripts.

The test's backup lives outside the guest, on its VM host. That is enough to
recover an independently installed guest, but is **not an independent offsite
failure domain**. Before real use, copy backups to storage outside that host and
keep the decryption key separately, verify retrieval and exercise recovery there.
Losing the only decryption key makes an encrypted backup unusable. A signed hash
helps authenticate stored bytes; it cannot replace a working restore test.

## Fresh-machine recovery procedure exercised

1. Stop the source guest. The recovered machine must not require its disk or any
   of its backing files to operate.
2. Install a new standalone disk from the verified signed ISO. Keep the fresh
   machine ID and SSH host keys; establish administrator access independently.
3. Verify the backup's pinned signature and digest before decryption. Decrypt into
   private staging and require successful GnuPG authentication before delivery.
   The tests reject a modified ciphertext and a missing key.
4. Admit only the declared application paths. Refuse traversal, unexpected system
   identity files, duplicate entries, device nodes and escaping links. Validate
   the staged manifest, database integrity and service UID/GID mapping before
   applying files. Refuse an existing application rather than overwrite it.
5. Recreate the service account after checking for numeric-ID collisions. Restore
   metadata, then enable/start the service. Compare database row count and digest,
   exercise a new write, and cold-boot again to verify persistence and service
   startup. Recheck server boot/package/trust/snapshot services and fresh identity.

This is application/configuration/user-data recovery onto a fresh OS. It does not
restore a whole-disk image or copy `/etc/machine-id`, machine SSH keys, global
account databases, network configuration or the boot loader from the old machine.
The package manifest and release provenance describe the clean OS baseline.
Logs and package-cache markers are intentionally outside this application backup.

## Reproduce on disposable guests

Run only on the authorized development VM, with a stopped accepted baseline:

```sh
python3 test/server/operational.py test-runs/server/<accepted-default-run> \
  release/<signed-generation>/omarchy-slim-server-unofficial-YYYY.MM.DD-x86_64.iso \
  --seconds 300 --port 2462
```

The harness refuses another hostname/edition in its guest helper, keeps evidence
and disks private, uses offline QEMU networking, and reserves host free space
before starting guests. It preserves the baseline, previous releases and disks.
It installs no extra server packages. The fixture runs as an unprivileged service
with a writable application directory, not as root.

The final receipt binds the ISO/source revision, workload duration, request/write
counts, retry latency, crash recovery, database/file/metadata checks, snapshot
boundaries, ciphertext digest and measured restoration time. The complete window
starts when the source is stopped and includes installing the replacement OS;
restore-only time is recorded separately. Deliberately acknowledged writes after
the backup cut are absent after recovery: the test reports this recovery point,
not a claim of zero data loss.

Physical hardware, independent offsite storage, public IPv6, Secure Boot and
multiweek workload stability require separate deployment evidence.
