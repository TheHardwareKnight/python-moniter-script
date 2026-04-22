#!/usr/bin/env python3
"""
pi_server.py — Control Panel API Server
pip3 install flask flask-cors
"""

import json
import time
import uuid
import secrets
import hashlib
import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app, origins=["https://myleskerschner.com", "http://localhost"])

LOG_DIR  = Path.home() / "login"
LOG_KEEP = 7
PORT     = 8080

# Permissions per role
ROLES = {
    "admin":    {"end_tasks", "view_screen", "view_logs", "manage_devices", "manage_users", "console"},
    "harrison": {"end_tasks", "view_screen"},
    "myles":    set()
}

LOG_DIR.mkdir(exist_ok=True)

devices     = {}   # { device_id: { ...data, pending_kills, pending_commands } }
tokens      = {}   # { token: { username, role, expires } }
device_meta = {}   # { device_id: { allowed_users: [], nickname: str } }
console_results = {}  # { cmd_id: { output, timestamp } }


# ── PERSISTENCE ──────────────────────────────────────────────────────────────

def load_meta():
    global device_meta
    f = LOG_DIR / "device_meta.json"
    if f.exists():
        device_meta = json.loads(f.read_text())

def save_meta():
    (LOG_DIR / "device_meta.json").write_text(json.dumps(device_meta, indent=2))

load_meta()


# ── USERS ────────────────────────────────────────────────────────────────────

def load_users():
    f = LOG_DIR / "users.json"
    if not f.exists():
        default = {
            "admin":    {"hash": hashlib.sha256("ehs508admin".encode()).hexdigest(), "role": "admin",    "locked": False},
            "myles":    {"hash": hashlib.sha256("137603Mk!".encode()).hexdigest(),   "role": "myles",    "locked": False},
            "harrison": {"hash": hashlib.sha256("tbd".encode()).hexdigest(),          "role": "harrison", "locked": False}
        }
        f.write_text(json.dumps(default, indent=2))
    raw = json.loads(f.read_text())
    # migrate old flat format
    migrated = {}
    changed  = False
    for u, v in raw.items():
        if isinstance(v, str):
            migrated[u] = {"hash": v, "role": u, "locked": False}
            changed = True
        else:
            migrated[u] = v
    if changed:
        (LOG_DIR / "users.json").write_text(json.dumps(migrated, indent=2))
    return migrated

def save_users(users):
    (LOG_DIR / "users.json").write_text(json.dumps(users, indent=2))


# ── LOGGING ──────────────────────────────────────────────────────────────────

def log_event(username, event, detail=""):
    log_file = LOG_DIR / f"{datetime.date.today().isoformat()}.log"
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    with open(log_file, "a") as f:
        f.write(f"{ts}  {username:<20} {event:<20} {detail}\n")
    purge_old_logs()

def purge_old_logs():
    cutoff = datetime.date.today() - datetime.timedelta(days=LOG_KEEP)
    for f in LOG_DIR.glob("*.log"):
        try:
            if datetime.date.fromisoformat(f.stem) < cutoff:
                f.unlink()
        except ValueError:
            pass


# ── AUTH HELPERS ─────────────────────────────────────────────────────────────

def get_entry(req):
    auth = req.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    entry = tokens.get(auth[7:])
    if not entry or time.time() > entry["expires"]:
        return None
    return entry

def check_token(req):
    e = get_entry(req)
    return e["username"] if e else None

def get_role(req):
    e = get_entry(req)
    return e["role"] if e else None

def can(req, perm):
    return perm in ROLES.get(get_role(req), set())

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = check_token(request)
        if not user:
            return jsonify({"error": "Unauthorized"}), 401
        request.username = user
        request.role     = get_role(request)
        return f(*args, **kwargs)
    return wrapper

def user_can_see_device(username, role, device_id):
    """Check if a user is allowed to see a device. Admins always can."""
    if role == "admin":
        return True
    meta = device_meta.get(device_id, {})
    allowed = meta.get("allowed_users", [])
    return username in allowed


# ── AUTH ROUTES ───────────────────────────────────────────────────────────────

@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.get_json()
    username = (data.get("username") or "").strip().lower()
    pw_hash  = hashlib.sha256((data.get("password") or "").encode()).hexdigest()
    users    = load_users()
    user     = users.get(username)

    if not user:
        log_event(username, "LOGIN_FAIL", f"no_such_user ip={request.remote_addr}")
        return jsonify({"error": "Invalid username or password."}), 401

    if user.get("locked", False):
        log_event(username, "LOGIN_BLOCKED", f"ip={request.remote_addr}")
        return jsonify({"error": "Account is locked. Contact admin."}), 403

    if user["hash"] != pw_hash:
        log_event(username, "LOGIN_FAIL", f"wrong_password ip={request.remote_addr}")
        return jsonify({"error": "Invalid username or password."}), 401

    role  = user.get("role", username)
    token = secrets.token_hex(32)
    tokens[token] = {"username": username, "role": role, "expires": time.time() + 86400}
    log_event(username, "LOGIN_OK", f"ip={request.remote_addr}")
    return jsonify({"token": token, "username": username, "role": role,
                    "permissions": list(ROLES.get(role, set()))})


@app.route("/auth/logout", methods=["POST"])
@require_auth
def logout():
    tokens.pop(request.headers.get("Authorization", "")[7:], None)
    log_event(request.username, "LOGOUT")
    return jsonify({"ok": True})


# ── DEVICE ROUTES ─────────────────────────────────────────────────────────────

@app.route("/devices", methods=["GET"])
@require_auth
def list_devices():
    now    = time.time()
    result = []
    for dev_id, d in devices.items():
        if not user_can_see_device(request.username, request.role, dev_id):
            continue
        meta   = device_meta.get(dev_id, {})
        age    = now - d.get("last_seen", 0)
        online = age < 15
        dt_str = datetime.datetime.fromtimestamp(d["last_seen"]).strftime("%H:%M:%S") \
                 if not online and d.get("last_seen") else "—"
        result.append({
            "id":            dev_id,
            "name":          meta.get("nickname") or d.get("name", dev_id),
            "os":            d.get("os", "Unknown"),
            "online":        online,
            "last_seen":     dt_str,
            "cpu_total":     round(d.get("cpu_total", 0), 1),
            "mem_used_gb":   round(d.get("mem_used_gb", 0), 2),
            "process_count": len(d.get("processes", [])),
            "allowed_users": meta.get("allowed_users", [])
        })
    result.sort(key=lambda x: (not x["online"], x["name"]))
    return jsonify(result)


@app.route("/devices/<device_id>/processes", methods=["GET"])
@require_auth
def get_processes(device_id):
    if not user_can_see_device(request.username, request.role, device_id):
        return jsonify({"error": "Not found"}), 404
    dev = devices.get(device_id)
    if not dev:
        return jsonify({"error": "Device not found"}), 404
    return jsonify(dev.get("processes", []))


@app.route("/devices/<device_id>/kill", methods=["POST"])
@require_auth
def kill_process(device_id):
    if not can(request, "end_tasks"):
        return jsonify({"error": "Permission denied"}), 403
    if not user_can_see_device(request.username, request.role, device_id):
        return jsonify({"error": "Not found"}), 404
    dev = devices.get(device_id)
    if not dev:
        return jsonify({"error": "Device not found"}), 404
    pid = (request.get_json() or {}).get("pid")
    if not pid:
        return jsonify({"error": "No PID"}), 400
    dev.setdefault("pending_kills", []).append({"pid": pid, "requested_at": time.time()})
    proc_name = next((p["name"] for p in dev.get("processes", []) if p["pid"] == pid), str(pid))
    log_event(request.username, "KILL", f"device={device_id} pid={pid} name={proc_name}")
    return jsonify({"ok": True})


@app.route("/devices/<device_id>/screenshot", methods=["GET"])
@require_auth
def get_screenshot(device_id):
    if not can(request, "view_screen"):
        return jsonify({"error": "Permission denied"}), 403
    if not user_can_see_device(request.username, request.role, device_id):
        return jsonify({"error": "Not found"}), 404
    dev = devices.get(device_id)
    if not dev:
        return jsonify({"error": "Device not found"}), 404
    ss = dev.get("screenshot")
    if not ss:
        return jsonify({"error": "No screenshot"}), 404
    return jsonify({"screenshot": ss, "timestamp": dev.get("last_seen")})


# ── CONSOLE ROUTES ────────────────────────────────────────────────────────────

@app.route("/devices/<device_id>/console", methods=["POST"])
@require_auth
def send_console_command(device_id):
    if not can(request, "console"):
        return jsonify({"error": "Permission denied"}), 403
    if not user_can_see_device(request.username, request.role, device_id):
        return jsonify({"error": "Not found"}), 404
    dev = devices.get(device_id)
    if not dev:
        return jsonify({"error": "Device not found"}), 404
    cmd    = (request.get_json() or {}).get("cmd", "").strip()
    if not cmd:
        return jsonify({"error": "No command"}), 400
    cmd_id = str(uuid.uuid4())
    dev.setdefault("pending_commands", []).append({"cmd": cmd, "cmd_id": cmd_id, "requested_at": time.time()})
    log_event(request.username, "CONSOLE", f"device={device_id} cmd={cmd[:60]}")
    return jsonify({"cmd_id": cmd_id})


@app.route("/devices/<device_id>/console/<cmd_id>", methods=["GET"])
@require_auth
def get_console_result(device_id, cmd_id):
    if not can(request, "console"):
        return jsonify({"error": "Permission denied"}), 403
    result = console_results.get(cmd_id)
    if not result:
        return jsonify({"ready": False})
    return jsonify({"ready": True, "output": result["output"]})


# ── ADMIN — DEVICE VISIBILITY ─────────────────────────────────────────────────

@app.route("/devices/<device_id>/access", methods=["POST"])
@require_auth
def set_device_access(device_id):
    if not can(request, "manage_devices"):
        return jsonify({"error": "Permission denied"}), 403
    data         = request.get_json() or {}
    allowed      = data.get("allowed_users", [])
    device_meta.setdefault(device_id, {})["allowed_users"] = allowed
    save_meta()
    log_event(request.username, "DEVICE_ACCESS", f"device={device_id} users={allowed}")
    return jsonify({"ok": True, "allowed_users": allowed})


# ── ADMIN — USER MANAGEMENT ───────────────────────────────────────────────────

@app.route("/admin/users", methods=["GET"])
@require_auth
def list_users():
    if not can(request, "manage_users"):
        return jsonify({"error": "Permission denied"}), 403
    users = load_users()
    return jsonify([
        {"username": u, "role": v.get("role", u), "locked": v.get("locked", False)}
        for u, v in users.items()
    ])


@app.route("/admin/users/<username>/lock", methods=["POST"])
@require_auth
def lock_user(username):
    if not can(request, "manage_users"):
        return jsonify({"error": "Permission denied"}), 403
    if username == request.username:
        return jsonify({"error": "Cannot lock yourself"}), 400
    data   = request.get_json() or {}
    locked = data.get("locked", True)
    users  = load_users()
    if username not in users:
        return jsonify({"error": "User not found"}), 404
    users[username]["locked"] = locked
    save_users(users)
    if locked:
        for tok, entry in list(tokens.items()):
            if entry["username"] == username:
                del tokens[tok]
    log_event(request.username, "USER_LOCK" if locked else "USER_UNLOCK", f"target={username}")
    return jsonify({"ok": True, "locked": locked})


@app.route("/admin/users/<username>/password", methods=["POST"])
@require_auth
def change_password(username):
    if not can(request, "manage_users"):
        return jsonify({"error": "Permission denied"}), 403
    data     = request.get_json() or {}
    new_pass = data.get("password", "").strip()
    if len(new_pass) < 4:
        return jsonify({"error": "Password must be at least 4 characters"}), 400
    users = load_users()
    if username not in users:
        return jsonify({"error": "User not found"}), 404
    users[username]["hash"] = hashlib.sha256(new_pass.encode()).hexdigest()
    save_users(users)
    # revoke existing sessions for that user
    for tok, entry in list(tokens.items()):
        if entry["username"] == username:
            del tokens[tok]
    log_event(request.username, "PASSWORD_CHANGE", f"target={username}")
    return jsonify({"ok": True})


# ── ADMIN — LOGS ──────────────────────────────────────────────────────────────

@app.route("/admin/logs", methods=["GET"])
@require_auth
def get_logs():
    if not can(request, "view_logs"):
        return jsonify({"error": "Permission denied"}), 403
    days   = int(request.args.get("days", 3))
    result = []
    for i in range(days):
        d    = datetime.date.today() - datetime.timedelta(days=i)
        logf = LOG_DIR / f"{d.isoformat()}.log"
        if logf.exists():
            for line in logf.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    result.append({
                        "date":   d.isoformat(),
                        "time":   parts[0],
                        "user":   parts[1],
                        "event":  parts[2],
                        "detail": " ".join(parts[3:]),
                        "fail":   "FAIL" in parts[2] or "BLOCK" in parts[2]
                    })
    result.sort(key=lambda x: (x["date"], x["time"]), reverse=True)
    return jsonify(result)


# ── AGENT ROUTES ──────────────────────────────────────────────────────────────

@app.route("/agent/report", methods=["POST"])
def agent_report():
    secret   = request.headers.get("X-Agent-Secret", "")
    expected = hashlib.sha256("ehs508".encode()).hexdigest()
    if secret != expected:
        return jsonify({"error": "Unauthorized"}), 401

    data      = request.get_json()
    device_id = data.get("device_id")
    if not device_id:
        return jsonify({"error": "No device_id"}), 400

    prev = devices.get(device_id, {})
    devices[device_id] = {
        "name":             data.get("name", device_id),
        "os":               data.get("os", "Windows"),
        "processes":        data.get("processes", []),
        "cpu_total":        data.get("cpu_total", 0),
        "mem_used_gb":      data.get("mem_used_gb", 0),
        "last_seen":        time.time(),
        "screenshot":       data.get("screenshot"),
        "pending_kills":    prev.get("pending_kills", []),
        "pending_commands": prev.get("pending_commands", [])
    }

    # flush stale kills
    kills = [k for k in devices[device_id].pop("pending_kills", [])
             if time.time() - k["requested_at"] < 30]

    # flush stale commands
    cmds = [c for c in devices[device_id].pop("pending_commands", [])
            if time.time() - c["requested_at"] < 60]

    return jsonify({"pending_kills": kills, "pending_commands": cmds})


@app.route("/agent/console_result", methods=["POST"])
def agent_console_result():
    secret   = request.headers.get("X-Agent-Secret", "")
    expected = hashlib.sha256("ehs508".encode()).hexdigest()
    if secret != expected:
        return jsonify({"error": "Unauthorized"}), 401

    data   = request.get_json()
    cmd_id = data.get("cmd_id")
    if not cmd_id:
        return jsonify({"error": "No cmd_id"}), 400

    console_results[cmd_id] = {
        "output":    data.get("output", ""),
        "timestamp": time.time()
    }

    # Clean up old results (older than 10 minutes)
    cutoff = time.time() - 600
    for k in list(console_results.keys()):
        if console_results[k]["timestamp"] < cutoff:
            del console_results[k]

    return jsonify({"ok": True})


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[START] Control Panel API on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
