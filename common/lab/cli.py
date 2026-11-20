import argparse
import sys
import tomllib
from datetime import date, timedelta
from pathlib import Path

from . import prices, prompts, scoring, session
from .db import ROOT, connect


def app_version() -> str:
    p = ROOT / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else "dev"


def load_cfg(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="python -m lab")
    p.add_argument("--config", default=str(ROOT / "config.toml"))
    p.add_argument("--today", help="override today's date (testing only)")
    p.add_argument("--version", action="version", version=f"Agents Lab {app_version()}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("monthly", help="Gemini (once a month) + ingest inbox + prices + resolve + report")
    dsh = sub.add_parser("dashboard", help="open the local monitoring dashboard")
    dsh.add_argument("--port", type=int, default=8765)
    dsh.add_argument("--no-browser", action="store_true")
    sub.add_parser("run-api", help="run API agents (Gemini) for this month if not done")
    sub.add_parser("check-key", help="verify the Gemini key can be read (does not print it)")
    sub.add_parser("list-models", help="list Gemini models available to your key")
    sub.add_parser("ingest", help="import pasted Gemini/ChatGPT/Claude replies from inbox/")
    pp = sub.add_parser("print-prompt", help="print the prompt for an app agent (gemini, chatgpt, claude)")
    pp.add_argument("agent")
    pp.add_argument("--out")
    f = sub.add_parser("fetch-prices", help="download adjusted prices from Yahoo")
    f.add_argument("--start", default="2025-01-01")
    sub.add_parser("import-prices", help="import CSV: ticker,date,close").add_argument("path")
    sub.add_parser("resolve", help="resolve due forecasts")
    rep = sub.add_parser("report", help="write the Markdown report")
    rep.add_argument("--out", default=str(ROOT / "report.md"))
    sh = sub.add_parser("show", help="show responses of a month, e.g. 2026-12")
    sh.add_argument("month")

    a = p.parse_args(argv)
    cfg = load_cfg(a.config)
    lab = cfg["lab"]
    conn = connect(lab["db_path"])
    today = a.today or date.today().isoformat()
    inbox = ROOT / lab.get("inbox", "inbox")

    def fetch(start):
        tickers = sorted(set(scoring.tickers_needed(conn, lab["universe"])) | {lab["benchmark"]})
        for t in tickers:
            try:
                print(f"  {t}: {prices.fetch_yahoo(conn, t, start, today)} rows")
            except Exception as e:
                print(f"  {t}: FAILED {e}", file=sys.stderr)

    def write_report(out):
        Path(out).write_text(scoring.report(conn, lab, today), encoding="utf-8")
        print(f"report written: {out}")

    if a.cmd in ("monthly", "dashboard"):
        prompts.write_prompt_files(lab, ROOT, inbox, prompts.manual_agents(cfg))
    if a.cmd == "dashboard":
        from . import dashboard
        dashboard.serve(cfg, a.port, not a.no_browser, a.config)
    elif a.cmd == "monthly":
        print("[1/4] API agents"); session.run_api_agents(conn, cfg, today)
        print("[2/4] inbox");      session.ingest(conn, cfg, inbox)
        first = conn.execute("SELECT MIN(start_date) FROM forecasts").fetchone()[0] or today
        year_ago = (date.fromisoformat(today) - timedelta(days=400)).isoformat()
        print("[3/4] prices");     fetch(min(first, year_ago))
        print("[4/4] resolve");    scoring.resolve(conn, today)
        write_report(ROOT / "report.md")
    elif a.cmd == "run-api":
        session.run_api_agents(conn, cfg, today)
    elif a.cmd == "check-key":
        from .providers import get_key
        if not any(ag["provider"] == "gemini" for ag in cfg["agents"].values()):
            print("no API agents configured (all agents reply through inbox) — no key needed")
        for n, ag in cfg["agents"].items():
            if ag["provider"] == "gemini":
                try:
                    k = get_key(ag)
                    print(f"{n}: key found ({len(k)} characters)")
                except RuntimeError as e:
                    print(f"{n}: {e}", file=sys.stderr)
                    sys.exit(1)
    elif a.cmd == "list-models":
        from .providers import get_key, list_gemini_models, pick_gemini_model
        key = get_key(cfg["agents"]["gemini"])
        models = list_gemini_models(key)
        print("\n".join(models))
        print(f"\nconfigured: {cfg['agents']['gemini']['model']}   suggested: {pick_gemini_model(models)}")
    elif a.cmd == "ingest":
        session.ingest(conn, cfg, inbox)
    elif a.cmd == "print-prompt":
        if a.agent not in cfg["agents"]:
            sys.exit(f"unknown agent '{a.agent}' (configured: {', '.join(cfg['agents'])})")
        txt = prompts.app_prompt(lab, a.agent, str(inbox))
        if a.out:
            Path(a.out).write_text(txt, encoding="utf-8")
            print(f"written {a.out}")
        else:
            print(txt)
    elif a.cmd == "fetch-prices":
        fetch(a.start)
    elif a.cmd == "import-prices":
        print(f"imported {prices.import_csv(conn, a.path)} rows")
    elif a.cmd == "resolve":
        scoring.resolve(conn, today)
    elif a.cmd == "report":
        write_report(a.out)
    elif a.cmd == "show":
        for r in conn.execute(
                "SELECT r.* FROM responses r JOIN sessions s ON s.id=r.session_id "
                "WHERE substr(s.created_at,1,7)=? ORDER BY r.agent", (a.month,)):
            print(f"\n=== {r['agent']} ({r['model']}) ===")
            print(r["analysis"])
            print("Wrong if:", r["risks"])
            for f in conn.execute("SELECT * FROM forecasts WHERE response_id=?", (r["id"],)):
                q = (f"{f['ticker_a']} > {f['ticker_b']}" if f["kind"] == "outperform"
                     else f"{f['ticker_a']} > {f['threshold']:+.1%}")
                st = ("pending" if f["outcome"] is None
                      else f"outcome={f['outcome']} brier={f['brier']:.3f}")
                print(f"  P({q} by {f['resolve_date']}) = {f['probability']:.2f}  [{st}]")
