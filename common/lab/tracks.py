"""مساران للتجربة بنفس المنطق (نفس الوكلاء، نفس صيغة JSON، Brier + محفظة ورقية):
  etf    — توزيع 100$ على قائمة الصناديق المسموحة (الإعداد [lab])
  stocks — اختيار الشركات الأجدر بالاستثمار ونسبة كل منها (الإعداد [stocks])"""

STOCKS_DEFAULTS = {
    "enabled": True,
    "benchmark": "XEQT.TO",
    "compare": ["SPY", "XIU.TO"],
    "min_picks": 5,
    "max_picks": 15,
    "max_weight": 0.25,
    "fx_fee": 0.015,
    "horizons_days": [30, 90, 180],
    "min_forecasts_per_agent": 9,
    "question": (
        "Today the investor puts 100 CAD into INDIVIDUAL COMPANIES (no ETFs or funds) inside a\n"
        "Wealthsimple TFSA, horizon 15 years, in addition to a diversified ETF core. Which listed\n"
        "companies (TSX, NYSE, NASDAQ or other exchanges available at Wealthsimple) are most worth\n"
        "owning now, and what percentage of the 100 CAD should go to each? Buying a non-CAD stock\n"
        "from the CAD account costs a 1.5% FX fee each way."),
    "core_forecasts": [
        {"kind": "outperform", "ticker_a": "NVDA", "ticker_b": "SPY", "horizon_days": 90},
        {"kind": "outperform", "ticker_a": "MSFT", "ticker_b": "SPY", "horizon_days": 90},
        {"kind": "outperform", "ticker_a": "AAPL", "ticker_b": "SPY", "horizon_days": 180},
        {"kind": "outperform", "ticker_a": "BRK-B", "ticker_b": "SPY", "horizon_days": 180},
        {"kind": "outperform", "ticker_a": "RY.TO", "ticker_b": "XIU.TO", "horizon_days": 90},
        {"kind": "outperform", "ticker_a": "SHOP.TO", "ticker_b": "XIU.TO", "horizon_days": 180},
    ],
}


def track_cfg(cfg: dict, track: str = "etf") -> dict:
    lab = cfg["lab"]
    if track == "etf":
        return {**lab, "track": "etf", "fx_fee": 0.0}
    if track != "stocks":
        raise ValueError(f"unknown track {track}")
    s = {**STOCKS_DEFAULTS, **cfg.get("stocks", {})}
    return {**s, "track": "stocks", "universe": None, "db_path": lab["db_path"],
            "inbox": lab.get("inbox", "inbox"), "session_day": lab.get("session_day", 20)}


def tracks(cfg: dict) -> list[str]:
    on = {**STOCKS_DEFAULTS, **cfg.get("stocks", {})}.get("enabled", True)
    return ["etf", "stocks"] if on else ["etf"]


def track_from(name: str, obj: dict | None) -> str:
    """المسار من حقل track في JSON، أو من اسم الملف (claude-stocks-2026-12.txt)."""
    t = str((obj or {}).get("track", "")).lower()
    if t in ("etf", "stocks"):
        return t
    return "stocks" if "stock" in name.lower() else "etf"
