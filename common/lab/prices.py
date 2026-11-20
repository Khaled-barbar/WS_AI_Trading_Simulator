"""أسعار يومية معدلة (تشمل التوزيعات). المصدر: Yahoo chart API، أو استيراد CSV يدوي."""
import csv
import json
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone


def fetch_yahoo(conn, ticker: str, start: str, end: str) -> int:
    p1 = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp()) - 7 * 86400
    p2 = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
           f"?period1={p1}&period2={p2}&interval=1d&events=div")
    req = urllib.request.Request(url, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.loads(r.read())["chart"]["result"][0]
    ts = res.get("timestamp") or []
    ind = res["indicators"]
    closes = (ind.get("adjclose") or [{}])[0].get("adjclose") or ind["quote"][0]["close"]
    n = 0
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, tz=timezone.utc).date().isoformat()
        conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?)", (ticker, d, c, "yahoo"))
        n += 1
    conn.commit()
    time.sleep(0.5)
    return n


def import_csv(conn, path: str) -> int:
    """CSV بأعمدة: ticker,date,close"""
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            conn.execute("INSERT OR REPLACE INTO prices VALUES (?,?,?,?)",
                         (row["ticker"], row["date"], float(row["close"]), "csv"))
            n += 1
    conn.commit()
    return n


def price_on_or_after(conn, ticker, d):
    r = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date>=? "
                     "ORDER BY date LIMIT 1", (ticker, d)).fetchone()
    return (r["date"], r["close"]) if r else None


def price_on_or_before(conn, ticker, d):
    r = conn.execute("SELECT date, close FROM prices WHERE ticker=? AND date<=? "
                     "ORDER BY date DESC LIMIT 1", (ticker, d)).fetchone()
    return (r["date"], r["close"]) if r else None


def total_return(conn, ticker, start, end):
    """None إذا كانت البيانات ناقصة."""
    if ticker == "CASH":
        return 0.0
    a = price_on_or_after(conn, ticker, start)
    b = price_on_or_before(conn, ticker, end)
    if not a or not b or b[0] <= a[0]:
        return None
    # نرفض الحسم إذا كان آخر سعر متاح أقدم من تاريخ الحسم بأكثر من 5 أيام
    if (date.fromisoformat(end) - date.fromisoformat(b[0])) > timedelta(days=5):
        return None
    return b[1] / a[1] - 1
