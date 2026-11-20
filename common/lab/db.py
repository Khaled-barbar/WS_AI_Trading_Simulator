import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_absolute():
        path = ROOT / path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    return conn
