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


def user_prompt(lab: dict, session_date: str, agent: str) -> str:
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


def app_prompt(lab: dict, agent: str, inbox_dir: str | None = None) -> str:
    """النص الكامل الذي يُلصق في تطبيق الوكيل (Gemini / Codex / Claude) أو مهمته المجدولة."""
    txt = SYSTEM + "\n\n" + user_prompt(lab, "today's date (write it in session_date)", agent)
    if inbox_dir:
        txt += f"""

AUTOMATION: If you have file access to the user's computer, also save your complete
reply (the ```json block) as a UTF-8 text file named {agent}-YYYY-MM.txt (current year
and month) inside this folder:
{inbox_dir}
Do not modify or delete any other file. If you do not have file access, just reply."""
    return txt


def manual_agents(cfg: dict) -> list[str]:
    """الوكلاء الذين يجيبون من تطبيقاتهم (ردودهم تصل عبر inbox)."""
    return [a for a, v in cfg["agents"].items() if v.get("provider") == "manual"]


def write_prompt_files(lab: dict, root, inbox_dir, agents=("chatgpt", "claude")) -> None:
    from pathlib import Path
    d = Path(root) / "prompts"
    d.mkdir(exist_ok=True)
    for a in agents:
        (d / f"{a}_scheduled_prompt.txt").write_text(
            app_prompt(lab, a, str(inbox_dir)), encoding="utf-8")
