from fastapi import FastAPI, Request, Form, Cookie
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import subprocess, socket, requests, paramiko, winrm, re, os, json, sqlite3
from datetime import datetime
from auth import login, verify_session, logout
from database import get_devices, log_action, init_db, add_device, decrypt_password
from ilo import get_all_servers_status, get_server_info

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "gemma4:26b"
sessions = {}

init_db()

# Start real time event monitor
from events import start_event_monitor
start_event_monitor()

# ── Cache ────────────────────────────────────────────────────────────
import threading
import time

_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 60  # seconds

def get_cache(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["time"]) < CACHE_TTL:
            return entry["data"]
        return None

def set_cache(key, data):
    with _cache_lock:
        _cache[key] = {"data": data, "time": time.time()}

def refresh_cache():
    """Staggered background refresh - one thing at a time"""
    while True:
        # Servers every 30 seconds
        try:
            data = get_all_servers_status()
            set_cache("servers", data)
        except: pass
        time.sleep(10)

        # Switch every 40 seconds
        try:
            from switch import get_switch_summary
            data = get_switch_summary()
            set_cache("switch", data)
        except: pass
        time.sleep(10)

        # Topology every 60 seconds
        try:
            from topology import build_topology
            data = build_topology()
            set_cache("topology", data)
        except: pass
        time.sleep(10)

# Pre-warm cache on startup
def warm_cache():
    time.sleep(2)
    try:
        data = get_all_servers_status()
        set_cache("servers", data)
    except: pass
    try:
        from switch import get_switch_summary
        data = get_switch_summary()
        set_cache("switch", data)
    except: pass
    try:
        from topology import build_topology
        data = build_topology()
        set_cache("topology", data)
    except: pass

# Start background cache refresh
cache_thread = threading.Thread(target=refresh_cache, daemon=True)
cache_thread.start()
warm_thread = threading.Thread(target=warm_cache, daemon=True)
warm_thread.start()


# ── Auth helper ──────────────────────────────────────────────────────

def get_user(session_token):
    if not session_token:
        return None
    return verify_session(session_token)

# ── Diagnostic functions ─────────────────────────────────────────────

def check_ping(host):
    try:
        r = subprocess.run(["ping","-c","2","-W","2",host], capture_output=True, text=True)
        return r.returncode == 0
    except: return False

def check_port(host, port):
    try:
        s = socket.socket()
        s.settimeout(3)
        r = s.connect_ex((host, port))
        s.close()
        return r == 0
    except: return False

def check_dns(h):
    try: socket.gethostbyname(h); return True
    except: return False

def check_ip_conflict(ip):
    try:
        r = subprocess.run(["sudo","arp-scan","--localnet"], capture_output=True, text=True)
        matches = []
        for line in r.stdout.split('\n'):
            if ip in line and not line.startswith('WARNING'):
                p = line.split()
                if len(p) >= 2:
                    matches.append({"mac": p[1], "type": "DUPLICATE" if "DUP" in line else "PRIMARY"})
        if len(matches) > 1:
            msg = f"IP {ip} found on {len(matches)} devices: " + " | ".join([f"MAC {d['mac']} ({d['type']})" for d in matches])
            return True, msg
        elif len(matches) == 1:
            return False, f"Unique — MAC {matches[0]['mac']}"
        return False, "Not found on network scan"
    except Exception as e:
        return False, f"Scan failed: {e}"

def detect_os(ip):
    if check_port(ip, 5985): return "windows"
    if check_port(ip, 22): return "linux"
    return "unknown"

def deep_linux(ip, user, pw):
    d = {"os_type":"linux"}
    try:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        c.connect(ip, username=user, password=pw, timeout=5)
        cmds = {
            "ssh_service":"systemctl is-active ssh 2>/dev/null || echo inactive",
            "http_service":"systemctl is-active nginx 2>/dev/null || echo inactive",
            "firewall":"sudo ufw status 2>/dev/null || echo unknown",
            "disk_usage":"df -h / | tail -1 | awk '{print $5}'",
            "memory_usage":"free -m | awk 'NR==2{printf \"%s/%s MB\",$3,$2}'",
            "os_version":"cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            "ip_address":"ip addr show eth0 | grep 'inet ' | awk '{print $2}'",
            "default_route":"ip route | grep default | awk '{print $3}'"
        }
        for k,cmd in cmds.items():
            _,out,_ = c.exec_command(cmd)
            d[k] = out.read().decode().strip() or "not found"
        c.close()
        d["accessible"] = True
    except Exception as e:
        d["accessible"] = False; d["error"] = str(e)
    return d

def deep_windows(ip, user, pw):
    d = {"os_type":"windows"}
    try:
        s = winrm.Session(ip, auth=(user,pw), transport='ntlm', server_cert_validation='ignore')
        cmds = {
            "os_version":"Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption",
            "ip_address":"(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.IPAddress -ne '127.0.0.1'} | Select-Object -First 1).IPAddress",
            "disk_usage":"$d=Get-PSDrive C;[math]::Round($d.Used/($d.Used+$d.Free)*100),'%' -join ''",
            "memory_usage":"$o=Get-CimInstance Win32_OperatingSystem;\"$([math]::Round(($o.TotalVisibleMemorySize-$o.FreePhysicalMemory)/1KB))/$([math]::Round($o.TotalVisibleMemorySize/1KB)) MB\""
        }
        for k,cmd in cmds.items():
            try:
                r = s.run_ps(cmd)
                d[k] = r.std_out.decode().strip() or "not found"
            except: d[k] = "error"
        d["accessible"] = True
    except Exception as e:
        d["accessible"] = False; d["error"] = str(e)
    return d

def run_diagnostic(ip, gw, dns, user, pw):
    results = {
        "gateway_reachable": check_ping(gw),
        "server_reachable": check_ping(ip),
        "dns_working": check_dns(dns),
        "ssh_open": check_port(ip, 22),
        "winrm_open": check_port(ip, 5985),
        "http_open": check_port(ip, 80),
        "https_open": check_port(ip, 443),
    }
    conflict, conflict_info = check_ip_conflict(ip)
    os_type = detect_os(ip)
    if os_type == "linux": details = deep_linux(ip, user, pw)
    elif os_type == "windows": details = deep_windows(ip, user, pw)
    else: details = {"accessible": False, "error": "OS unknown"}
    return results, conflict, conflict_info, os_type, details

def ask_ai(prompt):
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": MODEL,
            "messages": [{"role":"user","content":prompt}],
            "stream": False, "think": False,
            "options": {"temperature":0.1,"num_predict":500,"num_ctx":4096}
        }, timeout=120)
        if r.status_code == 200:
            return r.json().get("message",{}).get("content","").strip() or "No response"
        return f"API error {r.status_code}"
    except Exception as e:
        return f"Error: {e}"


def ask_hermes(message, session_id=None):
    """Call Hermes agent and extract response text"""
    import subprocess
    try:
        cmd = ["hermes", "--yolo", "chat", "-q", message, "--skills", "nexdeploy-dc"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=os.path.dirname(os.path.abspath(__file__))
        )
        output = result.stdout + result.stderr
        
        # Extract text between the box characters
        lines = output.split("\n")
        response_lines = []
        inside_box = False
        
        for line in lines:
            if "╭" in line and "✦" in line:
                inside_box = True
                continue
            if "╰" in line and inside_box:
                inside_box = False
                break
            if inside_box:
                # Clean up the line
                cleaned = line.strip()
                if cleaned:
                    response_lines.append(cleaned)
        
        if response_lines:
            return " ".join(response_lines).strip()
        
        # Fallback - return anything useful
        return "I need more specific information to help. Can you provide more details?"
        
    except subprocess.TimeoutExpired:
        return "Request timed out. Please try again."
    except Exception as e:
        return f"Error calling Hermes: {str(e)}"

def extract_ip(text):
    m = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
    return m[0] if m else None

# ── Event log helpers ────────────────────────────────────────────────

def get_events(limit=50):
    try:
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT username, action, details, timestamp FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = c.fetchall()
        conn.close()
        events = []
        for row in rows:
            action = row[1]
            level = "info"
            if "FAIL" in action or "ERROR" in action or "Critical" in action:
                level = "error"
            elif "WARN" in action or "Warning" in action:
                level = "warning"
            elif "LOGIN" in action or "SUCCESS" in action:
                level = "success"
            events.append({
                "username": row[0],
                "action": action,
                "message": row[2] or action,
                "timestamp": row[3],
                "level": level
            })
        return events
    except Exception as e:
        return []

# ── Routes ───────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request=request, name="base.html", context={"user": user})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": error})

@app.post("/login")
async def do_login(username: str = Form(...), password: str = Form(...)):
    token, error = login(username, password)
    if error:
        return RedirectResponse(f"/login?error={error}", status_code=302)
    response = RedirectResponse("/", status_code=302)
    response.set_cookie("session_token", token, httponly=True, max_age=28800)
    return response

@app.get("/logout")
async def do_logout(session_token: str = Cookie(None)):
    if session_token:
        logout(session_token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session_token")
    return response

@app.get("/api/servers")
async def api_servers(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("servers")
        if cached:
            return JSONResponse(cached)
        data = get_all_servers_status()
        set_cache("servers", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/switch")
async def api_switch(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("switch")
        if cached:
            return JSONResponse(cached)
        from switch import get_switch_summary
        data = get_switch_summary()
        set_cache("switch", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/topology")
async def api_topology(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        cached = get_cache("topology")
        if cached:
            return JSONResponse(cached)
        from topology import build_topology
        data = build_topology()
        set_cache("topology", data)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/events")
async def api_events(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    events = get_events()
    return JSONResponse({"events": events})

@app.get("/api/devices")
async def api_devices(session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    devices = get_devices()
    return JSONResponse({"devices": devices})

@app.post("/api/devices")
async def api_add_device(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    try:
        add_device(
            name=data.get("name"),
            device_type=data.get("type"),
            ip=data.get("ip"),
            username=data.get("username"),
            password=data.get("password"),
            brand=data.get("brand", ""),
            model=data.get("model", ""),
            added_by=user
        )
        log_action(user, "ADD_DEVICE", f"Added {data.get('name')} ({data.get('ip')})")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

@app.delete("/api/devices/{device_id}")
async def api_delete_device(device_id: int, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    try:
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        conn.commit()
        conn.close()
        log_action(user, "DELETE_DEVICE", f"Deleted device ID {device_id}")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

@app.post("/api/change-password")
async def api_change_password(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    try:
        from database import get_user, verify_password, hash_password
        DB_PATH = os.path.expanduser("~/diagnostic-tool/nexdeploy.db")
        user_data = get_user(user)
        if not verify_password(data.get("current"), user_data["password_hash"]):
            return JSONResponse({"error": "Current password incorrect", "success": False})
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE users SET password_hash = ? WHERE username = ?",
                  (hash_password(data.get("new")), user))
        conn.commit()
        conn.close()
        log_action(user, "CHANGE_PASSWORD", "Password changed")
        return JSONResponse({"success": True})
    except Exception as e:
        return JSONResponse({"error": str(e), "success": False})

@app.post("/chat")
async def chat(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    sid = data.get("session_id", user)
    msg = data.get("message", "").strip()
    if sid not in sessions:
        sessions[sid] = {"step":"idle","data":{}}

    # Fetch live data from cache
    live_servers = ""
    live_switch = ""
    
    try:
        servers = get_cache("servers") or get_all_servers_status()
        for key, s in servers.items():
            if "error" not in s:
                live_servers += f"- {s.get('name', key)} ({s.get('ilo_ip', 'unknown IP')}) | Power: {s.get('power_state')} | Health: {s.get('overall_health')} | RAM: {s.get('ram_gb')}GB\n"
                hd = s.get('health_details', {})
                for k, v in hd.items():
                    if v != 'OK':
                        live_servers += f"  WARNING: {k.replace('_',' ')}: {v}\n"
    except:
        live_servers = "- S2D-Node1 (192.168.99.104) - HPE ProLiant DL380 Gen10\n- S2D-Node2 (192.168.99.102) - HPE ProLiant DL380 Gen10\n"

    try:
        switch = get_cache("switch") or {}
        live_switch = f"H3C Switch (192.168.99.5): {switch.get('up_count', '?')} ports UP, {switch.get('down_count', '?')} ports DOWN out of {switch.get('total_ports', '?')} total ports"
    except:
        live_switch = "H3C Switch (192.168.99.5): status unknown"

    context = f"""You are NexDeploy AI assistant for Cloudify Asia data centre.
You help engineers with server deployment, network diagnostics, and infrastructure management.
Answer in plain text only - no markdown symbols like * or #.
Keep responses short and practical - maximum 4 sentences.

LIVE SYSTEM STATUS (real time):

Servers:
{live_servers}
Network:
- {live_switch}
- Cisco Catalyst 3550 (192.168.99.144) - managed switch
- HarrisPlayground AI Server (192.168.99.142) - Ubuntu 24.04, runs NexDeploy

Engineer message: {msg}"""

    reply = ask_hermes(context)
    log_action(user, "CHAT", f"Message: {msg[:50]}")
    return JSONResponse({"reply": reply, "step": "idle"})

@app.post("/run_diagnostic")
async def run_diag(request: Request, session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    data = await request.json()
    ip = data.get("target_ip"); gw = data.get("gateway_ip")
    dns = data.get("dns_server","8.8.8.8"); usr = data.get("username"); pw = data.get("password")
    results, conflict, conflict_info, os_type, details = run_diagnostic(ip, gw, dns, usr, pw)
    failed = [k for k,v in results.items() if not v]
    passed = [k for k,v in results.items() if v]
    detail_str = ""
    if details.get("accessible"):
        detail_str = f"\nDeep scan ({os_type.upper()}):\n"
        for k,v in details.items():
            if k not in ["accessible","os_type","error"]:
                detail_str += f"- {k.replace('_',' ').title()}: {v}\n"
    else:
        detail_str = f"\nCould not access: {details.get('error','unknown')}"
    prompt = f"""You are a network engineer. Server at {ip} ({os_type.upper()}).
PASSED: {', '.join(passed) if passed else 'None'}
FAILED: {', '.join(failed) if failed else 'None'}
IP conflict: {conflict_info}
{detail_str}
Give SHORT summary (3-4 sentences) of what is wrong and the most important fix."""
    reply = ask_ai(prompt)
    return JSONResponse({
        "reply": reply, "results": results,
        "conflict": conflict, "conflict_info": conflict_info,
        "os_type": os_type,
        "details": {k:v for k,v in details.items() if k not in ["accessible","error"]},
        "accessible": details.get("accessible", False),
        "show_report_btn": True,
        "target_ip": ip, "gateway_ip": gw, "dns_server": dns, "username": usr, "password": pw
    })

@app.get("/report", response_class=HTMLResponse)
async def report(request: Request, target_ip: str = "", gateway_ip: str = "",
                 dns_server: str = "8.8.8.8", username: str = "", password: str = "",
                 session_token: str = Cookie(None)):
    user = get_user(session_token)
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request=request, name="report.html", context={"target_ip": target_ip, "gateway_ip": gateway_ip, "dns_server": dns_server, "username": username, "password": password})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
