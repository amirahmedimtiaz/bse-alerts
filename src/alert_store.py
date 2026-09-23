"""Transactional collection watermarks, deduplication and a durable email outbox."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertStore:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS companies (
                key TEXT PRIMARY KEY, last_success TEXT);
            CREATE TABLE IF NOT EXISTS announcements (
                company_key TEXT, news_id TEXT, status TEXT NOT NULL,
                payload TEXT, discovered_at TEXT NOT NULL,
                PRIMARY KEY(company_key, news_id));
            CREATE TABLE IF NOT EXISTS deliveries (
                message_id TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    def seed_legacy(self, legacy: dict[str, set[str]], *, as_of: str | None = None) -> None:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO metadata VALUES ('migration_date', ?)",
                            (as_of or datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),))
            for key, ids in legacy.items():
                self.db.execute("INSERT OR IGNORE INTO companies VALUES (?, NULL)", (key,))
                self.db.executemany(
                    "INSERT OR IGNORE INTO announcements VALUES (?, ?, 'baseline', NULL, ?)",
                    [(key, nid, utc_now()) for nid in ids],
                )

    def company(self, key: str):
        return self.db.execute("SELECT * FROM companies WHERE key=?", (key,)).fetchone()

    def migration_date(self) -> str | None:
        row = self.db.execute("SELECT value FROM metadata WHERE key='migration_date'").fetchone()
        return row[0] if row else None

    def collect(self, key: str, through: str, announcements: list[dict]) -> int:
        baseline = self.company(key) is None
        added = 0
        with self.db:
            for item in announcements:
                nid = item["_id"]
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO announcements VALUES (?, ?, ?, ?, ?)",
                    (key, nid, "baseline" if baseline else "pending",
                     None if baseline else json.dumps(item), utc_now()),
                )
                added += cur.rowcount if not baseline else 0
            self.db.execute(
                "INSERT INTO companies VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET last_success=excluded.last_success",
                (key, through),
            )
        return added

    def pending(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT company_key, news_id, payload FROM announcements WHERE status='pending' "
            "ORDER BY discovered_at, company_key, news_id LIMIT ?", (limit,)
        ).fetchall()
        return [{"company_key": row[0], "news_id": row[1], "announcement": json.loads(row[2])} for row in rows]

    def pending_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM announcements WHERE status='pending'").fetchone()[0]

    def sent_recently(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        return self.db.execute("SELECT COUNT(*) FROM deliveries WHERE sent_at>=?", (cutoff,)).fetchone()[0]

    def mark_sent(self, batch: list[dict], message_id: str) -> None:
        with self.db:
            self.db.executemany(
                "UPDATE announcements SET status='sent', payload=NULL WHERE company_key=? AND news_id=?",
                [(row["company_key"], row["news_id"]) for row in batch],
            )
            self.db.execute("INSERT OR IGNORE INTO deliveries VALUES (?, ?)", (message_id, utc_now()))
            cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            self.db.execute("DELETE FROM deliveries WHERE sent_at<?", (cutoff,))

    def health(self, value: dict) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES ('health', ?)", (json.dumps(value),))

    def export(self) -> dict:
        return {
            "schema_version": 1,
            **{table: [dict(row) for row in self.db.execute(f"SELECT * FROM {table} ORDER BY 1, 2")]
               for table in ("companies", "announcements", "deliveries", "metadata")},
        }

    def restore(self, snapshot: dict) -> None:
        if snapshot.get("schema_version") != 1:
            raise ValueError("Unsupported alert state schema")
        columns = {
            "companies": ("key", "last_success"),
            "announcements": ("company_key", "news_id", "status", "payload", "discovered_at"),
            "deliveries": ("message_id", "sent_at"),
            "metadata": ("key", "value"),
        }
        with self.db:
            for table, names in columns.items():
                self.db.execute(f"DELETE FROM {table}")
                for row in snapshot[table]:
                    self.db.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in names)})",
                                    tuple(row[name] for name in names))


class GitState:
    """A separate state branch, with ordinary fast-forward pushes (never forced).

    Workflow concurrency supplies the single-writer lock. A concurrent external
    writer causes a safe failure; callers must stop delivery if checkpointing fails.
    """
    def __init__(self, directory: str | Path, remote: str):
        import subprocess
        self.path = Path(directory)
        self.path.mkdir(parents=True, exist_ok=True)
        self._subprocess = subprocess
        self.git("init", "--initial-branch=alert-state")
        self.git("remote", "add", "origin", remote)
        self.git("config", "user.name", "github-actions[bot]")
        self.git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
        exists = self.git("ls-remote", "--heads", "origin", "refs/heads/alert-state")
        if exists.strip():
            self.git("fetch", "--depth=1", "origin", "alert-state")
            self.git("checkout", "-B", "alert-state", "FETCH_HEAD")

    def git(self, *args: str) -> str:
        import base64
        import os
        env = os.environ.copy()
        if env.get("GITHUB_TOKEN"):
            auth = base64.b64encode(f"x-access-token:{env['GITHUB_TOKEN']}".encode()).decode()
            env.update(GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                       GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {auth}")
        result = self._subprocess.run(["git", *args], cwd=self.path, check=True,
                                      text=True, capture_output=True, timeout=90, env=env)
        return result.stdout

    def restore(self, store: AlertStore) -> bool:
        snapshot = self.path / "alerts.json"
        if not snapshot.exists():
            return False
        store.restore(json.loads(snapshot.read_text()))
        return True

    def save(self, store: AlertStore) -> None:
        import time
        snapshot = self.path / "alerts.json"
        content = json.dumps(store.export(), indent=2, sort_keys=True) + "\n"
        if snapshot.exists() and snapshot.read_text() == content:
            return
        temporary = self.path / "alerts.json.tmp"
        temporary.write_text(content)
        temporary.replace(snapshot)
        self.git("add", "alerts.json")
        self.git("commit", "-m", "Checkpoint alert outbox and collection progress")
        for attempt in range(3):
            try:
                self.git("push", "origin", "HEAD:refs/heads/alert-state")
                return
            except self._subprocess.SubprocessError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
