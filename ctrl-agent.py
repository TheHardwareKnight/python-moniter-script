#!/usr/bin/env python3
"""
ctrl-agent.py — Control Panel Agent
Drop on any Windows machine, compile to exe with PyInstaller.

python -m PyInstaller --onefile --noconsole ctrl-agent.py
"""

import io
import subprocess
import time
import socket
import hashlib
import platform
import base64
import threading
import requests
import psutil

# ── CONFIG ───────────────────────────────────────────────────────────────────
PI_URL              = "https://controlapi.myleskerschner.com"
AGENT_SECRET        = hashlib.sha256("ehs508".encode()).hexdigest()
REPORT_INTERVAL     = 5
SCREENSHOT_INTERVAL = 2
# ─────────────────────────────────────────────────────────────────────────────

DEVICE_ID   = socket.gethostname().lower().replace(" ", "-")
DEVICE_NAME = socket.gethostname()
HEADERS     = {"Content-Type": "application/json", "X-Agent-Secret": AGENT_SECRET}

try:
    import win32gui, win32process
    WIN32 = True
except Exception:
    WIN32 = False

try:
    from PIL import ImageGrab
    PIL = True
except Exception:
    PIL = False

_screenshot      = None
_screenshot_lock = threading.Lock()

SYSTEM_PROCS = {
    "svchost.exe","system","registry","smss.exe","csrss.exe","wininit.exe",
    "services.exe","lsass.exe","winlogon.exe","dwm.exe","fontdrvhost.exe",
    "spoolsv.exe","searchindexer.exe","wmiprvse.exe","dllhost.exe",
    "taskhostw.exe","sihost.exe","runtimebroker.exe","applicationframehost.exe",
    "ctfmon.exe","conhost.exe","audiodg.exe","msdtc.exe","unsecapp.exe",
    "wuauclt.exe","msmpeng.exe","securityhealthservice.exe","sgrmbroker.exe",
    "idle","system idle process","memory compression","ntoskrnl.exe",
    "lsaiso.exe","wlms.exe","sppsvc.exe"
}


# ── SCREENSHOT ───────────────────────────────────────────────────────────────

def screenshot_loop():
    global _screenshot
    while True:
        try:
            if PIL:
                img = ImageGrab.grab(all_screens=True)
                if img.width > 1280:
                    img = img.resize((1280, int(img.height * 1280 / img.width)))
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=50)
                with _screenshot_lock:
                    _screenshot = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            pass
        time.sleep(SCREENSHOT_INTERVAL)

def get_screenshot():
    with _screenshot_lock:
        return _screenshot


# ── PROCESSES ────────────────────────────────────────────────────────────────

def get_focused_pid():
    if not WIN32:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid
    except Exception:
        return None

def get_processes(focused_pid):
    procs = []
    for p in psutil.process_iter(['pid','name','username','cpu_percent','memory_info','status']):
        try:
            info      = p.info
            mem_bytes = info['memory_info'].rss if info['memory_info'] else 0
            name      = info['name'] or "Unknown"
            pid       = info['pid']
            if pid == focused_pid:
                cat = "focused"
            elif name.lower() in SYSTEM_PROCS or pid <= 4:
                cat = "background"
            else:
                cat = "user"
            procs.append({
                "pid":      pid,
                "name":     name,
                "user":     (info['username'] or "").split("\\")[-1],
                "cpu":      round(info['cpu_percent'] or 0.0, 1),
                "mem_mb":   round(mem_bytes / 1024 / 1024, 1),
                "status":   info['status'] or "running",
                "category": cat
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return procs

def get_stats():
    mem = psutil.virtual_memory()
    return {
        "cpu_total":   psutil.cpu_percent(interval=None),
        "mem_used_gb": round(mem.used / 1024**3, 2)
    }


# ── KILL ─────────────────────────────────────────────────────────────────────

def kill_pid(pid):
    try:
        p = psutil.Process(pid)
        p.terminate()
        time.sleep(0.5)
        if p.is_running():
            p.kill()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


# ── POWERSHELL CONSOLE ───────────────────────────────────────────────────────

def run_command(cmd, cmd_id):
    """Run a PowerShell command and return the result to the Pi."""
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            timeout=30
        )
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR]\n" + result.stderr
        if not output.strip():
            output = "(no output)"
    except subprocess.TimeoutExpired:
        output = "[ERROR] Command timed out after 30 seconds."
    except Exception as e:
        output = f"[ERROR] {e}"

    # Send result back to Pi
    try:
        requests.post(
            PI_URL + "/agent/console_result",
            json={"device_id": DEVICE_ID, "cmd_id": cmd_id, "output": output},
            headers=HEADERS,
            timeout=10
        )
    except Exception:
        pass


# ── REPORT LOOP ──────────────────────────────────────────────────────────────

def report():
    focused_pid = get_focused_pid()
    payload = {
        "device_id":   DEVICE_ID,
        "name":        DEVICE_NAME,
        "os":          f"Windows {platform.version()}",
        "processes":   get_processes(focused_pid),
        "focused_pid": focused_pid,
        "screenshot":  get_screenshot(),
        **get_stats()
    }
    try:
        res = requests.post(PI_URL + "/agent/report", json=payload, headers=HEADERS, timeout=10)
        if res.status_code == 200:
            data = res.json()
            for kill in data.get("pending_kills", []):
                kill_pid(kill["pid"])
            for cmd_task in data.get("pending_commands", []):
                threading.Thread(
                    target=run_command,
                    args=(cmd_task["cmd"], cmd_task["cmd_id"]),
                    daemon=True
                ).start()
    except Exception:
        pass


# ── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    psutil.cpu_percent(interval=1)
    threading.Thread(target=screenshot_loop, daemon=True).start()
    while True:
        report()
        time.sleep(REPORT_INTERVAL)
