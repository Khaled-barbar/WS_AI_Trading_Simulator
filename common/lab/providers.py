"""استدعاء Gemini عبر HTTP مباشرة + قراءة المفتاح من Windows Credential Manager."""
import json
import os
import random
import sys
import urllib.error
import urllib.request

TIMEOUT = 180


def _post(url: str, headers: dict, body: dict) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"content-type": "application/json", **headers},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())


def read_windows_credential(target: str) -> str | None:
    """يقرأ Generic Credential من Windows Credential Manager (بدون مكتبات خارجية)."""
    if sys.platform != "win32" or not target:
        return None
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
                    ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
                    ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD),
                    ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    pcred = ctypes.POINTER(CREDENTIAL)()
    CRED_TYPE_GENERIC = 1
    if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        return None
    try:
        c = pcred.contents
        blob = bytes(c.CredentialBlob[:c.CredentialBlobSize])
        try:
            return blob.decode("utf-16-le").strip()
        except UnicodeDecodeError:
            return blob.decode("utf-8").strip()
    finally:
        advapi.CredFree(pcred)


def read_linux_secret(target: str) -> str | None:
    """Linux: GNOME Keyring / KWallet عبر secret-tool، ثم ملف ~/.config/agents-lab/<name>_key."""
    if sys.platform == "win32" or not target:
        return None
    import shutil
    import subprocess
    if shutil.which("secret-tool"):
        try:
            out = subprocess.run(["secret-tool", "lookup", "service", target],
                                 capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    from pathlib import Path
    f = Path.home() / ".config" / "agents-lab" / (target.split("/")[-1] + "_key")
    if f.is_file():
        return f.read_text(encoding="utf-8").strip()
    return None


def get_key(agent_cfg: dict) -> str:
    target = agent_cfg.get("cred_target", "")
    key = (os.environ.get(agent_cfg.get("key_env", ""), "")
           or read_windows_credential(target) or read_linux_secret(target) or "")
    if not key:
        if sys.platform == "win32":
            how = f"add a Generic credential named '{target}' in Windows Credential Manager"
        else:
            how = (f"run: secret-tool store --label='Agents Lab Gemini' service {target}"
                   f"  (or put the key in ~/.config/agents-lab/{target.split('/')[-1]}_key)")
        raise RuntimeError(f"no API key: {how}, or set {agent_cfg.get('key_env')}")
    return key


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _http_error_text(e: urllib.error.HTTPError) -> str:
    try:
        msg = json.loads(e.read()).get("error", {}).get("message", "")
    except Exception:
        msg = ""
    return f"HTTP {e.code}: {msg or e.reason}"


def list_gemini_models(key: str) -> list[str]:
    """النماذج المتاحة لهذا المفتاح والتي تدعم generateContent."""
    req = urllib.request.Request(f"{GEMINI_BASE}/models?pageSize=1000",
                                 headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return sorted(m["name"].removeprefix("models/") for m in data.get("models", [])
                  if "generateContent" in m.get("supportedGenerationMethods", []))


def pick_gemini_model(models: list[str]) -> str | None:
    """أحدث نموذج Flash: المستقر أولاً، ثم preview، ثم lite."""
    import re
    best = None
    for m in models:
        g = re.fullmatch(r"gemini-(\d+(?:\.\d+)?)-flash(-lite)?(-preview(?:-[\w-]+)?)?", m)
        if not g:
            continue
        rank = (g.group(3) is None, g.group(2) is None, float(g.group(1)))
        if best is None or rank > best[0]:
            best = (rank, m)
    return best[1] if best else None


def _gemini_generate(model, key, body, use_search):
    url = f"{GEMINI_BASE}/models/{model}:generateContent"
    headers = {"x-goog-api-key": key}
    if use_search:
        try:
            return _post(url, headers, {**body, "tools": [{"google_search": {}}]})
        except urllib.error.HTTPError as e:
            if e.code not in (400, 403, 429):
                raise
            print(f"  (google_search refused: {_http_error_text(e)}; retrying without it)")
    return _post(url, headers, body)


def call_gemini(cfg, key, system, prompt):
    body = {"systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    model = cfg["model"]
    if model == "auto":
        model = pick_gemini_model(list_gemini_models(key))
        if not model:
            raise RuntimeError("no Gemini Flash model available for this key (run: lab list-models)")
    try:
        data = _gemini_generate(model, key, body, cfg.get("google_search"))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise RuntimeError(f"Gemini {model}: {_http_error_text(e)}") from None
        # النموذج أُوقف: اختر أحدث Flash متاح وأكمل
        new = pick_gemini_model(list_gemini_models(key))
        if not new or new == model:
            raise RuntimeError(f"Gemini model '{model}' not found (run: lab list-models)") from None
        print(f"  (model '{model}' not found; using '{new}' — set model = \"{new}\" in config.toml)")
        model = new
        try:
            data = _gemini_generate(model, key, body, cfg.get("google_search"))
        except urllib.error.HTTPError as e2:
            raise RuntimeError(f"Gemini {model}: {_http_error_text(e2)}") from None
    cfg["model"] = model  # يُسجَّل في قاعدة البيانات النموذج الذي استُخدم فعلاً
    cand = data.get("candidates") or []
    if not cand or "content" not in cand[0]:
        raise RuntimeError(f"Gemini returned no content: {json.dumps(data)[:300]}")
    return "".join(p.get("text", "") for p in cand[0]["content"].get("parts", []))


def call_mock(cfg, key, system, prompt):
    """وكيل وهمي للاختبار بدون مفاتيح API."""
    rnd = random.Random(hash((cfg["model"], prompt[-200:])))
    uni = json.loads(prompt.split("UNIVERSE_JSON=")[1].split("\n")[0])
    picks = [t for t in uni if t != "CASH"][:3]
    w = [rnd.random() + 0.2 for _ in picks]
    s = sum(w)
    fc = [{"kind": "outperform", "ticker_a": picks[0], "ticker_b": picks[i % 2 + 1],
           "horizon_days": h, "probability": round(rnd.uniform(0.3, 0.7), 2),
           "rationale": "mock"} for i, h in enumerate([30, 90])]
    fc += [{"kind": "return_above", "ticker_a": picks[i], "threshold": 0.0,
            "horizon_days": h, "probability": round(rnd.uniform(0.5, 0.8), 2),
            "rationale": "mock"} for i, h in enumerate([90, 180])]
    core = json.loads(prompt.split("CORE: ")[1].split("\n")[0])
    fc += [{**c, "probability": round(rnd.uniform(0.35, 0.75), 2)} for c in core]
    return json.dumps({"agent": cfg["model"], "analysis": "mock",
                       "what_would_prove_me_wrong": "mock",
                       "allocation": {t: round(x / s, 4) for t, x in zip(picks, w)},
                       "forecasts": fc})


PROVIDERS = {"gemini": call_gemini, "mock": call_mock}


def ask(agent_cfg: dict, system: str, prompt: str) -> str:
    provider = agent_cfg["provider"]
    if provider not in PROVIDERS:
        raise RuntimeError(f"provider '{provider}' is not callable via API (use inbox)")
    key = "" if provider == "mock" else get_key(agent_cfg)
    return PROVIDERS[provider](agent_cfg, key, system, prompt)
