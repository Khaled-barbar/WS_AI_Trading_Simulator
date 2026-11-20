"""Online updates from the GitHub repository (used by the dashboard's update button).

check(): compares the installed VERSION with VERSION on the repo's main branch.
start(): downloads the repo's main branch, then runs ITS updater (always the newest
updater logic) in a separate process. That process stops this dashboard, backs up,
installs every file of the latest version, runs every config migration between the
installed version and the latest one, installs requirements, tests, and restarts the
dashboard — or rolls everything back if a step fails.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime

from .db import ROOT

DEFAULT_REPO = "Khaled-barbar/WS_AI_Trading_Simulator"
BRANCH = "main"
UA = {"User-Agent": "agents-lab-updater"}


def repo(cfg) -> str:
    return cfg.get("lab", {}).get("update_repo", DEFAULT_REPO)


def installed_version() -> str:
    p = ROOT / "VERSION"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else "1.0.0"


def vtuple(v: str):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return (0,)


def _get(url: str, timeout=30, headers=None) -> bytes:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def latest_version(cfg) -> str:
    r = repo(cfg)
    try:  # API: not cached by a CDN, so a just-pushed version is seen at once
        return _get(f"https://api.github.com/repos/{r}/contents/VERSION?ref={BRANCH}",
                    headers={"Accept": "application/vnd.github.raw"}).decode().strip()
    except Exception:
        return _get(f"https://raw.githubusercontent.com/{r}/{BRANCH}/VERSION").decode().strip()


def check(cfg) -> dict:
    cur = installed_version()
    out = {"installed": cur, "repo": repo(cfg), "latest": None, "newer": False, "error": None}
    try:
        out["latest"] = latest_version(cfg)
        out["newer"] = vtuple(out["latest"]) > vtuple(cur)
    except Exception as e:
        out["error"] = f"cannot reach GitHub: {e}"
    return out


def log_path():
    return ROOT / "logs" / "update.log"


def start(cfg, port: int) -> dict:
    """Downloads the latest source and launches its updater detached from this process."""
    data = _get(f"https://codeload.github.com/{repo(cfg)}/zip/refs/heads/{BRANCH}", timeout=120)
    tmp = tempfile.mkdtemp(prefix="agentslab-update-")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(tmp)
    src = next(os.path.join(tmp, d) for d in os.listdir(tmp))  # <repo>-main/
    updater = os.path.join(src, "updater", "updater.py")
    if not os.path.isfile(updater):
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("the repository has no updater/updater.py")

    py = sys.executable
    if sys.platform == "win32" and py.lower().endswith("pythonw.exe"):
        cand = py[:-len("pythonw.exe")] + "python.exe"
        py = cand if os.path.isfile(cand) else py
    args = [py, updater, "--source", src, "--target", str(ROOT), "--yes",
            "--dashboard-pid", str(os.getpid()), "--port", str(port)]
    log_path().parent.mkdir(exist_ok=True)
    log = open(log_path(), "a", encoding="utf-8")
    log.write(f"\n==== {datetime.now():%Y-%m-%d %H:%M:%S} update from dashboard ====\n")
    log.flush()
    env = {**os.environ, "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
        subprocess.Popen(args, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    else:
        if os.environ.get("INVOCATION_ID") and shutil.which("systemd-run"):
            # inside the systemd service: run outside its cgroup, or stopping the
            # service would also kill the updater
            args = ["systemd-run", "--user", "--collect", "--quiet",
                    f"--unit=agents-lab-update-{os.getpid()}", f"--working-directory={ROOT}",
                    "--setenv=PYTHONUTF8=1", "/bin/sh", "-c",
                    " ".join(_q(a) for a in args) + f" >> {_q(str(log_path()))} 2>&1"]
            subprocess.Popen(args, cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                             stdout=log, stderr=subprocess.STDOUT)
        else:
            subprocess.Popen(args, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    log.close()
    return {"started": True}


def _q(s: str) -> str:
    import shlex
    return shlex.quote(s)


def log_tail(n=60) -> dict:
    p = log_path()
    if not p.is_file():
        return {"text": ""}
    return {"text": "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])}
