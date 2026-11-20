#!/usr/bin/env python3
"""Agents Lab updater — one file for Windows and Linux.

Run it with a double-click (Windows) or `python3 AgentsLab-update-X.Y.Z.pyz` (Linux).
It finds the installed Agents Lab folder, backs up your data, replaces only the program
files, migrates config.toml, tests the result and rolls back automatically if anything fails.
Your data is never overwritten: lab.db, inbox/, logs/, report.md stay as they are.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

IS_WIN = sys.platform == "win32"
PLAT = "windows" if IS_WIN else "linux"
PORT = 8765
# Never written by the updater (config.toml is only edited by migrations below)
USER_DATA = ("lab.db", "config.toml", "inbox", "logs", "report.md", "backups", "prompts")


# ---------------------------------------------------------------- payload
GROUPS = ("common", "windows", "linux")


class Payload:
    """The new version's files: bundled inside this .pyz, or a source tree
    (the GitHub repo checkout: VERSION + common/ windows/ linux/)."""

    def __init__(self, source=None):
        here = os.path.dirname(os.path.abspath(__file__))
        self.zip = self.src = None
        if source:
            self.src = Path(source)
        elif zipfile.is_zipfile(here):
            self.zip = zipfile.ZipFile(here)
        else:  # updater.py run from inside a source tree
            self.src = Path(here).parent
        if self.src:
            if not (self.src / "VERSION").is_file() or not (self.src / "common").is_dir():
                sys.exit(f"Not an Agents Lab source tree: {self.src}")
            self.manifest = {"version": (self.src / "VERSION").read_text(encoding="utf-8").strip()}
            for g in GROUPS:
                base = self.src / g
                self.manifest[g] = sorted(p.relative_to(base).as_posix() for p in base.rglob("*")
                                          if p.is_file() and "__pycache__" not in p.parts)
        else:
            self.manifest = json.loads(self.zip.read("payload/manifest.json"))
        self.version = self.manifest["version"]

    def read(self, name: str) -> bytes:
        if self.zip:
            return self.zip.read(name)
        return (self.src / name.removeprefix("payload/")).read_bytes()

    def files(self):
        """(relative install path, archive name) for this platform; config.toml excluded."""
        for group in ("common", PLAT):
            for rel in self.manifest[group]:
                if group == "common" and rel == "config.toml":
                    continue
                yield rel, f"payload/{group}/{rel}"


def vtuple(v: str):
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except ValueError:
        return (0,)


# ---------------------------------------------------------------- locate install
def marker_path() -> Path:
    if IS_WIN:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AgentsLab" / "install_dir.txt"
    return Path.home() / ".config" / "agents-lab" / "install_dir"


def is_install(d) -> bool:
    d = Path(d)
    return (d / "lab" / "cli.py").is_file() and (d / "config.toml").is_file()


def _quiet(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kw)
    except Exception:
        return None


def candidates():
    m = marker_path()
    if m.is_file():
        yield "saved location", m.read_text(encoding="utf-8-sig").strip()
    if IS_WIN:
        r = _quiet(["powershell", "-NoProfile", "-Command",
                    "(Get-ScheduledTask -TaskName 'AgentsLab Daily' -ErrorAction SilentlyContinue)"
                    ".Actions[0].WorkingDirectory"])
        if r and r.stdout.strip():
            yield "scheduled task", r.stdout.strip()
    else:
        unit = Path.home() / ".config/systemd/user/agents-lab-dashboard.service"
        if unit.is_file():
            g = re.search(r"^WorkingDirectory=(.+)$", unit.read_text(), re.M)
            if g:
                yield "systemd service", g.group(1).strip()
        r = _quiet(["crontab", "-l"])
        if r and r.returncode == 0:
            for line in r.stdout.splitlines():
                if "run_daily.sh" in line and not line.lstrip().startswith("#"):
                    yield "cron job", str(Path(line.split()[-1]).parent)
    exe_dir = Path(os.path.abspath(sys.argv[0])).parent
    yield "updater folder", str(exe_dir)
    yield "current folder", os.getcwd()


def find_install(args) -> Path:
    if args.target:
        if not is_install(args.target):
            sys.exit(f"Not an Agents Lab folder: {args.target}")
        return Path(args.target).resolve()
    for how, d in candidates():
        if d and is_install(d):
            print(f"Found Agents Lab ({how}): {d}")
            return Path(d).resolve()
    if args.yes or not sys.stdin.isatty():
        sys.exit("Could not find the Agents Lab folder. Run again with --target <folder>.")
    while True:
        d = input("Agents Lab folder not found. Paste its full path (the folder with lab.db): ").strip().strip('"')
        if is_install(d):
            return Path(d).resolve()
        print("  That folder does not contain lab/ and config.toml — try again.")


# ---------------------------------------------------------------- dashboard
def port_open() -> bool:
    import socket
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def stop_dashboard(pid=None):
    """Stops the dashboard server. Returns how it was running (None if it was not).
    With pid (update started from the dashboard) only that dashboard is stopped."""
    how = None
    if pid and not IS_WIN:
        r = _quiet(["systemctl", "--user", "show", "-p", "MainPID", "--value",
                    "agents-lab-dashboard.service"])
        if r and r.stdout.strip() == str(pid):
            _quiet(["systemctl", "--user", "stop", "agents-lab-dashboard.service"])
            how = "systemd"
        else:
            try:
                os.kill(pid, 15)
                how = "process"
            except OSError:
                pass
    elif pid:
        r = _quiet(["taskkill", "/F", "/PID", str(pid)])
        how = "process" if r and r.returncode == 0 else None
    elif IS_WIN:
        ps = ("$p = Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -like '*-m lab dashboard*' };"
              "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue };"
              "@($p).Count")
        r = _quiet(["powershell", "-NoProfile", "-Command", ps])
        if r and r.stdout.strip() not in ("", "0"):
            how = "process"
    else:
        r = _quiet(["systemctl", "--user", "is-active", "agents-lab-dashboard.service"])
        if r and r.stdout.strip() == "active":
            _quiet(["systemctl", "--user", "stop", "agents-lab-dashboard.service"])
            how = "systemd"
        else:
            r = _quiet(["pkill", "-f", "[-]m lab dashboard"])
            if r and r.returncode == 0:
                how = "process"
    for _ in range(20):
        if not port_open():
            break
        time.sleep(0.25)
    return how


def start_dashboard(root: Path, how):
    if not how:
        return
    if how == "systemd":
        _quiet(["systemctl", "--user", "start", "agents-lab-dashboard.service"])
    else:
        env = {**os.environ, "PYTHONUTF8": "1"}
        cmd = ["-m", "lab", "dashboard", "--no-browser"] + (["--port", str(PORT)] if PORT != 8765 else [])
        if IS_WIN:
            pyw = Path(sys.executable).with_name("pythonw.exe")
            exe = str(pyw if pyw.is_file() else sys.executable)
            flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
            subprocess.Popen([exe, *cmd], cwd=root, env=env, creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen([sys.executable, *cmd], cwd=root, env=env, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if port_open():
            print(f"Dashboard restarted: http://127.0.0.1:{PORT}/")
            return
        time.sleep(0.25)
    print("! Dashboard did not come back up; open it with open_dashboard."
          + ("bat" if IS_WIN else "sh"))


# ---------------------------------------------------------------- config migrations
def mig_1_1_0(text: str) -> str:
    """Gemini: API -> manual paste through inbox (the Gemini API stopped working)."""
    m = re.search(r"(?ms)^\[agents\.gemini\][ \t]*\n(.*?)(?=^\[|\Z)", text)
    if not m or not re.search(r'(?m)^\s*provider\s*=\s*"gemini"', m.group(1)):
        return text  # already manual / customised: leave it alone
    body = re.sub(r"(?m)^\s*(provider|model|key_env|cred_target|google_search)\s*=.*\n?", "", m.group(1))
    new = '[agents.gemini]\nprovider = "manual"\nmodel    = "gemini-app"\n' + body
    out = text[:m.start()] + new + text[m.end():]
    # drop the now-wrong comment about reading the Gemini key
    return re.sub(r"(?m)^#[^\n]*Gemini[^\n]*Credential Manager[^\n]*\n", "", out)


def mig_1_4_0(text: str) -> str:
    """AI stock picks: add the [stocks] section (defaults also live in lab/tracks.py)."""
    if re.search(r"(?m)^\[stocks\]", text):
        return text
    return text.rstrip() + "\n" + STOCKS_BLOCK


STOCKS_BLOCK = '\n# ===== 1.4.0: AI stock picks (companies) — same agents, same logic as [lab] =====\n# Prompts: dashboard → System → Prompts ("Company stocks"). Replies: inbox/<agent>-stocks-YYYY-MM.txt\n[stocks]\nenabled    = true\nbenchmark  = "XEQT.TO"\ncompare    = ["SPY", "XIU.TO"]\nmin_picks  = 5\nmax_picks  = 15\nmax_weight = 0.25\nfx_fee     = 0.015                     # Wealthsimple FX fee on non-CAD stocks (CAD account)\n'


MIGRATIONS = [("1.1.0", mig_1_1_0), ("1.4.0", mig_1_4_0)]


def migrate_config(root: Path, old: str, new: str):
    import tomllib
    p = root / "config.toml"
    text = p.read_text(encoding="utf-8-sig")
    out = text
    for ver, fn in MIGRATIONS:
        if vtuple(old) < vtuple(ver) <= vtuple(new):
            out = fn(out)
    tomllib.loads(out)  # refuse to write an invalid config
    if out != text:
        p.write_text(out, encoding="utf-8")
        print("config.toml migrated (your other settings were kept)")


# ---------------------------------------------------------------- backup / install / rollback
def backup(root: Path, payload: Payload, old: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    b = root / "backups" / f"{stamp}_v{old}_to_v{payload.version}"
    (b / "files").mkdir(parents=True)
    db = root / "lab.db"
    if db.is_file():  # consistent copy even if something has the DB open
        src, dst = sqlite3.connect(db), sqlite3.connect(b / "lab.db")
        with dst:
            src.backup(dst)
        src.close(), dst.close()
    shutil.copy2(root / "config.toml", b / "config.toml")
    if (root / "VERSION").is_file():
        shutil.copy2(root / "VERSION", b / "files" / "VERSION")
    shutil.copytree(root / "lab", b / "files" / "lab", ignore=shutil.ignore_patterns("__pycache__"))
    if (root / "prompts").is_dir():  # regenerated by the test; restored on rollback
        shutil.copytree(root / "prompts", b / "prompts")
    new_files = []
    for rel, _ in payload.files():
        src = root / rel
        if src.is_file():
            if not rel.startswith("lab/"):
                (b / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, b / "files" / rel)
        else:
            new_files.append(rel)
    (b / "new_files.json").write_text(json.dumps(new_files), encoding="utf-8")
    return b


def install_files(root: Path, payload: Payload):
    wanted = set()
    for rel, arc in payload.files():
        assert rel.split("/")[0] not in USER_DATA, rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".updtmp")
        tmp.write_bytes(payload.read(arc))
        os.replace(tmp, dst)
        if rel.endswith(".sh") and not IS_WIN:
            dst.chmod(0o755)
        wanted.add(rel)
    # program files that no longer exist in the new version (they are in the backup)
    for p in (root / "lab").rglob("*"):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and "__pycache__" not in rel and rel not in wanted:
            p.unlink()
    shutil.rmtree(root / "lab" / "__pycache__", ignore_errors=True)
    for d in ("inbox", "logs", "prompts"):
        (root / d).mkdir(exist_ok=True)


def rollback(root: Path, b: Path):
    print("Rolling back to the previous version...")
    shutil.rmtree(root / "lab", ignore_errors=True)
    for p in (b / "files").rglob("*"):
        if p.is_file():
            dst = root / p.relative_to(b / "files")
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    if not (b / "files" / "VERSION").is_file():
        (root / "VERSION").unlink(missing_ok=True)
    for rel in json.loads((b / "new_files.json").read_text(encoding="utf-8")):
        if rel != "VERSION":
            (root / rel).unlink(missing_ok=True)
    shutil.copy2(b / "config.toml", root / "config.toml")
    if (b / "prompts").is_dir():
        shutil.copytree(b / "prompts", root / "prompts", dirs_exist_ok=True)
    print(f"Previous version restored. Backup kept in {b}")


def install_requirements(root: Path):
    """Third-party packages listed in requirements.txt (none today: stdlib only)."""
    req = root / "requirements.txt"
    if not req.is_file():
        return
    pkgs = [l for l in req.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]
    if not pkgs:
        return
    print(f"Installing dependencies: {', '.join(pkgs)}")
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)]
    if sys.prefix == sys.base_prefix:  # not a virtualenv: per-user install
        cmd.insert(4, "--user")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"pip install failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}")


def smoke_test(root: Path, tmpdir: Path):
    """Imports the new code, regenerates the prompts and writes the report from the real DB."""
    import tomllib
    env = {**os.environ, "PYTHONUTF8": "1"}
    run = lambda *a: subprocess.run([sys.executable, "-m", "lab", *a], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=120)
    agents = [a for a, v in tomllib.loads((root / "config.toml").read_text(encoding="utf-8-sig"))
              ["agents"].items() if v.get("provider") == "manual"]
    steps = [("--version",)] + [("print-prompt", a, "--out", f"prompts/{a}_scheduled_prompt.txt")
                                for a in agents] + [("report", "--out", str(tmpdir / "report-test.md"))]
    for s in steps:
        r = run(*s)
        if r.returncode != 0:
            raise RuntimeError(f"test `lab {' '.join(s)}` failed:\n{r.stdout}{r.stderr}")
    subprocess.run([sys.executable, "-c", "import lab.dashboard, lab.session, lab.scoring"],
                   cwd=root, env=env, check=True, capture_output=True, timeout=60)


# ---------------------------------------------------------------- main
def main():
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    global PORT
    ap = argparse.ArgumentParser(description="Agents Lab updater")
    ap.add_argument("--source", help="install from this source tree (the GitHub repo) instead of the bundled files")
    ap.add_argument("--dashboard-pid", type=int, help="stop only this dashboard process (used by the update button)")
    ap.add_argument("--port", type=int, default=PORT, help="dashboard port")
    ap.add_argument("--target", help="Agents Lab folder (found automatically if omitted)")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--force", action="store_true", help="reinstall even if already up to date")
    ap.add_argument("--test-copy", action="store_true",
                    help="update a copy: leave the running dashboard and the saved location alone")
    args = ap.parse_args()
    PORT = args.port
    payload = Payload(args.source)
    interactive = sys.stdin.isatty() and not args.yes

    def done(code=0, msg=None):
        if msg:
            print(msg)
        if interactive and IS_WIN:
            input("\nPress Enter to close")
        sys.exit(code)

    print(f"Agents Lab updater — version {payload.version} ({PLAT})\n")
    if sys.version_info < (3, 11):
        done(1, "Python 3.11 or newer is required.")
    root = find_install(args)
    vf = root / "VERSION"
    old = vf.read_text(encoding="utf-8").strip() if vf.is_file() else "1.0.0"
    print(f"Installed: {old}   ->   New: {payload.version}")
    if vtuple(old) >= vtuple(payload.version) and not args.force:
        done(0, "Already up to date. Nothing to do.")
    if interactive and input("Update now? Your data is kept and backed up. [Y/n] ").strip().lower() not in ("", "y", "yes"):
        done(0, "Cancelled.")

    how = None if args.test_copy else stop_dashboard(args.dashboard_pid)
    b = backup(root, payload, old)
    print(f"Backup: {b}")
    try:
        install_files(root, payload)
        migrate_config(root, old, payload.version)
        install_requirements(root)
        (root / "VERSION").write_text(payload.version + "\n", encoding="utf-8")
        smoke_test(root, b)
        if os.environ.get("AGENTSLAB_UPDATER_SELFTEST_FAIL"):
            raise RuntimeError("simulated failure (self-test)")
    except Exception as e:
        print(f"\nUPDATE FAILED: {e}")
        rollback(root, b)
        start_dashboard(root, how)
        done(1)
    try:
        if args.test_copy:
            raise OSError
        marker_path().parent.mkdir(parents=True, exist_ok=True)
        marker_path().write_text(str(root), encoding="utf-8")
    except OSError:
        pass
    start_dashboard(root, how)
    done(0, f"\nUpdated to {payload.version}. Your data (lab.db, inbox, logs, report.md) was not touched.")


if __name__ == "__main__":
    main()
