import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cols(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn):
    """تعديلات على جداول قديمة (CREATE TABLE IF NOT EXISTS لا يضيف أعمدة)."""
    if "track" not in _cols(conn, "sessions"):
        # etf = الجلسة الأصلية (صناديق)، stocks = اختيارات الشركات
        conn.execute("ALTER TABLE sessions ADD COLUMN track TEXT NOT NULL DEFAULT 'etf'")
    if "raw_close" not in _cols(conn, "prices"):
        # سعر الإغلاق الفعلي (غير معدّل) — لتكلفة الشراء في المحافظ
        conn.execute("ALTER TABLE prices ADD COLUMN raw_close REAL")
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    if not path.is_absolute():
        path = ROOT / path
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
    _migrate(conn)
    from . import investors
    investors.seed(conn, str(path))
    return conn
