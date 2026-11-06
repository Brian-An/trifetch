from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path

from linda_worker.models import Record, timestamp


def atomic_json(path: Path, content: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(content, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Container-generated exports remain readable by the host's reviewing user.
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Store:
    """Own checkpoints only; this connection never opens the portal's database."""

    def __init__(self, directory: Path, identity: str) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(directory / "checkpoint.sqlite3")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS records (fax_id TEXT PRIMARY KEY, payload TEXT NOT NULL);"
        )
        previous = self.connection.execute(
            "SELECT value FROM metadata WHERE key='identity'"
        ).fetchone()
        if previous and previous[0] != identity:
            self.connection.close()
            raise ValueError(
                "State directory belongs to a different portal or username. "
                "Choose a separate --state-dir."
            )
        version = self.connection.execute(
            "SELECT value FROM metadata WHERE key='schema'"
        ).fetchone()
        if version and version[0] != "1":
            self.connection.close()
            raise ValueError("Unsupported checkpoint schema; choose a new --state-dir.")
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('identity', ?)", (identity,)
            )
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('schema', '1')")

    def close(self) -> None:
        self.connection.close()

    def get(self, fax_id: str) -> Record | None:
        row = self.connection.execute(
            "SELECT payload FROM records WHERE fax_id=?", (fax_id,)
        ).fetchone()
        return Record.model_validate_json(row[0]) if row else None

    def all_records(self) -> list[Record]:
        rows = self.connection.execute("SELECT payload FROM records ORDER BY fax_id").fetchall()
        return [Record.model_validate_json(row[0]) for row in rows]

    def save(self, record: Record) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO records VALUES (?, ?) "
                "ON CONFLICT(fax_id) DO UPDATE SET payload=excluded.payload",
                (record.fax.fax_id, record.model_dump_json()),
            )

    def export(self, records: list[Record], status: str, *, error: str | None = None) -> Path:
        path = self.directory / "results.json"
        atomic_json(
            path,
            {
                "schema_version": 1,
                "generated_at": timestamp(),
                "run_status": status,
                "run_error": error,
                "summary": dict(Counter(record.action or "pending" for record in records)),
                "faxes": [{"fax_id": r.fax.fax_id, **r.model_dump(mode="json")} for r in records],
            },
        )
        return path
