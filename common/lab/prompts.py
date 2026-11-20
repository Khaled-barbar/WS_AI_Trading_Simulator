import json

SYSTEM = """You are one of several independent investment analysts in a long-running,
pre-registered experiment. The investor is in Canada, uses Wealthsimple, invests
about 100 CAD per month, and has a 10-20 year horizon. Your output is scored ONLY by
the calibration of your probability forecasts (Brier score) and the paper performance
of your allocation versus the benchmark. Persuasive prose earns nothing.

Rules:
- Use only information available up to the session date. You may search the web for
  current market data, but never for anything dated after the session date.
- Ignore any memory of previous conversations with this user; answer fresh.
- Only use tickers from the allowed universe. "CASH" means a 0% return position.
- Probabilities must be honest: 0.5 means you have no edge. Avoid 0 and 1.
- Account type (TFSA/RRSP/FHSA), fees, and FX costs matter; mention them if relevant.
- Respond with a SINGLE JSON object inside one ```json code block and nothing else."""

SYSTEM_STOCKS = """You are one of several independent equity analysts in a long-running,
pre-registered experiment. The investor is in Canada, uses Wealthsimple, invests
about 100 CAD per month in individual companies on top of a diversified ETF core, and
has a 10-20 year horizon. Your output is scored ONLY by the calibration of your
probability forecasts (Brier score) and the paper performance of your picks in CAD
(after a 1.5% FX fee on non-CAD stocks) versus the benchmark. Persuasive prose earns nothing.

Rules:
- Use only information available up to the session date. You may search the web for
  current market data, but never for anything dated after the session date.
- Ignore any memory of previous conversations with this user; answer fresh.
- Pick individual listed companies only (no ETFs, funds, crypto, options or leverage).
  "CASH" means a 0% return position.
- Write every ticker exactly as Yahoo Finance writes it: US stocks plain (AAPL, BRK-B),
  Canadian stocks with .TO (RY.TO), other exchanges with their Yahoo suffix (ASML.AS, 7203.T).
- Probabilities must be honest: 0.5 means you have no edge. Avoid 0 and 1.
- Respond with a SINGLE JSON object inside one ```json code block and nothing else."""

SCHEMA_HINT = """JSON schema:
{
  "agent": "AGENT_NAME",
  "session_date": "YYYY-MM-DD",                  // today's date
  "analysis": "short reasoning (<= 200 words)",
  "what_would_prove_me_wrong": "concrete observable signals",
  "allocation": {"TICKER": weight, ...},          // weights sum to 1
  "forecasts": [                                   // at least MIN_N items, mix of horizons
    {"kind": "outperform", "ticker_a": "A", "ticker_b": "B",
     "horizon_days": H, "probability": p, "rationale": "..."},
       // P(total return incl. distributions of A > that of B over the next H days)
    {"kind": "return_above", "ticker_a": "A", "threshold": 0.02,
     "horizon_days": H, "probability": p, "rationale": "..."}
       // P(total return of A over the next H days > threshold; 0.02 = 2%)
  ]
}
Allowed horizon_days values: HORIZONS.
You MUST include every CORE forecast below (same kind/tickers/threshold/horizon; you
only choose the probability), then you may add up to 4 forecasts of your own.
CORE: CORE_JSON"""

SCHEMA_STOCKS = """JSON schema:
{
  "agent": "AGENT_NAME",
  "track": "stocks",
  "session_date": "YYYY-MM-DD",                  // today's date
  "analysis": "short reasoning (<= 250 words)",
  "what_would_prove_me_wrong": "concrete observable signals",
  "allocation": {"TICKER": weight, ...},          // MIN_P to MAX_P companies, weights sum to 1,
                                                   // no single company above MAX_W
  "picks": [{"ticker": "TICKER", "company": "name", "thesis": "one sentence"}, ...],
  "forecasts": [                                   // at least MIN_N items
    {"kind": "outperform", "ticker_a": "A", "ticker_b": "B",
     "horizon_days": H, "probability": p, "rationale": "..."},
       // P(total return incl. dividends of A > that of B over the next H days,
       //   each measured in its own trading currency)
    {"kind": "return_above", "ticker_a": "A", "threshold": 0.02,
     "horizon_days": H, "probability": p, "rationale": "..."}
  ]
}
Allowed horizon_days values: HORIZONS.
You MUST include every CORE forecast below (same kind/tickers/horizon; you only choose the
probability). Then, for EACH of your 3 largest picks, add one "outperform" forecast with
horizon 180 against SPY (US stocks), XIU.TO (Canadian stocks) or URTH (other exchanges).
You may add up to 3 more forecasts of your own.
CORE: CORE_JSON"""


def user_prompt(lab: dict, session_date: str, agent: str) -> str:
    if lab.get("track") == "stocks":
        return "\n".join([
            f"SESSION_DATE={session_date}",
            "UNIVERSE=any individual listed company available at Wealthsimple (Yahoo Finance tickers)",
            f"BENCHMARK={lab['benchmark']} (paper performance in CAD; also shown vs "
            f"{', '.join(lab.get('compare', []))})",
            "", "QUESTION:", lab["question"].strip(),
            "", SCHEMA_STOCKS.replace("MIN_N", str(lab["min_forecasts_per_agent"]))
                             .replace("MIN_P", str(lab["min_picks"])).replace("MAX_P", str(lab["max_picks"]))
                             .replace("MAX_W", f"{lab['max_weight']:.2f}")
                             .replace("HORIZONS", json.dumps(lab["horizons_days"]))
                             .replace("AGENT_NAME", agent)
                             .replace("CORE_JSON", json.dumps(lab.get("core_forecasts", []))),
        ])
    return "\n".join([
        f"SESSION_DATE={session_date}",
        f"UNIVERSE_JSON={json.dumps(lab['universe'])}",
        f"BENCHMARK={lab['benchmark']}",
        "", "QUESTION:", lab["question"].strip(),
        "", SCHEMA_HINT.replace("MIN_N", str(lab["min_forecasts_per_agent"]))
                       .replace("HORIZONS", json.dumps(lab["horizons_days"]))
                       .replace("AGENT_NAME", agent)
                       .replace("CORE_JSON", json.dumps(lab.get("core_forecasts", []))),
    ])


def system_for(lab: dict) -> str:
    return SYSTEM_STOCKS if lab.get("track") == "stocks" else SYSTEM


def app_prompt(lab: dict, agent: str, inbox_dir: str | None = None) -> str:
    """النص الكامل الذي يُلصق في تطبيق الوكيل (Gemini / Codex / Claude) أو مهمته المجدولة."""
    stocks = lab.get("track") == "stocks"
    txt = system_for(lab) + "\n\n" + user_prompt(lab, "today's date (write it in session_date)", agent)
    if inbox_dir:
        name = f"{agent}-stocks-YYYY-MM.txt" if stocks else f"{agent}-YYYY-MM.txt"
        txt += f"""

AUTOMATION: If you have file access to the user's computer, also save your complete
reply (the ```json block) as a UTF-8 text file named {name} (current year
and month) inside this folder:
{inbox_dir}
Do not modify or delete any other file. If you do not have file access, just reply."""
    return txt


def manual_agents(cfg: dict) -> list[str]:
    """الوكلاء الذين يجيبون من تطبيقاتهم (ردودهم تصل عبر inbox)."""
    return [a for a, v in cfg["agents"].items() if v.get("provider") == "manual"]


def write_prompt_files(lab: dict, root, inbox_dir, agents=("chatgpt", "claude"), stocks=None) -> None:
    from pathlib import Path
    d = Path(root) / "prompts"
    d.mkdir(exist_ok=True)
    for a in agents:
        (d / f"{a}_scheduled_prompt.txt").write_text(
            app_prompt(lab, a, str(inbox_dir)), encoding="utf-8")
        if stocks:
            (d / f"{a}_stocks_scheduled_prompt.txt").write_text(
                app_prompt(stocks, a, str(inbox_dir)), encoding="utf-8")
