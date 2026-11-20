"""جلسة شهرية واحدة: كل وكيل يجيب من تطبيقه وتصل ردوده إلى inbox (ودعم اختياري لوكلاء API)."""
import json
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import prompts
from .parse import extract_json, validate
from .providers import ask


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def month_session(conn, lab, d: str) -> int:
    """جلسة واحدة لكل شهر؛ تُنشأ عند أول رد."""
    r = conn.execute("SELECT id FROM sessions WHERE substr(created_at,1,7)=?", (d[:7],)).fetchone()
    if r:
        return r["id"]
    cur = conn.execute(
        "INSERT INTO sessions (created_at, question, context, universe) VALUES (?,?,?,?)",
        (d, lab["question"].strip(), "", json.dumps(lab["universe"])))
    conn.commit()
    return cur.lastrowid


def has_response(conn, sid, agent):
    return conn.execute("SELECT 1 FROM responses WHERE session_id=? AND agent=?",
                        (sid, agent)).fetchone() is not None


def _store(conn, sid, agent, model, rnd, raw, obj, alloc, fcs):
    cur = conn.execute(
        "INSERT INTO responses (session_id, agent, model, round, raw_text, analysis, risks, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (sid, agent, model, rnd, raw, (obj or {}).get("analysis"),
         (obj or {}).get("what_would_prove_me_wrong"), _now()))
    rid = cur.lastrowid
    conn.executemany("INSERT INTO allocations (response_id, ticker, weight) VALUES (?,?,?)",
                     [(rid, t, w) for t, w in alloc.items()])
    conn.executemany(
        "INSERT INTO forecasts (response_id, kind, ticker_a, ticker_b, threshold, start_date,"
        " resolve_date, probability, rationale) VALUES (?,?,?,?,?,?,?,?,?)",
        [(rid, f["kind"], f["ticker_a"], f["ticker_b"], f["threshold"], f["start_date"],
          f["resolve_date"], f["probability"], f["rationale"]) for f in fcs])
    conn.commit()
    return rid


def record(conn, cfg, agent, raw, obj, d, log=print):
    lab = cfg["lab"]
    sid = month_session(conn, lab, d)
    if has_response(conn, sid, agent):
        log(f"  {agent}: already has a response for {d[:7]} — skipped")
        return False
    alloc, fcs, warns = validate(obj, lab["universe"], lab["horizons_days"], d)
    if len(fcs) < lab["min_forecasts_per_agent"]:
        warns.append(f"only {len(fcs)} valid forecasts (min {lab['min_forecasts_per_agent']})")
    _store(conn, sid, agent, cfg["agents"][agent]["model"], 1, raw, obj, alloc, fcs)
    log(f"  {agent} [{d}]: {len(fcs)} forecasts, alloc=" +
        ", ".join(f"{t} {w:.0%}" for t, w in sorted(alloc.items(), key=lambda x: -x[1])))
    for w in warns:
        log(f"      ! {w}")
    refresh_consensus(conn, sid)
    return True


def run_api_agents(conn, cfg, d=None, log=print):
    """يشغّل وكلاء الـ API (Gemini) مرة واحدة في الشهر."""
    d = d or date.today().isoformat()
    if all(a["provider"] == "manual" for a in cfg["agents"].values()):
        log("  no API agents (all replies arrive through inbox)")
        return
    day = int(cfg["lab"].get("session_day", 1))
    if date.fromisoformat(d).day < day:
        log(f"  API agents wait until day {day} of the month")
        return
    for name, a in cfg["agents"].items():
        if a["provider"] == "manual":
            continue
        sid = month_session(conn, cfg["lab"], d)
        if has_response(conn, sid, name):
            log(f"  {name}: already ran for {d[:7]}")
            continue
        prompt = prompts.user_prompt(cfg["lab"], d, name)
        try:
            raw = ask(a, prompts.SYSTEM, prompt)
            try:
                obj = extract_json(raw)
            except ValueError:
                raw = ask(a, prompts.SYSTEM, prompt + "\n\nReply with the JSON object only.")
                obj = extract_json(raw)
        except Exception as e:
            log(f"  {name}: FAILED — {e}")
            continue
        record(conn, cfg, name, raw, obj, d, log)


def _agent_from_filename(name: str, agents) -> str | None:
    low = name.lower()
    for a in agents:
        if low.startswith(a):
            return a
    return None


def ingest(conn, cfg, inbox: Path, log=print):
    """يقرأ ملفات inbox/*.txt|*.json|*.md: اسم الملف يبدأ بـ chatgpt أو claude."""
    inbox.mkdir(exist_ok=True)
    done_dir = inbox / "processed"
    bad_dir = inbox / "failed"
    files = sorted(p for p in inbox.iterdir()
                   if p.is_file() and p.suffix.lower() in (".txt", ".json", ".md")
                   and not p.name.startswith("."))
    if not files:
        log("  inbox empty")
    for p in files:
        agent = _agent_from_filename(p.name, cfg["agents"])
        try:
            if not agent:
                raise ValueError("filename must start with an agent name, e.g. chatgpt-2026-12.txt")
            raw = p.read_text(encoding="utf-8-sig")
            obj = extract_json(raw)
            # التاريخ: ما كتبه الوكيل إن كان معقولاً، وإلا تاريخ تعديل الملف
            file_day = date.fromtimestamp(p.stat().st_mtime)
            d = file_day
            try:
                claimed = date.fromisoformat(str(obj.get("session_date", ""))[:10])
                if 0 <= (file_day - claimed).days <= 7:
                    d = claimed
            except ValueError:
                pass
            record(conn, cfg, agent, raw, obj, d.isoformat(), log)
            dest = done_dir
        except Exception as e:
            log(f"  {p.name}: FAILED — {e}")
            dest = bad_dir
            dest.mkdir(exist_ok=True)
            (dest / (p.name + ".error")).write_text(str(e), encoding="utf-8")
        dest.mkdir(exist_ok=True)
        target = dest / p.name
        if target.exists():
            target = dest / f"{p.stem}-{int(p.stat().st_mtime)}{p.suffix}"
        shutil.move(str(p), str(target))


def refresh_consensus(conn, sid):
    """الإجماع = متوسط احتمالات الأسئلة المتطابقة (≥ وكيلين) + متوسط الأوزان. يُعاد حسابه عند كل رد."""
    old = conn.execute("SELECT id FROM responses WHERE session_id=? AND agent='consensus'",
                       (sid,)).fetchone()
    if old:
        conn.execute("DELETE FROM forecasts WHERE response_id=?", (old["id"],))
        conn.execute("DELETE FROM allocations WHERE response_id=?", (old["id"],))
        conn.execute("DELETE FROM responses WHERE id=?", (old["id"],))
    resp = conn.execute("SELECT id FROM responses WHERE session_id=? AND agent!='consensus'",
                        (sid,)).fetchall()
    ids = [r["id"] for r in resp]
    if len(ids) < 2:
        conn.commit()
        return
    q = ",".join("?" * len(ids))
    groups = defaultdict(list)
    for f in conn.execute(f"SELECT * FROM forecasts WHERE response_id IN ({q})", ids):
        h = (date.fromisoformat(f["resolve_date"]) - date.fromisoformat(f["start_date"])).days
        groups[(f["kind"], f["ticker_a"], f["ticker_b"], f["threshold"], h)].append(
            (f["start_date"], f["probability"]))
    fcs = []
    for (kind, a, b, thr, h), v in groups.items():
        if len(v) < 2:
            continue
        start = max(s for s, _ in v)  # آخر تاريخ بدء: لا يستفيد الإجماع من معلومة لاحقة
        fcs.append(dict(kind=kind, ticker_a=a, ticker_b=b, threshold=thr, start_date=start,
                        resolve_date=(date.fromisoformat(start) + timedelta(days=h)).isoformat(),
                        probability=sum(p for _, p in v) / len(v),
                        rationale=f"mean of {len(v)} agents"))
    alloc = defaultdict(float)
    for a in conn.execute(f"SELECT ticker, weight FROM allocations WHERE response_id IN ({q})", ids):
        alloc[a["ticker"]] += a["weight"] / len(ids)
    _store(conn, sid, "consensus", "mean", 1, "", {"analysis": f"mean of {len(ids)} agents"},
           dict(alloc), fcs)
