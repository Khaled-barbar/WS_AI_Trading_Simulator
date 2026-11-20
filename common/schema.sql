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

-- ===== 1.4.0 =====
-- بيانات الأوراق المالية (من Yahoo): العملة والاسم والنوع
CREATE TABLE IF NOT EXISTS securities (
    ticker        TEXT PRIMARY KEY,
    name          TEXT,
    currency      TEXT,                    -- كما يرسلها Yahoo (GBp = بنس)
    exchange      TEXT,
    type          TEXT,                    -- EQUITY / ETF / MUTUALFUND / CRYPTOCURRENCY / CURRENCY
    updated       TEXT
);

-- المستثمرون الذين ينشرون محافظهم (من Investor-Watchlist)
CREATE TABLE IF NOT EXISTS investors (
    id            TEXT PRIMARY KEY,
    list          TEXT NOT NULL,           -- stocks / etf
    rank          INTEGER,
    name          TEXT NOT NULL,
    kind          TEXT,
    style         TEXT,
    since         TEXT,
    url           TEXT,
    five_y        REAL,
    record        TEXT,
    reported      TEXT,                    -- JSON {year: return} كما نشرها المصدر
    caveat        TEXT,
    verdict       TEXT,
    score         INTEGER,
    proxy         TEXT,                    -- ورقة مدرجة تمثل السجل الفعلي (BRK-B, PRPFX...)
    benchmarks    TEXT,                    -- JSON list
    follow_date   TEXT,                    -- تاريخ بدء المتابعة (يعدّله المستخدم)
    active        INTEGER NOT NULL DEFAULT 1,
    notes         TEXT
);

-- لقطات المحفظة المعلنة: كل لقطة تبدأ من تاريخها حتى اللقطة التالية
CREATE TABLE IF NOT EXISTS investor_holdings (
    investor_id   TEXT NOT NULL REFERENCES investors(id),
    as_of         TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    weight        REAL NOT NULL,
    estimated     INTEGER NOT NULL DEFAULT 0,
    source        TEXT,
    PRIMARY KEY (investor_id, as_of, ticker)
);

-- محافظ المستخدم الخاصة
CREATE TABLE IF NOT EXISTS portfolios (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    benchmark     TEXT NOT NULL DEFAULT 'XEQT.TO',
    fx_fee        REAL NOT NULL DEFAULT 0.015,
    notes         TEXT,
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY,
    portfolio_id  INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    date          TEXT NOT NULL,
    type          TEXT NOT NULL CHECK (type IN ('buy','sell','deposit','withdraw')),
    ticker        TEXT,
    quantity      REAL,
    price         REAL,                    -- بعملة الورقة
    fee           REAL NOT NULL DEFAULT 0, -- CAD
    amount        REAL,                    -- CAD (للإيداع والسحب)
    note          TEXT,
    created_at    TEXT
);
