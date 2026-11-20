import json
import re
from datetime import date, timedelta


def extract_json(text: str) -> dict:
    """يستخرج أول كائن JSON من رد النموذج (يتحمل ``` والنص الزائد)."""
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in response")
    depth, in_str, esc = 0, False, False
    for i, ch in enumerate(text[start:], start):
        if in_str:
            esc = (ch == "\\") and not esc
            if ch == '"' and not esc:
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("unbalanced JSON")


def validate(obj: dict, universe, horizons: list[int], start: str):
    """يُرجع (allocation, forecasts, warnings) بعد التنظيف.
    universe=None (مسار الشركات): أي رمز بصيغة Yahoo؛ التحقق من وجوده يتم عند الحفظ."""
    from .prices import TICKER_RE
    if universe is None:
        ok = lambda t: t == "CASH" or bool(TICKER_RE.match(str(t)))
    else:
        ok = lambda t: t in universe
    warns = []
    alloc = {}
    for t, w in (obj.get("allocation") or {}).items():
        t = str(t).strip().upper() if universe is None else t
        if not ok(t):
            warns.append(f"allocation ticker {t} not in universe — dropped")
            continue
        if float(w) > 0:
            alloc[t] = float(w)
    total = sum(alloc.values())
    if total <= 0:
        warns.append("empty allocation")
    else:
        if abs(total - 1) > 0.01:
            warns.append(f"weights summed to {total:.3f} — normalized")
        alloc = {t: w / total for t, w in alloc.items()}

    fcs = []
    # نقطة البداية = إغلاق أول يوم تداول بعد يوم الرد، فلا يستفيد أي وكيل من حركة يوم الرد
    d0 = date.fromisoformat(start) + timedelta(days=1)
    start = d0.isoformat()
    for f in obj.get("forecasts") or []:
        try:
            kind = f["kind"]
            a = f["ticker_a"]
            if universe is None:
                a = str(a).strip().upper()
            h = int(f["horizon_days"])
            p = min(max(float(f["probability"]), 0.01), 0.99)
            if kind not in ("outperform", "return_above"):
                raise ValueError(f"bad kind {kind}")
            if not ok(a) or a == "CASH":
                raise ValueError(f"bad ticker_a {a}")
            b = f.get("ticker_b") if kind == "outperform" else None
            if b and universe is None:
                b = str(b).strip().upper()
            if kind == "outperform" and (not b or not ok(b) or b == a):
                raise ValueError(f"bad ticker_b {b}")
            thr = float(f.get("threshold", 0.0)) if kind == "return_above" else None
            if h not in horizons:
                warns.append(f"horizon {h} not allowed — snapped")
                h = min(horizons, key=lambda x: abs(x - h))
            fcs.append(dict(kind=kind, ticker_a=a, ticker_b=b, threshold=thr,
                            start_date=start,
                            resolve_date=(d0 + timedelta(days=h)).isoformat(),
                            probability=p, rationale=f.get("rationale", "")))
        except (KeyError, ValueError, TypeError) as e:
            warns.append(f"forecast dropped: {e}")
    return alloc, fcs, warns
