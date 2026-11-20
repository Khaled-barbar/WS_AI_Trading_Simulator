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
from urllib.parse import urlparse

from . import prompts, scoring, updates
from .db import ROOT, connect

STATIC = Path(__file__).resolve().parent / "static"
_run_lock = threading.Lock()
_run_state = {"running": False, "started": None, "finished": None, "code": None}


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


class API:
    def __init__(self, cfg, config_path=None, port=8765):
        self.cfg = cfg
        self.port = port
        self.config_path = config_path
        self.lab = cfg["lab"]
        self.inbox = ROOT / self.lab.get("inbox", "inbox")

    def conn(self):
        return connect(self.lab["db_path"])

    # ---------- GET ----------
    def config(self):
        return {"agents": {k: {"provider": v["provider"], "model": v["model"]}
                           for k, v in self.cfg["agents"].items()},
                **{k: self.lab[k] for k in ("benchmark", "universe", "horizons_days",
                                            "min_forecasts_per_agent", "question")},
                "core_forecasts": self.lab.get("core_forecasts", []),
                "root": str(ROOT), "inbox": str(self.inbox), "platform": sys.platform,
                "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip()
                if (ROOT / "VERSION").is_file() else "dev"}

    def overview(self):
        c = self.conn()
        today = date.today().isoformat()
        month = today[:7]
        stats = c.execute(
            "SELECT COUNT(*) n, SUM(outcome IS NOT NULL) resolved, "
            "SUM(outcome IS NULL AND resolve_date<=?) overdue, "
            "MIN(CASE WHEN outcome IS NULL AND resolve_date>? THEN resolve_date END) next_due "
            "FROM forecasts", (today, today)).fetchone()
        this_month = {r["agent"]: r["created_at"] for r in c.execute(
            "SELECT r.agent, r.created_at FROM responses r JOIN sessions s ON s.id=r.session_id "
            "WHERE substr(s.created_at,1,7)=?", (month,))}
        last_price = c.execute("SELECT MAX(date) FROM prices").fetchone()[0]
        return {"today": today, "month": month,
                "sessions": c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                "responses": c.execute("SELECT COUNT(*) FROM responses WHERE agent!='consensus'"
                                       ).fetchone()[0],
                "forecasts": dict(stats), "this_month": this_month,
                "agents": list(self.cfg["agents"]), "last_price": last_price,
                "inbox_pending": len(self._files(self.inbox)),
                "inbox_failed": len([f for f in self._files(self.inbox / "failed")
                                     if not f["name"].endswith(".error")]),
                "run": _run_state, "scoreboard": self.scoreboard(c)}

    def scoreboard(self, c=None):
        c = c or self.conn()
        core = self.lab.get("core_forecasts", [])
        out = {}
        for x in c.execute("SELECT r.agent, f.* FROM forecasts f JOIN responses r "
                           "ON r.id=f.response_id"):
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

    def sessions(self):
        c = self.conn()
        out = []
        for s in _rows(c.execute("SELECT * FROM sessions ORDER BY created_at DESC")):
            resp = _rows(c.execute("SELECT * FROM responses WHERE session_id=? ORDER BY agent",
                                   (s["id"],)))
            for r in resp:
                r["allocation"] = _rows(c.execute(
                    "SELECT ticker, weight FROM allocations WHERE response_id=? "
                    "ORDER BY weight DESC", (r["id"],)))
                r["forecasts"] = _rows(c.execute(
                    "SELECT * FROM forecasts WHERE response_id=? ORDER BY resolve_date",
                    (r["id"],)))
            s["responses"] = resp
            s["universe"] = json.loads(s["universe"])
            out.append(s)
        return out

    def forecasts(self):
        c = self.conn()
        core = self.lab.get("core_forecasts", [])
        rows = _rows(c.execute(
            "SELECT f.*, r.agent, substr(s.created_at,1,7) AS month FROM forecasts f "
            "JOIN responses r ON r.id=f.response_id JOIN sessions s ON s.id=r.session_id "
            "ORDER BY f.resolve_date, r.agent"))
        from .prices import total_return
        for f in rows:
            f["core"] = scoring._is_core(f, core)
            end = min(f["resolve_date"], date.today().isoformat())
            f["ret_a"] = total_return(c, f["ticker_a"], f["start_date"], end)
            f["ret_b"] = (total_return(c, f["ticker_b"], f["start_date"], end)
                          if f["ticker_b"] else None)
        return rows

    def prices(self):
        c = self.conn()
        since = (date.today() - timedelta(days=400)).isoformat()
        out = {}
        for t in [x for x in self.lab["universe"] if x != "CASH"]:
            pts = [[r["date"], r["close"]] for r in c.execute(
                "SELECT date, close FROM prices WHERE ticker=? AND date>=? ORDER BY date",
                (t, since))]
            out[t] = pts
        return out

    def report(self):
        return {"markdown": scoring.report(self.conn(), self.lab, date.today().isoformat())}

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

    def inbox_list(self):
        failed = self._files(self.inbox / "failed")
        for f in failed:
            err = self.inbox / "failed" / (f["name"] + ".error")
            f["error"] = err.read_text(encoding="utf-8") if err.exists() else None
        return {"pending": self._files(self.inbox),
                "processed": self._files(self.inbox / "processed")[:50],
                "failed": [f for f in failed if not f["name"].endswith(".error")]}

    def prompt_texts(self):
        return {a: prompts.app_prompt(self.lab, a, str(self.inbox))
                for a in prompts.manual_agents(self.cfg)}

    def log(self):
        p = ROOT / "logs" / "run.log"
        if not p.exists():
            return {"text": ""}
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"text": "\n".join(lines[-300:])}

    # ---------- POST ----------
    def paste(self, body):
        agent = body.get("agent")
        text = (body.get("text") or "").strip()
        allowed = prompts.manual_agents(self.cfg)
        if agent not in allowed:
            raise ValueError("agent must be one of: " + ", ".join(allowed))
        if "{" not in text:
            raise ValueError("the pasted text contains no JSON")
        self.inbox.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        p = self.inbox / f"{agent}-{stamp}.txt"
        p.write_text(text, encoding="utf-8")
        # استيراد فوري
        from . import session
        msgs = []
        session.ingest(self.conn(), self.cfg, self.inbox, log=msgs.append)
        return {"saved": p.name, "log": msgs}

    def update_check(self):
        return updates.check(self.cfg)

    def update_log(self):
        return updates.log_tail()

    def update_install(self):
        if _run_state["running"]:
            raise ValueError("a run is in progress — try again when it finishes")
        info = updates.check(self.cfg)
        if info["error"]:
            raise ValueError(info["error"])
        if not info["newer"]:
            return {"started": False, "reason": "already up to date", **info}
        return {**updates.start(self.cfg, self.port), **info}

    def run_now(self):
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
                  "/api/prices": api.prices, "/api/report": api.report,
                  "/api/inbox": api.inbox_list, "/api/prompts": api.prompt_texts,
                  "/api/log": api.log, "/api/update/check": api.update_check,
                  "/api/update/log": api.update_log}

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
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(),
                                      "text/html; charset=utf-8")
                if path in routes_get:
                    return self._send(200, routes_get[path]())
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
                if path == "/api/paste":
                    return self._send(200, api.paste(body))
                if path == "/api/run":
                    return self._send(200, api.run_now())
                if path == "/api/update/install":
                    return self._send(200, api.update_install())
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
