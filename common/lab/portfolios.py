"""محافظ المستخدم الخاصة: عمليات شراء/بيع/إيداع/سحب، تقييم يومي بالدولار الكندي، ومقارنة
بمؤشر مرجعي يتلقى نفس الإيداعات في نفس الأيام (مقارنة عادلة لقراراتك).

- القيمة تشمل التوزيعات (سعر معدّل): وحدات = الكمية × السعر الفعلي ÷ السعر المعدّل يوم الشراء.
- شراء ورقة غير كندية من حساب CAD: رسم تحويل fx_fee (افتراضي 1.5%) عند الشراء وعند البيع.
- إن لم يكفِ النقد لشراء، يُعتبر الفرق إيداعاً تلقائياً في يوم الشراء."""
import math
from datetime import date, datetime

from .prices import Book, business_days, price_on_or_before
from .investors import metrics, thin


def _now():
    return datetime.now().isoformat(timespec="seconds")


def create(conn, name, benchmark="XEQT.TO", fx_fee=0.015, notes=""):
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    cur = conn.execute("INSERT INTO portfolios (name, created_at, benchmark, fx_fee, notes) VALUES (?,?,?,?,?)",
                       (name, date.today().isoformat(), benchmark.strip().upper() or "XEQT.TO",
                        float(fx_fee), notes))
    conn.commit()
    return cur.lastrowid


def update(conn, pid, **kw):
    allowed = {k: v for k, v in kw.items() if k in ("name", "benchmark", "fx_fee", "notes", "archived")
               and v is not None}
    if "benchmark" in allowed:
        allowed["benchmark"] = str(allowed["benchmark"]).strip().upper()
    for k, v in allowed.items():
        conn.execute(f"UPDATE portfolios SET {k}=? WHERE id=?", (v, pid))
    conn.commit()


def delete(conn, pid):
    conn.execute("DELETE FROM transactions WHERE portfolio_id=?", (pid,))
    conn.execute("DELETE FROM portfolios WHERE id=?", (pid,))
    conn.commit()


def add_tx(conn, pid, tx, fetch=None):
    """tx: date, type, ticker, quantity | amount (CAD), price (اختياري), fee (CAD)."""
    p = conn.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("unknown portfolio")
    d = str(tx.get("date") or date.today().isoformat())[:10]
    date.fromisoformat(d)
    if d > date.today().isoformat():
        raise ValueError("the date is in the future")
    typ = tx.get("type")
    fee = float(tx.get("fee") or 0)
    if typ in ("deposit", "withdraw"):
        amt = float(tx.get("amount") or 0)
        if amt <= 0:
            raise ValueError("amount must be positive")
        conn.execute("INSERT INTO transactions (portfolio_id, date, type, amount, fee, note, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (pid, d, typ, amt, fee, tx.get("note"), _now()))
        conn.commit()
        return
    if typ not in ("buy", "sell"):
        raise ValueError("type must be buy, sell, deposit or withdraw")
    t = str(tx.get("ticker") or "").strip().upper()
    if not t:
        raise ValueError("ticker is required")
    if fetch:
        fetch(t)
    book = Book(conn)
    price = tx.get("price")
    price = float(price) if price not in (None, "") else book.raw(t, d)
    if not price:
        raise ValueError(f"no price for {t} on or before {d} — enter the price manually")
    qty = tx.get("quantity")
    if qty in (None, ""):
        amt = float(tx.get("amount") or 0)
        if amt <= 0:
            raise ValueError("enter a quantity or an amount in CAD")
        fx = book.fx(t, d)
        if not fx:
            raise ValueError(f"no exchange rate for {t} on {d}")
        f = float(p["fx_fee"]) if book.is_foreign(t) else 0.0
        qty = (amt - fee) / (price * fx * (1 + f))
    qty = float(qty)
    if qty <= 0:
        raise ValueError("quantity must be positive")
    if typ == "sell":
        held = position_qty(conn, pid, t, d)
        if qty > held + 1e-9:
            raise ValueError(f"you hold only {held:g} {t} on {d}")
    conn.execute("INSERT INTO transactions (portfolio_id, date, type, ticker, quantity, price, fee, note, "
                 "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                 (pid, d, typ, t, qty, price, fee, tx.get("note"), _now()))
    conn.commit()


def delete_tx(conn, txid):
    conn.execute("DELETE FROM transactions WHERE id=?", (txid,))
    conn.commit()


def position_qty(conn, pid, t, d):
    q = 0.0
    for r in conn.execute("SELECT type, quantity FROM transactions WHERE portfolio_id=? AND ticker=? "
                          "AND date<=? ORDER BY date, id", (pid, t, d)):
        q += r["quantity"] if r["type"] == "buy" else -r["quantity"]
    return q


def tickers(conn):
    t = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM transactions WHERE ticker IS NOT NULL")}
    t.update(r[0] for r in conn.execute("SELECT DISTINCT benchmark FROM portfolios"))
    return sorted(t)


def value(conn, pid, end=None, book=None):
    """تقييم يومي كامل. يُرجع السلسلة والمراكز والملخص."""
    p = conn.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ValueError("unknown portfolio")
    book = book or Book(conn)
    txs = [dict(r) for r in conn.execute("SELECT * FROM transactions WHERE portfolio_id=? "
                                         "ORDER BY date, id", (pid,))]
    end = end or date.today().isoformat()
    bm, fxf = p["benchmark"], float(p["fx_fee"])
    out = {"portfolio": dict(p), "transactions": txs, "series": [], "positions": [],
           "summary": None, "warnings": []}
    if not txs:
        return out
    pos = {}  # ticker -> {qty, units, cost}
    cash = invested = 0.0
    b_units = 0.0
    twr, prev_v = 1.0, None
    realized = fees = 0.0
    i = 0
    series = []

    def b_flow(amount, d):
        nonlocal b_units
        px = book.cad_adj(bm, d)
        if not px:
            out["warnings"].append(f"benchmark {bm}: no price on {d}")
            return
        f = fxf if book.is_foreign(bm) else 0.0
        if amount > 0:
            b_units += amount * (1 - f) / px
        else:
            b_units -= min(b_units, -amount / (px * (1 - f)))

    for d in business_days(txs[0]["date"], end) or [end]:
        flow = 0.0
        while i < len(txs) and txs[i]["date"] <= d:
            x = txs[i]
            i += 1
            if x["type"] == "deposit":
                cash += x["amount"] - x["fee"]
                invested += x["amount"]
                flow += x["amount"]
                b_flow(x["amount"], d)
            elif x["type"] == "withdraw":
                cash -= x["amount"] + x["fee"]
                invested -= x["amount"]
                flow -= x["amount"]
                b_flow(-x["amount"], d)
            else:
                t = x["ticker"]
                fx = book.fx(t, x["date"]) or book.fx(t, d)
                adj, raw = book.adj(t, x["date"]), book.raw(t, x["date"])
                if not fx or not adj:
                    out["warnings"].append(f"{t}: no price/exchange rate on {x['date']} — transaction ignored")
                    continue
                f = fxf if book.is_foreign(t) else 0.0
                gross = x["quantity"] * x["price"] * fx
                ps = pos.setdefault(t, {"qty": 0.0, "units": 0.0, "cost": 0.0})
                if x["type"] == "buy":
                    cost = gross * (1 + f) + x["fee"]
                    fees += gross * f + x["fee"]
                    if cost > cash + 1e-9:  # automatic deposit of the shortfall
                        need = cost - max(cash, 0.0)
                        cash += need
                        invested += need
                        flow += need
                        b_flow(need, d)
                    cash -= cost
                    ps["qty"] += x["quantity"]
                    ps["units"] += x["quantity"] * raw / adj
                    ps["cost"] += cost
                else:
                    frac = min(1.0, x["quantity"] / ps["qty"]) if ps["qty"] > 0 else 0
                    proceeds = gross * (1 - f) - x["fee"]
                    fees += gross * f + x["fee"]
                    realized += proceeds - ps["cost"] * frac
                    ps["qty"] -= x["quantity"]
                    ps["units"] *= (1 - frac)
                    ps["cost"] *= (1 - frac)
                    cash += proceeds
        v = cash
        for t, ps in pos.items():
            px = book.cad_adj(t, d)
            if px and ps["units"] > 0:
                v += ps["units"] * px
        bv = b_units * (book.cad_adj(bm, d) or 0)
        if prev_v and prev_v > 0:
            twr *= (v - flow) / prev_v
        prev_v = v
        series.append([d, round(v, 4), round(invested, 4), round(bv, 4), twr])
    out["series"] = series
    total_v = series[-1][1]
    for t, ps in pos.items():
        if ps["qty"] <= 1e-9:
            continue
        px = book.cad_adj(t, end) or 0
        mv = ps["units"] * px
        out["positions"].append({"ticker": t, "qty": ps["qty"], "cost": ps["cost"], "value": mv,
                                 "price": book.raw(t, end), "price_date": book.last_date(t),
                                 "currency": (conn.execute("SELECT currency FROM securities WHERE ticker=?", (t,))
                                              .fetchone() or [book.currency(t)[0]])[0], "gain": mv - ps["cost"],
                                 "ret": mv / ps["cost"] - 1 if ps["cost"] else None,
                                 "weight": mv / total_v if total_v else None})
    if cash > 0.005 or cash < -0.005:
        out["positions"].append({"ticker": "CASH", "qty": None, "cost": cash, "value": cash,
                                 "price": None, "price_date": None, "currency": "CAD", "gain": 0,
                                 "ret": None, "weight": cash / total_v if total_v else None})
    out["positions"].sort(key=lambda x: -(x["value"] or 0))
    bv = series[-1][3]
    out["summary"] = {"value": total_v, "invested": invested, "gain": total_v - invested,
                      "gain_pct": (total_v - invested) / invested if invested else None,
                      "twr": twr - 1, "bench_value": bv, "bench_gain": bv - invested,
                      "vs_bench": total_v - bv, "realized": realized, "fees": fees, "cash": cash,
                      "first": txs[0]["date"], "risk": metrics([[s[0], s[4]] for s in series])}
    return out


def detail(conn, pid, start=None, end=None):
    v = value(conn, pid, end)
    s = v["series"]
    if start:
        s = [x for x in s if x[0] >= start] or s[-1:]
    v["series"] = thin(s, 600)
    names = {r[0]: r[1] for r in conn.execute("SELECT ticker, name FROM securities")}
    for p in v["positions"]:
        p["name"] = names.get(p["ticker"])
    for t in v["transactions"]:
        t["name"] = names.get(t["ticker"])
    if s:
        a, b = s[0], s[-1]
        v["period"] = {"start": a[0], "end": b[0], "twr": b[4] / a[4] - 1 if a[4] else None,
                       "bench_twr": None}
    return v


def overview(conn):
    book = Book(conn)
    out = []
    for p in conn.execute("SELECT * FROM portfolios WHERE archived=0 ORDER BY id"):
        v = value(conn, p["id"], book=book)
        out.append({**dict(p), "summary": v["summary"],
                    "n_positions": len([x for x in v["positions"] if x["ticker"] != "CASH"]),
                    "series": thin([[x[0], x[4]] for x in v["series"]], 250)})
    return out


def create_from_weights(conn, name, weights, amount, d, benchmark="XEQT.TO", fx_fee=0.015,
                        fetch=None, note=""):
    """ينشئ محفظة من أوزان (توزيع الوكلاء أو محفظة مستثمر) بمبلغ واحد في تاريخ d."""
    pid = create(conn, name, benchmark, fx_fee, note)
    amount = float(amount)
    conn.execute("INSERT INTO transactions (portfolio_id, date, type, amount, fee, note, created_at) "
                 "VALUES (?,?,?,?,?,?,?)", (pid, d, "deposit", amount, 0, note, _now()))
    conn.commit()
    tot = sum(w for t, w in weights.items() if t != "CASH" and w > 0)
    cash_w = weights.get("CASH", 0)
    tot_all = tot + cash_w
    errors = []
    for t, w in weights.items():
        if t == "CASH" or w <= 0:
            continue
        try:
            add_tx(conn, pid, {"date": d, "type": "buy", "ticker": t,
                               "amount": amount * w / tot_all, "note": note}, fetch)
        except Exception as e:
            errors.append(f"{t}: {e}")
    return pid, errors
