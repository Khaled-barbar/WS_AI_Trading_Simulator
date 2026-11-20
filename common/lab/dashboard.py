"""لوحة مراقبة محلية: خادم HTTP على 127.0.0.1 فقط، بدون مكتبات خارجية."""
import json
import re
import socket
import subprocess
import sys
import threading
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import investors, portfolios, prices, prompts, scoring, updates
from .db import ROOT, connect
from .parse import extract_json
from .tracks import track_cfg, tracks

STATIC = Path(__file__).resolve().parent / "static"
HISTORY = "2016-01-01"
STATIC_FILES = {"manifest.json": "application/manifest+json", "icon-192.png": "image/png",
                "icon-512.png": "image/png"}
_run_lock = threading.Lock()
_fetch_lock = threading.Lock()
_run_state = {"running": False, "started": None, "finished": None, "code": None}

FORECASTS_OF = ("FROM forecasts f JOIN responses r ON r.id=f.response_id "
                "JOIN sessions s ON s.id=r.session_id WHERE s.track=?")


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


class API:
    def __init__(self, cfg, config_path=None, port=8765):
        self.cfg = cfg
        self.port = port
        self.config_path = config_path
        self.lab = cfg["lab"]
        self.stocks = track_cfg(cfg, "stocks")
        self.inbox = ROOT / self.lab.get("inbox", "inbox")

    def conn(self):
        return connect(self.lab["db_path"])

    def tcfg(self, track):
        return track_cfg(self.cfg, track or "etf")

    # ---------- price helper: download a ticker (and its exchange rate) on demand ----------
    def ensure(self, t, conn=None):
        c = conn or self.conn()
        t = t.strip().upper()
        today = date.today().isoformat()
        with _fetch_lock:
            last = c.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (t,)).fetchone()[0]
            if not last or last < (date.today() - timedelta(days=4)).isoformat():
                prices.fetch_yahoo(c, t, HISTORY, today, incremental=True)
            for fx in prices.fx_needed(c, [t]):
                lf = c.execute("SELECT MAX(date) FROM prices WHERE ticker=?", (fx,)).fetchone()[0]
                if not lf or lf < (date.today() - timedelta(days=4)).isoformat():
                    prices.fetch_yahoo(c, fx, HISTORY, today, incremental=True)
        return t

    # ---------- GET ----------
    def config(self, q=None):
        s = self.stocks
        return {"agents": {k: {"provider": v["provider"], "model": v["model"]}
                           for k, v in self.cfg["agents"].items()},
                **{k: self.lab[k] for k in ("benchmark", "universe", "horizons_days",
                                            "min_forecasts_per_agent", "question")},
                "core_forecasts": self.lab.get("core_forecasts", []),
                "tracks": tracks(self.cfg),
                "stocks": {k: s[k] for k in ("benchmark", "compare", "min_picks", "max_picks", "max_weight",
                                             "fx_fee", "horizons_days", "min_forecasts_per_agent",
                                             "question", "core_forecasts")},
                "root": str(ROOT), "inbox": str(self.inbox), "platform": sys.platform,
                "today": date.today().isoformat(),
                "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip()
                if (ROOT / "VERSION").is_file() else "dev"}

    def overview(self, q=None):
        c = self.conn()
        today = date.today().isoformat()
        month = today[:7]
        # resolution waits for the final close on/after the resolve date (1-2 days is
        # normal), so only flag forecasts stuck for more than 4 days
        stuck = (date.today() - timedelta(days=4)).isoformat()
        stats = c.execute(
            "SELECT COUNT(*) n, SUM(outcome IS NOT NULL) resolved, "
            "SUM(outcome IS NULL AND resolve_date<?) overdue, "
            "MIN(CASE WHEN outcome IS NULL AND resolve_date>? THEN resolve_date END) next_due "
            + FORECASTS_OF, (stuck, today, "etf")).fetchone()
        this_month = {}
        for r in c.execute("SELECT r.agent, r.created_at, s.track FROM responses r JOIN sessions s "
                           "ON s.id=r.session_id WHERE substr(s.created_at,1,7)=?", (month,)):
            this_month.setdefault(r["track"], {})[r["agent"]] = r["created_at"]
        last_price = c.execute("SELECT MAX(date) FROM prices WHERE ticker NOT LIKE '%=X'").fetchone()[0]
        return {"today": today, "month": month,
                "sessions": c.execute("SELECT COUNT(*) FROM sessions WHERE track='etf'").fetchone()[0],
                "responses": c.execute("SELECT COUNT(*) FROM responses r JOIN sessions s ON s.id=r.session_id "
                                       "WHERE r.agent!='consensus' AND s.track='etf'").fetchone()[0],
                "forecasts": dict(stats), "this_month": this_month, "tracks": tracks(self.cfg),
                "agents": list(self.cfg["agents"]), "last_price": last_price,
                "inbox_pending": len(self._files(self.inbox)),
                "inbox_failed": len([f for f in self._files(self.inbox / "failed")
                                     if not f["name"].endswith(".error")]),
                "run": _run_state,
                "scoreboard": {t: self.scoreboard(c, t) for t in tracks(self.cfg)}}

    def scoreboard(self, c=None, track="etf"):
        c = c or self.conn()
        core = self.tcfg(track).get("core_forecasts", [])
        out = {}
        for x in c.execute("SELECT r.agent, f.* " + FORECASTS_OF, (track,)):
            v = out.setdefault(x["agent"], {"core_b": [], "all_b": [], "pending": 0})
            if x["outcome"] is None:
                v["pending"] += 1
                continue
            v["all_b"].append(x["brier"])
            if scoring._is_core(x, core):
                v["core_b"].append(x["brier"])
        res = {}
        for a, v in out.items():
            avg = lambda l: sum(l) / len(l) if l else None
            res[a] = {"core_n": len(v["core_b"]), "core_brier": avg(v["core_b"]),
                      "all_n": len(v["all_b"]), "all_brier": avg(v["all_b"]),
                      "pending": v["pending"]}
        return res

    def sessions(self, q=None):
        track = (q or {}).get("track", "etf")
        c = self.conn()
        out = []
        for s in _rows(c.execute("SELECT * FROM sessions WHERE track=? ORDER BY created_at DESC", (track,))):
            resp = _rows(c.execute("SELECT * FROM responses WHERE session_id=? ORDER BY agent",
                                   (s["id"],)))
            for r in resp:
                r["allocation"] = _rows(c.execute(
                    "SELECT a.ticker, a.weight, s.name FROM allocations a LEFT JOIN securities s "
                    "ON s.ticker=a.ticker WHERE response_id=? ORDER BY weight DESC", (r["id"],)))
                r["forecasts"] = _rows(c.execute(
                    "SELECT * FROM forecasts WHERE response_id=? ORDER BY resolve_date",
                    (r["id"],)))
            s["responses"] = resp
            s["universe"] = json.loads(s["universe"])
            out.append(s)
        return out

    def forecasts(self, q=None):
        track = (q or {}).get("track", "etf")
        c = self.conn()
        core = self.tcfg(track).get("core_forecasts", [])
        rows = _rows(c.execute(
            "SELECT f.*, r.agent, substr(s.created_at,1,7) AS month " + FORECASTS_OF +
            " ORDER BY f.resolve_date, r.agent", (track,)))
        for f in rows:
            f["core"] = scoring._is_core(f, core)
            end = min(f["resolve_date"], date.today().isoformat())
            f["ret_a"] = prices.total_return(c, f["ticker_a"], f["start_date"], end)
            f["ret_b"] = (prices.total_return(c, f["ticker_b"], f["start_date"], end)
                          if f["ticker_b"] else None)
        return rows

    def prices(self, q=None):
        c = self.conn()
        since = (date.today() - timedelta(days=400)).isoformat()
        out = {}
        for t in [x for x in self.lab["universe"] if x != "CASH"]:
            out[t] = [[r["date"], r["close"]] for r in c.execute(
                "SELECT date, close FROM prices WHERE ticker=? AND date>=? ORDER BY date", (t, since))]
        return out

    def picks(self, q=None):
        """مسار الشركات: آخر توصيات كل وكيل + الإجماع + الأداء الورقي لكل شهر."""
        c = self.conn()
        lab = self.stocks
        names = {r[0]: {"name": r[1], "currency": r[2]} for r in
                 c.execute("SELECT ticker, name, currency FROM securities")}
        months = []
        for s in _rows(c.execute("SELECT * FROM sessions WHERE track='stocks' ORDER BY created_at DESC")):
            agents = {}
            for r in _rows(c.execute("SELECT * FROM responses WHERE session_id=? ORDER BY agent", (s["id"],))):
                theses = {}
                try:
                    for p in extract_json(r["raw_text"]).get("picks") or []:
                        theses[str(p.get("ticker", "")).upper()] = p.get("thesis") or ""
                except Exception:
                    pass
                al = _rows(c.execute("SELECT ticker, weight FROM allocations WHERE response_id=? "
                                     "ORDER BY weight DESC", (r["id"],)))
                for a in al:
                    a.update(names.get(a["ticker"], {}))
                    a["thesis"] = theses.get(a["ticker"], "")
                agents[r["agent"]] = {"id": r["id"], "model": r["model"], "created_at": r["created_at"],
                                      "analysis": r["analysis"], "risks": r["risks"], "allocation": al}
            months.append({"month": s["created_at"][:7], "session_id": s["id"], "agents": agents})
        perf = scoring.picks_performance(c, lab)
        for p in perf:
            p["series"] = investors.thin(p["series"], 200)
        return {"months": months, "performance": perf, "scoreboard": self.scoreboard(c, "stocks"),
                "benchmark": lab["benchmark"], "compare": lab.get("compare", []), "fx_fee": lab["fx_fee"]}

    def report(self, q=None):
        c = self.conn()
        today = date.today().isoformat()
        txt = scoring.report(c, self.lab, today)
        if c.execute("SELECT 1 FROM sessions WHERE track='stocks'").fetchone():
            txt += "\n\n" + scoring.report(c, self.stocks, today)
        return {"markdown": txt}

    def _files(self, d):
        if not d.is_dir():
            return []
        res = []
        for p in sorted(d.iterdir(), key=lambda p: -p.stat().st_mtime):
            if p.is_file() and not p.name.startswith("."):
                res.append({"name": p.name, "size": p.stat().st_size,
                            "modified": datetime.fromtimestamp(p.stat().st_mtime)
                            .isoformat(timespec="minutes")})
        return res

    def inbox_list(self, q=None):
        failed = self._files(self.inbox / "failed")
        for f in failed:
            err = self.inbox / "failed" / (f["name"] + ".error")
            f["error"] = err.read_text(encoding="utf-8") if err.exists() else None
        return {"pending": self._files(self.inbox),
                "processed": self._files(self.inbox / "processed")[:50],
                "failed": [f for f in failed if not f["name"].endswith(".error")]}

    def prompt_texts(self, q=None):
        return {t: {a: prompts.app_prompt(self.tcfg(t), a, str(self.inbox))
                    for a in prompts.manual_agents(self.cfg)} for t in tracks(self.cfg)}

    def log(self, q=None):
        p = ROOT / "logs" / "run.log"
        if not p.exists():
            return {"text": ""}
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"text": "\n".join(lines[-300:])}

    # investors
    def _period(self, q):
        today = date.today().isoformat()
        end = (q.get("end") or today)[:10]
        start = (q.get("start") or (date.today() - timedelta(days=5 * 365)).isoformat())[:10]
        date.fromisoformat(start), date.fromisoformat(end)
        return start, min(end, today)

    def investors(self, q=None):
        q = q or {}
        start, end = self._period(q)
        return investors.overview(self.conn(), start, end, float(q.get("amount") or 1000),
                                  self.stocks["fx_fee"], self.lab["benchmark"])

    def investor(self, q=None):
        q = q or {}
        start, end = self._period(q)
        return investors.detail(self.conn(), q.get("id", ""), start, end, float(q.get("amount") or 1000),
                                self.stocks["fx_fee"], self.lab["benchmark"])

    # portfolios
    def portfolio_list(self, q=None):
        return portfolios.overview(self.conn())

    def portfolio(self, q=None):
        q = q or {}
        return portfolios.detail(self.conn(), int(q.get("id", 0)), q.get("start") or None)

    def search(self, q=None):
        return prices.search((q or {}).get("q", ""), self.conn())

    def quote(self, q=None):
        c = self.conn()
        t = self.ensure((q or {}).get("ticker", ""), c)
        s = c.execute("SELECT * FROM securities WHERE ticker=?", (t,)).fetchone()
        if not s:
            raise ValueError(f"{t}: not found on Yahoo Finance")
        book = prices.Book(c)
        d = (q or {}).get("date") or date.today().isoformat()
        return {**dict(s), "price": book.raw(t, d), "price_date": book.last_date(t),
                "fx": book.fx(t, d), "foreign": book.is_foreign(t)}

    # ---------- POST ----------
    def paste(self, body):
        agent = body.get("agent")
        track = body.get("track") or "etf"
        text = (body.get("text") or "").strip()
        allowed = prompts.manual_agents(self.cfg)
        if agent not in allowed:
            raise ValueError("agent must be one of: " + ", ".join(allowed))
        if track not in tracks(self.cfg):
            raise ValueError("unknown track")
        if "{" not in text:
            raise ValueError("the pasted text contains no JSON")
        self.inbox.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        p = self.inbox / (f"{agent}-stocks-{stamp}.txt" if track == "stocks" else f"{agent}-{stamp}.txt")
        p.write_text(text, encoding="utf-8")
        # استيراد فوري
        from . import session
        msgs = []
        session.ingest(self.conn(), self.cfg, self.inbox, log=msgs.append)
        return {"saved": p.name, "log": msgs}

    def investor_follow(self, b):
        investors.set_follow(self.conn(), b["id"], b["date"])
        return {"ok": True}

    def investor_active(self, b):
        investors.set_active(self.conn(), b["id"], b.get("active", True))
        return {"ok": True}

    def investor_holdings(self, b):
        c = self.conn()
        investors.add_snapshot(c, b["id"], b["as_of"], b["holdings"], lambda t: self.ensure(t, c))
        return {"ok": True}

    def investor_holdings_delete(self, b):
        investors.delete_snapshot(self.conn(), b["id"], b["as_of"])
        return {"ok": True}

    def portfolio_create(self, b):
        c = self.conn()
        bm = self.ensure(b.get("benchmark") or self.lab["benchmark"], c)
        return {"id": portfolios.create(c, b.get("name"), bm, float(b.get("fx_fee", 0.015)),
                                        b.get("notes", ""))}

    def portfolio_update(self, b):
        c = self.conn()
        if b.get("benchmark"):
            self.ensure(b["benchmark"], c)
        portfolios.update(c, int(b["id"]), **{k: b.get(k) for k in
                                              ("name", "benchmark", "fx_fee", "notes", "archived")})
        return {"ok": True}

    def portfolio_delete(self, b):
        portfolios.delete(self.conn(), int(b["id"]))
        return {"ok": True}

    def portfolio_tx(self, b):
        c = self.conn()
        portfolios.add_tx(c, int(b["portfolio_id"]), b, lambda t: self.ensure(t, c))
        return {"ok": True}

    def portfolio_tx_delete(self, b):
        portfolios.delete_tx(self.conn(), int(b["id"]))
        return {"ok": True}

    def portfolio_from(self, b):
        """محفظة جديدة من توصيات وكيل (response_id) أو من محفظة مستثمر (investor)."""
        c = self.conn()
        d = (b.get("date") or date.today().isoformat())[:10]
        amount = float(b.get("amount") or 1000)
        if b.get("source") == "investor":
            snaps = investors.snapshots(c, b["id"])
            eff = [s for s in snaps if s[0] <= d] or snaps[:1]
            weights = {h["ticker"]: h["weight"] for h in eff[-1][1]}
            name = b.get("name") or "Copy — " + c.execute("SELECT name FROM investors WHERE id=?",
                                                          (b["id"],)).fetchone()[0]
        else:
            r = c.execute("SELECT r.agent, s.created_at FROM responses r JOIN sessions s ON s.id=r.session_id "
                          "WHERE r.id=?", (int(b["id"]),)).fetchone()
            weights = {a["ticker"]: a["weight"] for a in c.execute(
                "SELECT ticker, weight FROM allocations WHERE response_id=?", (int(b["id"]),))}
            name = b.get("name") or f"AI picks — {r['agent']} {r['created_at'][:7]}"
        pid, errors = portfolios.create_from_weights(
            c, name, weights, amount, d, self.lab["benchmark"], self.stocks["fx_fee"],
            lambda t: self.ensure(t, c), note=name)
        return {"id": pid, "errors": errors}

    def update_check(self, q=None):
        return updates.check(self.cfg)

    def update_log(self, q=None):
        return updates.log_tail()

    def update_install(self, b=None):
        if _run_state["running"]:
            raise ValueError("a run is in progress — try again when it finishes")
        info = updates.check(self.cfg)
        if info["error"]:
            raise ValueError(info["error"])
        if not info["newer"]:
            return {"started": False, "reason": "already up to date", **info}
        return {**updates.start(self.cfg, self.port), **info}

    def run_now(self, b=None):
        if not _run_lock.acquire(blocking=False):
            return {"started": False, "reason": "already running"}

        def work():
            _run_state.update(running=True, started=datetime.now().isoformat(timespec="seconds"),
                              finished=None, code=None)
            try:
                (ROOT / "logs").mkdir(exist_ok=True)
                with open(ROOT / "logs" / "run.log", "a", encoding="utf-8") as log:
                    log.write(f"==== {datetime.now():%Y-%m-%d %H:%M} (dashboard) ====\n")
                    log.flush()
                    code = subprocess.call([sys.executable, "-m", "lab"]
                                           + (["--config", self.config_path] if self.config_path else [])
                                           + ["monthly"], cwd=ROOT,
                                           stdout=log, stderr=subprocess.STDOUT,
                                           env={**__import__("os").environ, "PYTHONUTF8": "1"})
                _run_state.update(code=code)
            finally:
                _run_state.update(running=False,
                                  finished=datetime.now().isoformat(timespec="seconds"))
                _run_lock.release()

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}


def make_handler(api: API):
    routes_get = {"/api/config": api.config, "/api/overview": api.overview,
                  "/api/sessions": api.sessions, "/api/forecasts": api.forecasts,
                  "/api/prices": api.prices, "/api/picks": api.picks, "/api/report": api.report,
                  "/api/inbox": api.inbox_list, "/api/prompts": api.prompt_texts,
                  "/api/log": api.log, "/api/update/check": api.update_check,
                  "/api/update/log": api.update_log,
                  "/api/investors": api.investors, "/api/investor": api.investor,
                  "/api/portfolios": api.portfolio_list, "/api/portfolio": api.portfolio,
                  "/api/search": api.search, "/api/quote": api.quote}
    routes_post = {"/api/paste": api.paste, "/api/run": api.run_now,
                   "/api/update/install": api.update_install,
                   "/api/investor/follow": api.investor_follow,
                   "/api/investor/active": api.investor_active,
                   "/api/investor/holdings": api.investor_holdings,
                   "/api/investor/holdings/delete": api.investor_holdings_delete,
                   "/api/portfolio/create": api.portfolio_create,
                   "/api/portfolio/update": api.portfolio_update,
                   "/api/portfolio/delete": api.portfolio_delete,
                   "/api/portfolio/tx": api.portfolio_tx,
                   "/api/portfolio/tx/delete": api.portfolio_tx_delete,
                   "/api/portfolio/from": api.portfolio_from}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False, default=str).encode()
            self.send_response(code)
            self.send_header("content-type", ctype)
            self.send_header("cache-control", "no-store")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[-1] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(),
                                      "text/html; charset=utf-8")
                if u.path.lstrip("/") in STATIC_FILES:  # app icon + manifest ("Add to Home screen")
                    name = u.path.lstrip("/")
                    return self._send(200, (STATIC / name).read_bytes(), STATIC_FILES[name])
                if u.path in routes_get:
                    return self._send(200, routes_get[u.path](q))
                self._send(404, {"error": "not found"})
            except Exception as e:
                self._send(500, {"error": str(e)})

        def do_POST(self):
            path = urlparse(self.path).path
            # حماية بسيطة: نقبل الطلبات من الصفحة نفسها فقط
            origin = self.headers.get("origin", "")
            if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin):
                return self._send(403, {"error": "forbidden origin"})
            try:
                n = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                if path in routes_post:
                    return self._send(200, routes_post[path](body))
                self._send(404, {"error": "not found"})
            except Exception as e:
                self._send(400, {"error": str(e)})
    return H


def _port_in_use(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def serve(cfg, port=8765, open_browser=True, config_path=None):
    url = f"http://127.0.0.1:{port}/"
    if _port_in_use(port):
        print(f"dashboard already running: {url}")
        if open_browser:
            webbrowser.open(url)
        return
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(API(cfg, config_path, port)))
    print(f"dashboard: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
