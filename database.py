"""SQLite database – singleton, auto-created on first launch.

Tables
------
saved_networks   (network_id TEXT PK, name TEXT)
settings         (key TEXT PK, value TEXT)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

DATA_DIR = Path.home() / ".local" / "share" / "zerotier-gui"
DB_PATH = DATA_DIR / "zerotier-gui.db"

# Legacy JSON path (one-time migration)
_LEGACY_JSON = Path.home() / ".config" / "zerotier-gui" / "saved_networks.json"


class Database:
    _instance: Optional[Database] = None

    def __init__(self):
        self._connection: Optional[sqlite3.Connection] = None

    @classmethod
    def instance(cls) -> Database:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --- Connection ---

    def connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(DB_PATH))
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._migrate_legacy_json()
        return self._connection

    def close(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None

    # --- Schema ---

    def _migrate(self) -> None:
        c = self._connection
        c.execute(
            """CREATE TABLE IF NOT EXISTS saved_networks (
                   network_id TEXT PRIMARY KEY,
                   name       TEXT NOT NULL DEFAULT ''
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS settings (
                   key   TEXT PRIMARY KEY,
                   value TEXT NOT NULL
               )"""
        )
        c.commit()

    def _migrate_legacy_json(self) -> None:
        """One-time import from old saved_networks.json."""
        if not _LEGACY_JSON.exists():
            return
        # Only migrate if table is empty
        row = self._connection.execute(
            "SELECT COUNT(*) FROM saved_networks"
        ).fetchone()
        if row[0] > 0:
            return
        try:
            data = json.loads(_LEGACY_JSON.read_text(encoding="utf-8"))
            for nid, name in data.items():
                self._connection.execute(
                    "INSERT OR IGNORE INTO saved_networks (network_id, name) VALUES (?, ?)",
                    (nid, name),
                )
            self._connection.commit()
        except (json.JSONDecodeError, OSError):
            pass

    # --- Saved networks ---

    def get_saved_networks(self) -> dict[str, str]:
        rows = self._connection.execute(
            "SELECT network_id, name FROM saved_networks"
        ).fetchall()
        return {r["network_id"]: r["name"] for r in rows}

    def save_network(self, network_id: str, name: str = "") -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO saved_networks (network_id, name) VALUES (?, ?)",
            (network_id, name),
        )
        self._connection.commit()

    def remove_network(self, network_id: str) -> None:
        self._connection.execute(
            "DELETE FROM saved_networks WHERE network_id = ?",
            (network_id,),
        )
        self._connection.commit()

    def update_network_name(self, network_id: str, name: str) -> None:
        self._connection.execute(
            "UPDATE saved_networks SET name = ? WHERE network_id = ?",
            (name, network_id),
        )
        self._connection.commit()

    # --- Settings ---

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        self._connection.commit()

    def get_all_settings(self) -> dict[str, str]:
        rows = self._connection.execute(
            "SELECT key, value FROM settings"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
