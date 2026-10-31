#!/usr/bin/env python3
"""Guest workload, consistent application backup and guarded fresh-host restore."""

import argparse
from contextlib import closing
import base64
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import pwd
import grp
import shutil
import socket
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request

from operational_workload import state, DB

SCOPES = (
    "opt/server-acceptance",
    "etc/server-acceptance.json",
    "etc/server-acceptance-marker",
    "etc/systemd/system/server-acceptance.service",
    "var/lib/server-acceptance",
    "home/admin/server-acceptance",
)
DIR_SCOPES = (
    "opt/server-acceptance",
    "var/lib/server-acceptance",
    "home/admin/server-acceptance",
)
USER = "server-acceptance"
UNIT = "server-acceptance.service"
BOUNDARIES = {
    "root": "/etc/server-acceptance-marker",
    "home": "/home/admin/server-acceptance/marker",
    "log": "/var/log/server-acceptance-marker",
    "cache": "/var/cache/pacman/pkg/server-acceptance-marker",
}


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def request(path, obj=None):
    data = None if obj is None else json.dumps(obj).encode()
    with urllib.request.urlopen(
        "http://127.0.0.1:9080" + path, data=data, timeout=3
    ) as response:
        blob = response.read()
    return blob if path == "/blob" else json.loads(blob)


def ready():
    for _ in range(100):
        try:
            if request("/health")["healthy"]:
                return
        except OSError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Workload did not become healthy")


def pid():
    return int(run("systemctl", "show", "--property=MainPID", "--value", UNIT))


def process():
    value = pid()
    rss = next(
        x.split()[1]
        for x in Path(f"/proc/{value}/status").read_text().splitlines()
        if x.startswith("VmRSS:")
    )
    return {
        "pid": value,
        "fds": len(list(Path(f"/proc/{value}/fd").iterdir())),
        "rss_kib": int(rss),
    }


def manifest(root):
    nodes, inodes = {}, {}
    for scope in SCOPES:
        base = root / scope
        paths = [base, *sorted(base.rglob("*"))] if base.is_dir() else [base]
        for path in paths:
            info = path.lstat()
            name = str(path.relative_to(root))
            entry = {
                "mode": stat.S_IMODE(info.st_mode),
                "uid": info.st_uid,
                "gid": info.st_gid,
            }
            entry["xattrs"] = {
                k: base64.b64encode(
                    os.getxattr(path, k, follow_symlinks=False)
                ).decode()
                for k in os.listxattr(path, follow_symlinks=False)
            }
            if path.is_symlink():
                entry.update(kind="symlink", target=os.readlink(path))
            elif path.is_dir():
                entry["kind"] = "directory"
            else:
                entry.update(
                    kind="file",
                    size=info.st_size,
                    sha256=hashlib.file_digest(path.open("rb"), "sha256").hexdigest(),
                )
                inodes.setdefault((info.st_dev, info.st_ino), []).append(name)
            nodes[name] = entry
    return {
        "nodes": nodes,
        "hardlinks": sorted(sorted(v) for v in inodes.values() if len(v) > 1),
    }


def allowed(name):
    path = PurePosixPath(name)
    return (
        not path.is_absolute()
        and ".." not in path.parts
        and (name in SCOPES or any(name.startswith(s + "/") for s in DIR_SCOPES))
    )


def validate_tar(path):
    seen = set()
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/")
            if name in seen:
                raise ValueError("Duplicate archive member")
            seen.add(name)
            if name == "MANIFEST.json":
                if not member.isfile():
                    raise ValueError("Manifest must be a file")
                continue
            if not allowed(name) or not (
                member.isfile() or member.isdir() or member.issym() or member.islnk()
            ):
                raise ValueError("Archive path or type is outside the recovery scope")
            if member.issym():
                target = posixpath.normpath(
                    posixpath.join(posixpath.dirname(name), member.linkname)
                )
                if PurePosixPath(member.linkname).is_absolute() or not allowed(target):
                    raise ValueError("Escaping symlink")
            if member.islnk() and not allowed(member.linkname):
                raise ValueError("Escaping hardlink")
    if "MANIFEST.json" not in seen:
        raise ValueError("Missing recovery manifest")


def install(args):
    if any((Path("/") / s).exists() for s in SCOPES):
        raise ValueError("Fixture already exists")
    run(
        "useradd",
        "--system",
        "--user-group",
        "--home-dir",
        "/var/lib/server-acceptance",
        "--shell",
        "/usr/bin/nologin",
        USER,
    )
    user = pwd.getpwnam(USER)
    root = Path("/var/lib/server-acceptance")
    (root / "documents").mkdir(parents=True)
    for index in range(16):
        (root / "documents" / f"blob-{index}").write_bytes(os.urandom(1024**2))
    note = root / "documents/note.txt"
    note.write_text("Persistent service data — שלום — português\n")
    note.chmod(0o640)
    os.setxattr(note, "user.omarchy-acceptance", b"preserve-this-xattr")
    (root / "documents/latest").symlink_to("note.txt")
    run("chown", "-R", USER + ":" + grp.getgrgid(user.pw_gid).gr_name, str(root))
    root.chmod(0o750)
    run("setfacl", "-m", "u:admin:r", str(note))
    home = Path("/home/admin/server-acceptance")
    home.mkdir(mode=0o700)
    (home / "notes.txt").write_text("User data outside root snapshots\n")
    (home / "notes.txt").chmod(0o640)
    os.link(home / "notes.txt", home / "notes-hardlink.txt")
    for name in BOUNDARIES.values():
        Path(name).write_text("before-snapshot\n")
    run("chown", "-R", "admin:admin", str(home))
    app = Path("/opt/server-acceptance")
    app.mkdir()
    shutil.copy2(Path(__file__).with_name("operational_workload.py"), app / "app.py")
    config = Path("/etc/server-acceptance.json")
    config.write_text(json.dumps({"port": 9080, "fixture_only": True}) + "\n")
    config.chmod(0o640)
    os.chown(config, 0, user.pw_gid)
    Path("/etc/systemd/system/" + UNIT).write_text("""[Unit]
Description=Disposable operational acceptance workload
After=network.target
[Service]
User=server-acceptance
Group=server-acceptance
ExecStart=/usr/bin/python3 /opt/server-acceptance/app.py
Restart=on-failure
RestartSec=1
Environment=PYTHONDONTWRITEBYTECODE=1
UMask=0027
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/server-acceptance
[Install]
WantedBy=multi-user.target
""")
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", UNIT)
    ready()
    return {"healthy": True, "state": request("/state"), "process": process()}


def soak(args):
    start = time.monotonic()
    end = start + args.seconds
    before = process()
    latencies = []
    acknowledged = []
    errors = []
    lock = threading.Lock()
    samples = []

    def client(index):
        seq = 0
        while time.monotonic() < end:
            path = (
                "/records" if seq % 4 == 0 else "/blob" if seq % 4 == 1 else "/health"
            )
            token = f"worker-{index}-{seq}"
            obj = (
                {"token": token, "payload": "committed:" + token}
                if path == "/records"
                else None
            )
            began = time.monotonic()
            for attempt in range(60):
                try:
                    result = request(path, obj)
                    if obj:
                        assert result["token"] == token
                    with lock:
                        latencies.append(time.monotonic() - began)
                        if obj:
                            acknowledged.append(token)
                    break
                except OSError as error:
                    with lock:
                        errors.append(type(error).__name__)
                    time.sleep(0.1)
            else:
                raise RuntimeError("A request did not recover after service crash")
            seq += 1
            time.sleep(0.1)

    killed = False
    recovered = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client, i) for i in range(4)]
        next_sample = start
        while time.monotonic() < end:
            now = time.monotonic()
            if not killed and now - start >= min(60, args.seconds / 3):
                old = pid()
                run("systemctl", "kill", "--kill-whom=main", "--signal=SIGKILL", UNIT)
                killed = True
                ready()
                assert pid() != old
                recovered = time.monotonic() - now
            if now >= next_sample:
                samples.append(process())
                next_sample = now + 15
            time.sleep(0.2)
        for future in futures:
            future.result()
    after = process()
    actual = state()
    assert actual["records"] == len(set(acknowledged)) == len(acknowledged)
    assert killed and recovered is not None
    assert max(x["fds"] for x in samples) <= before["fds"] + 16
    ordered = sorted(latencies)
    return {
        "elapsed_seconds": time.monotonic() - start,
        "requests": len(latencies),
        "acknowledged_writes": len(acknowledged),
        "retry_errors": len(errors),
        "p95_ms": ordered[int(0.95 * (len(ordered) - 1))] * 1000,
        "p99_ms": ordered[int(0.99 * (len(ordered) - 1))] * 1000,
        "process_before": before,
        "process_after": after,
        "process_samples": samples,
        "crash_recovery_seconds": recovered,
        "database": actual,
    }


def backup(args):
    ready()
    account = pwd.getpwnam(USER)
    admin = pwd.getpwnam("admin")
    staging = Path(tempfile.mkdtemp(prefix="server-app-backup-", dir="/run"))
    stop = threading.Event()
    started = threading.Event()
    writes = []

    writer_errors = []

    def writer_body():
        i = 0
        while not stop.is_set():
            token = "online-backup-" + str(i)
            request("/records", {"token": token, "payload": "backup-write:" + token})
            writes.append(token)
            started.set()
            i += 1
            time.sleep(0.01)

    def writer():
        try:
            writer_body()
        except BaseException as error:
            writer_errors.append(repr(error))
            started.set()

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert started.wait(10)
        run("cp", "-a", "--parents", *SCOPES, str(staging))
        for p in (staging / "var/lib/server-acceptance").glob("database.sqlite3*"):
            p.unlink()
        destination = staging / str(DB).lstrip("/")
        with closing(sqlite3.connect(DB)) as source, closing(
            sqlite3.connect(destination)
        ) as target:
            source.backup(target, pages=16, progress=lambda *_: time.sleep(0.005))
        os.chown(destination, account.pw_uid, account.pw_gid)
        destination.chmod(0o640)
        frozen = state(destination)
        probe = request("/health")
    finally:
        stop.set()
        thread.join(timeout=10)
    assert writes and not thread.is_alive() and not writer_errors, writer_errors
    metadata = {
        "format": 1,
        "scope": list(SCOPES),
        "files": manifest(staging),
        "database": frozen,
        "service_account": {"name": USER, "uid": account.pw_uid, "gid": account.pw_gid},
        "admin_uid": admin.pw_uid,
        "online_writes": len(writes),
        "health_during_backup": probe,
    }
    (staging / "MANIFEST.json").write_text(json.dumps(metadata, indent=2) + "\n")
    archive = Path("/run/server-acceptance-backup.tar")
    if archive.exists():
        raise ValueError("Backup output already exists")
    run(
        "tar",
        "--format=pax",
        "--xattrs",
        "--xattrs-include=*",
        "--acls",
        "-C",
        str(staging),
        "-cf",
        str(archive),
        "MANIFEST.json",
        *SCOPES,
    )
    os.chown(archive, admin.pw_uid, admin.pw_gid)
    archive.chmod(0o600)
    shutil.rmtree(staging)
    return {
        "backup": str(archive),
        "database": frozen,
        "online_writes": len(writes),
        "source_after_backup": state(),
        "bytes": archive.stat().st_size,
    }


def snapshot(args):
    run("systemctl", "stop", UNIT)
    with closing(sqlite3.connect(DB)) as db:
        db.execute("pragma wal_checkpoint(TRUNCATE)")
    Path("/etc/rollback-proof").write_text("baseline")
    number = run(
        "snapper",
        "--no-dbus",
        "-c",
        "root",
        "create",
        "-c",
        "number",
        "-d",
        "operational-boundaries",
        "--print-number",
    )
    Path("/var/log/server-test-snapshot").write_text(number + "\n")
    Path("/etc/rollback-proof").write_text("changed")
    run("systemctl", "start", UNIT)
    ready()
    for name in BOUNDARIES.values():
        Path(name).write_text("after-snapshot\n")
    for i in range(25):
        request("/records", {"token": f"after-backup-{i}", "payload": "not-in-backup"})
    run("limine-snapper-sync")
    return {
        "snapshot": number,
        "database": state(),
        "boundaries": {k: Path(p).read_text().strip() for k, p in BOUNDARIES.items()},
    }


def boundaries(args):
    ready()
    values = {k: Path(p).read_text().strip() for k, p in BOUNDARIES.items()}
    assert values["root"] == "before-snapshot"
    assert all(values[k] == "after-snapshot" for k in ("home", "log", "cache"))
    return {"boundaries": values, "database": state(), "healthy": request("/health")}


def restore(args):
    archive = Path(args.archive)
    validate_tar(archive)
    for scope in SCOPES:
        dest = Path("/") / scope
        if dest.exists() or dest.is_symlink():
            raise ValueError(
                "Fresh target required; refusing existing application data"
            )
        for parent in dest.parents:
            if parent.is_symlink():
                raise ValueError("Target path has a symlink ancestor")
    staging = Path(tempfile.mkdtemp(prefix="server-app-restore-", dir="/run"))
    run(
        "tar",
        "--xattrs",
        "--xattrs-include=*",
        "--acls",
        "--numeric-owner",
        "--same-owner",
        "-C",
        str(staging),
        "-xf",
        str(archive),
    )
    metadata = json.loads((staging / "MANIFEST.json").read_text())
    if metadata["format"] != 1 or metadata["scope"] != list(SCOPES):
        raise ValueError("Unknown backup contract")
    assert (
        manifest(staging) == metadata["files"]
    ), "Staged file contents/ownership/modes/ACL/xattrs/links differ"
    assert state(staging / str(DB).lstrip("/")) == metadata["database"]
    account = metadata["service_account"]
    assert account["name"] == USER
    assert pwd.getpwnam("admin").pw_uid == metadata["admin_uid"]
    for ident, lookup in [
        (account["uid"], pwd.getpwuid),
        (account["gid"], grp.getgrgid),
    ]:
        try:
            lookup(ident)
        except KeyError:
            pass
        else:
            raise ValueError("Service UID/GID collision on fresh target")
    run("groupadd", "--gid", str(account["gid"]), USER)
    run(
        "useradd",
        "--system",
        "--uid",
        str(account["uid"]),
        "--gid",
        str(account["gid"]),
        "--home-dir",
        "/var/lib/server-acceptance",
        "--shell",
        "/usr/bin/nologin",
        USER,
    )
    for scope in SCOPES:
        dest = Path("/") / scope
        dest.parent.mkdir(parents=True, exist_ok=True)
        run("cp", "-a", "--", str(staging / scope), str(dest))
    assert manifest(Path("/")) == metadata["files"], "Restored metadata differs"
    run("systemctl", "daemon-reload")
    run("systemctl", "enable", "--now", UNIT)
    ready()
    actual = request("/state")
    assert actual == metadata["database"]
    shutil.rmtree(staging)
    return {
        "database": actual,
        "files_verified": len(metadata["files"]["nodes"]),
        "hardlink_groups": len(metadata["files"]["hardlinks"]),
        "ownership_modes_xattrs_acls_verified": True,
        "healthy": request("/health"),
    }


def check(args):
    ready()
    return {
        "database": state(),
        "healthy": request("/health"),
        "process": process(),
        "enabled": run("systemctl", "is-enabled", UNIT),
        "listeners": run("ss", "-H", "-lntp"),
    }


def post_restore(args):
    request(
        "/records", {"token": "destination-proof", "payload": "new-write-after-restore"}
    )
    return check(args)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "phase",
        choices=(
            "install",
            "soak",
            "backup",
            "snapshot",
            "boundaries",
            "restore",
            "check",
            "post_restore",
        ),
    )
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--archive")
    args = parser.parse_args()
    if (
        os.geteuid() != 0
        or socket.gethostname() != "slim-test"
        or Path("/etc/omarchy-edition").read_text().strip() != "server"
    ):
        raise ValueError("Only a disposable acceptance server may run this helper")
    result = globals()[args.phase](args)
    print(json.dumps({"phase": args.phase, "passed": True, **result}), flush=True)


if __name__ == "__main__":
    main()
