"""المستثمرون الذين ينشرون محافظهم + محاكي النسخ (copycat).

النسخ = شراء المراكز المعلنة بنفس أوزانها (بعد جعل مجموعها 100%) بمبلغ بالدولار الكندي،
وإعادة التوازن عند كل لقطة محفظة جديدة. رسوم تحويل العملة تُخصم على الأوراق غير الكندية
عند الشراء وعند إعادة التوازن (رسوم البيع النهائي غير مخصومة).

الفترات قبل أول لقطة محفوظة = «ماذا لو» بأثر رجعي (hindsight): تطبيق محفظة اليوم على الماضي.
السجل الفعلي هو ما نشره المستثمر (reported) أو ورقته المدرجة (proxy)."""
import json
import math
from datetime import date, timedelta
from pathlib import Path

from .prices import Book, business_days

SEED = Path(__file__).resolve().parent / "data" / "investors.json"
_seeded = set()


def seed(conn, key=None):
    """يضيف المستثمرين ولقطات محافظهم من الملف المرفق مع البرنامج. لا يغيّر تاريخ المتابعة
    أو التفعيل أو اللقطات التي أضافها المستخدم."""
    key = key or id(conn)
    if key in _seeded or not SEED.is_file():
        return
    data = json.loads(SEED.read_text(encoding="utf-8"))
    as_of = data["as_of"]
    for i in data["investors"]:
        conn.execute(
            "INSERT INTO investors (id, list, rank, name, kind, style, since, url, five_y, record, reported, "
            "caveat, verdict, score, proxy, benchmarks, follow_date, notes) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET list=excluded.list, "
            "rank=excluded.rank, name=excluded.name, kind=excluded.kind, style=excluded.style, "
            "since=excluded.since, url=excluded.url, five_y=excluded.five_y, record=excluded.record, "
            "reported=excluded.reported, caveat=excluded.caveat, verdict=excluded.verdict, "
            "score=excluded.score, proxy=excluded.proxy, benchmarks=excluded.benchmarks, notes=excluded.notes",
            (i["id"], i["list"], i["rank"], i["name"], i["kind"], i["style"], i["since"], i["url"],
             i["five_y"], i["record"], json.dumps(i["reported"]), i["caveat"], i["verdict"], i["score"],
             i["proxy"], json.dumps(i["benchmarks"]), as_of, i.get("notes", "")))
        if not conn.execute("SELECT 1 FROM investor_holdings WHERE investor_id=? AND as_of=?",
                            (i["id"], as_of)).fetchone():
            conn.executemany(
                "INSERT INTO investor_holdings (investor_id, as_of, ticker, weight, estimated, source) "
                "VALUES (?,?,?,?,?,?)",
                [(i["id"], as_of, h["ticker"], h["weight"], int(h["estimated"]), data["source"])
                 for h in i["holdings"]])
    conn.commit()
    _seeded.add(key)


def tickers(conn):
    t = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM investor_holdings")}
    for r in conn.execute("SELECT proxy, benchmarks FROM investors"):
        if r[0]:
            t.add(r[0])
        t.update(json.loads(r[1] or "[]"))
    t.discard("CASH")
    return sorted(t)


def snapshots(conn, iid):
    snaps = {}
    for r in conn.execute("SELECT as_of, ticker, weight, estimated FROM investor_holdings "
                          "WHERE investor_id=? ORDER BY as_of, weight DESC", (iid,)):
        snaps.setdefault(r["as_of"], []).append(
            {"ticker": r["ticker"], "weight": r["weight"], "estimated": bool(r["estimated"])})
    return sorted(snaps.items())


# ------------------------------------------------------------------ simulation engine
def simulate(book, snaps, start, end, amount=1000.0, fx_fee=0.015):
    """snaps: [(as_of, {ticker: weight})] بترتيب التاريخ. يُرجع dict فيه series و warnings."""
    days = business_days(start, end)
    out = {"series": [], "warnings": [], "fees": 0.0, "hindsight": bool(snaps) and start < snaps[0][0]}
    if not days or not snaps:
        return out
    units, applied, warned, missing = {}, None, set(), set()

    def px(t, d):
        return book.cad_adj(t, d)

    def value(d):
        v = 0.0
        for t, u in units.items():
            p = px(t, d)
            if p is not None:
                v += u * p
        return v

    def rebalance(d, weights, V):
        avail = {t: w for t, w in weights.items() if w > 0 and px(t, d) is not None}
        missing.clear()
        for t in set(weights) - set(avail):
            if weights[t] > 0:
                missing.add(t)
            if (t, "np") not in warned:
                warned.add((t, "np"))
                out["warnings"].append(f"{t}: no price on {d} (not listed yet?) — added when it starts trading")
        tot = sum(avail.values())
        if tot <= 0:
            return V
        w = {t: x / tot for t, x in avail.items()}
        cur = {t: units.get(t, 0) * (px(t, d) or 0) for t in set(units) | set(w)}
        fee = sum(abs(V * w.get(t, 0) - cur.get(t, 0)) * fx_fee
                  for t in cur if t != "CASH" and book.is_foreign(t))
        V2 = V - fee
        out["fees"] += fee
        units.clear()
        for t, x in w.items():
            units[t] = V2 * x / px(t, d)
        return V2

    for d in days:
        eff = None
        for k, (as_of, _) in enumerate(snaps):
            if as_of <= d:
                eff = k
        if eff is None:
            eff = 0  # before the first known snapshot: hindsight
        late = applied == eff and any(px(t, d) is not None for t in missing)
        if applied != eff or late:
            V = amount if applied is None else value(d)
            rebalance(d, snaps[eff][1], V)
            applied = eff
            if not units:
                applied = None  # nothing could be bought yet: try again next day
                continue
        out["series"].append([d, value(d)])
    return out


def metrics(series):
    if len(series) < 2:
        return {"total": None, "cagr": None, "maxdd": None, "vol": None, "from": None, "to": None}
    v0, v1 = series[0][1], series[-1][1]
    total = v1 / v0 - 1
    years = (date.fromisoformat(series[-1][0]) - date.fromisoformat(series[0][0])).days / 365.25
    peak, dd = -1, 0.0
    rets = []
    prev = None
    for _, v in series:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
        if prev:
            rets.append(math.log(v / prev))
        prev = v
    vol = None
    if len(rets) > 20:
        m = sum(rets) / len(rets)
        vol = math.sqrt(sum((r - m) ** 2 for r in rets) / (len(rets) - 1)) * math.sqrt(252)
    return {"total": total, "cagr": (1 + total) ** (1 / years) - 1 if years >= 1 else None,
            "maxdd": dd, "vol": vol, "from": series[0][0], "to": series[-1][0]}


def yearly(series):
    out, prev_end, cur_year, last = {}, None, None, None
    base = series[0][1] if series else None
    for d, v in series:
        y = d[:4]
        if cur_year and y != cur_year:
            out[cur_year] = last / base - 1
            base = last
        cur_year, last = y, v
    if cur_year and base:
        out[cur_year] = last / base - 1
    return out


def thin(series, n=500):
    if len(series) <= n:
        return series
    k = len(series) / n
    return [series[int(i * k)] for i in range(n)] + [series[-1]]


# ------------------------------------------------------------------ API helpers
def _row(r):
    d = dict(r)
    d["reported"] = json.loads(d["reported"] or "{}")
    d["benchmarks"] = json.loads(d["benchmarks"] or "[]")
    return d


def _snaps_w(snaps):
    return [(a, {h["ticker"]: h["weight"] for h in hs}) for a, hs in snaps]


def overview(conn, start, end, amount=1000.0, fx_fee=0.015, home="XEQT.TO"):
    book = Book(conn)
    today = date.today().isoformat()
    res = []
    for r in conn.execute("SELECT * FROM investors ORDER BY list DESC, rank"):
        inv = _row(r)
        snaps = snapshots(conn, inv["id"])
        sw = _snaps_w(snaps)
        sim = simulate(book, sw, start, end, amount, fx_fee)
        live = simulate(book, sw, inv["follow_date"], today, amount, fx_fee) \
            if inv["follow_date"] < today else {"series": []}
        bench = simulate(book, [(start, {inv["benchmarks"][0]: 1})], start, end, amount, fx_fee)
        m, mb = metrics(sim["series"]), metrics(bench["series"])
        last = snaps[-1] if snaps else (None, [])
        res.append({**{k: inv[k] for k in ("id", "list", "rank", "name", "kind", "style", "since",
                                           "five_y", "verdict", "score", "follow_date", "active",
                                           "proxy", "reported")},
                    "benchmark": inv["benchmarks"][0], "metrics": m, "bench_metrics": mb,
                    "excess": None if m["total"] is None or mb["total"] is None else m["total"] - mb["total"],
                    "live": metrics(live["series"]), "hindsight": sim["hindsight"],
                    "holdings_as_of": last[0], "n_holdings": len(last[1]),
                    "coverage": sum(h["weight"] for h in last[1] if h["ticker"] != "CASH"),
                    "top": [h["ticker"] for h in last[1][:5]],
                    "series": thin(sim["series"], 250)})
    lines = {}
    for b in sorted({"SPY", "AOR", home}):
        s = simulate(book, [(start, {b: 1})], start, end, amount, fx_fee)
        lines[b] = {"series": thin(s["series"], 250), "metrics": metrics(s["series"])}
    return {"investors": res, "benchmarks": lines, "start": start, "end": end, "amount": amount,
            "fx_fee": fx_fee}


def detail(conn, iid, start, end, amount=1000.0, fx_fee=0.015, home="XEQT.TO"):
    r = conn.execute("SELECT * FROM investors WHERE id=?", (iid,)).fetchone()
    if not r:
        raise ValueError(f"unknown investor {iid}")
    inv = _row(r)
    book = Book(conn)
    snaps = snapshots(conn, iid)
    sim = simulate(book, _snaps_w(snaps), start, end, amount, fx_fee)
    lines = {"copy": sim}
    for b in dict.fromkeys(inv["benchmarks"] + [home] + ([inv["proxy"]] if inv["proxy"] else [])):
        lines[b] = simulate(book, [(start, {b: 1})], start, end, amount, fx_fee)
    today = date.today().isoformat()
    live = (simulate(book, _snaps_w(snaps), inv["follow_date"], today, amount, fx_fee)
            if inv["follow_date"] < today else {"series": [], "warnings": []})
    names = {x["ticker"]: x for x in (dict(s) for s in conn.execute(
        "SELECT ticker, name, currency, type FROM securities"))}
    for _, hs in snaps:
        for h in hs:
            s = names.get(h["ticker"], {})
            h["name"], h["currency"] = s.get("name"), s.get("currency")
            h["last"] = book.raw(h["ticker"], today)
            h["last_date"] = book.last_date(h["ticker"])
    return {"investor": inv, "snapshots": snaps,
            "lines": {k: {"series": thin(v["series"]), "metrics": metrics(v["series"]),
                          "yearly": yearly(v["series"])} for k, v in lines.items()},
            "warnings": sim["warnings"], "fees": sim["fees"], "hindsight": sim["hindsight"],
            "live": {"series": thin(live["series"]), "metrics": metrics(live["series"]),
                     "warnings": live.get("warnings", [])},
            "start": start, "end": end, "amount": amount, "fx_fee": fx_fee}


def set_follow(conn, iid, d):
    date.fromisoformat(d)
    conn.execute("UPDATE investors SET follow_date=? WHERE id=?", (d, iid))
    conn.commit()


def set_active(conn, iid, active):
    conn.execute("UPDATE investors SET active=? WHERE id=?", (1 if active else 0, iid))
    conn.commit()


def add_snapshot(conn, iid, as_of, holdings, fetch=None):
    """لقطة جديدة (مثلاً بعد ملف 13F ربعي أو تحديث eToro): الأوزان بالنسبة للمحفظة كاملة."""
    date.fromisoformat(as_of)
    if not conn.execute("SELECT 1 FROM investors WHERE id=?", (iid,)).fetchone():
        raise ValueError(f"unknown investor {iid}")
    rows = []
    for h in holdings:
        t = str(h["ticker"]).strip().upper()
        w = float(h["weight"])
        if w > 1.0001:
            w /= 100  # entered as a percentage
        if t and w > 0:
            rows.append((iid, as_of, t, w, int(bool(h.get("estimated"))), "manual"))
    if not rows:
        raise ValueError("no holdings")
    for t in {r[2] for r in rows} - {"CASH"}:
        if fetch:
            fetch(t)
    conn.execute("DELETE FROM investor_holdings WHERE investor_id=? AND as_of=?", (iid, as_of))
    conn.executemany("INSERT INTO investor_holdings VALUES (?,?,?,?,?,?)", rows)
    conn.commit()


def delete_snapshot(conn, iid, as_of):
    n = conn.execute("SELECT COUNT(DISTINCT as_of) FROM investor_holdings WHERE investor_id=?",
                     (iid,)).fetchone()[0]
    if n <= 1:
        raise ValueError("an investor needs at least one holdings snapshot")
    conn.execute("DELETE FROM investor_holdings WHERE investor_id=? AND as_of=?", (iid, as_of))
    conn.commit()
