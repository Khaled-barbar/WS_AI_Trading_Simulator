"""أسعار يومية معدلة (تشمل التوزيعات) + السعر الفعلي + العملة.
المصدر: Yahoo chart API، أو استيراد CSV يدوي. كل التحويل إلى CAD يتم هنا."""
import bisect
import csv
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

UA = {"user-agent": "Mozilla/5.0"}
TICKER_RE = re.compile(r"^[A-Z0-9^][A-Z0-9.\-=^]{0,19}$")
# عملات Yahoo بوحدات فرعية: GBp = بنس (1/100 جنيه)
SUBUNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01), "ILA": ("ILS", 0.01)}
CAD_SUFFIXES = (".TO", ".V", ".NE", ".CN")


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _download(ticker, start, end):
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()) - 7 * 86400
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div%7Csplit")
    res = _get(url)["chart"]["result"][0]
    ts = res.get("timestamp") or []
    ind = res["indicators"]
    raw = ind["quote"][0].get("close") or [None] * len(ts)
    adj = (ind.get("adjclose") or [{}])[0].get("adjclose") or raw
    meta = res.get("meta", {})
    offset = int(meta.get("gmtoffset") or 0)  # exchange local time -> correct trading date
    session = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    rows = {}
    for t, c, rc in zip(ts, adj, raw):
        if c is None:
            continue
        # A bar of today's session before the close is an intraday price, not a close:
        # never store it (the final close is stored by the next run after the market closes)
        if session and t >= session["start"] - 6 * 3600 and time.time() < session["end"]:
            continue
        d = datetime.fromtimestamp(t + offset, tz=timezone.utc).date().isoformat()
        rows[d] = (c, rc if rc is not None else c)
    return meta, rows


def _save_meta(conn, ticker, meta):
    conn.execute(
        "INSERT INTO securities (ticker, name, currency, exchange, type, updated) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(ticker) DO UPDATE SET name=COALESCE(excluded.name, name), "
        "currency=COALESCE(excluded.currency, currency), exchange=COALESCE(excluded.exchange, exchange), "
        "type=COALESCE(excluded.type, type), updated=excluded.updated",
        (ticker, meta.get("longName") or meta.get("shortName"), meta.get("currency"),
         meta.get("fullExchangeName") or meta.get("exchangeName"), meta.get("instrumentType"),
         date.today().isoformat()))


def fetch_yahoo(conn, ticker: str, start: str, end: str, incremental: bool = False) -> int:
    """يحفظ الأسعار بين start و end. incremental=True: يُنزّل آخر أيام فقط إن كان التاريخ
    الأقدم محفوظاً، ويعيد التنزيل كاملاً إذا تغيّر السعر المعدّل القديم (توزيع/تقسيم جديد يعيد
    حساب كل التاريخ — خلط المقياسين يُسقط التوزيع من العائد)."""
    if incremental:
        r = conn.execute("SELECT MIN(date), MAX(date) FROM prices WHERE ticker=?", (ticker,)).fetchone()
        if r[0] and r[0] <= (date.fromisoformat(start) + timedelta(days=10)).isoformat():
            s2 = (date.fromisoformat(r[1]) - timedelta(days=12)).isoformat()
            meta, rows = _download(ticker, s2, end)
            old = dict(conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date>=?",
                                    (ticker, s2)).fetchall())
            same = [d for d in rows if d in old]
            if same and all(abs(rows[d][0] / old[d] - 1) < 1e-4 for d in same):
                return _store(conn, ticker, meta, rows)
    meta, rows = _download(ticker, start, end)
    return _store(conn, ticker, meta, rows)


def _store(conn, ticker, meta, rows):
    conn.executemany("INSERT OR REPLACE INTO prices (ticker, date, close, source, raw_close) "
                     "VALUES (?,?,?,?,?)", [(ticker, d, a, "yahoo", r) for d, (a, r) in rows.items()])
    _save_meta(conn, ticker, meta)
    conn.commit()
    time.sleep(0.3)
    return len(rows)


def import_csv(conn, path: str) -> int:
    """CSV بأعمدة: ticker,date,close (اختياري: raw_close)"""
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            c = float(row["close"])
            conn.execute("INSERT OR REPLACE INTO prices (ticker, date, close, source, raw_close) "
                         "VALUES (?,?,?,?,?)",
                         (row["ticker"], row["date"], c, "csv", float(row.get("raw_close") or c)))
            n += 1
    conn.commit()
    return n


def search(q: str, conn=None, limit=10):
    """بحث عن شركات وصناديق (Yahoo)، مع القائمة المحلية إن فشل الاتصال."""
    q = q.strip()
    out, seen = [], set()
    if not q:
        return out
    try:
        url = ("https://query2.finance.yahoo.com/v1/finance/search?" + urllib.parse.urlencode(
            {"q": q, "quotesCount": limit, "newsCount": 0, "listsCount": 0}))
        for x in _get(url, 15).get("quotes", []):
            if x.get("quoteType") not in ("EQUITY", "ETF", "MUTUALFUND", "CRYPTOCURRENCY"):
                continue
            s = x.get("symbol")
            if s and s not in seen:
                seen.add(s)
                out.append({"ticker": s, "name": x.get("longname") or x.get("shortname") or "",
                            "exchange": x.get("exchDisp") or x.get("exchange") or "",
                            "type": x.get("quoteType"), "currency": None})
    except Exception:
        pass
    if conn is not None:
        like = f"%{q.upper()}%"
        for r in conn.execute("SELECT * FROM securities WHERE (upper(ticker) LIKE ? OR upper(name) LIKE ?) "
                              "AND COALESCE(type,'')!='CURRENCY' LIMIT ?", (like, like, limit)):
            if r["ticker"] not in seen:
                seen.add(r["ticker"])
                out.append({"ticker": r["ticker"], "name": r["name"] or "", "exchange": r["exchange"] or "",
                            "type": r["type"], "currency": r["currency"]})
    return out[:limit + 5]


# ------------------------------------------------------------------ lookups
def price_on_or_after(conn, ticker, d):
    r = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date>=? "
                     "ORDER BY date LIMIT 1", (ticker, d)).fetchone()
    return (r["date"], r["close"]) if r else None


def price_on_or_before(conn, ticker, d):
    r = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date<=? "
                     "ORDER BY date DESC LIMIT 1", (ticker, d)).fetchone()
    return (r["date"], r["close"]) if r else None


def total_return(conn, ticker, start, end, final=False):
    """عائد كلي بعملة الورقة. None إذا كانت البيانات ناقصة.
    final=True (للحسم): يلزم وجود إغلاق في تاريخ الحسم أو بعده، أي أن إغلاق تاريخ الحسم
    (أو آخر يوم تداول قبله إن كان عطلة) معروف نهائياً وليس سعراً أقدم."""
    if ticker == "CASH":
        return 0.0
    a = price_on_or_after(conn, ticker, start)
    b = price_on_or_before(conn, ticker, end)
    if not a or not b or b[0] <= a[0]:
        return None
    if final and not price_on_or_after(conn, ticker, end):
        return None
    # نرفض الحسم إذا كان آخر سعر متاح أقدم من تاريخ الحسم بأكثر من 5 أيام
    if (date.fromisoformat(end) - date.fromisoformat(b[0])) > timedelta(days=5):
        return None
    return b[1] / a[1] - 1


def currency_of(conn, ticker):
    """(عملة أساسية، معامل) — مثلاً GBp -> ('GBP', 0.01)."""
    if ticker == "CASH":
        return "CAD", 1.0
    r = conn.execute("SELECT currency FROM securities WHERE ticker=?", (ticker,)).fetchone()
    cur = r["currency"] if r and r["currency"] else ("CAD" if ticker.endswith(CAD_SUFFIXES) else "USD")
    return SUBUNITS.get(cur, (cur.upper(), 1.0))


def fx_ticker(cur):
    return None if cur == "CAD" else f"{cur}CAD=X"


class Book:
    """أسعار في الذاكرة مع ملء الأيام الناقصة بآخر سعر معروف (عطل البورصات المختلفة)."""

    def __init__(self, conn):
        self.conn = conn
        self._s = {}
        self._cur = {}

    def _series(self, ticker):
        if ticker not in self._s:
            rows = self.conn.execute("SELECT date, close, COALESCE(raw_close, close) FROM prices "
                                     "WHERE ticker=? ORDER BY date", (ticker,)).fetchall()
            self._s[ticker] = ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])
        return self._s[ticker]

    def first_date(self, ticker):
        if ticker == "CASH":
            return "1900-01-01"
        d = self._series(ticker)[0]
        return d[0] if d else None

    def last_date(self, ticker):
        if ticker == "CASH":
            return None
        d = self._series(ticker)[0]
        return d[-1] if d else None

    def _at(self, ticker, d, col):
        ds, adj, raw = self._series(ticker)
        i = bisect.bisect_right(ds, d) - 1
        if i < 0:
            return None
        return (adj if col == 1 else raw)[i]

    def adj(self, ticker, d):
        return 1.0 if ticker == "CASH" else self._at(ticker, d, 1)

    def raw(self, ticker, d):
        return 1.0 if ticker == "CASH" else self._at(ticker, d, 2)

    def currency(self, ticker):
        if ticker not in self._cur:
            self._cur[ticker] = currency_of(self.conn, ticker)
        return self._cur[ticker]

    def fx(self, ticker, d):
        """CAD لكل وحدة سعرية من الورقة في تاريخ d."""
        cur, mult = self.currency(ticker)
        if cur == "CAD":
            return mult
        r = self._at(fx_ticker(cur), d, 2)
        if r is None:  # قبل أول سعر صرف محفوظ: أقدم سعر معروف
            ds, _, raw = self._series(fx_ticker(cur))
            r = raw[0] if raw else None
        return None if r is None else r * mult

    def is_foreign(self, ticker):
        return self.currency(ticker)[0] != "CAD"

    def cad_adj(self, ticker, d):
        a, f = self.adj(ticker, d), self.fx(ticker, d)
        return None if a is None or f is None else a * f


def business_days(start, end):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    while d <= e:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def fx_needed(conn, tickers):
    s = set()
    for t in tickers:
        f = fx_ticker(currency_of(conn, t)[0])
        if f:
            s.add(f)
    return sorted(s)
