-- Agents Lab: سجل قرارات وتوقعات الوكلاء
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id            INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL,           -- ISO date (UTC)
    question      TEXT NOT NULL,
    context       TEXT,                    -- معلومات السوق/الحساب المقدمة لكل الوكلاء بالتساوي
    universe      TEXT NOT NULL            -- JSON list of allowed tickers
);

CREATE TABLE IF NOT EXISTS responses (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES sessions(id),
    agent         TEXT NOT NULL,           -- claude / chatgpt / gemini / consensus
    model         TEXT,
    round         INTEGER NOT NULL,        -- 1 = مستقل، 2 = بعد رؤية آراء الآخرين
    raw_text      TEXT NOT NULL,
    analysis      TEXT,
    risks         TEXT,                    -- "ما الذي سيثبت خطئي"
    created_at    TEXT NOT NULL,
    UNIQUE(session_id, agent, round)
);

-- محفظة ورقية: أوزان مقترحة + سعر الدخول
CREATE TABLE IF NOT EXISTS allocations (
    id            INTEGER PRIMARY KEY,
    response_id   INTEGER NOT NULL REFERENCES responses(id),
    ticker        TEXT NOT NULL,
    weight        REAL NOT NULL            -- 0..1، المجموع = 1
);

-- توقعات احتمالية قابلة للحسم آلياً (هذا ما يُقيَّم فعلاً)
CREATE TABLE IF NOT EXISTS forecasts (
    id            INTEGER PRIMARY KEY,
    response_id   INTEGER NOT NULL REFERENCES responses(id),
    kind          TEXT NOT NULL CHECK (kind IN ('outperform','return_above')),
    ticker_a      TEXT NOT NULL,
    ticker_b      TEXT,                    -- لـ outperform فقط
    threshold     REAL,                    -- لـ return_above فقط (مثلاً 0.02 = 2%)
    start_date    TEXT NOT NULL,
    resolve_date  TEXT NOT NULL,
    probability   REAL NOT NULL CHECK (probability >= 0 AND probability <= 1),
    rationale     TEXT,
    outcome       INTEGER,                 -- NULL = لم يُحسم، 1 = صحيح، 0 = خطأ
    brier         REAL,
    resolved_at   TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,
    close         REAL NOT NULL,           -- يُفضَّل السعر المعدل (يشمل التوزيعات)
    source        TEXT,
    PRIMARY KEY (ticker, date)
);
