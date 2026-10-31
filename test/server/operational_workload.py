#!/usr/bin/env python3
"""Synthetic loopback-only SQLite/HTTP service for disposable server acceptance."""

import hashlib
from contextlib import closing
import http.server
import json
from pathlib import Path
import sqlite3

DB = Path("/var/lib/server-acceptance/database.sqlite3")


def state(path=DB):
    uri = "file:" + str(path) + "?mode=ro" + ("&immutable=1" if path != DB else "")
    with closing(sqlite3.connect(uri, uri=True)) as db:
        assert db.execute("pragma quick_check").fetchone()[0] == "ok"
        rows = db.execute(
            "select token, payload from records order by token"
        ).fetchall()
    return {
        "records": len(rows),
        "sha256": hashlib.sha256(
            json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest(),
    }


class Handler(http.server.BaseHTTPRequestHandler):
    def respond(self, code, obj):
        blob = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def do_GET(self):
        if self.path == "/health":
            self.respond(200, {"healthy": True})
        elif self.path == "/state":
            self.respond(200, state())
        elif self.path == "/blob":
            blob = (DB.parent / "documents/blob-0").read_bytes()[:16384]
            self.send_response(200)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)
        else:
            self.respond(404, {})

    def do_POST(self):
        if self.path != "/records":
            self.respond(404, {})
            return
        size = int(self.headers.get("Content-Length", "0"))
        if not 1 <= size <= 2048:
            self.respond(400, {})
            return
        obj = json.loads(self.rfile.read(size))
        token, payload = obj["token"], obj["payload"]
        if not isinstance(token, str) or not isinstance(payload, str):
            self.respond(400, {})
            return
        with closing(sqlite3.connect(DB, timeout=30)) as db, db:
            db.execute("pragma synchronous=FULL")
            db.execute("insert or ignore into records values (?, ?)", (token, payload))
            if (
                db.execute(
                    "select payload from records where token=?", (token,)
                ).fetchone()[0]
                != payload
            ):
                self.respond(409, {"conflict": True})
                return
        self.respond(200, {"token": token})

    def log_message(self, *_):
        pass


def main():
    config = json.loads(Path("/etc/server-acceptance.json").read_text())
    with closing(sqlite3.connect(DB)) as db, db:
        db.execute("pragma journal_mode=WAL")
        db.execute(
            "create table if not exists records(token text primary key, payload text not null)"
        )
    http.server.ThreadingHTTPServer(
        ("127.0.0.1", config["port"]), Handler
    ).serve_forever()


if __name__ == "__main__":
    main()
