"""حسم التوقعات وحساب Brier ومقارنة المحافظ الورقية بالمؤشر المرجعي."""
import json
from collections import defaultdict
from datetime import date, datetime, timezone

from .prices import total_return

NAIVE_BRIER = 0.25  # ما يحصل عليه من يقول 50% دائماً


def resolve(conn, today=None, log=print):
    today = today or datetime.now(timezone.utc).date().isoformat()
    due = conn.execute("SELECT * FROM forecasts WHERE outcome IS NULL AND resolve_date<=?",
                       (today,)).fetchall()
    done = missing = 0
    for f in due:
        ra = total_return(conn, f["ticker_a"], f["start_date"], f["resolve_date"], final=True)
        if f["kind"] == "outperform":
            rb = total_return(conn, f["ticker_b"], f["start_date"], f["resolve_date"], final=True)
            outcome = None if ra is None or rb is None else int(ra > rb)
        else:
            outcome = None if ra is None else int(ra > f["threshold"])
        if outcome is None:
            missing += 1
            continue
        brier = (f["probability"] - outcome) ** 2
        conn.execute("UPDATE forecasts SET outcome=?, brier=?, resolved_at=? WHERE id=?",
                     (outcome, brier, today, f["id"]))
        done += 1
    conn.commit()
    log(f"resolved {done} forecasts; {missing} due but waiting for a final closing price "
        f"on/after the resolve date (normal for 1-2 days; if it lasts, run `fetch-prices` "
        f"or `import-prices`)")


def tickers_needed(conn, universe=()):
    t = {x for x in universe if x != "CASH"}
    for r in conn.execute("SELECT ticker_a, ticker_b FROM forecasts"):
        t.update(x for x in r if x and x != "CASH")
    for r in conn.execute("SELECT DISTINCT ticker FROM allocations"):
        if r[0] != "CASH":
            t.add(r[0])
    return sorted(t)


def _is_core(f, core):
    h = (date.fromisoformat(f["resolve_date"]) - date.fromisoformat(f["start_date"])).days
    return any(c["kind"] == f["kind"] and c["ticker_a"] == f["ticker_a"]
               and c.get("ticker_b") == f["ticker_b"]
               and (c.get("threshold") if f["kind"] == "return_above" else None) == f["threshold"]
               and c["horizon_days"] == h for c in core)


def _row(name, v):
    n = len(v["b"])
    mb = sum(v["b"]) / n if n else None
    return (f"| {name} | {n} | {v['pending']} | "
            f"{'%.3f' % mb if mb is not None else '—'} | "
            f"{'%+.1f%%' % ((1 - mb / NAIVE_BRIER) * 100) if mb is not None else '—'} | "
            f"{'%.0f%%' % (v['hits'] / n * 100) if n else '—'} |")


def report(conn, lab, today=None):
    today = today or datetime.now(timezone.utc).date().isoformat()
    benchmark, core = lab["benchmark"], lab.get("core_forecasts", [])
    rows = conn.execute(
        "SELECT r.agent, f.* FROM forecasts f JOIN responses r ON r.id=f.response_id").fetchall()
    g_all = defaultdict(lambda: {"b": [], "pending": 0, "hits": 0})
    g_core = defaultdict(lambda: {"b": [], "pending": 0, "hits": 0})
    for x in rows:
        for g in ([g_all, g_core] if _is_core(x, core) else [g_all]):
            v = g[x["agent"]]
            if x["outcome"] is None:
                v["pending"] += 1
            else:
                v["b"].append(x["brier"])
                v["hits"] += int((x["probability"] >= 0.5) == bool(x["outcome"]))

    head = ["| agent | resolved | pending | mean Brier | skill vs 50% | hit rate |",
            "|---|---|---|---|---|---|"]
    lines = ["# Agents Lab report — " + today, "",
             "Brier: lower is better. Saying 50% every time scores 0.250.", "",
             "## 1. Core questions (identical for every agent — the fair comparison)", ""]
    lines += head + [_row(a, v) for a, v in sorted(g_core.items())]
    lines += ["", "## 2. All forecasts (core + each agent's own picks)", ""]
    lines += head + [_row(a, v) for a, v in sorted(g_all.items())]
    n_min = min((len(v["b"]) for v in g_core.values()), default=0)
    lines += ["", f"> Fewest resolved core forecasts for any agent: {n_min}. "
              "Do not draw conclusions before ~30, and treat Brier differences under ~0.02 as noise.", ""]

    lines += [f"## 3. Paper allocations vs benchmark ({benchmark}), lump sum from response date", "",
              "| month | agent | from | return | benchmark | excess |", "|---|---|---|---|---|---|"]
    resp = conn.execute(
        "SELECT r.id, r.agent, s.created_at AS month, "
        "COALESCE((SELECT MIN(start_date) FROM forecasts WHERE response_id=r.id), s.created_at) AS d "
        "FROM responses r JOIN sessions s ON s.id=r.session_id ORDER BY s.created_at, r.agent").fetchall()
    for r in resp:
        al = conn.execute("SELECT ticker, weight FROM allocations WHERE response_id=?",
                          (r["id"],)).fetchall()
        rets = [(a["weight"], total_return(conn, a["ticker"], r["d"], today)) for a in al]
        bench = total_return(conn, benchmark, r["d"], today)
        if not al or any(x[1] is None for x in rets) or bench is None:
            lines.append(f"| {r['month'][:7]} | {r['agent']} | {r['d']} | n/a | n/a | n/a |")
            continue
        pr = sum(w * x for w, x in rets)
        lines.append(f"| {r['month'][:7]} | {r['agent']} | {r['d']} | {pr:+.2%} | "
                     f"{bench:+.2%} | {pr - bench:+.2%} |")
    lines += ["", "> Short-horizon returns are mostly noise. Weight section 1 over section 3."]
    return "\n".join(lines)
